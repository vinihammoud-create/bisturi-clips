"""Teste de `contexto_de_round` (cs2clip/emotion.py), lógica pura."""
from cs2clip.emotion import (
    CONTEXTO_ALEGRIA,
    CONTEXTO_INDEFINIDO,
    CONTEXTO_RAIVA,
    contexto_de_round,
)
from cs2clip.rounds import RoundTimelineEntry


def _rounds():
    return [
        RoundTimelineEntry(100.0, "derrota", 0.98),
        RoundTimelineEntry(250.0, "vitoria", 0.97),
        RoundTimelineEntry(400.0, "derrota", 0.96),
    ]


def test_momento_antes_de_round_perdido_e_raiva():
    assert contexto_de_round(80.0, _rounds()) == CONTEXTO_RAIVA


def test_momento_antes_de_round_ganho_e_alegria():
    assert contexto_de_round(230.0, _rounds()) == CONTEXTO_ALEGRIA


def test_momento_longe_de_qualquer_round_e_indefinido():
    assert contexto_de_round(1000.0, _rounds()) == CONTEXTO_INDEFINIDO


def test_janela_limita_a_distancia():
    # 95s antes do round em 100.0, janela padrão é 90 -> fica indefinido
    assert contexto_de_round(5.0, _rounds(), janela=90.0) == CONTEXTO_INDEFINIDO
    # com janela maior, já entra
    assert contexto_de_round(5.0, _rounds(), janela=100.0) == CONTEXTO_RAIVA


def test_sem_rounds_e_sempre_indefinido():
    assert contexto_de_round(50.0, []) == CONTEXTO_INDEFINIDO


# ── E2-C: classificar / anotar ────────────────────────────────────────────────────────────

import numpy as np  # noqa: E402

from cs2clip.emotion import SEQUENCIA_MIN, anotar, classificar  # noqa: E402
from cs2clip.moments import Event, Moment  # noqa: E402
from cs2clip.partes import dividir_partes  # noqa: E402
from cs2clip.transcribe import Word  # noqa: E402

R = RoundTimelineEntry


def _voz(picos: dict[float, float], duracao: float = 100.0) -> np.ndarray:
    v = np.zeros(int(duracao / 0.1) + 1)
    for t, valor in picos.items():
        v[int(t / 0.1)] = valor
    return v


def _risadas(*tempos: float) -> list[Word]:
    return [Word(t, t + 0.4, "kkkk") for t in tempos]


def test_voz_baixa_nao_tem_emocao():
    assert classificar(40, 50, _voz({45: 0.3}), [], [], None) is None


def _fala(t: float, texto: str) -> list[Word]:
    return [Word(t + 0.3 * i, t + 0.3 * i + 0.25, w) for i, w in enumerate(texto.split())]


def test_voz_alta_sozinha_nao_e_emocao():
    """Gabarito 17/10: grito sem palavra era tática ("bora rushar, confia") ou callout ("meio, meio"), não raiva."""
    assert classificar(40, 50, _voz({45: 0.9}), _fala(44, "só rush vai vai vai"), [], "derrota") is None
    assert classificar(40, 50, _voz({45: 0.9}), [], [], "vitoria") is None             # conversa alta não é HYPE


def test_palavra_de_frustracao_sem_abate_e_raiva_e_alta_com_round_perdido():
    e = classificar(40, 50, _voz({45: 0.9}), _fala(44, "nossa quase velho merda hein"), [], "derrota")
    assert e.categoria == "RAIVA" and e.confianca == "alta" and "round perdido" in e.motivos
    e = classificar(40, 50, _voz({45: 0.2}), _fala(44, "que merda"), [], None)
    assert e.categoria == "RAIVA" and e.confianca == "media"
    # "porra"/"caralho" servem para comemorar também: sem morte nem round perdido, não viram raiva
    assert classificar(40, 50, _voz({45: 0.2}), _fala(44, "porra"), [], None) is None
    assert classificar(40, 50, _voz({45: 0.9}), _fala(44, "puta que pariu que tiro"), [], "derrota").categoria == "HYPE"
    assert classificar(40, 50, _voz({45: 1.0}), _fala(44, "manito caralho meu"), [], "vitoria") is None   # conversa


def test_morte_antes_com_palavrao_e_raiva_alta_e_callout_depois_da_morte_nao_e():
    e = classificar(40, 50, _voz({45: 0.9}), _fala(44, "que merda"), [], None, mortes=[41.0])
    assert e.categoria == "RAIVA" and e.confianca == "alta" and "você tinha morrido" in e.motivos
    assert classificar(40, 50, _voz({45: 1.0}), _fala(44, "meio meio ali ali"), [], None, mortes=[41.0]) is None


