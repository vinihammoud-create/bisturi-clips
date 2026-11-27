"""Revisão de 26/11/2026 (lote B): a trava única do CS2 (G-003), o predicado único de "há trabalho" e o encerramento dos 4 gerenciadores
(M-008, R-001/A-001), e o bloqueio de "Apagar kit" e de "Gostei/Descartar" (G-007/R-011). Processos, Steam e Twitch FALSOS.
Nada decide pelo relógio: toda espera é por condição, com prazo."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

from cs2clip import entrada as ent
from cs2clip.config import Config
from ui.gravacao import GerenciadorGravacao
from ui.receitas import GerenciadorReceitas

BASE = 76561197960265728
PRINCIPAL_ID, BISTURI_ID = 100, 200
DEMO = "match730_003849321203751714819_0599033075_201.dem"


def _espera(condicao, tempo=10.0):
    fim = time.monotonic() + tempo
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.01)
    return condicao()


class _Proc:
    """Processo falso que só termina quando recebe um sinal (cancelar) ou quando `fim` é ligado."""

    def __init__(self, linhas=("trabalhando",), codigo=0):
        self._linhas, self._codigo = list(linhas), codigo
        self.fim = threading.Event()
        self.sinais: list = []
        self.stdout = self._ler()

    def _ler(self):
        yield from (x + "\n" for x in self._linhas)
        self.fim.wait(10)

    def send_signal(self, s):
        self.sinais.append(s)
        self.fim.set()

    def terminate(self):
        self.send_signal("terminate")

    def poll(self):
        return self._codigo if self.fim.is_set() else None

    def wait(self, timeout=None):
        self.fim.wait(10 if timeout is None else timeout)
        return self._codigo


class _Fabrica:
    def __init__(self):
        self.processos: list[_Proc] = []
        self.comandos: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.comandos.append(cmd)
        p = _Proc()
        self.processos.append(p)
        return p


def _cfg():
    cfg = Config()
    cfg.gravacao_demo.conta_steamid = str(BASE + BISTURI_ID)
    cfg.gravacao_demo.conta_principal_id = str(BASE + PRINCIPAL_ID)
    return cfg


def _demo(pasta: Path) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / DEMO
    arq.write_bytes(ent.CABECALHO_CS2 + b"corpo")
    return arq


# ── G-003: uma trava só do CS2 para a receita e para a tela "Gravar pela demo" ─────────────────────────────

def test_g003_receita_e_gravacao_manual_nunca_abrem_o_cs2_ao_mesmo_tempo(tmp_path):
    replays = tmp_path / "replays"
    demo = _demo(replays)
    trava = threading.Lock()
    fab_grav, fab_rec = _Fabrica(), _Fabrica()
    grav = GerenciadorGravacao(tmp_path / "raiz", popen_factory=fab_grav, python="PY", pasta_replays=lambda: replays, trava_cs2=trava)
    rec = GerenciadorReceitas(tmp_path / "raiz", popen_factory=fab_rec, python="PY", conta_logada=lambda: BISTURI_ID, config_fn=_cfg,
                              poll_s=0.01, procurar_em=lambda: [replays], trava_cs2=trava)
    # 1) a gravação manual está com o CS2: a receita espera (não abre o gravador)
    grav.iniciar_gravacao(DEMO)
    r = rec.iniciar(str(demo))
    assert _espera(lambda: "Esperando outra gravação" in rec.obter(r["id"])["etapas"][0]["detalhe"])
    assert fab_rec.comandos == []
    fab_grav.processos[0].fim.set()                                    # a gravação manual terminou: a receita segue sozinha
    assert _espera(lambda: len(fab_rec.comandos) == 1)
    assert fab_rec.comandos[0][2] == "cs2clip.gravador_demo"
    # 2) agora a receita está com o CS2: a tela "Gravar pela demo" recusa (vira 409 na rota)
    with pytest.raises(RuntimeError, match="CS2"):
        grav.iniciar_gravacao(DEMO)
    with pytest.raises(RuntimeError, match="CS2"):
        grav.iniciar_download(so_listar=True)
    assert len(fab_grav.comandos) == 1
    rec.cancelar(r["id"])
    assert rec.esperar(r["id"])["estado"] == "cancelada"
    # 3) com a receita fora, a gravação manual volta a poder usar o CS2
    grav.iniciar_gravacao(DEMO)
    assert len(fab_grav.comandos) == 2
    grav.cancelar()


def test_g003_falha_ao_abrir_o_processo_solta_a_trava(tmp_path):
    replays = tmp_path / "replays"
    _demo(replays)
    trava = threading.Lock()

    def quebra(cmd, **kw):
        raise OSError("não deu")
    grav = GerenciadorGravacao(tmp_path / "raiz", popen_factory=quebra, python="PY", pasta_replays=lambda: replays, trava_cs2=trava)
    assert grav.iniciar_gravacao(DEMO)["estado"] == "erro"
    assert trava.acquire(blocking=False)                               # a trava não ficou presa
    trava.release()


def test_g003_o_servidor_entrega_a_mesma_trava_aos_dois(tmp_path):
    from ui.servidor import create_app
    app = create_app(root_dir=tmp_path, port=8765, gravacao_replays=lambda: None)
    try:
        assert app.state.receitas._trava_cs2 is app.state.gravacao._trava_cs2
    finally:
        app.state.fila.encerrar()


# ── M-008 + R-001/A-001: o predicado único, o vigia da janela fechada e o encerramento dos 4 gerenciadores ─────────

class _Ger:
    """Gerenciador falso para a tabela do coordenador."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _vigiar(monkeypatch, estado, passos_max=80, ao_passar=None):
    """Roda o vigia com relógio falso (+5 s por volta). Devolve em que volta ELE mandou encerrar (None = não mandou)."""
    import types

    import ui.__main__ as ui_main
    relogio = {"t": 1000.0, "n": 0}
    server = types.SimpleNamespace(should_exit=False, pelo_vigia=True)

    def dorme(_):
        relogio["n"] += 1
        relogio["t"] += 5
        if ao_passar:
            ao_passar(relogio["n"])
        if relogio["n"] > passos_max:
            server.pelo_vigia = False
            server.should_exit = True
    monkeypatch.setattr(ui_main, "time", types.SimpleNamespace(sleep=dorme, monotonic=time.monotonic))
    estado.janela_fechada_em = 1000.0
    estado.ultima_atividade = 1000.0
    ui_main.vigia_encerramento(cast(Any, server), estado, agora=lambda: relogio["t"], intervalo=0)
    return relogio["n"] if server.pelo_vigia else None


