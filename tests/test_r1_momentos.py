"""Revisão R1 (17/10/2026), área momentos: round do abate pós-round na nota e na emoção, emoção só no que o vídeo
mostra, "clipa isso" no vão de uma sequência e limite de duração na fusão de repetidos."""
from cs2clip.comando import aplicar_comandos
from cs2clip.emotion import anotar
from cs2clip.moments import Event, Moment
from cs2clip.nota import pontuar
from cs2clip.partes import dividir_partes
from cs2clip.rounds import RoundTimelineEntry as R
from cs2clip.sequencia import remover_repetidos
from cs2clip.transcribe import Word


def partida():
    # banners: 100 vitória (round 1, pistol), 200 derrota, 300 vitória
    return dividir_partes([R(100.0, "vitoria", .9), R(200.0, "derrota", .9), R(300.0, "vitoria", .9)], [], 350.0)


def jogada(ts):
    return Moment(ts[0] - 4.0, ts[-1] + 2.5, "abate", kills=[Event(t, "kill") for t in ts], label="KILL")


def sequencia_com_vao():
    # abates em 110 e 150 do mesmo round: o tempo parado 112,5-146 é cortado no vídeo
    m = Moment(106.0, 152.5, "abate", kills=[Event(110.0, "kill"), Event(150.0, "kill")], label="DOUBLE KILL")
    m.segmentos = [(106.0, 112.5), (146.0, 152.5)]
    return m


# ── achado 8: abate logo depois do banner é do round que acabou ───────────────────────────────

def test_nota_abate_logo_depois_do_banner_do_pistol_ganha_o_bonus():
    m = jogada([101.0])
    pontuar(m, partida())
    assert any("pistol round ganho" in r for r in m.reasons)


def test_anotar_abate_pos_round_fica_no_round_que_acabou():
    partes = partida()
    seq = jogada([190.0, 201.0])           # 190 + 201 (1 s depois do banner de 200): round 2, perdido
    pistol = jogada([101.0])
    anotar([seq, pistol], partes, None, [], [101.0, 190.0, 201.0])
    assert (seq.round_n, seq.round_resultado) == (2, "derrota")
    assert (pistol.round_n, pistol.round_resultado) == (1, "vitoria")


def test_anotar_reacao_sem_abate_nao_usa_o_ajuste():
    m = Moment(198.0, 204.0, "reacao", pico=201.0, label="REAÇÃO")
    anotar([m], partida(), None, [], [])
    assert m.round_n == 3


# ── achado 10: emoção só nas janelas que o vídeo mostra ───────────────────────────────────────

def test_risada_no_vao_cortado_nao_vira_emocao():
    m = sequencia_com_vao()
    risadas = [Word(130.0, 130.4, "kkkkk"), Word(131.0, 131.4, "kkkk")]
    anotar([m], partida(), None, risadas, [110.0, 150.0])
    assert m.emocao is None


def test_risada_dentro_de_um_trecho_continua_valendo():
    m = sequencia_com_vao()
    risadas = [Word(147.0, 147.4, "kkkkk"), Word(148.0, 148.4, "kkkk")]
    anotar([m], partida(), None, risadas, [110.0, 150.0])
    assert m.emocao == "RISADA"


# ── achado 9: "clipa isso" no vão de uma sequência ────────────────────────────────────────────

def test_comando_no_vao_da_sequencia_atende_a_jogada_sem_duplicar():
    m = sequencia_com_vao()
    ms = aplicar_comandos([m], [125.0], 350.0)
    assert len(ms) == 1 and ms[0] is m and m.pedido


# ── achado 12: fusão de repetidos respeita sequencia_max ──────────────────────────────────────

def test_fusao_de_repetidos_respeita_o_maximo():
    a = Moment(10.0, 100.0, "abate", kills=[Event(95.0, "kill")], label="KILL", score=20.0)
    b = Moment(40.0, 130.0, "abate", kills=[Event(125.0, "kill")], label="KILL", score=10.0)
    ficam = remover_repetidos([a, b], maximo=90.0)
    assert len(ficam) == 1
    m = ficam[0]
    assert m.duration <= 90.0 and m.end == 130.0 and [k.t for k in m.kills] == [95.0, 125.0]


def test_fusao_sem_maximo_continua_como_antes():
    a = Moment(10.0, 100.0, "abate", kills=[Event(95.0, "kill")], label="KILL", score=20.0)
    b = Moment(40.0, 130.0, "abate", kills=[Event(125.0, "kill")], label="KILL", score=10.0)
    m, = remover_repetidos([a, b])
    assert (m.start, m.end) == (10.0, 130.0)
