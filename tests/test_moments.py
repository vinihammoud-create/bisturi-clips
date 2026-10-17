"""Testes de lógica pura da pontuação (cs2clip/moments.py). Sem GPU, sem vídeo:
só eventos sintéticos e as curvas de voz/chat/falas como arrays numpy."""
import numpy as np
import pytest

from cs2clip.config import Config
from cs2clip.moments import BASE, Event, build_moments

HOP = 0.1


def _curva(duration: float) -> np.ndarray:
    """Curva de voz/chat/falas zerada (sem empolgação nenhuma)."""
    return np.zeros(int(duration / HOP) + 1)


@pytest.fixture
def cfg() -> Config:
    return Config()


def test_kill_simples_sem_headshot(cfg):
    events = [Event(t=30.0, kind="kill", icons=[])]
    V = _curva(60.0)
    moments = build_moments(events, V, None, None, [], 60.0, cfg)
    assert len(moments) == 1
    m = moments[0]
    assert m.kind == "abate"
    assert m.label == "KILL"
    assert m.score == pytest.approx(BASE[1])  # 6, sem bônus (sem HS, sem empolgação)


def test_kill_simples_com_headshot_rotulo(cfg):
    events = [Event(t=30.0, kind="kill", icons=["headshot"])]
    V = _curva(60.0)
    m = build_moments(events, V, None, None, [], 60.0, cfg)[0]
    assert m.label == "HEADSHOT"
    assert m.score == pytest.approx(BASE[1] + 3)  # base + bônus de HS


def test_double_kill_todos_headshot(cfg):
    # 2 abates headshot, longe o bastante pra não ganhar o bônus de "sequência rápida"
    events = [
        Event(t=30.0, kind="kill", icons=["headshot"]),
        Event(t=35.0, kind="kill", icons=["headshot"]),
    ]
    V = _curva(60.0)
    m = build_moments(events, V, None, None, [], 60.0, cfg)[0]
    assert m.label == "DOUBLE KILL"
    # base(18) + 2*3(HS) + 10(todos HS) = 34; sem sequência rápida (5s > 2.5*(2-1))
    assert m.score == pytest.approx(BASE[2] + 2 * 3 + 10)


def test_ace_rotulo_e_base(cfg):
    events = [Event(t=30.0 + i, kind="kill", icons=[]) for i in range(5)]
    V = _curva(60.0)
    m = build_moments(events, V, None, None, [], 60.0, cfg)[0]
    assert m.label == "ACE"
    assert BASE[5] == 100
    assert m.score >= BASE[5]  # base + possível bônus de sequência rápida


def test_abates_distantes_viram_momentos_separados(cfg):
    # juntar_intervalo padrão é 8s; 20s de distância não junta
    events = [
        Event(t=30.0, kind="kill", icons=[]),
        Event(t=50.0, kind="kill", icons=[]),
    ]
    V = _curva(80.0)
    moments = build_moments(events, V, None, None, [], 80.0, cfg)
    assert len(moments) == 2
    assert all(m.label == "KILL" for m in moments)


def test_abates_proximos_viram_um_momento_so(cfg):
    events = [
        Event(t=30.0, kind="kill", icons=[]),
        Event(t=34.0, kind="kill", icons=[]),  # 4s < juntar_intervalo (8s)
    ]
    V = _curva(60.0)
    moments = build_moments(events, V, None, None, [], 60.0, cfg)
    assert len(moments) == 1
    assert moments[0].label == "DOUBLE KILL"


def test_sem_empolgacao_nao_aplica_multiplicador_de_concordancia(cfg):
    """Com V/C/T zerados, só o sinal 'tem abate' conta (1 de 4) — não deve
    bater o limiar de 2 sinais fortes que dispara o ×1,2."""
    events = [Event(t=30.0, kind="kill", icons=["headshot"])]
    V = _curva(60.0)
    m = build_moments(events, V, None, None, [], 60.0, cfg)[0]
    assert "concordam" not in " ".join(m.reasons)
    assert m.score == pytest.approx(BASE[1] + 3)


