"""P4.2b: o download do modelo de voz ligado ao programa: as rotas /api/modelo*, o 5º trabalho do Coordenador, o `--baixar-modelo` da
linha de comando e o cartão da tela Início.

Sem rede: o `modelo_voz.baixar` é trocado por um falso (ou o catálogo aponta para uma pasta temporária). Espera por condição, com prazo."""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest

import cs2clip.modelo_voz as mv
# O huggingface_hub lê HF_HUB_OFFLINE uma vez só, ao ser importado. Se a 1ª importação no processo do xdist acontecer dentro do teste que
# liga essa variável (test_modelo_voz, "modo sem internet"), o processo fica "sem internet" até o fim e os testes do servidor local falham
# conforme a ordem dos processos. Importado aqui, na coleta (antes de qualquer teste), ele lê o ambiente de verdade em todo processo.
import huggingface_hub.constants  # noqa: F401,E402
from test_r1_ui import rodar_js
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

REV = "ab" * 20
REV_OUTRA = "cd" * 20
REPO = "org-falsa/faster-whisper-falso"
CONTEUDO = {"config.json": b'{"falso": 1}', "model.bin": bytes(range(256)) * 8}
TOTAL = sum(len(c) for c in CONTEUDO.values())
ROTAS = [("GET", "/api/modelo"), ("POST", "/api/modelo/baixar"), ("POST", "/api/modelo/cancelar")]


def _st(c) -> Any:
    """O `app.state` do servidor do teste (Any: o verificador de tipos vê `app_state` como dict)."""
    return c.app_state


def _esperar(condicao, prazo: float = 5.0) -> bool:
    fim = time.monotonic() + prazo
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.01)
    return bool(condicao())


@pytest.fixture
def catalogo(tmp_path, monkeypatch):
    """Um componentes.json falso só com o large-v3-turbo (2 arquivos pequenos) e o cache numa pasta temporária."""
    arquivos = {}
    for nome, dados in CONTEUDO.items():
        info: dict[str, Any] = {"tamanho_bytes": len(dados)}
        if nome == "model.bin":
            info["sha256"] = hashlib.sha256(dados).hexdigest()
        else:
            info["git_blob"] = hashlib.sha1(b"blob %d\x00" % len(dados) + dados).hexdigest()
        arquivos[nome] = info
    arq = tmp_path / "componentes.json"
    arq.write_text(json.dumps({"modelos_de_voz": {"large-v3-turbo": {"repo": REPO, "revision": REV, "arquivos": arquivos}}}), encoding="utf-8")
    monkeypatch.setattr(mv, "ARQUIVO_COMPONENTES", arq)
    cache = tmp_path / "hub"
    monkeypatch.setattr(mv, "_cache_padrao", lambda: cache)
    return cache


def _snapshot(cache: Path, revisao: str = REV) -> Path:
    return cache / ("models--" + REPO.replace("/", "--")) / "snapshots" / revisao


def _projeto(tmp_path: Path, modelo: str = "large-v3-turbo") -> Path:
    raiz = tmp_path / "projeto"
    raiz.mkdir()
    criar_projeto(raiz)
    (raiz / "config.toml").write_text(f'[transcricao]\nmodelo = "{modelo}"\n', encoding="utf-8")
    return raiz


class BaixarFalso:
    """Faz o papel do `modelo_voz.baixar`: avisa o progresso, fica "baixando" até `liberar` (ou até o pedido de cancelar) e termina."""

    def __init__(self, erro: BaseException | None = None):
        self.chamadas: list[str] = []
        self.comecou, self.liberar, self.viu_cancelar = threading.Event(), threading.Event(), threading.Event()
        self.erro = erro

    def __call__(self, nome, ao_progredir=None, cancelar=None, cache_dir=None):
        self.chamadas.append(nome)
        assert ao_progredir is not None and cancelar is not None
        ao_progredir(500, TOTAL, 2_000_000.0, 4.0)
        self.comecou.set()
        prazo = time.monotonic() + 10
        while not self.liberar.is_set():
            if cancelar.wait(0.01):
                self.viu_cancelar.set()
                raise mv.DownloadCancelado("Download do modelo de voz cancelado. O que já veio fica guardado.")
            if time.monotonic() > prazo:
                raise AssertionError("o teste esqueceu de liberar o download falso")
        if self.erro is not None:
            raise self.erro
        ao_progredir(TOTAL, TOTAL, 2_000_000.0, 0.0)
        return Path("pasta-falsa")


