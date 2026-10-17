"""Nota explicável 0-99 e faixa (cs2clip/nota.py)."""
from cs2clip.moments import Event, Moment
from cs2clip.nota import DESTAQUE, PADRAO, faixa, pontuar
from cs2clip.partes import dividir_partes
from cs2clip.rounds import RoundTimelineEntry as R


def partida(resultados):
    """Partida com um banner a cada 100 s (round n termina em 100*n)."""
    return dividir_partes([R(100.0 * (i + 1), r, .9) for i, r in enumerate(resultados)], [], 100.0 * len(resultados) + 50)


def jogada(ts, hs=(), voz=0.0):
    m = Moment(ts[0] - 4, ts[-1] + 2.5, "abate", kills=[Event(t, "kill", ["headshot"] if t in hs else []) for t in ts])
    m.voice = voz
    return m


def test_faixas():
    assert faixa(DESTAQUE) == "DESTAQUE" and faixa(PADRAO) == "PADRAO" and faixa(PADRAO - 1) == "RELATORIO"


def test_mais_abates_vale_mais_e_ace_rapido_e_destaque():
    partes = partida(["derrota"] * 5)
    um, tres, ace = jogada([150.0]), jogada([150.0, 160.0, 170.0]), jogada([150.0, 151.0, 152.0, 153.0, 154.0])
    for m in (um, tres, ace):
        pontuar(m, partes)
    assert um.score < tres.score < ace.score and ace.faixa == "DESTAQUE" and um.faixa == "RELATORIO"
    assert any("ACE" in r for r in ace.reasons) and any("5 abates em" in r for r in ace.reasons)


def test_cada_motivo_mostra_os_pontos_e_a_nota_nunca_passa_de_99():
    partes = partida(["derrota"] * 5)
    m = jogada([150.0, 150.5, 151.0, 151.5, 152.0], hs=(150.0, 150.5, 151.0, 151.5, 152.0), voz=0.9)
    m.chat, m.talk, m.emocao, m.confianca = 0.9, 0.9, "HYPE", "alta"
    pontuar(m, partes)
    assert m.score == 99 and all("(+" in r or "limitada" in r for r in m.reasons)


def test_pistol_e_match_point_pelo_placar_lido():
    # 13 rounds: os 12 primeiros ganhos -> o 13º é match point; o 1º é pistol
    partes = partida(["vitoria"] * 13)
    pistol, mp = jogada([60.0]), jogada([1260.0])
    pontuar(pistol, partes)
    pontuar(mp, partes)
    assert any("pistol" in r for r in pistol.reasons) and any("match point" in r for r in mp.reasons)


def test_reacao_sem_abate_usa_a_voz():
    partes = partida(["derrota"] * 3)
    fraca, forte = Moment(140, 150, "reacao", pico=145), Moment(140, 150, "reacao", pico=145)
    fraca.voice, forte.voice = 0.3, 1.0
    pontuar(fraca, partes)
    pontuar(forte, partes)
    assert fraca.score < forte.score
