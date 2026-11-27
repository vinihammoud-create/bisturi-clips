"""P4.2 (parte de trás): o módulo que sabe se o modelo de voz está no PC e o baixa com progresso, cancelamento e retomada, na revisão
FIXADA do `componentes.json`; e o `transcribe.get_model`, que passa a pasta do modelo ao faster-whisper quando ela está inteira.

Nada aqui usa a rede nem o cache real do Hugging Face: o "servidor" é falso (troca `modelo_voz._fluxo`), o catálogo é um
`componentes.json` temporário e o cache é uma pasta temporária. Teste de concorrência espera por condição, com prazo."""
from __future__ import annotations

import errno
import hashlib
import json
import re
import sys
import threading
import time
import types
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

import cs2clip.modelo_voz as mv
import cs2clip.transcribe as tr

RAIZ = Path(__file__).resolve().parents[1]
REV = "ab" * 20                       # revisão falsa de 40 caracteres
REV_OUTRA = "cd" * 20
REPO = "org-falsa/faster-whisper-falso"
REPO_LEVE = "org-falsa/faster-whisper-leve"

# Conteúdo dos arquivos do modelo falso: os pequenos primeiro, o grande (LFS, com sha256) por último, como no catálogo de verdade.
CONTEUDO = {
    "config.json": b'{"falso": 1}',
    "vocabulary.json": b'["a", "b"]',
    "model.bin": bytes(range(256)) * 40,          # 10.240 bytes
}
TOTAL = sum(len(c) for c in CONTEUDO.values())


def _git_blob(dados: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(dados) + dados).hexdigest()


def _entrada(repo: str, revisao: str, conteudo: dict[str, bytes]) -> dict:
    arquivos = {}
    for nome, dados in conteudo.items():
        info: dict = {"tamanho_bytes": len(dados)}
        if nome == "model.bin":
            info["sha256"] = hashlib.sha256(dados).hexdigest()
        else:
            info["git_blob"] = _git_blob(dados)
        arquivos[nome] = info
    return {"repo": repo, "revision": revisao, "tamanho_bytes": sum(len(c) for c in conteudo.values()), "arquivos": arquivos}


@pytest.fixture
def catalogo(tmp_path, monkeypatch):
    """Um componentes.json falso com os dois nomes que o programa gerencia (o turbo e o small), ambos com o conteúdo de CONTEUDO."""
    arq = tmp_path / "componentes.json"
    arq.write_text(json.dumps({"modelos_de_voz": {
        "large-v3-turbo": _entrada(REPO, REV, CONTEUDO),
        "small": _entrada(REPO_LEVE, REV, CONTEUDO),
    }}), encoding="utf-8")
    monkeypatch.setattr(mv, "ARQUIVO_COMPONENTES", arq)
    return arq


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "hub"


def _pasta_repo(cache: Path, repo: str = REPO) -> Path:
    return cache / ("models--" + repo.replace("/", "--"))


def _snapshot(cache: Path, repo: str = REPO, revisao: str = REV) -> Path:
    return _pasta_repo(cache, repo) / "snapshots" / revisao


def _incompleto(cache: Path, nome: str = "model.bin") -> Path:
    blob = hashlib.sha256(CONTEUDO[nome]).hexdigest() if nome == "model.bin" else _git_blob(CONTEUDO[nome])
    return _pasta_repo(cache) / "blobs" / f"{blob}.incomplete"


def _montar(cache: Path, repo: str = REPO, revisao: str = REV, sem: tuple[str, ...] = (), cortar: dict[str, int] | None = None) -> Path:
    """Escreve o snapshot direto no cache (como o huggingface_hub deixa no Windows sem link simbólico)."""
    snap = _snapshot(cache, repo, revisao)
    snap.mkdir(parents=True, exist_ok=True)
    for nome, dados in CONTEUDO.items():
        if nome in sem:
            continue
        n = (cortar or {}).get(nome, len(dados))
        (snap / nome).write_bytes(dados[:n])
    return snap


