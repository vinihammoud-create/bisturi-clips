"""Revisão de 26/11/2026 (lote B): a fila de análise. R-004/A-005 (cancelar sem prender a trava e sem derrubar o processo duas vezes),
R-003/A-003 (progresso.json velho com PID reaproveitado não trava a fila) e G-012 (progresso.json gravado de forma atômica).
Os processos são falsos, ou de verdade criados pelo próprio teste. Nunca roda `taskkill` em PID que o teste não criou."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import pytest

import ui.fila as fila_mod
from test_ui_fila import FakeProcess
from ui.fila import GerenciadorFila


@pytest.fixture(autouse=True)
def taskkill_falso(monkeypatch):
    """O taskkill de verdade nunca roda aqui (os PIDs dos processos falsos podem existir no Windows)."""
    monkeypatch.setattr(fila_mod.subprocess, "run", lambda args, *a, **k: subprocess.CompletedProcess(args, 1))


def _espera(condicao, tempo=10.0):
    fim = time.monotonic() + tempo
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.01)
    return condicao()


def _fila_com_um_rodando(tmp_path, monkeypatch, encerrar):
    (tmp_path / "saida").mkdir()
    arquivo = tmp_path / "longo.mp4"
    arquivo.write_bytes(b"x")
    processos = []

    def fabrica(args, **kw):
        processos.append(FakeProcess(code=0, delay=30))
        return processos[-1]
    monkeypatch.setattr(GerenciadorFila, "_encerrar_processo", staticmethod(encerrar))
    m = GerenciadorFila(tmp_path, popen_factory=fabrica, command_builder=lambda *a: ["falso"])
    job = m.adicionar(arquivo, {})
    assert _espera(lambda: m.listar()["ativo"] == job["id"] and len(processos) == 1)
    return m, job, processos


# ── R-004 + A-005 ───────────────────────────────────────────────────────────────────────────────────────────

def test_r004_cancelar_nao_prende_a_fila_enquanto_o_processo_cai(tmp_path, monkeypatch):
    entrou, solta = threading.Event(), threading.Event()

    def encerrar_demorado(processo, pid):                            # o taskkill demorando (árvore grande, antivírus)
        entrou.set()
        solta.wait(10)
        processo.terminate()
    m, job, _ = _fila_com_um_rodando(tmp_path, monkeypatch, encerrar_demorado)
    try:
        threading.Thread(target=m.cancelar, args=(job["id"],), daemon=True).start()
        assert entrou.wait(10)
        respondeu = threading.Event()
        threading.Thread(target=lambda: (m.listar(), m.esta_processando(), respondeu.set()), daemon=True).start()
        assert respondeu.wait(5)                                       # a tela (que pergunta /api/fila a cada 1 s) não fica sem resposta
    finally:
        solta.set()
        m.encerrar()


def test_a005_cancelar_derruba_o_processo_uma_vez_so(tmp_path, monkeypatch):
    chamadas = []

    def encerrar(processo, pid):
        chamadas.append(pid)
        processo.terminate()
    m, job, processos = _fila_com_um_rodando(tmp_path, monkeypatch, encerrar)
    try:
        assert m.cancelar(job["id"])["estado"] == "cancelado"
        assert _espera(lambda: m.listar()["ativo"] is None)
        assert chamadas == [processos[0].pid]                          # antes: o cancelar e depois o laço do job, de novo, no mesmo PID
    finally:
        m.encerrar()


def test_a005_processo_que_nao_caiu_no_cancelar_ainda_e_derrubado_pelo_laco(tmp_path, monkeypatch):
    """CONTROLE: se a primeira tentativa não derrubou (taskkill e terminate falharam), o laço do job tenta de novo."""
    chamadas = []

    def encerrar(processo, pid):
        chamadas.append(pid)
        if len(chamadas) > 1:
            processo.terminate()
    m, job, processos = _fila_com_um_rodando(tmp_path, monkeypatch, encerrar)
    try:
        m.cancelar(job["id"])
        assert _espera(lambda: m.listar()["ativo"] is None)
        assert chamadas == [processos[0].pid, processos[0].pid]
    finally:
        m.encerrar()


# ── R-003 + A-003: progresso.json velho com o PID reaproveitado não trava a fila ───────────────────────────────

so_windows = pytest.mark.skipif(sys.platform != "win32", reason="o PID reaproveitado e o acesso negado são do Windows")


@pytest.fixture
def processo_de_teste():
    """Processos de verdade criados pelo teste (e só eles são encerrados no fim)."""
    criados = []

    def criar(cmd):
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        criados.append(p)
        return p
    yield criar
    for p in criados:
        p.kill()
        p.wait(10)


def _agora_iso(delta_s=0.0):
    return (datetime.now() + timedelta(seconds=delta_s)).isoformat(timespec="seconds")


def _fila_com_progresso(tmp_path, progresso):
    (tmp_path / "saida").mkdir(exist_ok=True)
    (tmp_path / "saida" / "progresso.json").write_text(json.dumps(progresso), encoding="utf-8")
    arquivo = tmp_path / "video.mp4"
    arquivo.write_bytes(b"x")
    m = GerenciadorFila(tmp_path, popen_factory=lambda *a, **k: FakeProcess(0), command_builder=lambda *a: ["falso"])
    return m, arquivo


@so_windows
def test_r003_pid_reaproveitado_por_outro_programa_nao_bloqueia_a_fila(tmp_path, processo_de_teste):
    outro = processo_de_teste(["cmd.exe", "/q", "/k"])                  # um programa qualquer que NÃO é o Python do Bisturi
    m, arquivo = _fila_com_progresso(tmp_path, {"estado": "rodando", "pid": outro.pid, "iniciado_em": _agora_iso(-3600)})
    try:
        assert m.esta_processando() is False
        m.adicionar(arquivo)                                            # antes: "Já existe um processamento iniciado fora da interface"
    finally:
        m.encerrar()


@so_windows
def test_r003_python_que_nasceu_depois_do_progresso_nao_e_o_nucleo_antigo(tmp_path, processo_de_teste):
    novo = processo_de_teste([sys.executable, "-c", "import sys; sys.stdin.read()"])
    m, arquivo = _fila_com_progresso(tmp_path, {"estado": "rodando", "pid": novo.pid, "iniciado_em": _agora_iso(-3600)})
    try:
        assert m.esta_processando() is False
        m.adicionar(arquivo)
    finally:
        m.encerrar()


@so_windows
def test_r003_controle_o_nucleo_rodando_de_verdade_continua_bloqueando(tmp_path, processo_de_teste):
    nucleo = processo_de_teste([sys.executable, "-c", "import sys; sys.stdin.read()"])
    m, arquivo = _fila_com_progresso(tmp_path, {"estado": "rodando", "pid": nucleo.pid, "iniciado_em": _agora_iso(+1)})
    try:
        assert m.esta_processando() is True
        with pytest.raises(RuntimeError, match="fora da interface"):
            m.adicionar(arquivo)
    finally:
        m.encerrar()


@so_windows
def test_a003_acesso_negado_nao_e_o_python_do_bisturi(monkeypatch):
    from ui import fila
    monkeypatch.setattr(fila, "_processo_windows", lambda pid: {"negado": True, "nome": "", "criado_em": None})
    assert fila.nucleo_vivo(4242, _agora_iso()) is False
    monkeypatch.undo()
    assert fila.nucleo_vivo(4, None) is False                           # o processo System (PID 4) do Windows de verdade


@so_windows
@pytest.mark.parametrize("info, esperado", [
    (None, False),                                                       # o processo não existe mais
    ({"negado": False, "nome": "", "criado_em": None}, False),           # o Windows não disse o nome: não dá para confirmar
    ({"negado": False, "nome": "notepad.exe", "criado_em": 0.0}, False),
    ({"negado": False, "nome": "python.exe", "criado_em": None}, True),  # sem a hora de criação: vale o nome
    ({"negado": False, "nome": "pythonw.exe", "criado_em": 0.0}, True),
    ({"negado": False, "nome": "python3.12.exe", "criado_em": 0.0}, True),
])
def test_a003_casos_de_erro_com_duble_do_windows(monkeypatch, info, esperado):
    from ui import fila
    monkeypatch.setattr(fila, "_processo_windows", lambda pid: info)
    assert fila.nucleo_vivo(4242, _agora_iso()) is esperado


@so_windows
def test_a003_falha_ao_perguntar_ao_windows_nao_trava_a_fila(monkeypatch):
    from ui import fila

    def quebra(pid):
        raise OSError("ctypes indisponível")
    monkeypatch.setattr(fila, "_processo_windows", quebra)
    assert fila.nucleo_vivo(4242, None) is False
    assert fila.nucleo_vivo(None) is False and fila.nucleo_vivo("abc") is False and fila.nucleo_vivo(True) is False


@so_windows
def test_r003_a_tela_tambem_nao_mostra_rodando_para_o_pid_reaproveitado(tmp_path, processo_de_teste):
    from test_ui_dados_seguranca import cliente, criar_projeto, token_headers
    criar_projeto(tmp_path)
    outro = processo_de_teste(["cmd.exe", "/q", "/k"])
    (tmp_path / "saida" / "progresso.json").write_text(json.dumps({"estado": "rodando", "pid": outro.pid, "iniciado_em": _agora_iso(-3600)}),
                                                       encoding="utf-8")
    with cliente(tmp_path, gravacao_replays=lambda: None) as c:
        progresso = c.get("/api/progresso", headers=token_headers(c)).json()
        assert progresso["externo"] is False and progresso["progresso"]["estado"] == "erro"



# ── G-012 (parte do lote B): o progresso.json que a fila reescreve ao cancelar é trocado inteiro ──────────────────

def test_g012_cancelar_com_queda_no_meio_da_gravacao_nao_deixa_o_progresso_pela_metade(tmp_path, monkeypatch):
    from pathlib import Path as P
    real = P.write_text

    def grava_pela_metade(self, texto, *a, **kw):                     # a luz cai (ou o disco enche) no meio da gravação
        if self.name.startswith("progresso.json"):
            real(self, texto[: len(texto) // 2], *a, **kw)
            raise OSError(28, "sem espaço")
        return real(self, texto, *a, **kw)

    def encerrar(processo, pid):
        processo.terminate()
    m, job, _ = _fila_com_um_rodando(tmp_path, monkeypatch, encerrar)
    antes = {"formato": 1, "estado": "rodando", "pid": 1, "iniciado_em": _agora_iso(), "video": "partida.mp4"}
    (tmp_path / "saida" / "progresso.json").write_text(json.dumps(antes), encoding="utf-8")
    monkeypatch.setattr(P, "write_text", grava_pela_metade)
    try:
        m.cancelar(job["id"])
        assert _espera(lambda: m.listar()["ativo"] is None)
    finally:
        m.encerrar()
    monkeypatch.setattr(P, "write_text", real)
    assert json.loads((tmp_path / "saida" / "progresso.json").read_text(encoding="utf-8")) == antes   # o antigo, inteiro


def test_g012_controle_cancelar_marca_o_progresso_como_cancelado(tmp_path, monkeypatch):
    def encerrar(processo, pid):
        processo.terminate()
    m, job, _ = _fila_com_um_rodando(tmp_path, monkeypatch, encerrar)
    (tmp_path / "saida" / "progresso.json").write_text(json.dumps({"formato": 1, "estado": "rodando", "pid": 1}), encoding="utf-8")
    try:
        m.cancelar(job["id"])
        assert _espera(lambda: m.listar()["ativo"] is None)
    finally:
        m.encerrar()
    salvo = json.loads((tmp_path / "saida" / "progresso.json").read_text(encoding="utf-8"))
    assert salvo["estado"] == "erro" and salvo["mensagem"] == "Cancelado"
