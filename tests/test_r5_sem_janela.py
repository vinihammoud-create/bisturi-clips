"""Abrir o programa sem a janela preta (pythonw.exe): log em arquivo, aviso de erro, encerramento sozinho."""
import sys
import threading
import types
from pathlib import Path

import pytest

import ui.__main__ as ui_main
from ui.fila import construir_argumentos
from ui.seguranca import python_de_console
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

RAIZ = Path(__file__).resolve().parents[1]


# ── o núcleo e a checagem rodam com python.exe, não com o pythonw do atalho ──────────────────────

def test_python_de_console_troca_o_pythonw_pelo_python(tmp_path, monkeypatch):
    (tmp_path / "pythonw.exe").write_bytes(b"")
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert python_de_console() == tmp_path / "python.exe"
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    assert python_de_console() == tmp_path / "python.exe"
    (tmp_path / "python.exe").unlink()
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert python_de_console() == tmp_path / "pythonw.exe"          # sem irmão: não inventa caminho


def test_comando_da_fila_usa_o_python_com_console(tmp_path, monkeypatch):
    # chamado sem python_exe, como o servidor real faz (antes um NameError derrubava todo processamento)
    (tmp_path / "pythonw.exe").write_bytes(b"")
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "v.mp4").write_bytes(b"x")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    args = construir_argumentos(tmp_path, [tmp_path / "v.mp4"], {})
    assert args[0] == str(tmp_path / "python.exe") and args[1:3] == ["-m", "cs2clip"]


# ── sem console: log em arquivo e aviso numa caixa do Windows ────────────────────────────────────

def test_sem_console_a_saida_vai_para_o_ui_log(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_main, "RAIZ", tmp_path)
    monkeypatch.setattr(ui_main, "_SEM_CONSOLE", True)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    log = ui_main.preparar_log()
    print("olá do programa")
    sys.stdout.flush()
    assert log == tmp_path / "saida" / "ui.log"
    assert "olá do programa" in log.read_text(encoding="utf-8")
    assert sys.stderr is sys.stdout                                    # uvicorn não quebra por falta de stderr
    sys.stdout.close()


def test_com_console_nao_mexe_na_saida(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_main, "RAIZ", tmp_path)
    monkeypatch.setattr(ui_main, "_SEM_CONSOLE", False)
    assert ui_main.preparar_log() is None and not (tmp_path / "saida").exists()


def test_log_grande_recomeca(tmp_path, monkeypatch):
    (tmp_path / "saida").mkdir()
    (tmp_path / "saida" / "ui.log").write_text("x" * (ui_main.LOG_MAX + 10), encoding="utf-8")
    monkeypatch.setattr(ui_main, "RAIZ", tmp_path)
    monkeypatch.setattr(ui_main, "_SEM_CONSOLE", True)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    log = ui_main.preparar_log()
    sys.stdout.close()
    assert log.stat().st_size < 200


def test_erro_ao_iniciar_sem_console_vira_caixa_de_aviso(monkeypatch):
    caixas = []
    fake = types.SimpleNamespace(windll=types.SimpleNamespace(user32=types.SimpleNamespace(
        MessageBoxW=lambda *a: caixas.append(a[1]))))
    monkeypatch.setitem(sys.modules, "ctypes", fake)
    monkeypatch.setattr(ui_main, "_SEM_CONSOLE", True)
    ui_main.avisar("Não consegui iniciar")
    assert caixas == ["Não consegui iniciar"]
    caixas.clear()
    monkeypatch.setattr(ui_main, "_SEM_CONSOLE", False)
    ui_main.avisar("com console: só o texto")
    assert caixas == []


# ── encerra sozinho quando a janela fecha ────────────────────────────────────────────────────────

class _Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _vigiar(estado, passos, processando=False, relogio=None):
    """Roda a lógica do vigia `passos` vezes, avançando o relógio em 5 s a cada volta."""
    relogio = relogio or _Relogio()
    server = types.SimpleNamespace(should_exit=False)
    estado.fila = types.SimpleNamespace(esta_processando=lambda: processando)
    real_sleep = ui_main.time.sleep
    n = {"i": 0}

    def sleep(_):
        n["i"] += 1
        relogio.t += 5
        if n["i"] > passos:
            server.should_exit = True        # só para o teste não rodar para sempre

    ui_main.time.sleep = sleep
    try:
        ui_main.vigia_encerramento(server, estado, agora=relogio, intervalo=0)
    finally:
        ui_main.time.sleep = real_sleep
    return n["i"] <= passos                      # True = o vigia encerrou por conta própria