def _esperar(condicao, prazo: float = 5.0) -> bool:
    fim = time.monotonic() + prazo
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.01)
    return condicao()


class ConnectError(Exception):
    """Mesmo nome da exceção do cliente HTTP do huggingface_hub (sem internet)."""


class ErroHttp(OSError):
    def __init__(self, status: int):
        super().__init__(f"{status} Server Error")
        self.response = types.SimpleNamespace(status_code=status)


class Servidor:
    """O Hugging Face falso. `pedidos` = (arquivo, byte de início) de cada pedido. `travar_em=(arquivo, n)` entrega n bytes e fica parado
    (rede travada) até `soltar`; depois a conexão "cai" (o fluxo acaba antes do fim)."""

    def __init__(self, conteudo: dict[str, bytes] | None = None, pedaco: int = 1024):
        self.conteudo = dict(conteudo or CONTEUDO)
        self.pedaco = pedaco
        self.pedidos: list[tuple[str, int]] = []
        self.travar_em: tuple[str, int] | None = None
        self.travado = threading.Event()
        self.soltar = threading.Event()
        self.erro: BaseException | None = None
        self.ignora_range = False

    @contextmanager
    def fluxo(self, url: str, inicio: int):
        nome = url.rsplit("/", 1)[-1]
        self.pedidos.append((nome, inicio))
        if self.erro is not None:
            raise self.erro
        dados = self.conteudo[nome]
        comeco = 0 if self.ignora_range else inicio

        def pedacos():
            pos = comeco
            while pos < len(dados):
                limite = len(dados)
                if self.travar_em and self.travar_em[0] == nome:
                    if pos >= self.travar_em[1]:
                        self.travado.set()
                        self.soltar.wait(10)
                        return
                    limite = self.travar_em[1]
                fim = min(pos + self.pedaco, limite)
                yield dados[pos:fim]
                pos = fim
        yield (200 if comeco == 0 else 206), pedacos()


@pytest.fixture
def servidor(monkeypatch):
    s = Servidor()
    monkeypatch.setattr(mv, "_fluxo", s.fluxo)
    monkeypatch.setattr(mv, "_espaco_livre", lambda pasta: 10**12)
    monkeypatch.setattr(mv, "ESPERA_ENTRE_TENTATIVAS_S", 0.0)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    yield s
    s.soltar.set()                                   # nenhum trabalho de fundo fica preso no fim do teste
    assert _esperar(lambda: not mv.em_andamento(), 5), "o download de fundo não terminou"


@pytest.fixture(autouse=True)
def _sem_rede_de_verdade(monkeypatch):
    def proibido(url, inicio):
        raise AssertionError(f"teste tentou a rede de verdade: {url}")
    monkeypatch.setattr(mv, "_fluxo", contextmanager(proibido))


# ── estado ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_estado_ausente(catalogo, cache):
    e = mv.estado("large-v3-turbo", cache_dir=cache)
    assert e.gerenciado and e.presente is False
    assert e.tamanho_bytes == TOTAL and e.baixado_bytes == 0
    assert e.revisao == REV and e.pasta == _snapshot(cache)
    assert mv.caminho_do_modelo("large-v3-turbo", cache_dir=cache) is None


def test_estado_parcial_conta_o_que_ja_veio_incluindo_o_incomplete(catalogo, cache):
    _montar(cache, sem=("model.bin",))
    inc = _incompleto(cache)
    inc.parent.mkdir(parents=True, exist_ok=True)
    inc.write_bytes(CONTEUDO["model.bin"][:3000])
    e = mv.estado("large-v3-turbo", cache_dir=cache)
    assert e.presente is False
    assert e.baixado_bytes == len(CONTEUDO["config.json"]) + len(CONTEUDO["vocabulary.json"]) + 3000


