"""Revisão de 26/11/2026 (lote B), R-012: cancelar o download do VOD derruba a ÁRVORE do baixador (o TwitchDownloaderCLI usa o FFmpeg, um neto).
Processos de verdade criados pelo próprio teste: um "baixador" Python que abre um neto e espera. Só os PIDs deles são encerrados."""
from __future__ import annotations

import ctypes
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ui.vods import GerenciadorVod

so_windows = pytest.mark.skipif(sys.platform != "win32", reason="taskkill e árvore de processos são do Windows")
# o Python de verdade (no .venv, o python.exe é um lançador que põe os filhos num "job" do Windows e esconderia o defeito)
PYTHON = getattr(sys, "_base_executable", None) or sys.executable

BAIXADOR = textwrap.dedent("""
    import subprocess, sys, time
    neto = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], creationflags=subprocess.CREATE_NO_WINDOW)
    open(sys.argv[1], "w").write(str(neto.pid))
    print("[STATUS] - Downloading 3%", flush=True)
    time.sleep(120)
""")


def _vivo(pid: int) -> bool:
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    codigo = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(codigo))
    ctypes.windll.kernel32.CloseHandle(h)
    return codigo.value == 259


def _espera(condicao, tempo=15.0):
    fim = time.monotonic() + tempo
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.05)
    return condicao()


@so_windows
def test_r012_cancelar_o_vod_derruba_o_baixador_e_o_neto(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    cli = tmp_path / "TwitchDownloaderCLI.exe"
    cli.write_bytes(b"x")
    script, arq_neto = tmp_path / "baixador.py", tmp_path / "neto.pid"
    script.write_text(BAIXADOR, encoding="utf-8")
    criados: list[subprocess.Popen] = []

    def popen(cmd, **kw):
        criados.append(subprocess.Popen([PYTHON, str(script), str(arq_neto)], **kw))
        return criados[-1]
    g = GerenciadorVod(entrada, popen_factory=popen, cli=cli)
    neto = 0
    try:
        job = g.iniciar("twitch.tv/videos/1234567890")
        assert _espera(lambda: arq_neto.is_file() and arq_neto.read_text().strip().isdigit())
        neto = int(arq_neto.read_text())
        assert _vivo(criados[0].pid) and _vivo(neto)
        g.cancelar(job["id"])
        assert _espera(lambda: g.listar()["itens"][0]["estado"] == "cancelado")
        assert _espera(lambda: not _vivo(neto)), "o neto (o FFmpeg do baixador) ficou vivo"
    finally:                                                             # nada sobra, passe ou falhe
        for pid in [p.pid for p in criados] + ([neto] if neto else []):
            if _vivo(pid):
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


def test_r012_cancelar_antes_de_o_baixador_existir_tambem_derruba(tmp_path, monkeypatch):
    """Com dublê: o pedido de cancelar chegou antes do processo existir; quando ele nasce, cai pelo mesmo caminho (árvore)."""
    import ui.vods as vods_mod
    derrubados = []
    monkeypatch.setattr(vods_mod, "derrubar_arvore", lambda proc: derrubados.append(proc) or proc.terminate())

    class Proc:
        def __init__(self):
            self.stdout = iter(())
            self.terminado = False

        def terminate(self):
            self.terminado = True

        def wait(self, timeout=None):
            return 1
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    cli = tmp_path / "TwitchDownloaderCLI.exe"
    cli.write_bytes(b"x")
    proc = Proc()
    g = GerenciadorVod(entrada, popen_factory=lambda cmd, **kw: proc, cli=cli)
    g._motivo_parada["x"] = "cancelado"
    g._executar({"id": "x", "vod": "1234567890", "estado": "baixando", "percentual": 0, "arquivo": "a.mp4", "erro": ""}, entrada / "a.mp4")
    assert derrubados == [proc] and proc.terminado
    assert Path(entrada).is_dir()
