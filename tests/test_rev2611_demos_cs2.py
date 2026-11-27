"""Revisão de 26/11/2026, lote A1: G-016 (copiar a demo para a pasta em que ela já está não pode falhar por causa do .dem.info)."""
from __future__ import annotations

from cs2clip import demos_cs2


def _demo_com_info(pasta):
    dem = pasta / "match730_1.dem"
    dem.write_bytes(b"PBDEMS2\x00" + b"x" * 100)
    info = pasta / "match730_1.dem.info"
    info.write_text('{"matchtime": 1700000000}', encoding="utf-8")
    return dem, info


def test_g016_demo_ja_na_pasta_de_destino_com_info_nao_levanta_erro(tmp_path):
    dem, info = _demo_com_info(tmp_path)
    assert demos_cs2.copiar_para_projeto(dem, destino=tmp_path) == dem
    assert info.read_text(encoding="utf-8") == '{"matchtime": 1700000000}'           # o .info continua intacto


def test_g016_copia_para_outra_pasta_leva_o_info_junto(tmp_path):
    origem, destino = tmp_path / "origem", tmp_path / "destino"
    origem.mkdir()
    dem, _ = _demo_com_info(origem)
    copia = demos_cs2.copiar_para_projeto(dem, destino=destino)
    assert copia == destino / dem.name and copia.is_file()
    assert (destino / "match730_1.dem.info").read_text(encoding="utf-8") == '{"matchtime": 1700000000}'