def test_estado_completo(catalogo, cache):
    snap = _montar(cache)
    e = mv.estado("large-v3-turbo", cache_dir=cache)
    assert e.presente is True and e.baixado_bytes == e.tamanho_bytes == TOTAL
    assert mv.caminho_do_modelo("large-v3-turbo", cache_dir=cache) == snap


@pytest.mark.parametrize("nome", ["tiny", "base", "medium", "large-v3", "turbo", "org/outro-modelo"])
def test_modelo_desconhecido_nao_e_gerenciado(catalogo, cache, nome):
    e = mv.estado(nome, cache_dir=cache)
    assert e.presente is None and not e.gerenciado and e.pasta is None and e.revisao is None
    assert mv.caminho_do_modelo(nome, cache_dir=cache) is None
    with pytest.raises(mv.ModeloNaoGerenciado):
        mv.baixar(nome, cache_dir=cache)


# ── caminho_do_modelo: só snapshot completo e da revisão fixada ───────────────────────────────────────────────────────────────────────
def test_caminho_recusa_snapshot_sem_um_arquivo(catalogo, cache):
    _montar(cache, sem=("vocabulary.json",))
    assert mv.caminho_do_modelo("large-v3-turbo", cache_dir=cache) is None


def test_caminho_recusa_arquivo_cortado(catalogo, cache):
    _montar(cache, cortar={"model.bin": 5000})
    assert mv.caminho_do_modelo("large-v3-turbo", cache_dir=cache) is None
    assert mv.estado("large-v3-turbo", cache_dir=cache).presente is False


def test_caminho_recusa_outra_revisao_e_o_estado_conta_qual_e(catalogo, cache):
    _montar(cache, revisao=REV_OUTRA)                # o modelo inteiro, mas de outro commit
    assert mv.caminho_do_modelo("large-v3-turbo", cache_dir=cache) is None
    e = mv.estado("large-v3-turbo", cache_dir=cache)
    assert e.presente is False and e.outras_revisoes == (REV_OUTRA,)


