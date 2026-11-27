"""Revisão de 26/11/2026 (lote B): o servidor e os dados da Biblioteca. R-009 (sobras do "Apagar kit"), R-008 (importar `ui.servidor` não liga
nada), R-010 (rotas que leem disco fora do laço de eventos). Pastas temporárias; nada da pasta do projeto é tocado."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, cast

import pytest

from test_ui_dados_seguranca import criar_projeto
from ui.dados import DadosProjeto


# ── R-009 ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_r009_sobra_de_um_apagar_que_falhou_no_meio_e_limpa_na_proxima_abertura(tmp_path):
    criar_projeto(tmp_path)
    resto = tmp_path / "saida" / ".apagando-v9-1234-1700000000"
    (resto / "cortes").mkdir(parents=True)
    (resto / "cortes" / "corte.mp4").write_bytes(b"x" * 1000)
    DadosProjeto(tmp_path)
    assert not resto.exists()
    assert (tmp_path / "saida" / "v1").is_dir()                          # o resto da Biblioteca não é tocado


def test_r009_apagar_avisa_quando_sobrou_algo_preso_e_a_proxima_vez_limpa(tmp_path, monkeypatch):
    import ui.dados as dados_mod
    criar_projeto(tmp_path)
    d = DadosProjeto(tmp_path)
    real = dados_mod.shutil.rmtree
    monkeypatch.setattr(dados_mod.shutil, "rmtree", lambda *a, **kw: None)   # um arquivo preso (antivírus, player): nada sai
    r = d.apagar_kit("v1")
    assert r["apagado"] == "v1" and r["sobrou"] is True
    assert [p.name for p in (tmp_path / "saida").iterdir() if p.name.startswith(".apagando-")]
    monkeypatch.setattr(dados_mod.shutil, "rmtree", real)
    DadosProjeto(tmp_path)
    assert not [p for p in (tmp_path / "saida").iterdir() if p.name.startswith(".apagando-")]


def test_r009_controle_apagar_normal_nao_avisa(tmp_path):
    criar_projeto(tmp_path)
    r = DadosProjeto(tmp_path).apagar_kit("v1")
    assert r["sobrou"] is False and not (tmp_path / "saida" / "v1").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="junção de pasta é do Windows")
def test_r009_a_limpeza_nao_entra_em_juncao_nem_link(tmp_path):
    import subprocess
    criar_projeto(tmp_path)
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "importante.txt").write_text("não apagar", encoding="utf-8")
    juncao = tmp_path / "saida" / ".apagando-x-1-1"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(juncao), str(fora)], capture_output=True, check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        DadosProjeto(tmp_path)
        assert (fora / "importante.txt").read_text(encoding="utf-8") == "não apagar"
    finally:
        if juncao.exists() or os.path.lexists(juncao):
            os.rmdir(juncao)                                             # tira só a junção (não o conteúdo)


# ── R-008: importar `ui.servidor` (ou `ui.__main__`) não cria pastas nem liga a fila ─────────────────────────────

RAIZ = Path(__file__).resolve().parents[1]


def test_r008_importar_o_servidor_nao_liga_a_fila_nem_cria_o_app():
    codigo = ("import threading, ui.servidor, ui.__main__; "
              "print(any(t.name == 'cs2clip-ui-fila' for t in threading.enumerate()), hasattr(ui.servidor, 'app'))")
    r = subprocess.run([sys.executable, "-c", codigo], cwd=str(RAIZ), capture_output=True, text=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["False", "False"]


def test_r008_main_cria_o_app_ja_com_a_porta_escolhida(tmp_path, monkeypatch):
    import types

    import ui.__main__ as ui_main
    from cs2clip import caminhos
    from ui.servidor import create_app
    criados, rodados, pedidos = [], [], []

    def criar(**kw):
        pedidos.append(dict(kw))
        app = create_app(root_dir=tmp_path, **{**kw, "dados_dir": tmp_path})   # nunca a pasta do projeto (P2: nem como pasta de dados)
        criados.append(app)
        return app

    class Servidor:
        def __init__(self, config):
            self.config, self.should_exit, self.started = config, False, False

        def run(self):
            rodados.append(self.config)
    monkeypatch.setattr(ui_main, "create_app", criar)
    monkeypatch.setattr(ui_main, "uvicorn", types.SimpleNamespace(Config=lambda app, **kw: types.SimpleNamespace(app=app, **kw), Server=Servidor))
    monkeypatch.setattr(sys, "argv", ["ui", "--sem-navegador", "--port", "8770"])
    try:
        assert ui_main.main() == 0
        assert len(criados) == 1 and rodados[0].app is criados[0]
        assert criados[0].state.port == 8770 and rodados[0].port == 8770
        assert pedidos[0].get("dados_dir") == caminhos.PASTA_DADOS       # P2: o main() abre o app com a pasta de dados do programa
    finally:
        for app in criados:
            app.state.fila.encerrar()


# ── R-010: rota que lê disco não trava as outras (o /api/fila a cada 1 s, o envio) ───────────────────────────────

@pytest.mark.parametrize("rota, objeto, metodo, corpo", [
    ("/api/indice", "dados", "indice", None),
    ("/api/videos/v1", "dados", "momentos", None),
    ("/api/config", "configuracao", "ler", None),
    ("/api/config", "configuracao", "salvar", {"alteracoes": {}}),
    ("/api/config/padroes", "configuracao", "voltar_padrao", {"chaves": []}),
])
def test_r010_rota_lenta_no_disco_nao_trava_o_resto(tmp_path, rota, objeto, metodo, corpo):
    from test_ui_dados_seguranca import cliente
    criar_projeto(tmp_path)
    entrou, solta = threading.Event(), threading.Event()
    with cliente(tmp_path, gravacao_replays=lambda: None) as c:
        alvo = getattr(c.app_state, objeto)
        real = getattr(alvo, metodo)

        def lento(*a, **kw):                                              # 120 kits sem miniatura, antivírus, disco externo...
            entrou.set()
            solta.wait(10)
            return real(*a, **kw)
        setattr(alvo, metodo, lento)
        h = {"X-Token": cast(Any, c.app_state).token}
        pedido = (lambda: c.get(rota, headers=h)) if corpo is None else (lambda: c.post(rota, json=corpo, headers=h))
        fio = threading.Thread(target=pedido, daemon=True)
        fio.start()
        try:
            assert entrou.wait(10)
            respondeu = threading.Event()
            threading.Thread(target=lambda: (c.get("/health"), respondeu.set()), daemon=True).start()
            assert respondeu.wait(5), f"{rota} travou o laço de eventos"
        finally:
            solta.set()
            fio.join(10)


class _ArquivoVigiado:
    def __init__(self, real, threads):
        self._real, self._threads = real, threads

    def write(self, dados):
        self._threads.append(threading.get_ident())
        return self._real.write(dados)

    def __getattr__(self, nome):
        return getattr(self._real, nome)


def test_r010_envio_grava_os_blocos_e_confere_o_disco_fora_do_laco(tmp_path, monkeypatch):
    import ui.envio as envio
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    threads_disco, threads_escrita = [], []
    real_uso, real_fdopen = envio.shutil.disk_usage, os.fdopen
    monkeypatch.setattr(envio.shutil, "disk_usage", lambda p: (threads_disco.append(threading.get_ident()), real_uso(p))[1])
    monkeypatch.setattr(os, "fdopen", lambda *a, **kw: _ArquivoVigiado(real_fdopen(*a, **kw), threads_escrita))
    monkeypatch.setattr(envio, "CONFERIR_ESPACO_A_CADA", 1024 * 1024)
    total = 3 * 1024 * 1024 + 10

    async def fluxo():
        enviado = 0
        while enviado < total:
            n = min(64 * 1024, total - enviado)
            enviado += n
            yield b"x" * n

    async def principal():
        laco = threading.get_ident()
        destino, n = await envio.receber(fluxo(), entrada, "partida.mp4", total, 10 * 1024 ** 3, folga=0)
        return laco, destino, n
    laco, destino, n = asyncio.run(principal())
    assert n == total and destino.stat().st_size == total
    assert threads_disco and threads_escrita
    assert laco not in threads_disco and laco not in threads_escrita
