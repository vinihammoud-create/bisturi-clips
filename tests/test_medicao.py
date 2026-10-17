"""Medição contra o gabarito (cs2clip/medicao.py, tools/medir_gabarito.py)."""
import json
from pathlib import Path

import pytest

from cs2clip import medicao
from cs2clip.medicao import Linha, ler_gabarito, medir

MOMENTOS = [
    {"n": 1, "foco": 100.0, "inicio": 95.0, "fim": 108.0, "emocao": "HYPE", "abates": [{"t": 100.0}]},
    {"n": 2, "foco": 300.0, "inicio": 290.0, "fim": 306.0, "emocao": "RAIVA", "abates": []},
    {"n": 3, "foco": 500.0, "inicio": 492.0, "fim": 505.0, "emocao": None, "abates": [{"t": 500.0}]},
    {"n": 4, "foco": 700.0, "inicio": 690.0, "fim": 704.0, "emocao": "RAIVA", "abates": []},
]


def L(t, cats, abate=""):
    return Linha("v", t, set(cats), abate)


def test_precisao_e_recall_por_emocao():
    linhas = [L(100, {"alegria"}), L(300, {"raiva"}), L(500, {"risada"}), L(700, {"nenhuma"}), L(310, {"raiva"})]
    rel = medir(linhas, {"v": MOMENTOS})
    r = rel.metricas["raiva"]
    assert (r.vp, r.fp, r.fn) == (1, 1, 1)          # 300 certo; 700 o programa errou; 310 não casou com momento
    assert r.precisao == 0.5 and r.recall == 0.5
    assert (rel.metricas["alegria"].vp, rel.metricas["alegria"].fp) == (1, 0)
    assert rel.metricas["risada"].fn == 1 and rel.metricas["risada"].precisao is None   # ninguém disse risada
    assert rel.sem_momento == 1


def test_varias_emocoes_na_mesma_linha():
    rel = medir([L(100, {"alegria", "risada"})], {"v": MOMENTOS})
    assert rel.metricas["alegria"].vp == 1 and rel.metricas["risada"].fn == 1


def test_acerto_dos_abates_so_conta_momento_com_abate():
    rel = medir([L(100, {"alegria"}, "sim"), L(500, {"nenhuma"}, "nao"), L(300, {"raiva"}, "sim")], {"v": MOMENTOS})
    assert (rel.abate_sim, rel.abate_nao) == (1, 1)    # o momento 300 não tem abate: não entra


def test_casa_pelo_intervalo_e_pelo_foco_truncado():
    assert medicao._casar(103.0, MOMENTOS)["n"] == 1     # dentro de [inicio, fim] (gabarito à mão)
    assert medicao._casar(99.4, MOMENTOS)["n"] == 1
    assert medicao._casar(200.0, MOMENTOS) is None


def test_ler_gabarito_aceita_bom_ponto_e_virgula_e_formatos_de_tempo(tmp_path):
    f = tmp_path / "g.csv"
    f.write_bytes("﻿vod_id;tempo;categoria;observacao\nv;01:40;alegria+risada;ok\nv;1:05:00;Raiva;\nv;125;hype;\n"
                  "v;xx;raiva;\nv;10;coisa;\n".encode("utf-8"))
    linhas, avisos = ler_gabarito(f)
    assert [(ln.t, ln.categorias) for ln in linhas] == [(100.0, {"alegria", "risada"}), (3900.0, {"raiva"}),
                                                        (125.0, {"alegria"})]
    assert len(avisos) == 2


def test_csv_exportado_pela_pagina_de_revisao(tmp_path):
    f = tmp_path / "g.csv"
    f.write_text("vod_id,tempo,categoria,observacao,abate_certo,programa_rotulo,programa_emocao,programa_confianca,"
                 "round_resultado\nv,01:40,alegria,,sim,KILL,HYPE,alta,vitoria\n", encoding="utf-8")
    linhas, _ = ler_gabarito(f)
    assert linhas[0].abate_certo == "sim" and linhas[0].categorias == {"alegria"}


def test_script_roda_ponta_a_ponta(tmp_path, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("medir_gabarito", Path(__file__).parents[1] / "tools" / "medir_gabarito.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "saida" / "v").mkdir(parents=True)
    (tmp_path / "saida" / "v" / "momentos.json").write_text(json.dumps({"momentos": MOMENTOS}), encoding="utf-8")
    csv_ = tmp_path / "g.csv"
    csv_.write_text("vod_id,tempo,categoria,observacao\nv,01:40,alegria,\nv,05:00,raiva,\nout,00:10,raiva,\n", encoding="utf-8")
    assert mod.main([str(csv_), "--saida", str(tmp_path / "saida")]) == 0
    out = capsys.readouterr().out
    assert "alegria" in out and "100%" in out and "não achei" in out          # 'out' não tem momentos.json
    assert mod.main([str(tmp_path / "nao.csv")]) == 1
