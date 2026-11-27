"""P5 (instalador), item 8: testes do `instalador/smoke/smoke.py`.

A lógica do smoke é provada com falsos (processo, cliente HTTP, ffmpeg, retrato de processos): nada pesado sobe e nada usa a rede. Os testes
marcados `lento` sobem a interface de verdade (e processam um vídeo de 60 s), na suíte completa.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

RAIZ = Path(__file__).resolve().parents[1]


def _carregar(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


smoke = _carregar("smoke_p5", RAIZ / "instalador" / "smoke" / "smoke.py")
ErroSmoke = smoke.ErroSmoke


# ── falsos ────────────────────────────────────────────────────────────────────────────────────────
class Processo:
    def __init__(self, pid: int = 4242):
        self.pid, self.codigo = pid, None

    def poll(self):
        return self.codigo

    def sair(self, codigo: int = 0):
        self.codigo = codigo


class Servidor:
    """A interface de mentira: responde como a de verdade (token, 403, fila, receita, encerrar) e anota os pedidos."""

    def __init__(self, ctx, processo: Processo, *, versao: str = "1.1.0", token: str = "tok-secreto", exige_token: bool = True,
                 codigo_saida: int = 0, estado_fila: str = "pronto", estado_receita: str = "pronta", encerra: bool = True,
                 grava_momentos: bool = True, qualidade: str | None = None, token_no_html: bool = True):
        self.ctx, self.processo, self.versao, self.token = ctx, processo, versao, token
        self.exige_token, self.codigo_saida, self.estado_fila, self.estado_receita = exige_token, codigo_saida, estado_fila, estado_receita
        self.encerra, self.grava_momentos, self.qualidade, self.token_no_html = encerra, grava_momentos, qualidade, token_no_html
        self.pedidos: list[tuple[str, str]] = []
        self.jobs: dict[str, dict] = {}

    def tratar(self, metodo: str, caminho: str, corpo, usado: str) -> tuple[int, Any]:
        self.pedidos.append((metodo, caminho))
        autorizado = (usado == self.token) or not self.exige_token
        if caminho == "/health":
            return 200, {"ok": True}
        if caminho == "/":
            html = f'<meta name="ui-token" content="{self.token if self.token_no_html else "__UI_TOKEN__"}">'
            return 200, html
        if caminho.startswith("/api/") and not autorizado:
            return 403, {"erro": "sessão expirada"}
        if caminho == "/api/versao":
            return 200, {"versao": self.versao}
        if caminho == "/api/encerrar":
            if self.encerra:
                self.processo.sair(0)
            return 200, {"sucesso": True}
        if caminho == "/api/entrada":
            pasta = self.ctx.teste / "entrada"
            return 200, {"itens": [{"nome": p.name} for p in sorted(pasta.iterdir())] if pasta.is_dir() else []}
        if caminho == "/api/fila" and metodo == "POST":
            nome = corpo["arquivos"][0].removeprefix("entrada:")
            self._cria_job(nome)
            return 200, {"itens": [self.jobs[nome]]}
        if caminho == "/api/fila":
            return 200, {"itens": list(self.jobs.values())}
        if caminho == "/api/trabalhos" and metodo == "POST":
            self._cria_job(Path(corpo["texto"]).name)
            return 200, {"id": "r1", "estado": "na_fila"}
        if caminho == "/api/trabalhos/r1":
            return 200, {"id": "r1", "estado": self.estado_receita, "erro": "", "linhas": []}
        if caminho == "/api/indice":
            return 200, {"itens": [{"id": Path(n).stem, "arquivo": n} for n in self.jobs]}
        return 404, {"erro": caminho}

    def _cria_job(self, nome: str) -> None:
        self.jobs[nome] = {"id": f"j{len(self.jobs)}", "nome": nome, "estado": self.estado_fila, "codigo_saida": self.codigo_saida,
                           "mensagem": "", "detalhes": []}
        pasta = self.ctx.teste / "saida" / Path(nome).stem
        pasta.mkdir(parents=True, exist_ok=True)
        if self.grava_momentos:
            (pasta / "momentos.json").write_text(json.dumps({"formato": 1, "momentos": []}), encoding="utf-8")
        if self.qualidade is not None:
            (pasta / "qualidade.json").write_text(self.qualidade, encoding="utf-8")


class Cliente:
    def __init__(self, servidor: Servidor, base: str, token: str):
        self.servidor, self.token = servidor, token

    def pedir(self, metodo, caminho, corpo=None, com_token=True):
        return self.servidor.tratar(metodo, caminho, corpo, self.token if com_token else "")


class Relogio:
    def __init__(self):
        self.agora = 0.0

    def __call__(self) -> float:
        return self.agora

    def dormir(self, s: float) -> None:
        self.agora += max(float(s), 0.01)


def rodar_falso(*, ffmpeg_ok: bool = True):
    """Um `subprocess.run` de mentira: o ffmpeg cria o arquivo de saída; os demais comandos devolvem o que o teste combinar."""
    chamadas: list[list[str]] = []

    def rodar(cmd, **kw):
        chamadas.append(list(cmd))
        assert kw.get("shell") is False                      # nunca shell
        assert isinstance(cmd, list) and all(isinstance(x, str) for x in cmd)
        if Path(cmd[0]).name.startswith("ffmpeg"):
            if ffmpeg_ok:
                Path(cmd[-1]).write_bytes(b"video")
            return SimpleNamespace(returncode=0 if ffmpeg_ok else 1, stdout="", stderr="" if ffmpeg_ok else "erro do ffmpeg")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    rodar.chamadas = chamadas                               # type: ignore[attr-defined]
    return rodar


@pytest.fixture
def ctx(tmp_path):
    raiz, dados = tmp_path / "programa com espaço", tmp_path / "dados ação"
    (raiz / "cs2clip").mkdir(parents=True)
    (raiz / "ui").mkdir()
    dados.mkdir()
    (raiz / "cs2clip" / "__init__.py").write_text("", encoding="utf-8")
    (raiz / "ui" / "__main__.py").write_text("", encoding="utf-8")
    relogio, saidas = Relogio(), []
    c = smoke.Contexto(raiz=raiz, dados=dados, python=Path(sys.executable), ffmpeg="ffmpeg-falso", dormir=relogio.dormir, relogio=relogio,
                       saida=saidas.append)
    c.teste = smoke.criar_pasta_de_teste(dados)
    c.saidas = saidas                                       # type: ignore[attr-defined]
    c.rodar = rodar_falso()
    return c


def ligar(ctx, **opcoes) -> Servidor:
    """Põe a interface falsa no contexto (processo vivo + cliente que fala com ela)."""
    processo = Processo()
    servidor = Servidor(ctx, processo, **opcoes)
    ctx.subir = lambda c, porta: processo
    ctx.novo_cliente = lambda base, token: Cliente(servidor, base, token)
    ctx.versao = "1.1.0"
    return servidor


# ── funções puras ─────────────────────────────────────────────────────────────────────────────────
def test_extrair_token_do_html():
    html = '<head><meta charset="utf-8"><meta name="ui-token" content="abc123_-XY"></head>'
    assert smoke.extrair_token(html) == "abc123_-XY"


@pytest.mark.parametrize("html", ["", "<html>sem meta</html>", '<meta name="ui-token" content="">', '<meta name="ui-token" content="__UI_TOKEN__">'])
def test_extrair_token_ausente_ou_modelo_nao_vale(html):
    assert smoke.extrair_token(html) == ""


def test_porta_fica_na_faixa_da_interface_e_pula_as_ocupadas():
    assert smoke.escolher_porta(lambda p: False) == 8765
    assert smoke.escolher_porta(lambda p: p < 8768) == 8768
    assert smoke.escolher_porta(lambda p: p != 8775) == 8775


def test_sem_porta_livre_a_mensagem_e_clara():
    with pytest.raises(ErroSmoke, match="porta livre"):
        smoke.escolher_porta(lambda p: True)


def test_porta_real_esta_livre_de_verdade():
    porta = smoke.escolher_porta()
    assert 8765 <= porta <= 8775


def test_comando_do_video_e_lista_com_o_caminho_inteiro_num_so_argumento():
    destino = Path("C:/pasta com espaço/d'água ação.mp4")
    cmd = smoke.comando_do_video("ffmpeg", destino, com_voz=False)
    assert isinstance(cmd, list) and cmd[0] == "ffmpeg" and cmd[-1] == str(destino)
    assert sum("d'água" in x for x in cmd) == 1
    assert not any("sine" in x for x in cmd) and "-c:a" not in cmd          # sem voz: sem trilha de som (o Whisper nem entra)
    assert any(x.startswith("testsrc2") and "duration=60" in x for x in cmd)


def test_comando_do_video_com_voz_ganha_o_tom():
    cmd = smoke.comando_do_video("ffmpeg", Path("v.mp4"), com_voz=True)
    assert any(x.startswith("sine=") for x in cmd) and "-c:a" in cmd


def test_ambiente_de_teste_isola_dados_modelos_rede_e_placa(tmp_path):
    base = {"BISTURI_DADOS": "C:\\dados do dono", "HF_HOME": "C:\\cache do dono", "PATH": "x"}
    amb = smoke.ambiente_de_teste(tmp_path / "t", base)
    assert amb["BISTURI_DADOS"] == str(tmp_path / "t")
    assert amb["HF_HOME"] == str(tmp_path / "t" / "modelos") and amb["HF_HUB_OFFLINE"] == "1" and amb["CUDA_VISIBLE_DEVICES"] == "-1"
    assert amb["PATH"] == "x" and base["BISTURI_DADOS"] == "C:\\dados do dono"          # o original não é mexido


def test_pasta_de_teste_nasce_dentro_dos_dados_com_config_proprio(tmp_path):
    dados = tmp_path / "dados"
    dados.mkdir()
    (dados / "config.toml").write_text("do dono", encoding="utf-8")
    pasta = smoke.criar_pasta_de_teste(dados)
    assert pasta.parent == dados.resolve() and pasta.name.startswith("smoke-")
    assert 'encoder = "libx264"' in (pasta / "config.toml").read_text(encoding="utf-8")
    assert (dados / "config.toml").read_text(encoding="utf-8") == "do dono"
    assert smoke.criar_pasta_de_teste(dados) != pasta                                  # cada execução tem a sua


def test_apagar_so_apaga_a_pasta_do_smoke(tmp_path):
    dados = tmp_path / "dados"
    dados.mkdir()
    do_dono = dados / "saida"
    do_dono.mkdir()
    (do_dono / "video.mp4").write_bytes(b"x")
    pasta = smoke.criar_pasta_de_teste(dados)
    (pasta / "saida").mkdir()
    (pasta / "saida" / "x.json").write_text("{}", encoding="utf-8")
    assert smoke.apagar_pasta_de_teste(pasta, dados) is True
    assert not pasta.exists() and (do_dono / "video.mp4").is_file()


@pytest.mark.parametrize("nome", ["saida", "entrada", "dados", "smoke"])
def test_apagar_recusa_pasta_que_nao_e_do_smoke(tmp_path, nome):
    dados = tmp_path / "dados"
    alvo = dados / nome
    alvo.mkdir(parents=True)
    (alvo / "x.txt").write_text("dono", encoding="utf-8")
    assert smoke.apagar_pasta_de_teste(alvo, dados) is False
    assert (alvo / "x.txt").is_file()


def test_apagar_recusa_pasta_de_outro_lugar_mesmo_com_o_nome_certo(tmp_path):
    dados, fora = tmp_path / "dados", tmp_path / "fora" / "smoke-12345678"
    dados.mkdir()
    fora.mkdir(parents=True)
    assert smoke.apagar_pasta_de_teste(fora, dados) is False and fora.is_dir()
    assert smoke.apagar_pasta_de_teste(dados, dados) is False


def test_descendentes_e_sobreviventes():
    retrato = {10: (1, "pythonw.exe"), 11: (10, "python.exe"), 12: (11, "python.exe"), 13: (12, "ffmpeg.exe"), 20: (1, "outro.exe")}
    assert smoke.descendentes(retrato, 10) == {11: "python.exe", 12: "python.exe", 13: "ffmpeg.exe"}
    vistos = {11: "python.exe", 13: "ffmpeg.exe"}
    assert smoke.sobreviventes(vistos, {13: (12, "FFMPEG.exe")}) == {13: "ffmpeg.exe"}
    assert smoke.sobreviventes(vistos, {13: (1, "notepad.exe")}) == {}                   # o número foi reaproveitado por outro programa
    assert smoke.sobreviventes(vistos, {}) == {}


@pytest.mark.skipif(os.name != "nt", reason="o retrato de processos usa o kernel32 (só Windows)")
def test_retrato_de_processos_enxerga_este_processo_e_seu_pai():
    retrato = smoke.retrato_de_processos()
    assert retrato is not None and os.getpid() in retrato
    assert retrato[os.getpid()][1].lower().startswith("python")
    filho = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert filho.pid in smoke.descendentes(smoke.retrato_de_processos() or {}, os.getpid())
    finally:
        filho.kill()
        filho.wait()


def test_final_do_log_pega_so_as_ultimas_linhas(tmp_path):
    arquivo = tmp_path / "ui.log"
    arquivo.write_text("\n".join(f"linha {i}" for i in range(50)), encoding="utf-8")
    assert smoke.final_do_log(arquivo, 3).splitlines() == ["linha 47", "linha 48", "linha 49"]
    assert smoke.final_do_log(tmp_path / "nao_existe.log") == ""


# ── passo 1 e 2 ───────────────────────────────────────────────────────────────────────────────────
def _resposta(codigo=0, saida="", erro=""):
    return SimpleNamespace(returncode=codigo, stdout=saida, stderr=erro)


def _comando_de_versao(ctx, dita: str, real: str = "1.1.0", codigo: int = 0):
    outros = ctx.rodar                                       # o ffmpeg e o taskkill continuam falsos

    def rodar(cmd, **kw):
        if cmd[0] != str(ctx.python):
            return outros(cmd, **kw)
        assert kw["shell"] is False and kw["cwd"] == str(ctx.raiz)
        return _resposta(codigo, f"Bisturi Clips {dita}\n") if "--versao" in cmd else _resposta(0, real)
    rodar.chamadas = outros.chamadas                         # type: ignore[attr-defined]
    ctx.rodar = rodar


def test_passo_versao_ok(ctx):
    _comando_de_versao(ctx, "1.1.0")
    r = smoke.passo_versao(ctx)
    assert r.ok and ctx.versao == "1.1.0"


def test_passo_versao_diferente_do_pacote_falha(ctx):
    _comando_de_versao(ctx, "1.0.9", real="1.1.0")
    with pytest.raises(ErroSmoke, match="1.0.9"):
        smoke.passo_versao(ctx)


def test_passo_versao_com_codigo_de_saida_ruim_falha(ctx):
    _comando_de_versao(ctx, "1.1.0", codigo=1)
    with pytest.raises(ErroSmoke, match="código 1"):
        smoke.passo_versao(ctx)


def test_passo_checar_sem_o_modulo_novo_usa_o_checar_antigo_na_pasta_de_teste(ctx):
    chamadas = []
    ctx.rodar = lambda cmd, **kw: chamadas.append((cmd, kw)) or _resposta(0, "ok")
    r = smoke.passo_checar(ctx)
    cmd, kw = chamadas[0]
    assert r.ok and "ainda não existe" in r.mensagem
    assert cmd[1:4] == ["-m", "cs2clip", "--checar"] and cmd[cmd.index("--saida") + 1] == str(ctx.teste / "saida")
    assert kw["env"]["BISTURI_DADOS"] == str(ctx.teste)


def test_passo_checar_com_o_modulo_novo_usa_a_pasta_de_dados_informada(ctx):
    (ctx.raiz / "cs2clip" / "instalacao.py").write_text("", encoding="utf-8")
    chamadas = []
    ctx.rodar = lambda cmd, **kw: chamadas.append((cmd, kw)) or _resposta(0, "ok")
    assert smoke.passo_checar(ctx).ok
    cmd, kw = chamadas[0]
    assert cmd[1:] == ["-m", "cs2clip.instalacao", "checar"] and kw["env"]["BISTURI_DADOS"] == str(ctx.dados)


def test_passo_checar_com_saida_1_falha_e_mostra_o_motivo(ctx):
    ctx.rodar = lambda cmd, **kw: _resposta(1, "ffmpeg: NÃO ACHEI")
    with pytest.raises(ErroSmoke, match="NÃO ACHEI"):
        smoke.passo_checar(ctx)


# ── passo 3 ───────────────────────────────────────────────────────────────────────────────────────
def test_passo_interface_ok_confere_token_e_403(ctx):
    servidor = ligar(ctx)
    r = smoke.passo_interface(ctx)
    assert r.ok and ctx.token == "tok-secreto" and 8765 <= ctx.porta <= 8775
    assert ("GET", "/health") in servidor.pedidos and ("GET", "/") in servidor.pedidos


def test_passo_interface_sem_o_403_sem_token_falha(ctx):
    ligar(ctx, exige_token=False)                  # a interface "esqueceu" de exigir o token
    with pytest.raises(ErroSmoke, match="SEM o token"):
        smoke.passo_interface(ctx)


def test_passo_interface_versao_errada_falha(ctx):
    ligar(ctx, versao="0.9.0")
    with pytest.raises(ErroSmoke, match="1.1.0"):
        smoke.passo_interface(ctx)


def test_passo_interface_sem_token_no_html_falha(ctx):
    ligar(ctx, token_no_html=False)
    with pytest.raises(ErroSmoke, match="token"):
        smoke.passo_interface(ctx)


def test_passo_interface_que_fecha_sozinha_falha_com_o_log(ctx):
    servidor = ligar(ctx)
    servidor.processo.sair(1)
    (ctx.teste / "logs").mkdir()
    (ctx.pasta_log()).write_text("Traceback: faltou o uvicorn", encoding="utf-8")
    with pytest.raises(ErroSmoke, match="faltou o uvicorn"):
        smoke.passo_interface(ctx)


def test_passo_interface_que_nao_responde_estoura_o_prazo(ctx):
    servidor = ligar(ctx)
    servidor.tratar = lambda *a, **k: (503, {})
    with pytest.raises(ErroSmoke, match="não respondeu em /health"):
        smoke.passo_interface(ctx)


# ── passo 4 ───────────────────────────────────────────────────────────────────────────────────────
def _pronta(ctx, **opcoes) -> Servidor:
    servidor = ligar(ctx, **opcoes)
    smoke.passo_interface(ctx)
    return servidor


def test_passo_processar_ok_gera_os_dois_videos_e_confere_fila_e_receita(ctx):
    servidor = _pronta(ctx)
    r = smoke.passo_processar(ctx)
    assert r.ok and "fila ação.mp4" in r.mensagem and "receita d'água.mp4" in r.mensagem
    assert (ctx.teste / "entrada" / smoke.VIDEO_DA_FILA).is_file() and (ctx.teste / "entrada" / smoke.VIDEO_DA_RECEITA).is_file()
    assert ("POST", "/api/fila") in servidor.pedidos and ("POST", "/api/trabalhos") in servidor.pedidos
    ffmpeg = [c for c in ctx.rodar.chamadas if c[0] == "ffmpeg-falso"]
    assert len(ffmpeg) == 1 and not any("sine" in x for x in ffmpeg[0])                  # um vídeo só; sem voz por padrão


def test_passo_processar_sem_processar_pula(ctx):
    ctx.sem_processar = True
    r = smoke.passo_processar(ctx)
    assert r.ok and r.pulado and not ctx.rodar.chamadas


def test_passo_processar_codigo_de_saida_diferente_de_zero_falha(ctx):
    _pronta(ctx, codigo_saida=3)
    with pytest.raises(ErroSmoke, match="código de saída 3"):
        smoke.passo_processar(ctx)


def test_passo_processar_analise_com_erro_falha(ctx):
    _pronta(ctx, estado_fila="erro", codigo_saida=1)
    with pytest.raises(ErroSmoke, match="'erro'"):
        smoke.passo_processar(ctx)


def test_passo_processar_receita_que_nao_fica_pronta_falha(ctx):
    _pronta(ctx, estado_receita="erro")
    with pytest.raises(ErroSmoke, match="receita terminou como 'erro'"):
        smoke.passo_processar(ctx)


def test_passo_processar_sem_momentos_json_falha(ctx):
    _pronta(ctx, grava_momentos=False)
    with pytest.raises(ErroSmoke, match="momentos.json"):
        smoke.passo_processar(ctx)


def test_passo_processar_qualidade_quebrada_falha_e_valida_aceita(ctx):
    _pronta(ctx, qualidade="{isso nao e json")
    with pytest.raises(ErroSmoke, match="qualidade.json"):
        smoke.passo_processar(ctx)


def test_passo_processar_com_qualidade_valida_passa(ctx):
    _pronta(ctx, qualidade=json.dumps({"status": "ok"}))
    assert "qualidade.json ok" in smoke.passo_processar(ctx).mensagem


def test_passo_processar_prazo_estourado_falha(ctx):
    servidor = _pronta(ctx, estado_fila="rodando")             # nunca termina
    ctx.prazo_s = 30
    with pytest.raises(ErroSmoke, match="não terminou em 30 s"):
        smoke.passo_processar(ctx)
    assert servidor.pedidos


def test_passo_processar_interface_que_morre_no_meio_falha(ctx):
    servidor = _pronta(ctx, estado_fila="rodando")
    servidor.processo.sair(1)
    with pytest.raises(ErroSmoke, match="fechou no meio"):
        smoke.passo_processar(ctx)


def test_passo_processar_sem_ffmpeg_falha_com_mensagem(ctx):
    _pronta(ctx)
    ctx.ffmpeg = None
    with pytest.raises(ErroSmoke, match="ffmpeg"):
        smoke.passo_processar(ctx)


def test_passo_processar_ffmpeg_que_falha_falha(ctx):
    _pronta(ctx)
    ctx.rodar = rodar_falso(ffmpeg_ok=False)
    with pytest.raises(ErroSmoke, match="erro do ffmpeg"):
        smoke.passo_processar(ctx)


def test_achar_ffmpeg_prefere_o_da_pasta_bin_do_programa(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke.shutil, "which", lambda nome: "C:\\path\\ffmpeg.exe")
    assert smoke.achar_ffmpeg(tmp_path) == "C:\\path\\ffmpeg.exe"
    (tmp_path / "bin").mkdir()
    nome = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    (tmp_path / "bin" / nome).write_bytes(b"x")
    assert smoke.achar_ffmpeg(tmp_path) == str(tmp_path / "bin" / nome)


# ── passo 5 ───────────────────────────────────────────────────────────────────────────────────────
def test_passo_encerrar_pede_o_encerrar_e_confere_que_o_processo_saiu(ctx):
    servidor = _pronta(ctx)
    ctx.processos = lambda: {}
    r = smoke.passo_encerrar(ctx)
    assert r.ok and ("POST", "/api/encerrar") in servidor.pedidos and servidor.processo.poll() == 0


def test_passo_encerrar_que_nao_encerra_falha_e_derruba_so_o_que_abriu(ctx):
    servidor = _pronta(ctx, encerra=False)                   # o servidor aceita o pedido, mas o processo não sai
    ctx.processos = lambda: {}
    with pytest.raises(ErroSmoke, match="continuou rodando"):
        smoke.passo_encerrar(ctx)
    derrubadas = [c for c in ctx.rodar.chamadas if c[0] == "taskkill"] if sys.platform == "win32" else []
    if sys.platform == "win32":
        assert derrubadas == [["taskkill", "/PID", str(servidor.processo.pid), "/T", "/F"]]


def test_passo_encerrar_com_filho_vivo_falha(ctx):
    _pronta(ctx)
    ctx.vistos = {777: "ffmpeg.exe"}
    ctx.processos = lambda: {777: (1, "ffmpeg.exe")}          # o ffmpeg continua lá depois do Encerrar
    with pytest.raises(ErroSmoke, match="Ficaram processos vivos"):
        smoke.passo_encerrar(ctx)


def test_passo_encerrar_com_codigo_de_saida_ruim_falha(ctx):
    servidor = _pronta(ctx)
    ctx.processos = lambda: {}
    original = servidor.tratar

    def tratar(metodo, caminho, corpo, usado):
        r = original(metodo, caminho, corpo, usado)
        if caminho == "/api/encerrar":
            servidor.processo.sair(1)
        return r
    servidor.tratar = tratar
    with pytest.raises(ErroSmoke, match="código 1"):
        smoke.passo_encerrar(ctx)


def test_passo_encerrar_fora_do_windows_so_diz_que_nao_conferiu_os_filhos(ctx):
    _pronta(ctx)
    ctx.processos = lambda: None
    assert "só existe no Windows" in smoke.passo_encerrar(ctx).mensagem


# ── o conjunto ────────────────────────────────────────────────────────────────────────────────────
def test_executar_tudo_certo_sai_0_e_imprime_um_ok_por_passo(ctx):
    _comando_de_versao(ctx, "1.1.0")
    ligar(ctx)
    ctx.processos = lambda: {}
    ctx.sem_processar = True
    assert smoke.executar(ctx) == 0
    linhas = ctx.saidas
    assert [x.split(":")[0] for x in linhas[:5]] == ["OK [1/5] versão", "OK [2/5] checagem", "OK [3/5] interface", "PULADO [4/5] processar (fila e receita)",
                                                     "OK [5/5] encerrar"]
    assert linhas[-1] == "SMOKE: tudo certo"


def test_executar_com_uma_falha_sai_1_pula_o_resto_mas_encerra_a_interface(ctx):
    _comando_de_versao(ctx, "1.1.0")
    servidor = ligar(ctx, codigo_saida=1)
    ctx.processos = lambda: {}
    assert smoke.executar(ctx) == 1, ctx.saidas
    assert any(x.startswith("FALHA [4/5]") and "código de saída 1" in x for x in ctx.saidas), ctx.saidas
    assert any(x.startswith("OK [5/5]") for x in ctx.saidas)                                # o Encerrar rodou mesmo assim
    assert servidor.processo.poll() == 0 and ctx.saidas[-1] == "SMOKE: FALHOU"


def test_executar_depois_de_uma_falha_pula_os_passos_seguintes(ctx):
    _comando_de_versao(ctx, "1.0.0", real="1.1.0")
    ligar(ctx)
    assert smoke.executar(ctx) == 1
    assert ctx.saidas[0].startswith("FALHA [1/5]")
    assert all(x.startswith("PULADO") for x in ctx.saidas[1:4])
    assert ctx.saidas[4].startswith("FALHA [5/5]")                                           # sem interface: não há o que encerrar


def test_executar_erro_inesperado_vira_falha_e_nao_traceback(ctx):
    def quebra(_ctx):
        raise KeyError("x")
    assert smoke.executar(ctx, [("estranho", quebra)]) == 1
    assert ctx.saidas[0].startswith("FALHA [1/1] estranho: erro inesperado (KeyError)")


# ── linha de comando ──────────────────────────────────────────────────────────────────────────────
def test_principal_recusa_raiz_que_nao_e_o_programa(tmp_path, capsys):
    dados = tmp_path / "d"
    dados.mkdir()
    assert smoke.principal(["--raiz", str(tmp_path), "--dados", str(dados)]) == 2
    assert "não é a pasta do programa" in capsys.readouterr().err


def test_principal_recusa_dados_que_nao_existem(ctx, capsys):
    assert smoke.principal(["--raiz", str(ctx.raiz), "--dados", str(ctx.dados / "nao_existe")]) == 2
    assert "--dados" in capsys.readouterr().err


def test_principal_limpa_a_pasta_de_teste_e_nao_toca_no_resto(ctx, monkeypatch):
    (ctx.dados / "config.toml").write_text("do dono", encoding="utf-8")
    vistos = []
    monkeypatch.setattr(smoke, "PASSOS", [("um", lambda c: vistos.append(c.teste) or smoke.Resultado(True, "ok"))])
    assert smoke.principal(["--raiz", str(ctx.raiz), "--dados", str(ctx.dados), "--sem-processar"]) == 0
    assert vistos and vistos[0].parent == ctx.dados.resolve() and not vistos[0].exists()
    assert (ctx.dados / "config.toml").read_text(encoding="utf-8") == "do dono"
    assert sorted(p.name for p in ctx.dados.iterdir() if p.name.startswith("smoke-")) == [ctx.teste.name]   # só a do fixture


def test_principal_guardar_mantem_a_pasta(ctx, monkeypatch):
    vistos = []
    monkeypatch.setattr(smoke, "PASSOS", [("um", lambda c: vistos.append(c.teste) or smoke.Resultado(True, "ok"))])
    assert smoke.principal(["--raiz", str(ctx.raiz), "--dados", str(ctx.dados), "--guardar"]) == 0
    assert vistos[0].is_dir()


def test_principal_voz_e_desligada_por_padrao(monkeypatch, ctx):
    visto = []
    monkeypatch.setattr(smoke, "PASSOS", [("um", lambda c: visto.append(c.com_voz) or smoke.Resultado(True, ""))])
    smoke.principal(["--raiz", str(ctx.raiz), "--dados", str(ctx.dados)])
    smoke.principal(["--raiz", str(ctx.raiz), "--dados", str(ctx.dados), "--com-voz"])
    smoke.principal(["--raiz", str(ctx.raiz), "--dados", str(ctx.dados), "--sem-voz"])
    assert visto == [False, True, False]


def test_o_smoke_nao_monta_comando_de_shell():
    fonte = (RAIZ / "instalador" / "smoke" / "smoke.py").read_text(encoding="utf-8")
    assert "shell=True" not in fonte and "os.system" not in fonte


# ── de verdade (lentos): a interface real, o ffmpeg real, o Python deste ambiente ─────────────────
def _tem_ffmpeg() -> bool:
    return smoke.achar_ffmpeg(RAIZ) is not None


@pytest.fixture(scope="module")
def execucao_real(tmp_path_factory):
    """Roda o smoke inteiro uma vez, com a interface real (passos 1 a 5; o 4 só se há ffmpeg). Devolve (código, linhas, pasta de dados)."""
    dados = tmp_path_factory.mktemp("dados real")
    argumentos = ["--raiz", str(RAIZ), "--dados", str(dados), "--prazo-encerrar", "15"]
    if not _tem_ffmpeg():
        argumentos.append("--sem-processar")
    processo = subprocess.run([sys.executable, str(RAIZ / "instalador" / "smoke" / "smoke.py"), *argumentos], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600, cwd=str(RAIZ), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return processo.returncode, processo.stdout.splitlines(), dados


@pytest.mark.lento
def test_real_passos_1_a_4_passam_e_a_pasta_de_teste_some(execucao_real):
    codigo, linhas, dados = execucao_real
    for n in (1, 2, 3):
        assert any(x.startswith(f"OK [{n}/5]") for x in linhas), linhas
    assert any(x.startswith(("OK [4/5]", "PULADO [4/5]")) for x in linhas), linhas
    assert not [p for p in dados.iterdir() if p.name.startswith("smoke-")]                # limpou o que criou


@pytest.mark.lento
def test_real_encerrar_pela_rota_faz_a_interface_sair(execucao_real):
    codigo, linhas, _ = execucao_real
    assert any(x.startswith("OK [5/5]") for x in linhas), linhas
    assert codigo == 0