def test_caminho_nunca_quebra_com_catalogo_ruim(tmp_path, monkeypatch, cache):
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{ isto não é json", encoding="utf-8")
    monkeypatch.setattr(mv, "ARQUIVO_COMPONENTES", ruim)
    assert mv.caminho_do_modelo("large-v3-turbo", cache_dir=cache) is None
    monkeypatch.setattr(mv, "ARQUIVO_COMPONENTES", tmp_path / "nao_existe.json")
    assert mv.caminho_do_modelo("large-v3-turbo", cache_dir=cache) is None


@pytest.mark.parametrize("arquivo", ["../fora.bin", "sub/model.bin", "..", ""])
def test_catalogo_com_nome_de_arquivo_perigoso_e_recusado(tmp_path, monkeypatch, cache, arquivo):
    entrada = _entrada(REPO, REV, {arquivo: b"x"})
    arq = tmp_path / "c.json"
    arq.write_text(json.dumps({"modelos_de_voz": {"large-v3-turbo": entrada}}), encoding="utf-8")
    monkeypatch.setattr(mv, "ARQUIVO_COMPONENTES", arq)
    with pytest.raises(ValueError):
        mv.estado("large-v3-turbo", cache_dir=cache)
    assert mv.caminho_do_modelo("large-v3-turbo", cache_dir=cache) is None


# ── baixar ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_baixar_do_zero_monta_o_snapshot_da_revisao_fixada(catalogo, cache, servidor):
    avisos: list[tuple] = []
    pasta = mv.baixar("large-v3-turbo", ao_progredir=lambda *a: avisos.append(a), cache_dir=cache)
    assert pasta == _snapshot(cache) and mv.caminho_do_modelo("large-v3-turbo", cache_dir=cache) == pasta
    for nome, dados in CONTEUDO.items():
        assert (pasta / nome).read_bytes() == dados
    assert sorted(servidor.pedidos) == sorted((n, 0) for n in CONTEUDO)
    assert not list((_pasta_repo(cache) / "blobs").glob("*.incomplete"))
    # progresso em bytes: começa no que já havia, nunca passa do total, nunca volta, e o último aviso é 100 %
    assert avisos[0][:2] == (0, TOTAL) and avisos[-1][:2] == (TOTAL, TOTAL) and avisos[-1][3] == 0
    baixados = [a[0] for a in avisos]
    assert baixados == sorted(baixados) and all(a[1] == TOTAL for a in avisos)


def test_baixar_o_que_ja_esta_completo_nao_pede_nada(catalogo, cache, servidor):
    snap = _montar(cache)
    assert mv.baixar("large-v3-turbo", cache_dir=cache) == snap
    assert servidor.pedidos == []


def test_cancelar_para_em_ate_1s_e_guarda_o_que_veio(catalogo, cache, servidor):
    servidor.travar_em = ("model.bin", 4096)         # a rede trava no meio do arquivo grande
    cancelar = threading.Event()
    resultado: dict = {}

    def rodar():
        try:
            mv.baixar("large-v3-turbo", cancelar=cancelar, cache_dir=cache)
        except BaseException as e:  # noqa: BLE001
            resultado["erro"] = e

    t = threading.Thread(target=rodar, daemon=True)
    t.start()
    assert servidor.travado.wait(5), "o download não chegou ao ponto travado"
    cancelar.set()
    t.join(1.0)
    assert not t.is_alive(), "cancelar não interrompeu em até 1 segundo"
    assert isinstance(resultado.get("erro"), mv.DownloadCancelado)
    assert mv.em_andamento("large-v3-turbo")          # o trabalho de fundo ainda espera a rede travada...
    servidor.soltar.set()                            # ...a rede volta: ele vê o pedido de parar e sai sem apagar nada
    assert _esperar(lambda: not mv.em_andamento(), 5)
    inc = _incompleto(cache)
    assert inc.exists() and inc.read_bytes() == CONTEUDO["model.bin"][:4096]
    assert mv.estado("large-v3-turbo", cache_dir=cache).baixado_bytes == TOTAL - len(CONTEUDO["model.bin"]) + 4096


def test_retomar_so_baixa_o_que_falta(catalogo, cache, servidor):
    servidor.travar_em = ("model.bin", 4096)
    cancelar = threading.Event()
    erros: list[BaseException] = []

    def rodar():
        try:
            mv.baixar("large-v3-turbo", cancelar=cancelar, cache_dir=cache)
        except BaseException as e:  # noqa: BLE001
            erros.append(e)

    t = threading.Thread(target=rodar, daemon=True)
    t.start()
    assert servidor.travado.wait(5)
    cancelar.set()
    t.join(5)
    assert not t.is_alive() and len(erros) == 1 and isinstance(erros[0], mv.DownloadCancelado)
    servidor.soltar.set()
    assert _esperar(lambda: not mv.em_andamento(), 5)
    servidor.pedidos.clear()
    servidor.travar_em = None
    avisos: list[tuple] = []
    pasta = mv.baixar("large-v3-turbo", ao_progredir=lambda *a: avisos.append(a), cache_dir=cache)
    assert servidor.pedidos == [("model.bin", 4096)]           # os pequenos já estavam; o grande continua do byte 4096
    assert (pasta / "model.bin").read_bytes() == CONTEUDO["model.bin"]
    assert avisos[0][0] == TOTAL - len(CONTEUDO["model.bin"]) + 4096 and avisos[-1][:2] == (TOTAL, TOTAL)


def test_retomar_quando_o_servidor_ignora_o_range_recomeca_o_arquivo_sem_corromper(catalogo, cache, servidor):
    inc = _incompleto(cache)
    inc.parent.mkdir(parents=True, exist_ok=True)
    inc.write_bytes(CONTEUDO["model.bin"][:2000])
    servidor.ignora_range = True                     # responde 200 com o arquivo inteiro
    pasta = mv.baixar("large-v3-turbo", cache_dir=cache)
    assert (pasta / "model.bin").read_bytes() == CONTEUDO["model.bin"]


def test_arquivo_corrompido_e_recusado_e_descartado(catalogo, cache, servidor):
    errado = bytearray(CONTEUDO["model.bin"])
    errado[100] ^= 0xFF
    servidor.conteudo["model.bin"] = bytes(errado)
    with pytest.raises(mv.ErroDownloadModelo, match="diferente do esperado"):
        mv.baixar("large-v3-turbo", cache_dir=cache)
    assert not _incompleto(cache).exists()
    assert mv.caminho_do_modelo("large-v3-turbo", cache_dir=cache) is None


@pytest.mark.parametrize("erro, trecho", [
    (ConnectError("[Errno 11001] getaddrinfo failed"), "internet"),
    (ErroHttp(503), "fora do ar"),
    (OSError(errno.ENOSPC, "No space left on device"), "espaço"),
])
def test_erros_viram_mensagem_simples(catalogo, cache, servidor, erro, trecho):
    servidor.erro = erro
    with pytest.raises(mv.ErroDownloadModelo) as exc:
        mv.baixar("large-v3-turbo", cache_dir=cache)
    assert trecho in str(exc.value)
    assert "Traceback" not in str(exc.value)


def test_sem_espaco_no_disco_avisa_antes_de_comecar_e_diz_quanto_falta(catalogo, cache, servidor, monkeypatch):
    monkeypatch.setattr(mv, "_espaco_livre", lambda pasta: 1000)
    with pytest.raises(mv.ErroDownloadModelo) as exc:
        mv.baixar("large-v3-turbo", cache_dir=cache)
    texto = str(exc.value)
    assert "espaço" in texto and "Libere" in texto and "MB" in texto
    assert servidor.pedidos == []                    # nem começou


def test_hf_hub_offline_nao_tenta_a_rede(catalogo, cache, servidor, monkeypatch):
    import huggingface_hub.constants  # noqa: F401  (importa ANTES de ligar o offline: senão a constante do processo fica "offline" para sempre)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(mv.ErroDownloadModelo, match="HF_HUB_OFFLINE"):
        mv.baixar("large-v3-turbo", cache_dir=cache)
    assert servidor.pedidos == []
    snap = _montar(cache)
    assert mv.baixar("large-v3-turbo", cache_dir=cache) == snap   # já presente: devolve sem rede
    assert servidor.pedidos == []


# ── o fluxo HTTP de verdade (sessão do huggingface_hub), contra um servidor LOCAL que entende Range: nada sai do PC ─────────────────────
@pytest.fixture
def servidor_local():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    dados = CONTEUDO["model.bin"]
    vistos: list[dict] = []

    class Tratador(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            vistos.append({k.lower(): v for k, v in self.headers.items()})
            if not self.path.endswith("/model.bin"):
                self.send_response(404)
                self.end_headers()
                return
            faixa = self.headers.get("Range")
            inicio = int(faixa.split("=")[1].rstrip("-")) if faixa else 0
            corpo = dados[inicio:]
            self.send_response(206 if faixa else 200)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, format, *args):  # noqa: A002  (o nome vem da classe-mãe)
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Tratador)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", vistos
    srv.shutdown()
    srv.server_close()


