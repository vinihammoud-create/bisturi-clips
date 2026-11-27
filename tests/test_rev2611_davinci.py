"""Revisão de 26/11/2026, lote A1: G-011 (marcadores de abate do FCPXML pela posição no corte, com os pulos)."""
from __future__ import annotations

import re

from cs2clip import davinci
from cs2clip.moments import Event, Moment


def _marcadores(txt: str) -> list[tuple[int, str]]:
    return [(int(a) // 6000, v) for a, v in re.findall(r'<marker start="(\d+)/6000s" duration="[^"]+" value="(ABATE|HEADSHOT)"', txt)]


def test_g011_marcador_depois_do_pulo_cai_na_posicao_do_corte(tmp_path):
    corte = tmp_path / "corte.mp4"
    corte.write_bytes(b"fake")
    # dois trechos [10, 18] e [38, 46] (pulo de 20 s): duração do corte = 16 s
    m = Moment(10.0, 46.0, "abate", kills=[Event(14.0, "kill"), Event(42.0, "kill", ["headshot"])], label="DOUBLE KILL",
               segmentos=[(10.0, 18.0), (38.0, 46.0)], idx=1)
    txt = davinci.exportar_fcpxml(tmp_path / "out.fcpxml", [m], [corte], fps=60).read_text(encoding="utf-8")
    # o 1º abate está a 4 s do começo do corte; o 2º, a 8 + 4 = 12 s (e não 15,95 s, esmagado no fim do clipe)
    assert _marcadores(txt) == [(4, "ABATE"), (12, "HEADSHOT")]


def test_g011_sem_pulo_a_posicao_e_a_mesma_de_antes(tmp_path):
    corte = tmp_path / "corte.mp4"
    corte.write_bytes(b"fake")
    m = Moment(10.0, 30.0, "abate", kills=[Event(14.0, "kill"), Event(25.5, "kill")], label="DOUBLE KILL", idx=1)
    txt = davinci.exportar_fcpxml(tmp_path / "out.fcpxml", [m], [corte], fps=60).read_text(encoding="utf-8")
    assert re.findall(r'<marker start="(\d+)/6000s"', txt) == ["24000", "93000"]   # 4,0 s e 15,5 s


def test_g011_posicao_no_corte_com_tres_trechos():
    jan = [(0.0, 5.0), (20.0, 25.0), (40.0, 50.0)]
    assert davinci._posicao_no_corte(2.0, jan) == 2.0
    assert davinci._posicao_no_corte(22.0, jan) == 7.0
    assert davinci._posicao_no_corte(45.0, jan) == 15.0
    assert davinci._posicao_no_corte(-3.0, jan) == 0.0
    assert davinci._posicao_no_corte(99.0, jan) == 20.0
