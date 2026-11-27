"""Revisão de 26/11/2026, lote A1: G-018 (a folga nunca passa do espaço que existe: o corte não começa antes de 0)."""
from __future__ import annotations

import pytest

from cs2clip import sequencia
from cs2clip.config import Cortes
from cs2clip.moments import Event, Moment


def test_g018_primeiro_abate_colado_no_inicio_nao_gera_start_negativo():
    m = Moment(0.0, 50.0, "abate", kills=[Event(0.2, "kill"), Event(40.0, "kill")], label="DOUBLE KILL",
               segmentos=[(0.0, 6.2), (34.0, 50.0)])
    sequencia._limitar(m, 10.0, sequencia.regra_do_corte(Cortes()))
    assert m.start >= 0.0 and all(a >= 0.0 for a, _ in m.janelas)
    assert any(a <= 0.2 <= b for a, b in m.janelas) and any(a <= 40.0 <= b for a, b in m.janelas)   # os dois abates ficam (regra de ouro)


def test_g018_folga_nunca_passa_do_espaco_disponivel():
    assert sequencia._folga(0.2, 1.0) == pytest.approx(0.2)
    assert sequencia._folga(0.2, 0.0) == pytest.approx(0.2)
    assert sequencia._folga(0.0, 0.5) == 0.0


def test_g018_folga_normal_nao_muda():
    assert sequencia._folga(6.0, 1.0) == 6.0
    assert sequencia._folga(6.0, 0.5) == 3.0
    assert sequencia._folga(6.0, 0.0) == sequencia.MARGEM_MIN      # o mínimo de 0,5 s continua valendo quando há espaço