def test_fluxo_de_verdade_pede_range_e_entrega_os_bytes(servidor_local, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    base, vistos = servidor_local
    with mv._fluxo_hf(f"{base}/resolve/{REV}/model.bin", 0) as (status, pedacos):
        assert status == 200 and b"".join(pedacos) == CONTEUDO["model.bin"]
    with mv._fluxo_hf(f"{base}/resolve/{REV}/model.bin", 4096) as (status, pedacos):
        assert status == 206 and b"".join(pedacos) == CONTEUDO["model.bin"][4096:]
    assert vistos[1].get("range") == "bytes=4096-" and "authorization" not in vistos[1]
    assert vistos[1].get("user-agent", "").startswith("BisturiClips/") and "/None" not in vistos[1]["user-agent"]
    assert vistos[1].get("accept-encoding") == "identity"


def test_fluxo_de_verdade_erro_http_vira_mensagem(servidor_local, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    base, _ = servidor_local
    with pytest.raises(Exception) as exc:
        with mv._fluxo_hf(f"{base}/resolve/{REV}/nao_existe.bin", 0):
            pass
    assert mv._status_http(exc.value) == 404 and not mv._passageiro(exc.value)
    assert "erro 404" in str(mv._traduzir(exc.value))


# ── velocidade suavizada, tempo restante e ritmo dos avisos ───────────────────────────────────────────────────────────────────────────
def test_velocidade_e_a_media_dos_ultimos_segundos():
    m = mv.MedidorVelocidade(janela_s=5.0)
    assert m.velocidade() == 0.0
    for s in range(11):                              # 1 MB/s constante por 10 s
        m.registrar(float(s), s * 1_000_000)
    assert m.velocidade() == pytest.approx(1_000_000)
    m.registrar(10.5, 10_000_000 + 5_000_000)        # um pico de 5 MB em meio segundo não faz a velocidade saltar para 10 MB/s
    assert m.velocidade() == pytest.approx((15_000_000 - 5_000_000) / 5.5)
    for s in range(11, 20):                          # a rede parou: a velocidade cai aos poucos até zero
        m.registrar(float(s), 15_000_000)
    assert m.velocidade() == 0.0


def test_tempo_restante():
    assert mv.tempo_restante(1000, 400, 100.0) == pytest.approx(6.0)
    assert mv.tempo_restante(1000, 400, 0.0) is None
    assert mv.tempo_restante(1000, 1000, 0.0) == 0.0


def test_avisos_no_maximo_duas_vezes_por_segundo():
    m = mv.MedidorVelocidade(intervalo_s=0.5)
    vezes = [t / 10 for t in range(0, 21)]           # de 0 a 2 s, de 0,1 em 0,1
    avisou = [t for t in vezes if m.hora_de_avisar(t)]
    assert avisou == [0.0, 0.5, 1.0, 1.5, 2.0]


# ── transcribe.get_model usa a pasta quando ela está inteira, e o nome quando não ─────────────────────────────────────────────────────
@pytest.fixture
def whisper_falso(monkeypatch):
    pedidos: list[tuple[str, str]] = []
    gpu = {"carrega": True}

    class Falso:
        def __init__(self, fonte, device, compute_type):
            pedidos.append((fonte, device))
            if device == "cuda" and not gpu["carrega"]:
                raise RuntimeError("CUDA error 35")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Falso))
    monkeypatch.setattr(tr, "_add_cuda_dlls", lambda: None)
    monkeypatch.setattr(tr, "_MODEL", None)
    monkeypatch.setattr(tr, "_MODEL_NA_CPU", False)
    monkeypatch.setattr(tr, "_MODEL_CHAVE", None)
    tr.zerar_troca_de_modelo()
    yield pedidos, gpu
    tr.liberar_modelo()
    tr.zerar_troca_de_modelo()


