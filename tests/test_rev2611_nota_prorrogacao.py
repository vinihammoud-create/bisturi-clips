"""Revisão de 26/11/2026, lote A1: M-004 (match point e empate na prorrogação: MR3 depois de 12x12)."""
from __future__ import annotations

import re

import pytest

from cs2clip.moments import Event, Moment
from cs2clip.nota import pontuar
from cs2clip.partes import dividir_partes
from cs2clip.rounds import RoundTimelineEntry as R


def _bonus(g: int, d: int) -> list[str]:
    """Motivos de placar de uma jogada feita no round que começa com o placar g x d (g vitórias e d derrotas lidas antes dele)."""
    resultados = ["vitoria"] * g + ["derrota"] * d + ["vitoria"]
    partes = dividir_partes([R(100.0 * (i + 1), r, .9) for i, r in enumerate(resultados)], [], 100.0 * len(resultados) + 50)
    t = 100.0 * (g + d) + 50.0
    m = Moment(t - 4, t + 2.5, "abate", kills=[Event(t, "kill")])
    pontuar(m, partes)
    return [r for r in m.reasons if r.startswith("match point") or re.match(r"\d+x\d+", r)]


@pytest.mark.parametrize("g,d", [(12, 0), (12, 11), (11, 12), (15, 12), (15, 13), (15, 14), (14, 15), (18, 15), (18, 17), (17, 18), (21, 20)])
def test_m004_match_point_no_tempo_normal_e_nas_prorrogacoes(g, d):
    assert any(r.startswith("match point") for r in _bonus(g, d))


@pytest.mark.parametrize("g,d", [(12, 13), (13, 12), (12, 14), (14, 12), (13, 13), (14, 14), (13, 14), (15, 16), (16, 16), (16, 17), (11, 11), (0, 0)])
def test_m004_sem_match_point_indevido(g, d):
    assert not any(r.startswith("match point") for r in _bonus(g, d))


@pytest.mark.parametrize("g,d,rotulo", [(12, 12, "12x12"), (15, 15, "15x15"), (18, 18, "18x18")])
def test_m004_empate_do_inicio_de_cada_prorrogacao_vale_como_12x12(g, d, rotulo):
    motivos = _bonus(g, d)
    assert any(r.startswith(rotulo) for r in motivos) and not any(r.startswith("match point") for r in motivos)
