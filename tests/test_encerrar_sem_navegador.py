"""P5: o botão "Encerrar" (e o smoke do instalador) precisam encerrar o servidor também quando ele sobe com --sem-navegador ou --manter.
Antes só a vigia da janela lia `pedir_saida`, e ela nunca nascia nesses modos: o POST dava 200 e o processo seguia vivo."""
import threading
import types
from typing import Any, cast

import ui.__main__ as ui_main


def test_vigia_do_pedido_de_saida_encerra_quando_o_pedido_chega():
    server = types.SimpleNamespace(should_exit=False)
    estado = types.SimpleNamespace(pedir_saida=False)
    t = threading.Thread(target=ui_main.vigia_pedido_de_saida, args=(cast(Any, server), estado, 0.01), daemon=True)
    t.start()
    assert not server.should_exit
    estado.pedir_saida = True
    t.join(5)
    assert not t.is_alive() and server.should_exit


def test_vigia_do_pedido_de_saida_para_se_o_servidor_ja_saiu():
    server = types.SimpleNamespace(should_exit=True)
    estado = types.SimpleNamespace(pedir_saida=False)
    t = threading.Thread(target=ui_main.vigia_pedido_de_saida, args=(cast(Any, server), estado, 0.01), daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive()


def _subir(monkeypatch, argv):
    """Roda `main()` com um servidor falso e devolve que vigias nasceram."""
    nasceram = []

    class ServidorFalso:
        def __init__(self, config):
            self.should_exit = False
            self.started = True

        def run(self):
            pass

    class ThreadFalsa:
        def __init__(self, target=None, args=(), daemon=None, **_):
            self.target = target

        def start(self):
            nasceram.append(getattr(self.target, "__name__", str(self.target)))

    monkeypatch.setattr(ui_main, "preparar_log", lambda: None)
    monkeypatch.setattr(ui_main, "instancia_ativa", lambda: None)
    monkeypatch.setattr(ui_main, "porta_livre", lambda: 8765)
    monkeypatch.setattr(ui_main, "create_app", lambda **k: types.SimpleNamespace(state=types.SimpleNamespace(pedir_saida=False)))
    monkeypatch.setattr(ui_main.uvicorn, "Config", lambda *a, **k: None)
    monkeypatch.setattr(ui_main.uvicorn, "Server", ServidorFalso)
    monkeypatch.setattr(ui_main.threading, "Thread", ThreadFalsa)
    monkeypatch.setattr(ui_main.sys, "argv", ["ui", *argv])
    assert ui_main.main() == 0
    return nasceram


def test_sem_navegador_liga_a_vigia_do_pedido_de_saida(monkeypatch):
    nasceram = _subir(monkeypatch, ["--sem-navegador"])
    assert "vigia_pedido_de_saida" in nasceram
    assert "vigia_encerramento" not in nasceram      # sem janela, nada de fechar sozinho por inatividade


def test_manter_liga_a_vigia_do_pedido_de_saida(monkeypatch):
    nasceram = _subir(monkeypatch, ["--manter"])
    assert "vigia_pedido_de_saida" in nasceram and "vigia_encerramento" not in nasceram


def test_modo_normal_continua_com_a_vigia_da_janela(monkeypatch):
    nasceram = _subir(monkeypatch, [])
    assert "vigia_encerramento" in nasceram


# ── o encerramento não pode ficar preso numa conexão aberta (achado no 1º uso real, 27/11) ─────────────────────────────────────────────

def _subir_e_pegar_config(monkeypatch):
    visto = {}

    class ServidorFalso:
        def __init__(self, config):
            self.should_exit = False
            self.started = True

        def run(self):
            pass

    def config_falsa(*a, **k):
        visto.update(k)

    monkeypatch.setattr(ui_main, "preparar_log", lambda: None)
    monkeypatch.setattr(ui_main, "instancia_ativa", lambda: None)
    monkeypatch.setattr(ui_main, "porta_livre", lambda: 8765)
    monkeypatch.setattr(ui_main, "create_app", lambda **k: types.SimpleNamespace(state=types.SimpleNamespace(pedir_saida=False)))
    monkeypatch.setattr(ui_main.uvicorn, "Config", config_falsa)
    monkeypatch.setattr(ui_main.uvicorn, "Server", ServidorFalso)
    monkeypatch.setattr(ui_main.threading, "Thread", lambda *a, **k: types.SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(ui_main.sys, "argv", ["ui", "--sem-navegador"])
    assert ui_main.main() == 0
    return visto


def test_o_servidor_tem_prazo_para_encerrar_conexoes_abertas(monkeypatch):
    """Sem `timeout_graceful_shutdown` o uvicorn espera para sempre: um pedido de música parado (o navegador não lê) segurava o processo vivo."""
    visto = _subir_e_pegar_config(monkeypatch)
    assert 0 < visto.get("timeout_graceful_shutdown", 0) <= 10


def test_encerrar_com_musica_parada_no_navegador_nao_prende_o_processo():
    """Real (sobe a interface): abre um Range de música sem ler e pede Encerrar; o processo tem de sair em poucos segundos."""
    import os
    import socket
    import subprocess
    import time
    import urllib.request
    from pathlib import Path
    from urllib.parse import quote
    raiz = Path(__file__).resolve().parents[1]
    faixas = sorted((raiz / "musicas").glob("*.mp3"))
    if not faixas:
        import pytest
        pytest.skip("sem música no catálogo (a cópia pública não leva os mp3)")
    sys_path = str(raiz / "instalador" / "smoke")
    import sys as _sys
    _sys.path.insert(0, sys_path)
    from smoke import escolher_porta, extrair_token  # pyright: ignore[reportMissingImports]  (o caminho entra no sys.path na hora)
    porta = escolher_porta()
    import tempfile
    dados = tempfile.mkdtemp(prefix="enc_")
    p = subprocess.Popen([_sys.executable, "-m", "ui", "--sem-navegador", "--port", str(porta)], cwd=str(raiz),
                         env={**os.environ, "BISTURI_DADOS": dados}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{porta}"
    s = None
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(base + "/health", timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        token = extrair_token(urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8", "replace"))
        cr = chr(13) + chr(10)
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        s.connect(("127.0.0.1", porta))
        s.sendall(("GET /api/musicas/audio/" + quote(faixas[0].name) + " HTTP/1.1" + cr + "Host: 127.0.0.1:" + str(porta) + cr + "Range: bytes=0-" + cr + cr).encode())
        time.sleep(1.5)
        s.settimeout(1)
        assert b"206" in s.recv(200)                      # a música começou a ser servida e o "navegador" parou de ler
        req = urllib.request.Request(base + "/api/encerrar", method="POST", headers={"X-Token": token}, data=b"")
        assert urllib.request.urlopen(req, timeout=10).status == 200
        t0 = time.time()
        while time.time() - t0 < 20 and p.poll() is None:
            time.sleep(0.25)
        assert p.poll() is not None, "o processo não saiu em 20 s: ficou preso na conexão da música"
    finally:
        if s is not None:
            s.close()
        if p.poll() is None:
            p.kill()