def test_assistencia_nao_vira_momento_proprio(cfg):
    events = [
        Event(t=30.0, kind="kill", icons=[]),
        Event(t=31.0, kind="assist", icons=[]),
    ]
    V = _curva(60.0)
    moments = build_moments(events, V, None, None, [], 60.0, cfg)
    assert len(moments) == 1
    assert len(moments[0].assists) == 1
    assert moments[0].assists[0].kind == "assist"


def test_sequencia_longa_de_abates_e_cortada_no_teto_de_25s(cfg):
    """Deathmatch: abates a cada 7 s encadeiam pelo juntar_intervalo (8 s), mas um grupo
    não pode passar de TETO_GRUPO (25 s): senão vira 1 corte de minutos."""
    events = [Event(t=30.0 + 7 * i, kind="kill", icons=[]) for i in range(5)]  # 30, 37, 44, 51, 58
    V = _curva(100.0)
    moments = build_moments(events, V, None, None, [], 100.0, cfg)
    assert [len(m.kills) for m in moments] == [4, 1]


# ── escolher_shorts ───────────────────────────────────────────────────────────────────────

from cs2clip.moments import Moment, escolher_shorts  # noqa: E402


def _m(idx, score, emocao=None, confianca=""):
    return Moment(idx * 10.0, idx * 10.0 + 8, "abate", score=score, idx=idx, emocao=emocao, confianca=confianca)


def test_shorts_sem_emocao_sao_as_melhores_notas_acima_do_corte():
    ms = [_m(1, 50), _m(2, 30), _m(3, 25), _m(4, 10)]
    assert [m.idx for m in escolher_shorts(ms, nota_short=20, quantidade=2)] == [1, 2]


def test_shorts_reservam_vagas_para_emocao_mesmo_com_nota_abaixo_do_corte():
    ms = [_m(1, 50), _m(2, 45), _m(3, 40), _m(4, 35), _m(5, 22, "RAIVA", "alta"), _m(6, 25, "HYPE", "media")]
    sel = escolher_shorts(ms, nota_short=30, quantidade=4, reserva_emocao=2)
    assert {m.idx for m in sel} == {5, 6, 1, 2}        # 2 vagas de emoção + as 2 melhores notas
    assert [m.idx for m in sel] == [1, 2, 6, 5]        # em ordem de nota


def test_emocao_de_confianca_alta_vem_primeiro_na_reserva():
    ms = [_m(1, 60), _m(2, 26, "HYPE", "media"), _m(3, 22, "RAIVA", "alta"), _m(4, 24, "RISADA", "media")]
    sel = escolher_shorts(ms, nota_short=100, quantidade=3, reserva_emocao=1)
    assert 3 in {m.idx for m in sel}                   # a única vaga reservada é da emoção de confiança alta


def test_sem_emocao_suficiente_as_vagas_voltam_para_as_notas():
    ms = [_m(1, 50), _m(2, 40), _m(3, 30)]
    assert len(escolher_shorts(ms, nota_short=20, quantidade=3, reserva_emocao=2)) == 3


def test_emocao_com_nota_muito_baixa_nao_ocupa_vaga():
    ms = [_m(1, 50), _m(2, 4, "RAIVA", "alta")]
    assert [m.idx for m in escolher_shorts(ms, nota_short=20, quantidade=2)] == [1]


def test_quantidade_zero_nao_gera_short():
    assert escolher_shorts([_m(1, 50)], nota_short=20, quantidade=0) == []


def test_elegiveis_so_partida_quando_ha_partida():
    from cs2clip.moments import elegiveis
    a, b, c = _m(1, 30), _m(2, 30), _m(3, 30)
    a.parte_tipo, b.parte_tipo, c.parte_tipo = "deathmatch", "partida", "fora"
    assert elegiveis([a, b, c]) == [b]
    assert elegiveis([a, b, c], so_partidas=False) == [a, b, c]


def test_elegiveis_sem_nenhuma_partida_mantem_tudo():
    from cs2clip.moments import elegiveis
    a, b = _m(1, 30), _m(2, 30)          # parte_tipo vazio: nenhum banner lido
    assert elegiveis([a, b]) == [a, b]
