"""Página de revisão (cs2clip/revisao.py): dados embutidos, JavaScript válido e sem `toggle` com valor indefinido."""
import json
import re
import shutil
import subprocess

import pytest

from cs2clip.moments import Event, Moment
from cs2clip.revisao import gerar_revisao, itens_revisao


def _m(idx, **kw):
    m = Moment(10.0 * idx, 10.0 * idx + 8, kw.pop("tipo", "abate"), kills=[Event(10.0 * idx + 4, "kill")], idx=idx,
               label="DOUBLE KILL", score=28.0, **kw)
    return m


def test_so_entram_momentos_com_corte_renderizado():
    ms = [_m(1), _m(2), _m(3)]
    arq = {1: {"corte": "cortes/01.mp4", "miniatura": "miniaturas/01.jpg"}, 2: {"corte": None}, 3: None}
    it = itens_revisao(ms, arq)
    assert [x["n"] for x in it] == [1]
    assert it[0]["corte"] == "cortes/01.mp4" and it[0]["tempo"] == "00:14" and it[0]["rotulo"] == "DOUBLE KILL"


def test_item_traz_emocao_round_e_motivos():
    m = _m(1)
    m.emocao, m.confianca, m.emocao_motivos = "RAIVA", "alta", ["voz alta (0.81)", "round perdido"]
    m.round_n, m.round_resultado, m.parte = 7, "derrota", "Partida 1 (11x11)"
    x = itens_revisao([m], {1: {"corte": "cortes/01.mp4"}})[0]
    assert x["emocao"] == "RAIVA" and x["confianca"] == "alta" and x["round"] == 7
    assert x["round_resultado"] == "derrota" and "round perdido" in x["motivos"]


def test_pagina_embute_os_dados_e_nao_quebra_com_tag_no_texto(tmp_path):
    m = _m(1)
    m.parte = "Partida </script><b>1"
    f = gerar_revisao(tmp_path, "2894824156", itens_revisao([m], {1: {"corte": "cortes/01.mp4"}}))
    h = f.read_text(encoding="utf-8")
    assert f.name == "revisao.html" and "2894824156" in h and "cortes/01.mp4" in h
    assert h.count("</script>") == 1                       # a tag dentro do texto foi escapada
    assert "const VIDEO=" in h and "__DATA__" not in h and "__VIDEO__" not in h


def test_javascript_da_pagina_tem_sintaxe_valida(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node indisponível")
    f = gerar_revisao(tmp_path, "v", itens_revisao([_m(1)], {1: {"corte": "cortes/01.mp4"}}))
    js = re.search(r"<script>(.*)</script>", f.read_text(encoding="utf-8"), re.S).group(1)
    arq = tmp_path / "pagina.js"
    arq.write_text(js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(arq)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_feito_devolve_booleano_para_o_toggle_nao_inverter(tmp_path):
    """Regressão: classList.toggle('feito', undefined) INVERTE em vez de forçar falso (cartão aparecia como
    revisado antes de qualquer resposta)."""
    f = gerar_revisao(tmp_path, "v", [])
    assert "return !!(" in f.read_text(encoding="utf-8")


def test_csv_exportado_usa_o_formato_do_gabarito(tmp_path):
    h = gerar_revisao(tmp_path, "v", []).read_text(encoding="utf-8")
    assert "['vod_id','tempo','categoria','observacao'" in h and "gabarito_momentos.csv" in h
    json.dumps([])                                           # (só garante o import usado)
