"""N3b: cancelar mata a ÁRVORE do trabalho (filho + ffmpeg neto) e a checagem do servidor usa o Python de console.

Causa (medida pelo QA do N3): sob `pythonw` o `send_signal(CTRL_BREAK_EVENT)` falha ("Identificador inválido", o app não tem console) e caía no
`terminate()`, que mata só o filho; o ffmpeg (neto) seguia vivo e podia terminar de escrever um arquivo DEPOIS de o trabalho aparecer como "Cancelada".
O conserto fica em `cs2clip.tools.cancelar_processo` / `encerrar_arvore` (taskkill /PID <pid> /T /F, só do PID do trabalho).
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cs2clip import tools

RAIZ = Path(__file__).resolve().parent.parent
so_windows = pytest.mark.skipif(sys.platform != "win32", reason="taskkill e árvore de processos são do Windows")


class _Proc:
    """Processo falso: registra o que o cancelamento fez com ele."""

    def __init__(self, pid=4242, sinal_falha=False, vivo=True):
        self.pid, self.sinais, self.vivo, self.sinal_falha = pid, [], vivo, sinal_falha
        self._fim = threading.Event()
        self.stdout = self._linhas()

    def _linhas(self):
        self._fim.wait(10)                                    # a leitura da saída só acaba quando o processo "morre"
        yield from ()

    def send_signal(self, s):
        if self.sinal_falha:
            raise OSError(6, "O identificador é inválido")
        self.sinais.append(s)

    def poll(self):
        return None if self.vivo else 0

    def terminate(self):
        self.sinais.append("terminate")
        self.vivo = False

    def wait(self, timeout=None):
        return 0


class _Rodar:
    """Faz o papel de `subprocess.run`: anota os comandos e devolve o código de saída combinado."""

    def __init__(self, codigo=0):
        self.codigo, self.chamadas = codigo, []

    def __call__(self, cmd, **kw):
        self.chamadas.append((cmd, kw))
        return SimpleNamespace(returncode=self.codigo)


def _espera(condicao, tempo=5.0):
    fim = time.monotonic() + tempo
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.02)
    return condicao()


# ── a regra, com processos falsos ────────────────────────────────────────────────────────────────────────────

@so_windows
def test_sem_como_pedir_com_educacao_mata_a_arvore_do_pid_do_trabalho():
    p, rodar = _Proc(pid=4242, sinal_falha=True), _Rodar(0)
    tools.cancelar_processo(p, rodar=rodar)
    assert [c for c, _ in rodar.chamadas] == [["taskkill", "/PID", "4242", "/T", "/F"]]          # só o PID do trabalho, nunca pelo nome do programa
    kw = rodar.chamadas[0][1]
    assert kw["shell"] is False and kw["creationflags"] == subprocess.CREATE_NO_WINDOW            # sem shell e sem janela preta
    assert "terminate" not in p.sinais                                                             # o taskkill bastou


@so_windows
def test_se_o_taskkill_falha_cai_no_terminate():
    p, rodar = _Proc(sinal_falha=True), _Rodar(128)
    tools.cancelar_processo(p, rodar=rodar)
    assert len(rodar.chamadas) == 1 and p.sinais == ["terminate"]


@so_windows
def test_se_o_taskkill_nem_roda_cai_no_terminate():
    def quebrado(cmd, **kw):
        raise OSError("sem taskkill")
    p = _Proc(sinal_falha=True)
    tools.cancelar_processo(p, rodar=quebrado)
    assert p.sinais == ["terminate"]


@so_windows
def test_pedido_educado_que_funciona_nao_mata_a_arvore_de_cara():
    esperado = signal.CTRL_BREAK_EVENT
    p, rodar = _Proc(), _Rodar(0)
    tools.cancelar_processo(p, prazo_s=30.0, rodar=rodar)
    assert p.sinais == [esperado] and rodar.chamadas == []                                          # o gravador fecha o jogo sozinho; ninguém mata nada ainda


@so_windows
def test_se_nao_sair_no_prazo_a_arvore_cai():
    p, rodar = _Proc(), _Rodar(0)
    tools.cancelar_processo(p, prazo_s=0.1, rodar=rodar)
    assert _espera(lambda: len(rodar.chamadas) == 1)
    assert rodar.chamadas[0][0] == ["taskkill", "/PID", "4242", "/T", "/F"]


@so_windows
def test_se_sair_no_prazo_ninguem_e_morto_depois():
    p, rodar = _Proc(), _Rodar(0)
    tools.cancelar_processo(p, prazo_s=0.15, rodar=rodar)
    p.vivo = False                                                                                   # saiu sozinho depois do pedido educado
    time.sleep(0.5)
    assert rodar.chamadas == []


@so_windows
@pytest.mark.parametrize("pid", [None, 0, -5, "12", True, "PROPRIO"])
def test_nunca_mata_o_proprio_app_nem_pid_invalido(pid):
    pid = os.getpid() if pid == "PROPRIO" else pid                                                  # (o PID muda por processo: não pode estar no parametrize)
    p, rodar = _Proc(pid=pid, sinal_falha=True), _Rodar(0)
    assert tools.encerrar_arvore(p, rodar) is False
    assert rodar.chamadas == []


@so_windows
def test_processo_que_ja_saiu_nao_recebe_taskkill():
    """O PID de um processo que já saiu pode ser de outro programa agora: só mata se o trabalho ainda está vivo."""
    p, rodar = _Proc(vivo=False, sinal_falha=True), _Rodar(0)
    tools.cancelar_processo(p, rodar=rodar)
    assert rodar.chamadas == []


def test_sem_processo_nao_faz_nada():
    tools.cancelar_processo(None)


@so_windows
def test_os_dois_gerenciadores_cancelam_pelo_mesmo_auxiliar(tmp_path, monkeypatch):
    """`ui/receitas.py` e `ui/gravacao.py` não têm mais cópia própria do pedido educado + terminate."""
    from ui.gravacao import GerenciadorGravacao
    from ui.receitas import GerenciadorReceitas
    chamados = []
    monkeypatch.setattr("ui.receitas.cancelar_processo", lambda p, *a, **k: chamados.append(("receitas", p)))
    monkeypatch.setattr("ui.gravacao.cancelar_processo", lambda p, *a, **k: chamados.append(("gravacao", p)))
    p = _Proc()
    GerenciadorReceitas._parar_processo(p)
    g = GerenciadorGravacao(tmp_path / "raiz", popen_factory=lambda cmd, **kw: p, python="PY")
    g._iniciar("gravar", ["cs2clip.gravador_demo"], "teste")
    g.cancelar()
    p._fim.set()
    assert chamados == [("receitas", p), ("gravacao", p)]


# ── a prova de verdade: pai → filho → neto, sem console (como o pythonw) ────────────────────────────────────

RUNNER = textwrap.dedent('''
    import ctypes, json, subprocess, sys, threading, time
    from pathlib import Path
    raiz, modo, saida = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    sys.path.insert(0, str(raiz))
    FILHO = ("import os, subprocess, sys, time\\n"
             "n = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\\n"
             "print('PIDS', os.getpid(), n.pid, flush=True)\\n"
             "time.sleep(30)\\n")

    CONSOLE = str(Path(sys.executable).with_name("python.exe"))              # o filho do app é o python.exe (console escondido), nunca o pythonw

    def fabrica(cmd, **kw):
        return subprocess.Popen([CONSOLE, "-c", FILHO], **kw)

    def vivo(pid):
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        codigo = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(codigo))
        ctypes.windll.kernel32.CloseHandle(h)
        return codigo.value == 259

    (saida.parent / "raiz").mkdir(exist_ok=True)                            # o gerenciador roda o filho nesta pasta (cwd): ela tem de existir
    res = {"processos_no_console": ctypes.windll.kernel32.GetConsoleProcessList((ctypes.c_ulong * 4)(), 4)}
    if modo == "gravacao":
        from ui.gravacao import GerenciadorGravacao
        g = GerenciadorGravacao(saida.parent / "raiz", popen_factory=fabrica, python="PY")
        g._iniciar("gravar", ["cs2clip.gravador_demo"], "teste")
        linhas = lambda: g.estado()["linhas"]
        pid_popen = lambda: g._proc.pid
        cancelar = g.cancelar
        terminou = lambda: g.estado()["job"]["estado"] == "cancelado"
    else:
        from ui.receitas import GerenciadorReceitas
        g = GerenciadorReceitas(saida.parent / "raiz", popen_factory=fabrica, python="PY")
        r = {"id": "x", "estado": "rodando", "dados": {}, "etapas": [], "criada_em": 0.0}
        g._receitas["x"] = r
        g._cancel["x"] = threading.Event()
        fio = threading.Thread(target=lambda: _ignora(lambda: g._rodar(r, {}, ["cs2clip.editar_demo"])), daemon=True)
        def _ignora(f):
            try:
                f()
            except BaseException:
                pass
        fio.start()
        linhas = lambda: g._linhas.get("x", [])
        pid_popen = lambda: g._procs["x"].pid
        cancelar = lambda: g.cancelar("x")
        terminou = lambda: not fio.is_alive()

    fim = time.monotonic() + 20
    while time.monotonic() < fim and not any(l.startswith("PIDS") for l in linhas()):
        time.sleep(0.05)
    achada = [l for l in linhas() if l.startswith("PIDS")][0].split()
    real, neto, popen = int(achada[1]), int(achada[2]), pid_popen()
    res.update(popen=popen, filho=real, neto=neto, vivos_antes=[vivo(p) for p in (popen, real, neto)])
    saida.write_text(json.dumps(res), encoding="utf-8")                     # os PIDs ficam salvos já: o teste limpa mesmo se algo travar
    t0 = time.monotonic()
    cancelar()
    res["cancelar_demorou_s"] = round(time.monotonic() - t0, 2)
    fim = time.monotonic() + 20
    while time.monotonic() < fim and not terminou():
        time.sleep(0.05)
    res["terminou"] = terminou()
    time.sleep(1.5)                                                           # dá tempo de um neto sobrevivente "terminar de escrever"
    res["vivos_depois"] = [vivo(p) for p in (popen, real, neto)]
    saida.write_text(json.dumps(res), encoding="utf-8")
''')


def _vivo(pid: int) -> bool:
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    codigo = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(codigo))
    ctypes.windll.kernel32.CloseHandle(h)
    return codigo.value == 259


def _rodar_runner(tmp_path: Path, modo: str) -> dict:
    runner, saida = tmp_path / "runner.py", tmp_path / f"{modo}.json"
    runner.write_text(RUNNER, encoding="utf-8")
    pythonw = Path(sys.executable).with_name("pythonw.exe")                                            # o mesmo executável do app do dono: sem console
    if not pythonw.is_file():
        pytest.skip("sem pythonw.exe neste Python")
    try:
        r = subprocess.run([str(pythonw), str(runner), str(RAIZ), modo, str(saida)], cwd=str(tmp_path),
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=(tmp_path / f"{modo}.err").open("wb"), timeout=90)
        assert r.returncode == 0, (tmp_path / f"{modo}.err").read_text(errors="replace")
        return json.loads(saida.read_text(encoding="utf-8"))
    finally:                                                                                           # nada de processo sobrando, falhe o que falhar
        if saida.is_file():
            try:
                pids = [json.loads(saida.read_text(encoding="utf-8")).get(k) for k in ("popen", "filho", "neto")]
            except ValueError:
                pids = []
            for pid in pids:
                if pid:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


@so_windows
@pytest.mark.parametrize("modo", ["gravacao", "receitas"])
def test_cancelar_sem_console_mata_filho_e_neto(tmp_path, modo):
    """Pelo caminho do gerenciador (`cancelar`), com o app SEM console: o filho (`python -m cs2clip.*`) e o ffmpeg dele morrem juntos."""
    res = _rodar_runner(tmp_path, modo)
    assert res["processos_no_console"] == 0                                                           # a cena é a do pythonw: o app não tem console
    assert res["vivos_antes"] == [True, True, True]                                                   # os três existiam de verdade
    assert res["terminou"] is True
    assert res["vivos_depois"] == [False, False, False], res                                           # nenhum processo da árvore sobra
    assert res["cancelar_demorou_s"] < 15


# ── M26: a checagem do servidor usa o Python de console ──────────────────────────────────────────────────────

def test_checagem_do_servidor_roda_com_o_python_de_console(tmp_path, monkeypatch):
    from ui import servidor
    comandos = []

    def falso(cmd, **kw):
        comandos.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    console = tmp_path / "console" / "python.exe"
    monkeypatch.setattr(servidor, "python_de_console", lambda: console)
    monkeypatch.setattr(servidor.subprocess, "run", falso)
    assert servidor._executar_checagem(tmp_path)["codigo"] == 0
    cmd, kw = comandos[0]
    assert cmd == [str(console), "-m", "cs2clip", "--checar"]
    assert cmd[0] != sys.executable                                                                   # sys.executable é o pythonw do app: janela preta por ffmpeg
    if sys.platform == "win32":
        assert kw["creationflags"] == subprocess.CREATE_NO_WINDOW and kw["shell"] is False