@pytest.fixture
def app_vazio(tmp_path):
    from ui.servidor import create_app
    app = create_app(root_dir=tmp_path, port=8765, gravacao_replays=lambda: None)
    yield app
    app.state.fila.encerrar()


def _receita(estado):
    return {"id": "x", "estado": estado, "etapas": [{"nome": "gravar", "estado": estado}], "entrada": {}, "dados": {}, "criada_em": 0.0}


def test_r001_janela_fechada_com_receita_rodando_espera_ela_terminar_e_so_entao_sai(monkeypatch, app_vazio):
    rec = app_vazio.state.receitas
    rec._receitas["x"] = _receita("rodando")

    def termina(n):
        if n == 60:                                                   # 300 s de janela fechada: a gravação acaba agora
            rec._receitas["x"]["estado"] = "pronta"
    volta = _vigiar(monkeypatch, app_vazio.state, ao_passar=termina)
    assert volta is not None and 60 <= volta <= 62, volta             # não saiu no meio; saiu logo depois de terminar


def test_r001_receita_so_esperando_voce_nao_segura_o_programa(monkeypatch, app_vazio):
    app_vazio.state.receitas._receitas["x"] = _receita("esperando_voce")
    volta = _vigiar(monkeypatch, app_vazio.state)
    assert volta is not None and volta <= 6                            # os 20 s de sempre


@pytest.mark.parametrize("o_que", ["gravacao", "vod"])
def test_r001_gravacao_pela_demo_e_download_de_vod_seguram_o_programa(monkeypatch, app_vazio, o_que):
    if o_que == "gravacao":
        app_vazio.state.gravacao._job = {"estado": "rodando"}
    else:
        app_vazio.state.vods._jobs["v"] = {"id": "v", "estado": "baixando"}
    assert _vigiar(monkeypatch, app_vazio.state, passos_max=60) is None


class _Baixador:
    """Baixador da Twitch falso: só termina com terminate()/kill, como o de verdade."""

    def __init__(self):
        self._parar = threading.Event()
        self.terminado = False
        self.stdout = self._linhas()

    def _linhas(self):
        yield "[STATUS] - Downloading 3%\n"
        self._parar.wait(10)

    def poll(self):
        return 1 if self._parar.is_set() else None

    def terminate(self):
        self.terminado = True
        self._parar.set()

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        self._parar.wait(10)
        return 1


class _ProcFila:
    """Análise falsa da fila: roda até ser encerrada."""
    _pid = 61000

    def __init__(self):
        type(self)._pid += 1
        self.pid = type(self)._pid
        self.terminado = threading.Event()
        self.stdout = None

    def poll(self):
        return -15 if self.terminado.is_set() else None

    def wait(self, timeout=None):
        self.terminado.wait(timeout if timeout is not None else 10)
        return self.poll()

    def terminate(self):
        self.terminado.set()

    def kill(self):
        self.terminado.set()


