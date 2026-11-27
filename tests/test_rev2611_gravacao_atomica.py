"""Revisão de 26/11/2026, lote A1: G-012 (partes.json e demo.json gravados pelo `gravar_atomico`; `carregar_partes` tolera arquivo estragado)."""
from __future__ import annotations

import json

import pytest

from cs2clip import demo, partes, tools
from cs2clip.config import Config
from cs2clip.demo import Abate, PartidaDemo, RoundDemo
from cs2clip.rounds import RoundTimelineEntry


def _uma_partida():
    rounds = [RoundTimelineEntry(80.0, "vitoria", 0.9), RoundTimelineEntry(160.0, "derrota", 0.9),
              RoundTimelineEntry(240.0, "vitoria", 0.9)]
    return partes.dividir_partes(rounds, [10.0, 20.0, 30.0], 260.0, exatos=[(0.0, 250.0)])


def _troca_que_falha(monkeypatch):
    def falha(tmp, destino):
        raise OSError("queda no meio da troca")
    monkeypatch.setattr(tools, "substituir_arquivo", falha)


def test_g012_partes_json_antigo_fica_inteiro_se_a_gravacao_for_interrompida(tmp_path, monkeypatch):
    partes.gravar_partes(tmp_path, _uma_partida(), "v.mp4", 260.0)
    antes = (tmp_path / "partes.json").read_text(encoding="utf-8")
    _troca_que_falha(monkeypatch)
    with pytest.raises(OSError):
        partes.gravar_partes(tmp_path, _uma_partida()[:1], "v.mp4", 260.0)
    assert (tmp_path / "partes.json").read_text(encoding="utf-8") == antes                 # o arquivo antigo continua válido
    assert len(partes.carregar_partes(tmp_path)) == len(_uma_partida())


def test_g012_partes_json_gravado_normal_continua_igual(tmp_path):
    partes.gravar_partes(tmp_path, _uma_partida(), "v.mp4", 260.0)
    assert not (tmp_path / "partes.json.tmp").exists() and (tmp_path / "partes.txt").is_file()
    voltou = partes.carregar_partes(tmp_path)
    assert [(p.tipo, p.inicio, p.fim, p.exata) for p in voltou] == [(p.tipo, p.inicio, p.fim, p.exata) for p in _uma_partida()]


def test_g012_carregar_partes_tolera_arquivo_truncado(tmp_path):
    partes.gravar_partes(tmp_path, _uma_partida(), "v.mp4", 260.0)
    txt = (tmp_path / "partes.json").read_text(encoding="utf-8")
    (tmp_path / "partes.json").write_text(txt[: len(txt) // 2], encoding="utf-8")
    assert partes.carregar_partes(tmp_path) == []


def test_g012_carregar_partes_tolera_json_de_formato_errado(tmp_path):
    for conteudo in (json.dumps([{"tipo": "partida"}]), "{}", "[1, 2]", "", '[{"tipo": "partida", "rounds": 5}]'):
        (tmp_path / "partes.json").write_text(conteudo, encoding="utf-8")
        assert partes.carregar_partes(tmp_path) == []


def _partida_da_demo():
    return PartidaDemo("de_x", {1: "Eu"}, 1, "Eu", [RoundDemo(1, 0.0, 100.0, "T")], [Abate(10.0, 1, "v", "ak47")], [], [], (1, 0), {1: "T"})


def test_g012_demo_json_e_atomico(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "ler_demo", lambda caminho, quem=None: _partida_da_demo())
    _, _, pasta = demo.analisar_demo(tmp_path / "x.dem", Config(), tmp_path / "saida", jogador="Eu")
    bom = (pasta / "demo.json").read_text(encoding="utf-8")
    assert json.loads(bom)["mapa"] == "de_x" and not (pasta / "demo.json.tmp").exists()
    _troca_que_falha(monkeypatch)
    with pytest.raises(OSError):
        demo.analisar_demo(tmp_path / "x.dem", Config(), tmp_path / "saida", jogador="Eu")
    assert (pasta / "demo.json").read_text(encoding="utf-8") == bom                          # o antigo continua inteiro