# ── as rotas pedem o token, como todas as outras ──────────────────────────────────────────────────────────────────────────────────────
def test_rotas_do_modelo_pedem_o_token(tmp_path, catalogo):
    with cliente(_projeto(tmp_path)) as c:
        for metodo, rota in ROTAS:
            assert c.request(metodo, rota).status_code == 403, rota
            assert c.request(metodo, rota, headers={"X-Token": "x" * 20}).status_code == 403, rota
        assert c.get("/api/modelo", headers=token_headers(c)).status_code == 200          # com o token, a rota existe e responde


def test_inventario_tem_as_3_rotas_novas():
    rotas = {tuple(x) for x in json.loads((Path(__file__).parent / "fixtures" / "rotas_ui.json").read_text(encoding="utf-8"))}
    assert set(ROTAS) <= rotas


# ── GET /api/modelo ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_estado_sem_o_modelo_diz_que_falta_e_quanto_e(tmp_path, catalogo):
    with cliente(_projeto(tmp_path)) as c:
        r = c.get("/api/modelo", headers=token_headers(c)).json()
    assert (r["nome"], r["gerenciado"], r["presente"], r["ja_tem"]) == ("large-v3-turbo", True, False, False)
    assert (r["tamanho_bytes"], r["baixado_bytes"]) == (TOTAL, 0)
    assert r["download"]["baixando"] is False


def test_estado_com_o_modelo_inteiro_diz_presente(tmp_path, catalogo):
    snap = _snapshot(catalogo)
    snap.mkdir(parents=True)
    for nome, dados in CONTEUDO.items():
        (snap / nome).write_bytes(dados)
    with cliente(_projeto(tmp_path)) as c:
        r = c.get("/api/modelo", headers=token_headers(c)).json()
    assert (r["presente"], r["ja_tem"], r["baixado_bytes"]) == (True, True, TOTAL)


def test_modelo_em_outra_revisao_conta_como_ja_tem(tmp_path, catalogo):
    _snapshot(catalogo, REV_OUTRA).mkdir(parents=True)
    with cliente(_projeto(tmp_path)) as c:
        r = c.get("/api/modelo", headers=token_headers(c)).json()
    assert (r["presente"], r["ja_tem"], r["outras_revisoes"]) == (False, True, True)


def test_modelo_nao_gerenciado_aparece_assim_e_nao_baixa(tmp_path, catalogo, monkeypatch):
    falso = BaixarFalso()
    monkeypatch.setattr(mv, "baixar", falso)
    with cliente(_projeto(tmp_path, "medium")) as c:
        r = c.get("/api/modelo", headers=token_headers(c)).json()
        assert (r["nome"], r["gerenciado"], r["presente"]) == ("medium", False, None)
        b = c.post("/api/modelo/baixar", json={}, headers=token_headers(c))
    assert b.status_code == 400 and "medium" in b.json()["erro"]
    assert falso.chamadas == []


def test_sem_componentes_json_o_estado_responde_e_o_baixar_explica(tmp_path, monkeypatch):
    """Cópia do programa sem o componentes.json: a consulta não quebra (a tela só não mostra o cartão) e o baixar diz o que houve."""
    monkeypatch.setattr(mv, "ARQUIVO_COMPONENTES", tmp_path / "nao_existe.json")
    falso = BaixarFalso()
    monkeypatch.setattr(mv, "baixar", falso)
    with cliente(_projeto(tmp_path)) as c:
        r = c.get("/api/modelo", headers=token_headers(c))
        b = c.post("/api/modelo/baixar", json={}, headers=token_headers(c))
    assert r.status_code == 200 and (r.json()["gerenciado"], r.json()["presente"]) == (False, None) and "componentes.json" in r.json()["aviso"]
    assert b.status_code == 503 and "componentes.json" in b.json()["erro"]
    assert falso.chamadas == []


