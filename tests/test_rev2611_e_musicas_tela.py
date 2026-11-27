"""M-007 (rev2611, lote E): sem nenhum arquivo de música na pasta, a aba Músicas diz como ter músicas e não deixa tentar ouvir o que não existe."""
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


def _node(corpo: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node não instalado")
    r = subprocess.run([node, "-e", PREAMBULO + corpo, str(JS)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


_FAIXA = """const faixa = (n, existe) => ({ arquivo: n + ".mp3", titulo: "Faixa " + n, autor: "X", licenca: "CC0 1.0", bpm: 100, drop_s: 10, duracao_s: 120, emocao: 0.5,
  tem_drop: true, exige_credito: false, aviso: "", existe, ativa: existe, entra_no_sorteio: existe, posicao: existe ? 1 : null });"""


def test_aviso_aparece_so_quando_nada_entra_no_sorteio_e_ha_faixa_sem_arquivo():
    r = _node(_FAIXA + """
const todasAusentes = m.avisoSemMusicas({ total: 3, no_sorteio: 0 }, [faixa("a", false), faixa("b", false), faixa("c", false)]);
const algumasTocam = m.avisoSemMusicas({ total: 2, no_sorteio: 1 }, [faixa("a", true), faixa("b", false)]);
const todasPresentesSemSorteio = m.avisoSemMusicas({ total: 1, no_sorteio: 0 }, [faixa("a", true)]);
const catalogoVazio = m.avisoSemMusicas({ total: 0, no_sorteio: 0 }, []);
console.log(JSON.stringify({ todasAusentes, algumasTocam, todasPresentesSemSorteio, catalogoVazio }));
""")
    aviso = r["todasAusentes"]
    assert "tools\\baixar_musicas_cc0.py" in aviso and "licença" in aviso and "3" in aviso and "sorteio" in aviso
    assert r["algumasTocam"] == "" and r["todasPresentesSemSorteio"] == "" and r["catalogoVazio"] == ""


def test_desenhar_mostra_o_aviso_e_esconde_quando_ha_musica():
    r = _node(_FAIXA + r"""
(async () => {
  const resp = corpo => { globalThis.fetch = async () => ({ ok: true, headers: { get: () => "application/json" }, json: async () => corpo }); };
  const aviso = document.querySelector("#musicas-aviso");
  resp({ faixas: [faixa("a", false), faixa("b", false)], licencas: [], resumo: { total: 2, ativas: 0, com_drop: 0, no_sorteio: 0 } });
  await window.BisturiMusicas.carregar();
  const ligado = { texto: aviso.textContent, escondido: aviso.escondido };
  resp({ faixas: [faixa("a", true), faixa("b", false)], licencas: [], resumo: { total: 2, ativas: 1, com_drop: 1, no_sorteio: 1 } });
  await window.BisturiMusicas.carregar();
  console.log(JSON.stringify({ ligado, desligado: { texto: aviso.textContent, escondido: aviso.escondido } })); process.exit(0);
})();
""")
    assert "baixar_musicas_cc0.py" in r["ligado"]["texto"] and r["ligado"]["escondido"] is False
    assert r["desligado"]["escondido"] is True


def test_faixa_ausente_tem_o_botao_desabilitado_com_o_motivo():
    r = _node(_FAIXA + """
const sumiu = m.linhaDaFaixa(faixa("a", false));
const ok = m.linhaDaFaixa(faixa("b", true));
const bt = no => no.todos(n => n.tag === "button").map(b => ({ texto: b.textContent, desabilitado: !!b.disabled, dica: b.title || "", clique: !!b.ouvintes.click }));
console.log(JSON.stringify({ sumiu: bt(sumiu), ok: bt(ok) }));
""")
    assert r["sumiu"] == [{"texto": "▶ Ouvir o drop", "desabilitado": True, "dica": r["sumiu"][0]["dica"], "clique": False}]
    assert "arquivo" in r["sumiu"][0]["dica"].lower()
    assert r["ok"] == [{"texto": "▶ Ouvir o drop", "desabilitado": False, "dica": "", "clique": True}]


def test_html_tem_o_lugar_do_aviso():
    html = HTML.read_text(encoding="utf-8")
    assert 'id="musicas-aviso"' in html
    assert re.search(r'id="musicas-aviso"[^>]*class="[^"]*hidden|class="[^"]*hidden[^"]*"[^>]*id="musicas-aviso"', html)
