"""Revisão de 26/11/2026, lote A1: partes (G-001)."""
from __future__ import annotations

from cs2clip import partes, sequencia
from cs2clip.config import Cortes
from cs2clip.moments import Event, Moment
from cs2clip.rounds import RoundTimelineEntry


def _partida_exata_com_round_longo():
    rounds = [RoundTimelineEntry(80.0, "vitoria", 0.9), RoundTimelineEntry(160.0, "derrota", 0.9),
              RoundTimelineEntry(240.0, "vitoria", 0.9), RoundTimelineEntry(395.0, "vitoria", 0.9)]   # round 4: 155 s
    ps = partes.dividir_partes(rounds, [], 420.0, exatos=[(0.0, 400.0)])
    p = next(x for x in ps if x.tipo == "partida")
    assert p.exata and p.dur_round == 80.0
    return ps


def test_g001_round_longo_da_demo_nao_perde_o_numero_do_inicio():
    ps = _partida_exata_com_round_longo()
    # 255 s está 140 s antes do banner do round 4: acima de 1,4 x 80 s, mas com a demo o banner não se perde
    assert partes.round_de(255.0, ps)[1:] == (4, "vitoria")
    assert partes.round_de(385.0, ps)[1:] == (4, "vitoria")
    assert partes.banner_do_round(255.0, ps) == 395.0


def test_g001_abates_do_round_longo_da_demo_ficam_num_video_so():
    ps = _partida_exata_com_round_longo()
    m1 = Moment(250.0, 260.0, "abate", kills=[Event(255.0, "kill")], label="ABATE")
    m2 = Moment(380.0, 390.0, "abate", kills=[Event(385.0, "kill")], label="ABATE")
    unidos = sequencia.unir_sequencias([m1, m2], ps, Cortes())
    assert len(unidos) == 1 and len(unidos[0].kills) == 2


def test_g001_sem_demo_o_limite_do_banner_perdido_continua():
    rounds = [RoundTimelineEntry(80.0, "vitoria", 0.9), RoundTimelineEntry(160.0, "derrota", 0.9),
              RoundTimelineEntry(240.0, "vitoria", 0.9), RoundTimelineEntry(395.0, "vitoria", 0.9)]
    ps = partes.dividir_partes(rounds, [], 420.0)
    p = next(x for x in ps if x.tipo == "partida")
    assert not p.exata
    t = 395.0 - (partes.JANELA_ROUND * p.dur_round + 5.0)
    assert partes.round_de(t, ps)[1:] == (None, None)
    assert partes.banner_do_round(t, ps) is None
