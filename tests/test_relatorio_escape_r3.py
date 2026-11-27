"""R3 (revisão do Opus, item 6 do auditor): o relatorio.html montava os cartões com `innerHTML` sem escapar e o JSON entrava no <script> sem trocar `</`.
Hoje os campos vêm de nomes de arquivo do Windows e de valores internos, mas a página roda na MESMA origem da interface (que tem o token): se algum campo ganhar texto
livre, vira script com acesso ao programa. Agora tudo que vem de fora passa por E() e o JSON não fecha o <script>."""
import json
import shutil
import subprocess

import pytest

from cs2clip import report

HOSTIL = '<img src=x onerror=alert(1)>'
FECHA = '</script><script>alert(2)</script>'
ASPAS = 'x" onerror="alert(3)'


def _entrada(**sobrescrever):
    e = {"video": "v", "start": "000000.00", "time": "00:01–00:09", "dur": "8", "label": "ACE", "kind": "abate", "score": 90, "reasons": ["ok"], "detail": "5 abates",
         "thumb": "miniaturas/a.jpg", "clip": "cortes/a.mp4", "short": "shorts/a.mp4", "emocao": None, "confianca": "", "emocao_motivos": [], "parte": "", "round_n": 3,
         "contexto": None}
    e.update(sobrescrever)
    return e


def _pagina(tmp_path, entradas):
    destino = tmp_path / "relatorio.html"
    report.write_report(destino, entradas, {"Abates": "5"}, "sub")
    return destino.read_text(encoding="utf-8")


def test_o_json_dos_dados_nunca_fecha_o_script_nem_abre_comentario(tmp_path):
    html = _pagina(tmp_path, [_entrada(label=FECHA, video="<!-- x")])
    dados = html.split("const D=", 1)[1].split(";let F=", 1)[0]
    assert "</" not in dados and "<!--" not in dados
    assert html.count("</script>") == 1                                                                       # só o do próprio relatório
    # CONTROLE: o JSON continua legível para o JavaScript (a barra escapada vale como a barra)
    assert json.loads(dados.replace("<\\/", "</").replace("<\\!--", "<!--"))[0]["label"] == FECHA


def _node(tmp_path, html, corpo):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node não instalado")
    script = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    harness = r"""
const cartoes = [];
globalThis.document = {
  createElement: () => { const e = { className: "", _h: "", querySelector: () => ({}) }; Object.defineProperty(e, "innerHTML", { set(v) { e._h = v; }, get() { return e._h; } }); cartoes.push(e); return e; },
  getElementById: () => ({ appendChild() {}, set innerHTML(v) {} }),
  querySelectorAll: () => [], querySelector: () => ({ classList: { add() {} } })
};
""" + script + corpo
    arq = tmp_path / "pagina.js"
    arq.write_text(harness, encoding="utf-8")
    r = subprocess.run([node, str(arq)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_campo_hostil_aparece_como_texto_e_nunca_vira_tag_ou_atributo(tmp_path):
    html = _pagina(tmp_path, [_entrada(label=HOSTIL, video=FECHA, detail=HOSTIL, thumb=ASPAS, clip=ASPAS, short=ASPAS, kind='abate" onmouseover="alert(4)', emocao='HYPE"><b>',
                                       confianca='x"', round_n='<i>', time=HOSTIL, dur=HOSTIL, parte="ja &amp; escapado")])
    saida = _node(tmp_path, html, "\nconsole.log(JSON.stringify(cartoes.map(c => c._h)));")
    cartao = saida[0]
    for perigo in (HOSTIL, "</script><script>", 'x" onerror="', 'abate" onmouseover="', 'HYPE"><b>', "<i>"):
        assert perigo not in cartao, perigo
    assert "&lt;img src=x onerror=alert(1)&gt;" in cartao and "&quot;" in cartao
    assert "ja &amp; escapado" in cartao                                                                      # o que o Python já escapou (parte, reasons) NÃO é escapado de novo


def test_conteudo_normal_continua_igual_o_controle(tmp_path):
    html = _pagina(tmp_path, [_entrada(emocao="HYPE", confianca="media", emocao_motivos=["gritou"], parte="Partida 1 (13x5)", contexto="round_ganho")])
    cartao = _node(tmp_path, html, "\nconsole.log(JSON.stringify(cartoes.map(c => c._h)));")[0]
    assert 'src="miniaturas/a.jpg"' in cartao and 'href="cortes/a.mp4"' in cartao and 'href="shorts/a.mp4"' in cartao
    assert ">ACE<" in cartao and "5 abates" in cartao and "round ganho" in cartao and 'class="emo HYPE media"' in cartao and "HYPE?" in cartao and "Partida 1 (13x5) · round 3" in cartao