# ── POST /api/modelo/baixar e /cancelar ───────────────────────────────────────────────────────────────────────────────────────────────
def test_baixar_roda_no_fundo_com_progresso_e_termina_pronto(tmp_path, catalogo, monkeypatch):
    falso = BaixarFalso()
    monkeypatch.setattr(mv, "baixar", falso)
    with cliente(_projeto(tmp_path)) as c:
        h = token_headers(c)
        r = c.post("/api/modelo/baixar", json={}, headers=h)
        assert r.status_code == 200 and r.json()["baixando"] is True
        assert falso.comecou.wait(5)
        d = c.get("/api/modelo", headers=h).json()["download"]
        assert (d["baixando"], d["baixado"], d["total"], d["velocidade"], d["restante"]) == (True, 500, TOTAL, 2_000_000.0, 4.0)
        assert _st(c).coordenador.rodando() and _st(c).coordenador.segura_o_programa()
        falso.liberar.set()
        assert _esperar(lambda: c.get("/api/modelo", headers=h).json()["download"]["pronto"])
        d = c.get("/api/modelo", headers=h).json()["download"]
    assert (d["baixando"], d["baixado"], d["erro"], d["cancelado"]) == (False, TOTAL, "", False)
    assert falso.chamadas == ["large-v3-turbo"]


def test_segundo_download_e_recusado_enquanto_o_primeiro_roda(tmp_path, catalogo, monkeypatch):
    falso = BaixarFalso()
    monkeypatch.setattr(mv, "baixar", falso)
    with cliente(_projeto(tmp_path)) as c:
        h = token_headers(c)
        assert c.post("/api/modelo/baixar", json={}, headers=h).status_code == 200
        assert falso.comecou.wait(5)
        r = c.post("/api/modelo/baixar", json={}, headers=h)
        assert r.status_code == 409 and "baixando" in r.json()["erro"]
        falso.liberar.set()
        assert _esperar(lambda: not _st(c).modelo.ativo())
    assert falso.chamadas == ["large-v3-turbo"]


def test_cancelar_pede_ao_download_para_parar_e_a_tela_ve_cancelado(tmp_path, catalogo, monkeypatch):
    falso = BaixarFalso()
    monkeypatch.setattr(mv, "baixar", falso)
    with cliente(_projeto(tmp_path)) as c:
        h = token_headers(c)
        assert c.post("/api/modelo/cancelar", json={}, headers=h).status_code == 409    # nada para cancelar
        c.post("/api/modelo/baixar", json={}, headers=h)
        assert falso.comecou.wait(5)
        assert c.post("/api/modelo/cancelar", json={}, headers=h).status_code == 200
        assert falso.viu_cancelar.wait(5)
        assert _esperar(lambda: not c.get("/api/modelo", headers=h).json()["download"]["baixando"])
        d = c.get("/api/modelo", headers=h).json()["download"]
    assert (d["cancelado"], d["pronto"], d["erro"]) == (True, False, "")


def test_erro_do_download_vira_a_mensagem_do_modelo_voz(tmp_path, catalogo, monkeypatch):
    texto = "Sem conexão com a internet (ou o site do Hugging Face não respondeu). Confira a internet e tente de novo."
    falso = BaixarFalso(erro=mv.ErroDownloadModelo(texto))
    falso.liberar.set()
    monkeypatch.setattr(mv, "baixar", falso)
    with cliente(_projeto(tmp_path)) as c:
        h = token_headers(c)
        c.post("/api/modelo/baixar", json={}, headers=h)
        assert _esperar(lambda: c.get("/api/modelo", headers=h).json()["download"]["erro"] != "")
        d = c.get("/api/modelo", headers=h).json()["download"]
    assert (d["erro"], d["baixando"], d["pronto"]) == (texto, False, False)


@pytest.mark.parametrize("ocupado", ["fila", "gravacao", "receita"])
def test_recusa_baixar_com_analise_gravacao_ou_receita_rodando(tmp_path, catalogo, monkeypatch, ocupado):
    falso = BaixarFalso()
    monkeypatch.setattr(mv, "baixar", falso)
    with cliente(_projeto(tmp_path)) as c:
        st: Any = c.app_state
        if ocupado == "fila":
            st.fila.esta_processando = lambda video_id=None: True
        elif ocupado == "gravacao":
            st.gravacao.ocupado = lambda: True
        else:
            st.receitas.rodando_de_fato = lambda: True
        r = c.post("/api/modelo/baixar", json={}, headers=token_headers(c))
    assert r.status_code == 409 and "modelo de voz" in r.json()["erro"]
    assert falso.chamadas == []