def test_get_model_usa_a_pasta_quando_o_modelo_esta_inteiro(catalogo, cache, whisper_falso, monkeypatch):
    pedidos, _ = whisper_falso
    snap = _montar(cache)
    monkeypatch.setattr(mv, "_cache_padrao", lambda: cache)
    tr.get_model("large-v3-turbo")
    assert pedidos == [(str(snap), "cuda")]


def test_get_model_usa_o_nome_quando_falta_ou_e_outra_revisao(catalogo, cache, whisper_falso, monkeypatch):
    pedidos, _ = whisper_falso
    monkeypatch.setattr(mv, "_cache_padrao", lambda: cache)
    _montar(cache, revisao=REV_OUTRA)
    tr.get_model("large-v3-turbo")
    tr.get_model("base")                             # não gerenciado
    assert pedidos == [("large-v3-turbo", "cuda"), ("base", "cuda")]


def test_get_model_queda_para_o_small_tambem_usa_a_pasta_dele(catalogo, cache, whisper_falso, monkeypatch):
    pedidos, gpu = whisper_falso
    monkeypatch.setattr(mv, "_cache_padrao", lambda: cache)
    leve = _montar(cache, repo=REPO_LEVE)            # só o small está no PC
    gpu["carrega"] = False
    tr.get_model("large-v3-turbo")
    assert pedidos == [("large-v3-turbo", "cuda"), (str(leve), "cpu")]
    assert (tr.troca_de_modelo() or {}).get("modelo_usado") == "small"   # o registro continua com o NOME, não com a pasta


