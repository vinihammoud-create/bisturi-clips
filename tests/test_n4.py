"""N4 (15/11/2026): com a parte EXATA (rounds vindos da demo) `partes.intervalo_do_round` nunca devolve `suspeito=True`.

Por quê: o "intervalo suspeito" (vão grande entre banners) é só uma desconfiança de que um banner não foi lido. Com a demo os
rounds são certos, então todo consumidor concorda com a regra de ouro "nunca dividir os abates do mesmo round" (mirage de
14/11, round 10: abates em 3414, 3467 e 3473 s do VOD). SEM demo nada muda: a trava continua igual (17/10/2026: um TRIPLE + 1
abate do round seguinte viraram "QUADRA KILL" porque o banner entre eles não foi lido).
"""
import pytest

from cs2clip.config import Config
from cs2clip.moments import Event, Moment
from cs2clip.partes import dividir_partes, intervalo_do_round
from cs2clip.rounds import RoundTimelineEntry as R
from cs2clip.sequencia import unir_sequencias

BANNERS_MIRAGE = [3000, 3110, 3220, 3330, 3490, 3600, 3710, 3820]   # o round 10 vai de 3330 a 3490: 160 s (> ROUND_LONGO)
BANNERS_RAZAO = [3000, 3060, 3120, 3180, 3280, 3340, 3400, 3460]    # round típico 60 s; o de 3180 a 3280 tem 100 s (> 1,5 x 60, < 150)
ABATES_MIRAGE = (3414.0, 3467.0, 3473.0)                             # 56:54, 57:47 e 57:53 (AWP)


def _rounds(banners):
    return [R(float(b), "vitoria", 1.0) for b in banners]


def _exata(banners):
    p = dividir_partes(_rounds(banners), [], banners[-1] + 100.0, exatos=[(banners[0] - 100.0, banners[-1] + 50.0)])
    assert [x.exata for x in p if x.tipo == "partida"] == [True]
    return p


def _estimada(banners):
    p = dividir_partes(_rounds(banners), [], banners[-1] + 100.0)
    assert [x.exata for x in p if x.tipo == "partida"] == [False]
    return p


def _jogada(ts):
    """Um corte como o `build_moments` faz: 4 s antes do 1º abate, 2,5 s depois do último."""
    return Moment(max(0.0, ts[0] - 4.0), ts[-1] + 2.5, "abate", kills=[Event(t, "kill", []) for t in ts], label="KILL", score=20.0)


# ── o conserto: parte exata nunca é suspeita ──────────────────────────────────────────────────

@pytest.mark.parametrize("t", ABATES_MIRAGE)
def test_exata_os_3_abates_da_mirage_nao_estao_em_intervalo_suspeito(t):
    assert intervalo_do_round(t, _exata(BANNERS_MIRAGE)) == (3330.0, 3490.0, False)


def test_exata_vao_de_53_s_do_caso_real_continua_no_mesmo_round_sem_suspeita():
    partes = _exata(BANNERS_MIRAGE)
    a, b = intervalo_do_round(3414.0, partes), intervalo_do_round(3467.0, partes)   # 53 s entre o 1º e o 2º abate
    assert 3467.0 - 3414.0 == 53.0 and a == b == (3330.0, 3490.0, False)


def test_exata_suspeita_pela_razao_de_1_5_round_tambem_some():
    """O outro ramo da regra (gap > 1,5 x round típico) também fica falso com a demo."""
    assert intervalo_do_round(3250.0, _exata(BANNERS_RAZAO)) == (3180.0, 3280.0, False)


def test_exata_mantem_os_banners_e_fora_de_partida_continua_none():
    partes = _exata(BANNERS_MIRAGE)
    assert intervalo_do_round(3050.0, partes) == (3000.0, 3110.0, False)
    assert intervalo_do_round(3550.0, partes) == (3490.0, 3600.0, False)
    assert intervalo_do_round(2000.0, partes) is None and intervalo_do_round(4000.0, partes) is None


# ── controle: SEM demo a trava é idêntica à de antes (valores medidos no código de 76bfe25) ──────────

@pytest.mark.parametrize("t, esperado", [
    (3050.0, (3000.0, 3110.0, False)), (3150.0, (3110.0, 3220.0, False)), (3260.0, (3220.0, 3330.0, False)),
    (3414.0, (3330.0, 3490.0, True)), (3467.0, (3330.0, 3490.0, True)), (3473.0, (3330.0, 3490.0, True)),
    (3489.0, (3330.0, 3490.0, True)), (3550.0, (3490.0, 3600.0, False)), (3700.0, (3600.0, 3710.0, False)),
    (2000.0, None), (4000.0, None),
])
def test_controle_sem_demo_o_intervalo_e_o_mesmo_de_antes(t, esperado):
    assert intervalo_do_round(t, _estimada(BANNERS_MIRAGE)) == esperado


@pytest.mark.parametrize("t, esperado", [
    (3030.0, (3000.0, 3060.0, False)), (3250.0, (3180.0, 3280.0, True)), (3300.0, (3280.0, 3340.0, False)),
])
def test_controle_sem_demo_a_razao_de_1_5_round_continua_suspeita(t, esperado):
    assert intervalo_do_round(t, _estimada(BANNERS_RAZAO)) == esperado


# ── o efeito no agrupamento (vale igual com ou sem a trava especial do N2 em `unir_sequencias`) ──────

def test_mirage_antes_e_depois_com_demo_vira_1_triple_e_sem_demo_continua_em_2_videos():
    ms = lambda: [_jogada([ABATES_MIRAGE[0]]), _jogada(list(ABATES_MIRAGE[1:]))]
    [triple] = unir_sequencias(ms(), _exata(BANNERS_MIRAGE), Config().cortes)
    assert [k.t for k in triple.kills] == list(ABATES_MIRAGE) and triple.label == "TRIPLE KILL"
    separados = unir_sequencias(ms(), _estimada(BANNERS_MIRAGE), Config().cortes)
    assert [len(m.kills) for m in separados] == [1, 2]


def test_sem_demo_um_triple_mais_um_abate_do_round_seguinte_nao_vira_quadra():
    """17/10/2026: o banner entre o TRIPLE e o abate seguinte não foi lido; o vão grande separa (não afrouxar)."""
    banners = [3000, 3110, 3220, 3330, 3640, 3750]                      # banner perdido: 3330 a 3640 = 310 s
    ms = [_jogada([3350.0, 3355.0, 3360.0]), _jogada([3420.0])]            # vão de 60 s (> VAO_SUSPEITO), banner do round perdido
    separados = unir_sequencias(ms, _estimada(banners), Config().cortes)
    assert [len(m.kills) for m in separados] == [3, 1]
