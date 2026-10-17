"""Teste da função pura `_dedupe` (cs2clip/rounds.py). O `scan_rounds` em si
precisa de vídeo real e foi validado manualmente (ver docs/STATUS.md e a
issue #2 fechada: 7 rounds achados no VOD 2894824156, 0 falsos positivos)."""
from cs2clip.rounds import RoundTimelineEntry, _dedupe


def test_sem_deteccoes():
    assert _dedupe([], min_gap=20.0) == []


def test_uma_deteccao_so():
    out = _dedupe([(100.0, "derrota", 0.9)], min_gap=20.0)
    assert out == [RoundTimelineEntry(100.0, "derrota", 0.9)]


def test_junta_deteccoes_repetidas_do_mesmo_banner():
    # o mesmo banner aparece em 3 quadros seguidos (banner fica ~3-4s na tela)
    raw = [(100.0, "derrota", 0.95), (103.0, "derrota", 0.98), (106.0, "derrota", 0.90)]
    out = _dedupe(raw, min_gap=20.0)
    assert len(out) == 1
    assert out[0].t == 100.0          # fica com o instante da 1ª detecção
    assert out[0].confianca == 0.98   # mas com a maior confiança do grupo


def test_dois_rounds_diferentes_nao_se_juntam():
    raw = [(100.0, "derrota", 0.9), (220.0, "vitoria", 0.9)]  # 120s de distância
    out = _dedupe(raw, min_gap=20.0)
    assert len(out) == 2
    assert [e.resultado for e in out] == ["derrota", "vitoria"]


def test_resultado_pode_mudar_dentro_do_grupo_se_a_confianca_for_maior():
    # caso raro (OCR errou uma leitura no meio do grupo), mas o código permite:
    # fica com o resultado da detecção de maior confiança do grupo
    raw = [(100.0, "derrota", 0.5), (102.0, "vitoria", 0.95)]
    out = _dedupe(raw, min_gap=20.0)
    assert len(out) == 1
    assert out[0].resultado == "vitoria"
    assert out[0].confianca == 0.95