def test_transcribe_com_a_pasta_continua_entregando_as_palavras(catalogo, cache, monkeypatch):
    """O caminho de ponta a ponta: com a pasta, o modelo falso transcreve igual (as palavras não dependem de como o modelo foi achado)."""
    snap = _montar(cache)
    monkeypatch.setattr(mv, "_cache_padrao", lambda: cache)
    fontes = []

    class Falso:
        def __init__(self, fonte, device, compute_type):
            fontes.append(fonte)

        def transcribe(self, audio, **k):
            seg = types.SimpleNamespace(words=[types.SimpleNamespace(start=0.0, end=0.5, word=" oi ")])
            return iter([seg]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Falso))
    monkeypatch.setattr(tr, "_add_cuda_dlls", lambda: None)
    monkeypatch.setattr(tr, "_MODEL", None)
    monkeypatch.setattr(tr, "_MODEL_CHAVE", None)
    try:
        palavras = tr.transcribe(np.zeros(16000, np.float32), 2.0, "large-v3-turbo")
    finally:
        tr.liberar_modelo()
    assert [(w.start, w.text) for w in palavras] == [(2.0, "oi")] and fontes == [str(snap)]


def test_a_suite_nao_enxerga_o_cache_real():
    """O conftest aponta o cache padrão do módulo para uma pasta temporária: nenhum teste depende do modelo que o dono tem no PC."""
    assert ".pytest_tmp" in str(mv._cache_padrao())


# ── componentes.json de verdade ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_componentes_json_fixa_revisao_e_tamanhos_dos_dois_modelos(tmp_path):
    dados = json.loads((RAIZ / "componentes.json").read_text(encoding="utf-8"))
    modelos = dados["modelos_de_voz"]
    # os MESMOS repositórios que o faster-whisper 1.2.1 usa para esses nomes (o cache de quem já tem o modelo continua valendo)
    assert modelos["large-v3-turbo"]["repo"] == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    assert modelos["small"]["repo"] == "Systran/faster-whisper-small"
    for nome in ("large-v3-turbo", "small"):
        m = modelos[nome]
        assert re.fullmatch(r"[0-9a-f]{40}", m["revision"]), nome
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", m["conferido_em"]), nome
        assert m["tamanho_bytes"] > 0
        arquivos = m["arquivos"]
        assert sum(a["tamanho_bytes"] for a in arquivos.values()) == m["tamanho_bytes"], nome
        assert {"config.json", "model.bin", "tokenizer.json"} <= set(arquivos), nome
        assert any(n.startswith("vocabulary.") for n in arquivos), nome
        assert not {"README.md", ".gitattributes"} & set(arquivos), nome      # o faster-whisper não baixa esses
        for a in arquivos.values():
            assert a["tamanho_bytes"] > 0
            assert re.fullmatch(r"[0-9a-f]{64}", a.get("sha256", "")) or re.fullmatch(r"[0-9a-f]{40}", a.get("git_blob", ""))
    # o módulo lê o arquivo de verdade sem reclamar
    for nome in ("large-v3-turbo", "small"):
        e = mv.estado(nome, cache_dir=tmp_path / "hub")
        assert e.gerenciado and e.tamanho_bytes == modelos[nome]["tamanho_bytes"] and e.revisao == modelos[nome]["revision"]
