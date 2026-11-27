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