# ── o 5º trabalho do Coordenador: fechar o programa e o botão Encerrar param o download ─────────────────────────────────────────────
def test_fim_do_servidor_cancela_o_download_e_espera_ele_parar(tmp_path, catalogo, monkeypatch):
    falso = BaixarFalso()
    monkeypatch.setattr(mv, "baixar", falso)
    c = cliente(_projeto(tmp_path))
    with c:
        c.post("/api/modelo/baixar", json={}, headers=token_headers(c))
        assert falso.comecou.wait(5)
    # saiu do `with`: o servidor terminou (lifespan) e o Coordenador cancelou e esperou o download, como os outros trabalhos
    assert falso.viu_cancelar.is_set()
    assert not _st(c).modelo.ativo() and _st(c).modelo.estado()["cancelado"] is True


def test_botao_encerrar_cancela_o_download(tmp_path, catalogo, monkeypatch):
    falso = BaixarFalso()
    monkeypatch.setattr(mv, "baixar", falso)
    with cliente(_projeto(tmp_path)) as c:
        h = token_headers(c)
        c.post("/api/modelo/baixar", json={}, headers=h)
        assert falso.comecou.wait(5)
        assert c.post("/api/encerrar", json={}, headers=h).status_code == 200
        assert falso.viu_cancelar.wait(5)
        assert _esperar(lambda: not _st(c).coordenador.rodando())


class _Ger:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _parado(**kw):
    base = dict(esta_processando=lambda: False, tem_pendentes=lambda: False, rodando_de_fato=lambda: False, editando=lambda: False,
                ocupado=lambda: False, baixando=lambda: False, cancelar_tudo=lambda: None, encerrar=lambda: None, interromper_todas=lambda: None,
                parar=lambda: None, esperar_fim=lambda p: None)
    base.update(kw)
    return _Ger(**base)


def test_coordenador_conta_o_download_do_modelo_em_tudo():
    from ui.coordenador import Coordenador
    feito: list[Any] = []
    modelo = _Ger(baixando=lambda: True, parar=lambda: feito.append("parar"), esperar_fim=lambda p: feito.append(("esperar", p)))
    c = Coordenador(_parado(), _parado(), _parado(), _parado(), modelo)
    assert (c.rodando(), c.segura_o_programa(), c.mexe_nos_kits()) == (True, True, False)   # baixar o modelo não grava kit nenhum
    c.cancelar_tudo()
    assert feito == ["parar"]
    feito.clear()
    c.encerrar(prazo_s=5.0)
    assert feito[0] == "parar" and feito[1][0] == "esperar" and 0 < feito[1][1] <= 5.0


def test_coordenador_ve_o_download_de_fundo_do_modelo_voz_mesmo_sem_o_gerenciador(monkeypatch):
    """Depois do cancelar, o trabalho de fundo do `modelo_voz` ainda pode estar soltando a conexão: isso também conta como rodando."""
    from ui.coordenador import Coordenador
    c = Coordenador(_parado(), _parado(), _parado(), _parado())
    assert not c.rodando()
    monkeypatch.setattr(mv, "em_andamento", lambda nome=None: True)
    assert c.rodando() and c.segura_o_programa()


def test_esperar_fim_espera_o_trabalho_de_fundo_soltar(monkeypatch):
    from ui.servidor import DownloadDoModelo
    vivo = {"sim": True}
    monkeypatch.setattr(mv, "em_andamento", lambda nome=None: vivo["sim"])
    d = DownloadDoModelo()
    assert d.baixando()
    threading.Timer(0.2, lambda: vivo.update(sim=False)).start()
    d.esperar_fim(5.0)
    assert vivo["sim"] is False and not d.baixando()


# ── --baixar-modelo na linha de comando ──────────────────────────────────────────────────────────────────────────────────────────────
def _config(tmp_path: Path, modelo: str = "large-v3-turbo") -> str:
    arq = tmp_path / "config.toml"
    arq.write_text(f'[transcricao]\nmodelo = "{modelo}"\n', encoding="utf-8")
    return str(arq)


def test_cli_baixar_modelo_usa_o_modelo_voz_e_mostra_progresso(tmp_path, monkeypatch, capsys):
    from cs2clip.__main__ import main
    vistos: list[Any] = []

    def falso(nome, ao_progredir=None, cancelar=None, cache_dir=None):
        vistos.append(nome)
        assert ao_progredir is not None
        ao_progredir(0, 1_621_665_983, 0.0, None)
        ao_progredir(810_000_000, 1_621_665_983, 5_000_000.0, 162.0)
        ao_progredir(1_621_665_983, 1_621_665_983, 5_000_000.0, 0.0)
        return tmp_path / "snap"
    monkeypatch.setattr(mv, "baixar", falso)
    assert main(["--baixar-modelo", "--config", _config(tmp_path)]) == 0
    saida = capsys.readouterr().out
    assert vistos == ["large-v3-turbo"]
    assert "1622 MB" in saida and "MB/s" in saida and "pronto" in saida


