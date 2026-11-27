"""E7.1 / E7.2 (Plano E): a lógica da tela Músicas (musicas.js) no Node com DOM de mentira, mais a estrutura da página."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_trabalhos_js_e2 import PREAMBULO

RAIZ = Path(__file__).resolve().parents[1]
JS = RAIZ / "ui" / "static" / "musicas.js"
HTML = RAIZ / "ui" / "static" / "index.html"
APP = RAIZ / "ui" / "static" / "app.js"


def _node(corpo: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node não instalado")
    r = subprocess.run([node, "-e", PREAMBULO + corpo, str(JS)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_linha_da_faixa_mostra_licenca_drop_credito_e_so_deixa_ouvir_se_o_arquivo_existe():
    saida = _node(r"""
const base = { arquivo: "a.mp3", titulo: "Victory", autor: "Komiku", licenca: "CC0 1.0 (domínio público)", bpm: 100.1, drop_s: 11.5, duracao_s: 235, emocao: 0.865,
  tem_drop: true, exige_credito: false, credito: "", aviso: "Licença CC0 conforme o repositório.", existe: true, ativa: true };
const ok = m.linhaDaFaixa(base);
const ncs = m.linhaDaFaixa(Object.assign({}, base, { exige_credito: true, aviso: "Crédito obrigatório na descrição.", tem_drop: false, drop_s: null }));
const sumiu = m.linhaDaFaixa(Object.assign({}, base, { existe: false, ativa: false, licenca: "" }));
console.log(JSON.stringify({ ok: ok.textContent, okBotoes: botoes(ok), okClasse: ok.className, ncs: ncs.textContent, sumiu: sumiu.textContent,
  sumiuBotoes: botoes(sumiu), sumiuDesabilitado: sumiu.todos(n => n.tag === "button").map(b => !!b.disabled), sumiuClasse: sumiu.className, tempos: [m.segundos(null), m.segundos(11.5), m.segundos(235), m.segundos(60), m.segundos(119.6), m.segundos(0.2)] }));
""")
    assert "Victory" in saida["ok"] and "drop em 0:12" in saida["ok"] and "100 BPM" in saida["ok"] and "emoção 0.86" in saida["ok"]
    assert saida["okBotoes"] == ["▶ Ouvir o drop"] and "Licença CC0 conforme" not in saida["ok"]              # o aviso de rotina não polui
    assert "pede crédito" in saida["ncs"] and "Crédito obrigatório na descrição." in saida["ncs"] and "sem drop" in saida["ncs"]
    assert "arquivo sumiu" in saida["sumiu"] and "SEM LICENÇA" in saida["sumiu"] and saida["sumiuBotoes"] == ["▶ Ouvir o drop"] and saida["sumiuDesabilitado"] == [True] and "musica-off" in saida["sumiuClasse"]
    assert saida["tempos"] == ["–", "0:12", "3:55", "1:00", "2:00", "0:00"]


def test_html_tem_a_tela_musicas_o_formulario_e_o_menu_aponta_para_ela():
    html, app = HTML.read_text(encoding="utf-8"), APP.read_text(encoding="utf-8")
    for ident in ("view-musicas", "nav-musicas", "musicas-lista", "musicas-resumo", "musicas-player", "musicas-player-caixa", "musicas-tocando", "musica-escolher",
                  "musica-arquivo", "musica-licenca", "musica-autor", "musica-titulo", "musica-link", "musica-link-rotulo", "musica-credito-linha",
                  "musica-pede-credito", "musica-credito-texto-linha", "musica-credito-texto", "musica-licenca-dica", "musica-adicionar", "musica-resultado"):
        assert f'id="{ident}"' in html, ident
    assert "/static/musicas.js" in html and 'data-view="musicas"' in html
    assert '"musicas"' in app.split("const TELAS", 1)[1].splitlines()[0] and "BisturiMusicas" in app
    assert {v for v in re.findall(r'class="nav-item[^"]*"[^>]*data-view="([a-z]+)"', html)} <= set(re.findall(r'id="view-([a-z]+)"', html))
    assert 'id="musica-adicionar" type="button" disabled' in html                                              # o botão só liga depois de escolher arquivo e licença