def _app_com_tudo_rodando(tmp_path, monkeypatch):
    """Servidor com os 4 rodando ao mesmo tempo: uma receita (vídeo → análise na fila), a gravação pela demo e um download de VOD."""
    import subprocess

    import ui.fila as fila_mod
    from fastapi.testclient import TestClient
    from ui.servidor import create_app
    monkeypatch.setattr(fila_mod.subprocess, "run", lambda args, *a, **k: subprocess.CompletedProcess(args, 1))   # nunca o taskkill de verdade
    replays = tmp_path / "replays"
    _demo(replays)
    (tmp_path / "entrada").mkdir()
    video = tmp_path / "entrada" / "live.mp4"
    video.write_bytes(b"x" * 50)
    cli = tmp_path / "TwitchDownloaderCLI.exe"
    cli.write_bytes(b"x")
    analises, gravacoes, baixadores = [], _Fabrica(), []

    def fila_popen(args, **kw):
        analises.append(_ProcFila())
        return analises[-1]

    def vod_popen(cmd, **kw):
        baixadores.append(_Baixador())
        return baixadores[-1]
    app = create_app(root_dir=tmp_path, port=8765, popen_factory=fila_popen, command_builder=lambda *a: ["falso"],
                     gravacao_popen_factory=gravacoes, gravacao_replays=lambda: replays, vod_popen_factory=vod_popen, vod_cli=cli)
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    return app, c, video, analises, gravacoes, baixadores


def _por_os_4_para_rodar(app, c, video, analises, gravacoes, baixadores):
    h = {"X-Token": app.state.token}
    r = c.post("/api/trabalhos", json={"texto": str(video)}, headers=h)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert c.post("/api/gravacao/gravar", json={"demo": DEMO}, headers=h).status_code == 200
    assert c.post("/api/baixar-vod", json={"link": "twitch.tv/videos/1234567890"}, headers=h).status_code == 200
    assert _espera(lambda: len(analises) == 1 and len(baixadores) == 1 and len(gravacoes.processos) == 1)
    assert app.state.coordenador.rodando()
    return rid, h


def test_a001_botao_encerrar_cancela_os_4_gerenciadores(tmp_path, monkeypatch):
    app, c, video, analises, gravacoes, baixadores = _app_com_tudo_rodando(tmp_path, monkeypatch)
    with c:
        rid, h = _por_os_4_para_rodar(app, c, video, analises, gravacoes, baixadores)
        assert c.post("/api/encerrar", json={}, headers=h).status_code == 200
        assert app.state.pedir_saida is True
        assert analises[0].terminado.is_set()                                         # a análise da fila
        assert gravacoes.processos[0].sinais                                          # a gravação pela demo recebeu o pedido de parar
        assert _espera(lambda: baixadores[0].terminado)                               # o download do VOD
        assert _espera(lambda: app.state.receitas.obter(rid)["estado"] == "interrompida")   # a receita fica para o Retomar
        assert _espera(lambda: not app.state.coordenador.rodando())


def test_a001_fim_do_servidor_para_tudo_e_a_receita_fica_interrompida(tmp_path, monkeypatch):
    import json
    app, c, video, analises, gravacoes, baixadores = _app_com_tudo_rodando(tmp_path, monkeypatch)
    with c:
        rid, _ = _por_os_4_para_rodar(app, c, video, analises, gravacoes, baixadores)
    # saiu do `with`: o servidor terminou (lifespan). Ninguém ficou rodando sem dono.
    assert analises[0].terminado.is_set() and gravacoes.processos[0].sinais and baixadores[0].terminado
    assert not app.state.coordenador.rodando()
    salvo = json.loads((tmp_path / "trabalhos" / rid / "receita.json").read_text(encoding="utf-8"))
    assert salvo["estado"] == "interrompida"


def test_a001_processo_que_nao_atende_o_pedido_educado_tem_a_arvore_derrubada_no_prazo(tmp_path):
    from ui.coordenador import Coordenador
    replays = tmp_path / "replays"
    _demo(replays)

    class Surdo(_Proc):
        def send_signal(self, s):                                    # ignora o CTRL+BREAK (gravador travado)
            self.sinais.append(s)

        def terminate(self):
            self.sinais.append("terminate")
            self.fim.set()
    procs = []

    def fabrica(cmd, **kw):
        procs.append(Surdo())
        return procs[-1]
    grav = GerenciadorGravacao(tmp_path / "raiz", popen_factory=fabrica, python="PY", pasta_replays=lambda: replays)
    grav.iniciar_gravacao(DEMO)
    vazio = _Ger(esta_processando=lambda: False, tem_pendentes=lambda: False, cancelar_tudo=lambda: None, encerrar=lambda: None)
    rec = _Ger(tem_estado=lambda e: False, tem_viva=lambda: False, interromper_todas=lambda: None, esperar_fim=lambda p: None)
    vods = _Ger(baixando=lambda: False, parar=lambda: None, esperar_fim=lambda p: None)
    Coordenador(vazio, rec, grav, vods).encerrar(prazo_s=0.2)
    assert procs[0].sinais[0] != "terminate" and "terminate" in procs[0].sinais    # primeiro o pedido educado; no prazo, a árvore cai
    assert _espera(lambda: not grav.ocupado())


# ── G-007 + R-011: "Apagar kit" e "Gostei/Descartar" usam o predicado único ─────────────────────────────────────

def _cliente_kit(tmp_path):
    from test_ui_dados_seguranca import cliente, criar_projeto
    criar_projeto(tmp_path)
    return cliente(tmp_path, gravacao_replays=lambda: None)


def _receita_com(estado, rid="x", criada_em=0.0):
    r = _receita(estado)
    r.update(id=rid, criada_em=criada_em)
    return r