def test_cli_baixar_modelo_erro_sai_com_1_e_explica(tmp_path, monkeypatch, capsys):
    from cs2clip.__main__ import main

    def falso(nome, ao_progredir=None, cancelar=None, cache_dir=None):
        raise mv.ErroDownloadModelo("Falta espaço no disco para o modelo de voz.")
    monkeypatch.setattr(mv, "baixar", falso)
    assert main(["--baixar-modelo", "--config", _config(tmp_path)]) == 1
    assert "Falta espaço no disco" in capsys.readouterr().out


def test_cli_ctrl_c_cancela_limpo_e_sai_diferente_de_zero(tmp_path, monkeypatch, capsys):
    from cs2clip.__main__ import main

    def falso(nome, ao_progredir=None, cancelar=None, cache_dir=None):
        raise KeyboardInterrupt
    monkeypatch.setattr(mv, "baixar", falso)
    codigo = main(["--baixar-modelo", "--config", _config(tmp_path)])
    assert codigo not in (0, None)
    assert "cancelado" in capsys.readouterr().out


def test_cli_modelo_nao_gerenciado_continua_pelo_faster_whisper(tmp_path, monkeypatch, capsys):
    from cs2clip.__main__ import main
    pedidos: list[str] = []
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(download_model=lambda nome: pedidos.append(nome)))
    monkeypatch.setattr(mv, "baixar", lambda *a, **k: pytest.fail("modelo não gerenciado não passa pelo modelo_voz"))
    assert main(["--baixar-modelo", "--config", _config(tmp_path, "medium")]) == 0
    assert pedidos == ["medium"]


# ── a tela: o cartão da Início e o "processar sem o modelo" ─────────────────────────────────────────────────────────────────────────
FALTA = {"nome": "large-v3-turbo", "gerenciado": True, "presente": False, "ja_tem": False, "outras_revisoes": False,
         "tamanho_bytes": 1621665983, "baixado_bytes": 0,
         "download": {"baixando": False, "baixado": 0, "total": 0, "velocidade": 0, "restante": None, "erro": "", "cancelado": False, "pronto": False}}


def _js_modelo(tmp_path, corpo: str):
    return rodar_js(tmp_path, f"const FALTA = {json.dumps(FALTA)};\n" + corpo)


def test_js_cartao_aparece_quando_falta_e_some_quando_tem(tmp_path):
    r = _js_modelo(tmp_path, """
      const caixa = $("#modelo-voz");
      ui.montarModelo(FALTA);
      const falta = { escondido: caixa.classList.contains("hidden"), texto: textoDe(caixa), botoes: acharTag(caixa, "BUTTON").map(textoDe) };
      ui.montarModelo({ ...FALTA, presente: true, ja_tem: true });
      const tem = caixa.classList.contains("hidden");
      ui.montarModelo({ ...FALTA, ja_tem: true, outras_revisoes: true });
      const outra = caixa.classList.contains("hidden");
      ui.montarModelo({ ...FALTA, gerenciado: false, presente: null });
      const naoGerenciado = caixa.classList.contains("hidden");
      return { falta, tem, outra, naoGerenciado };
    """)
    assert r["falta"]["escondido"] is False
    assert "Falta baixar o modelo de voz (1,6 GB)" in r["falta"]["texto"]
    assert any("Baixar agora" in b for b in r["falta"]["botoes"])
    assert (r["tem"], r["outra"], r["naoGerenciado"]) == (True, True, True)


def test_js_durante_o_download_mostra_barra_mb_velocidade_tempo_e_cancelar(tmp_path):
    r = _js_modelo(tmp_path, """
      const caixa = $("#modelo-voz");
      win.setTimeout = () => 0; win.clearTimeout = () => {};      // sem o relógio de 1 s neste teste
      ui.montarModelo({ ...FALTA, download: { ...FALTA.download, baixando: true, baixado: 811000000, total: 1621665983, velocidade: 5000000, restante: 162 } });
      const barras = [];
      const achar = no => { if ((no.className || "").includes("progress-fill")) barras.push(no.style.width); no.children.forEach(achar); };
      achar(caixa);
      return { texto: textoDe(caixa), botoes: acharTag(caixa, "BUTTON").map(textoDe), barras, escondido: caixa.classList.contains("hidden") };
    """)
    assert r["escondido"] is False
    assert "811 MB de 1622 MB" in r["texto"] and "5,0 MB/s" in r["texto"] and "02:42" in r["texto"]
    assert r["barras"] == ["50%"]
    assert any("Cancelar" in b for b in r["botoes"])