def _estado(relogio_t=1000.0, **kw):
    base = dict(ultima_atividade=relogio_t, janela_fechada_em=None, pedir_saida=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_janela_fechada_encerra_depois_do_prazo():
    assert _vigiar(_estado(janela_fechada_em=1000.0), passos=20)             # 5 s por volta: passa dos 20 s
    assert not _vigiar(_estado(janela_fechada_em=1000.0), passos=2)          # 10 s: ainda dentro do prazo


def test_com_processamento_nao_encerra_mesmo_com_a_janela_fechada():
    assert not _vigiar(_estado(janela_fechada_em=1000.0), passos=60, processando=True)


def test_pagina_que_sumiu_sem_avisar_encerra_depois_de_3_minutos():
    assert _vigiar(_estado(), passos=60)                                      # 60 x 5 s = 300 s sem nenhum pedido
    assert not _vigiar(_estado(), passos=20)                                  # 100 s: ainda cedo


def test_botao_encerrar_sai_na_hora():
    assert _vigiar(_estado(pedir_saida=True), passos=1)


def test_processamento_zera_o_relogio_de_ociosidade():
    est = _estado()
    assert not _vigiar(est, passos=50, processando=True)
    assert est.ultima_atividade > 1000.0 + 200                                # acompanhou o processamento


# ── rotas ────────────────────────────────────────────────────────────────────────────────────────

def test_rotas_de_fechar_marcam_o_estado(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        h = token_headers(c)
        assert c.post("/api/janela-fechada", headers=h, json={}).json() == {"sucesso": True}
        assert c.app_state.janela_fechada_em is not None
        c.get("/api/fila")                                                    # a página voltou (F5): cancela o aviso
        assert c.app_state.janela_fechada_em is None
        assert c.post("/api/encerrar", headers=h, json={}).json()["sucesso"] is True
        assert c.app_state.pedir_saida is True


def test_rotas_de_fechar_exigem_o_token(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert c.post("/api/encerrar", json={}).status_code == 403
        assert c.post("/api/janela-fechada", json={}).status_code == 403
        assert c.app_state.pedir_saida is False


# ── atalho e instalador ──────────────────────────────────────────────────────────────────────────

def test_atalho_abre_o_pythonw_sem_console():
    texto = (RAIZ / "ui" / "criar_atalho.ps1").read_bytes()
    assert b"\r\n" in texto and b"\n" not in texto.replace(b"\r\n", b"")
    s = texto.decode("ascii")                          # só ASCII: o PowerShell 5.1 lê .ps1 sem BOM como ANSI
    assert "pythonw.exe" in s and "'-m ui'" in s and "Arguments" in s and "WindowStyle = 7" in s
    assert "iniciar.bat" in s                          # plano B se o pythonw não existir


def test_instalador_abre_o_programa_sem_janela_preta():
    texto = (RAIZ / "instalar.bat").read_text(encoding="utf-8")
    assert 'start "" ".venv\\Scripts\\pythonw.exe" -m ui' in texto


def test_pagina_avisa_o_servidor_ao_fechar_e_tem_o_botao_de_encerrar():
    js = (RAIZ / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    html = (RAIZ / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "/api/janela-fechada" in js and "keepalive: true" in js and '"pagehide"' in js
    assert "/api/encerrar" in js and 'id="quit-app"' in html
    assert "janela preta" not in html


def test_video_de_relatorio_ainda_tocando_segura_o_programa():
    # a janela principal fechou, mas um relatório aberto à parte pediu um trecho de vídeo há 5 s
    est = _estado(janela_fechada_em=1000.0, ultima_midia=1000.0 + 5 * 4)
    assert not _vigiar(est, passos=4)                    # 20 s após o fechamento, mas o último trecho foi há 5 s
    assert _vigiar(_estado(janela_fechada_em=1000.0, ultima_midia=1000.0), passos=20)   # parou de pedir: encerra


def test_pc_que_dormiu_nao_conta_como_pagina_sumida():
    relogio = _Relogio()
    est = _estado()
    estado_fila = types.SimpleNamespace(esta_processando=lambda: False)
    est.fila = estado_fila
    server = types.SimpleNamespace(should_exit=False)
    n = {"i": 0}
    real_sleep = ui_main.time.sleep

    def sleep(_):
        n["i"] += 1
        relogio.t += 4000 if n["i"] == 1 else 2     # 1ª volta: o PC dormiu ~1 h; depois, tempo normal
        if n["i"] >= 3:
            server.should_exit = True
    ui_main.time.sleep = sleep
    try:
        ui_main.vigia_encerramento(server, est, agora=relogio, intervalo=0)
    finally:
        ui_main.time.sleep = real_sleep
    assert est.ultima_atividade >= relogio.t - 4        # a contagem recomeçou depois do salto
    assert server.should_exit and n["i"] == 3           # saiu só pelo fim do teste, não pelo vigia


def test_servidor_marca_a_ultima_midia(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        antes = c.app_state.ultima_midia
        assert c.get("/media/v1/cortes/corte.mp4").status_code in (200, 206)
        assert c.app_state.ultima_midia > antes