def test_fala_forte_sem_abate_com_voz_alta_e_hype_medio():
    e = classificar(40, 50, _voz({45: 0.9}), _fala(44, "que tiro"), [], "vitoria")
    assert e.categoria == "HYPE" and e.confianca == "media"


def test_voz_alta_logo_depois_de_abate_e_hype_e_com_round_ganho_e_alta():
    assert classificar(40, 50, _voz({45: 0.9}), [], [43.0], None).categoria == "HYPE"
    e = classificar(40, 50, _voz({45: 0.9}), [], [43.0], "vitoria")
    assert e.categoria == "HYPE" and e.confianca == "alta"


def test_abate_antigo_demais_nao_conta_como_comemoracao():
    # abate 8 s antes do pico (e fora do trecho): o palavrão é frustração, não comemoração
    e = classificar(38, 50, _voz({45: 0.9}), _fala(44, "merda"), [37.0], "derrota")
    assert e.categoria == "RAIVA"


def test_duas_risadas_viram_risada_e_ganham_de_raiva():
    e = classificar(40, 50, _voz({45: 0.9}), _risadas(44, 46), [], "derrota")
    assert e.categoria == "RISADA" and e.confianca == "alta"
    e = classificar(40, 50, _voz({45: 0.2}), _risadas(44, 46), [], None)
    assert e.categoria == "RISADA" and e.confianca == "media"


def test_uma_risada_so_nao_basta():
    assert classificar(40, 50, _voz({45: 0.2}), _risadas(44), [], None) is None


def test_risada_fora_do_trecho_nao_conta():
    assert classificar(40, 50, _voz({45: 0.2}), _risadas(10, 12), [], None) is None


def _partes_demo():
    rounds = [R(300.0, "vitoria", 0.9), R(400.0, "vitoria", 0.9), R(500.0, "derrota", 0.9), R(600.0, "vitoria", 0.9)]
    return dividir_partes(rounds, [55.0, 58.0, 62.0], 800.0)


def test_anotar_reacao_na_partida_ganha_parte_round_e_rotulo_de_emocao():
    partes = _partes_demo()
    m = Moment(440.0, 452.0, "reacao", label="REAÇÃO")        # foco ~447,8 s: termina no banner de 500 s (derrota, round 3)
    anotar([m], partes, _voz({446.0: 0.9}, 800.0), _fala(445.0, "que merda velho"), [])
    assert m.parte_tipo == "partida" and m.round_n == 3 and m.round_resultado == "derrota"
    assert m.emocao == "RAIVA" and m.confianca == "alta" and m.label == "RAIVA"


def test_anotar_abate_no_deathmatch_vira_sequencia():
    partes = _partes_demo()
    kills = [Event(t, "kill") for t in (55.0, 58.0, 62.0)]
    m = Moment(51.0, 64.5, "abate", kills=kills, label="TRIPLE KILL")
    anotar([m], partes, _voz({}, 800.0), [], [55.0, 58.0, 62.0])
    assert m.parte_tipo == "deathmatch" and m.label == f"SEQUÊNCIA x{len(kills)}"
    assert len(kills) >= SEQUENCIA_MIN


def test_anotar_abate_na_partida_mantem_o_rotulo_do_jogo():
    partes = _partes_demo()
    m = Moment(430.0, 440.0, "abate", kills=[Event(435.0, "kill"), Event(436.0, "kill"), Event(437.0, "kill")],
               label="TRIPLE KILL")
    anotar([m], partes, _voz({}, 800.0), [], [435.0, 436.0, 437.0])
    assert m.parte_tipo == "partida" and m.label == "TRIPLE KILL" and m.emocao is None


def test_anotar_sem_partes_nao_quebra_e_deixa_vazio():
    m = Moment(10.0, 20.0, "reacao", label="REAÇÃO")
    anotar([m], [], _voz({}), [], [])
    assert m.parte == "" and m.round_n is None and m.emocao is None and m.label == "REAÇÃO"


def test_regex_de_risada_pega_kkkk_hahaha_e_rsrs_mas_nao_um_k_solto():
    """Regressão: _plain colapsa "kkkk" em "kk", e a regex antiga (k{3,}) nunca casava."""
    from cs2clip.transcribe import hype_events
    for txt in ("kkkk", "KKKKKKK", "hahaha", "rsrs"):
        assert [t for *_, t in hype_events([Word(1.0, 1.4, txt)])] == ["risada"], txt
    assert hype_events([Word(1.0, 1.4, "K")]) == []