def test_js_ao_terminar_o_cartao_some_e_avisa_pronto(tmp_path):
    r = _js_modelo(tmp_path, """
      win.setTimeout = () => 0; win.clearTimeout = () => {};
      ui.montarModelo({ ...FALTA, download: { ...FALTA.download, baixando: true, baixado: 1, total: 2, velocidade: 1, restante: 1 } });
      ui.montarModelo({ ...FALTA, presente: true, ja_tem: true, download: { ...FALTA.download, pronto: true } });
      return { escondido: $("#modelo-voz").classList.contains("hidden"), toasts: $("#toast-region").children.map(textoDe) };
    """)
    assert r["escondido"] is True
    assert any("Modelo de voz pronto" in t for t in r["toasts"])


def test_js_processar_sem_o_modelo_explica_e_nao_manda_para_a_fila(tmp_path):
    r = _js_modelo(tmp_path, """
      const pedidos = [];
      const resp = corpo => ({ ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => corpo });
      cfg.fetch = async (url, op) => { pedidos.push((op.method || "GET") + " " + url); return resp(url === "/api/modelo" ? FALTA : { itens: [] }); };
      ui.state.arquivos = [{ caminho: "entrada:live.mp4", nome: "live.mp4", tamanho: 1, tipo: "video" }];
      await ui.processarSelecionados();
      await espera();
      const comVideo = { pedidos: pedidos.slice(), toasts: $("#toast-region").children.map(textoDe), cartao: !$("#modelo-voz").classList.contains("hidden") };
      pedidos.length = 0;
      ui.state.arquivos = [{ caminho: "entrada:partida.dem", nome: "partida.dem", tamanho: 1, tipo: "demo" }];
      await ui.processarSelecionados();                           // demo sozinha não usa o Whisper: segue para a fila
      await espera();
      return { comVideo, soDemo: pedidos.slice() };
    """)
    assert "POST /api/fila" not in r["comVideo"]["pedidos"] and "GET /api/modelo" in r["comVideo"]["pedidos"]
    assert any("modelo de voz" in t for t in r["comVideo"]["toasts"]) and r["comVideo"]["cartao"] is True
    assert "POST /api/fila" in r["soDemo"] and "GET /api/modelo" not in r["soDemo"]


def test_js_sem_resposta_da_rota_o_processar_segue_como_antes(tmp_path):
    r = _js_modelo(tmp_path, """
      const pedidos = [];
      cfg.fetch = async (url, op) => { pedidos.push((op.method || "GET") + " " + url);
        if (url === "/api/modelo") return { ok: false, status: 503, headers: { get: () => "application/json" }, json: async () => ({ erro: "x" }) };
        return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ itens: [] }) }; };
      ui.state.arquivos = [{ caminho: "entrada:live.mp4", nome: "live.mp4", tamanho: 1, tipo: "video" }];
      await ui.processarSelecionados();
      await espera();
      return pedidos;
    """)
    assert "POST /api/fila" in r


def test_js_baixar_e_cancelar_chamam_as_rotas(tmp_path):
    r = _js_modelo(tmp_path, """
      const pedidos = [];
      win.setTimeout = () => 0; win.clearTimeout = () => {};
      cfg.fetch = async (url, op) => { pedidos.push((op.method || "GET") + " " + url);
        return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => (url === "/api/modelo" ? FALTA : { sucesso: true }) }; };
      await ui.baixarModelo();
      await ui.cancelarModelo();
      return pedidos;
    """)
    assert r[0] == "POST /api/modelo/baixar" and "POST /api/modelo/cancelar" in r and "GET /api/modelo" in r


def test_tela_tem_o_lugar_do_cartao_e_os_trabalhos_tambem_conferem():
    raiz = Path(__file__).resolve().parents[1] / "ui" / "static"
    html = (raiz / "index.html").read_text(encoding="utf-8")
    assert 'id="modelo-voz"' in html
    trabalhos = (raiz / "trabalhos.js").read_text(encoding="utf-8")
    assert "BisturiModelo" in trabalhos.split("async function comecar", 1)[1].split("async function escolherArquivo", 1)[0]
