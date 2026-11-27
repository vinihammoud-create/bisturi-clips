"""R-015 (rev2611, lote E): o token vale também para o GET de /api/. Só o áudio das músicas (tag <audio>, que não manda cabeçalho) fica de fora."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_r1_ui import rodar_js
from test_trabalhos_js_e2 import PREAMBULO
from test_ui_dados_seguranca import PORT, criar_projeto, cliente, token_headers
from ui.servidor import create_app

RAIZ = Path(__file__).resolve().parents[1]
ESTATICOS = RAIZ / "ui" / "static"
GETS_DA_API = ["/api/indice", "/api/entrada", "/api/fila", "/api/config", "/api/gravacao", "/api/progresso", "/api/log", "/api/videos/v1",
               "/api/musicas", "/api/trabalhos", "/api/boas-vindas", "/api/baixar-vod", "/api/partidas-novas"]


def test_get_de_api_sem_token_ou_com_token_errado_e_recusado(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        for rota in GETS_DA_API:
            assert c.get(rota).status_code == 403, rota
            assert c.get(rota, headers={"X-Token": "errado"}).status_code == 403, rota
            assert c.get(rota, headers=token_headers(c)).status_code != 403, rota
        corpo = c.get("/api/config").json()
        assert "erro" in corpo and "Atualize a página" in corpo["erro"]


def test_o_que_nao_e_api_continua_sem_token(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert c.get("/").status_code == 200
        assert c.get("/health").status_code == 200
        assert c.get("/static/app.js").status_code == 200
        assert c.get("/media/v1/cortes/corte.mp4").status_code == 200       # <video> não manda cabeçalho


def test_audio_das_musicas_e_a_unica_excecao_de_get_em_api(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = c.get("/api/musicas/audio/nao-existe.mp3")                       # a tag <audio> não manda cabeçalho: chega na rota (404), não no portão (403)
        assert r.status_code == 404
        assert c.get("/api/musicas/audio/../config").status_code in (403, 404)
        assert c.get("/api/musicas/audio").status_code == 403                # só o prefixo com arquivo; a rota de lista continua protegida
        assert c.get("/api/musicasX").status_code == 403


def test_origem_e_host_continuam_conferidos_antes_do_token(tmp_path):
    criar_projeto(tmp_path)
    app = create_app(root_dir=tmp_path, port=PORT)
    with TestClient(app, base_url="http://evil.example:8765") as c:
        assert c.get("/api/indice", headers={"X-Token": app.state.token}).status_code == 403


def _node_fetch_gravando(arquivo: str, corpo: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node não instalado")
    r = subprocess.run([node, "-e", PREAMBULO + corpo, str(ESTATICOS / arquivo)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


_FETCH_GRAVA = r"""
const chamadas = [];
globalThis.fetch = async (url, op) => { chamadas.push({ url, metodo: op.method, token: op.headers.get("X-Token") });
  return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ faixas: [], licencas: [], itens: [] }) }; };
"""


def test_musicas_js_manda_o_token_ate_no_get():
    saida = _node_fetch_gravando("musicas.js", _FETCH_GRAVA + r"""
(async () => { await window.BisturiMusicas.carregar(); console.log(JSON.stringify(chamadas)); process.exit(0); })();
""")
    assert saida and saida[0]["url"] == "/api/musicas" and saida[0]["metodo"] == "GET" and saida[0]["token"] == "tok"


def test_trabalhos_js_manda_o_token_ate_no_get():
    saida = _node_fetch_gravando("trabalhos.js", _FETCH_GRAVA + r"""
(async () => { await m.partidasNovas(); console.log(JSON.stringify(chamadas)); process.exit(0); })();
""")
    assert saida and saida[0]["url"] == "/api/partidas-novas" and saida[0]["metodo"] == "GET" and saida[0]["token"] == "tok"


def test_app_js_manda_o_token_ate_no_get(tmp_path):
    r = rodar_js(tmp_path, """
      const vistos = [];
      cfg.fetch = async (url, op) => { vistos.push({ url, metodo: op.method, token: op.headers.get("X-Token") });
        return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ itens: [], progresso: {} }) }; };
      await ui.atualizarFila();
      return vistos;
    """)
    assert r and r[0]["url"] == "/api/fila" and r[0]["metodo"] == "GET" and r[0]["token"] is not None


def test_nenhum_js_pede_api_sem_passar_pela_funcao_api():
    """fetch/EventSource direto em /api/: só os que já mandam o X-Token à mão (janela-fechada). O envio por arrastar (XHR) também manda. Áudio é tag <audio>."""
    for nome in ("app.js", "musicas.js", "trabalhos.js"):
        texto = (ESTATICOS / nome).read_text(encoding="utf-8")
        assert "EventSource" not in texto, nome
        for linha in texto.splitlines():
            if "fetch(" in linha and "/api/" in linha and "fetch(caminho," not in linha:
                assert "X-Token" in linha, (nome, linha.strip())
    assert 'xhr.setRequestHeader("X-Token", token)' in (ESTATICOS / "app.js").read_text(encoding="utf-8")


def test_funcao_api_dos_tres_arquivos_nao_condiciona_o_token_ao_metodo():
    for nome in ("app.js", "musicas.js", "trabalhos.js"):
        texto = (ESTATICOS / nome).read_text(encoding="utf-8")
        api = texto.split("async function api(", 1)[1].split("\n  }\n", 1)[0]
        assert 'cabecalhos.set("X-Token", token)' in api, nome
        assert not re.search(r'if\s*\([^)]*(metodo|GET)[^)]*\)\s*cabecalhos\.set\("X-Token"', api), nome


def test_paginas_geradas_nao_chamam_a_api():
    """revisao.html / relatorio.html abrem em /media e não podem depender do token (a CSP delas também corta a rede)."""
    for arq in (RAIZ / "cs2clip").glob("*.py"):
        texto = arq.read_text(encoding="utf-8")
        assert "/api/" not in texto, arq.name
