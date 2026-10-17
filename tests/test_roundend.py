"""Teste do detector de fim de round (cs2clip/roundend.py).

Usa 2 quadros reais extraídos de um VOD do usuário (não um VOD inteiro — só
2 PNGs em tests/fixtures/round_end/), um com o banner "DERROTA" na tela e um
quadro normal de jogo, para provar que o detector acerta os dois casos.
"""
from pathlib import Path

import pytest

pytest.importorskip("cv2")

from cs2clip.roundend import detect_round_end  # noqa: E402
from cs2clip.tools import imread  # lê caminho com acento (cv2.imread não lê no Windows)  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "round_end"


@pytest.fixture(scope="module")
def ocr():
    from cs2clip.rounds import criar_ocr  # o mesmo OCR (já ajustado) que o pipeline usa
    return criar_ocr()


def _load(name: str):
    img = imread(FIXTURES / name)
    assert img is not None, f"fixture ausente: {name}"
    return img


def test_detecta_banner_derrota(ocr):
    frame = _load("derrota_1280x720.png")
    r = detect_round_end(frame, ocr)
    assert r is not None, "deveria ter encontrado o banner de DERROTA"
    assert r.resultado == "derrota"


def test_nao_detecta_em_quadro_normal(ocr):
    frame = _load("normal_1280x720.png")
    r = detect_round_end(frame, ocr)
    assert r is None, "não deveria achar banner de fim de round num quadro normal de jogo"


def test_detecta_banner_vitoria_no_layout_4x3_esticado(ocr):
    """Mesmo detector, agora no layout 4:3 esticado real do usuário (o minimapa
    aparece oval — ver docs/STATUS.md). Achado por varredura: 7 acertos em
    205 quadros amostrados do VOD 2894824156, confiança 0,97-0,98, sem nenhum
    falso positivo (inclusive em cenas de fumaça/flash que cruzam o pré-filtro)."""
    frame = _load("vitoria_4x3_1920x1080.png")
    r = detect_round_end(frame, ocr)
    assert r is not None, "deveria ter encontrado o banner de VITÓRIA no layout 4:3"
    assert r.resultado == "vitoria"


def test_detecta_banner_escuro_que_nao_tinge_a_tela(ocr):
    """Regressão (17/10/2026, live 2902813681): o banner veio como caixa escura com letras e borda vermelhas,
    sem tingir a tela. O pré-filtro antigo (só tingimento) dava 0,024 < 0,08 e o round se perdia: de 15:00 a
    45:00 só 5 de 14 fins de round eram lidos. O filtro de cor das letras/borda resolve."""
    from cs2clip.roundend import COR_MIN, _cor_banner, _tint_score
    frame = _load("derrota_banner_escuro_1280x720.png")
    assert _tint_score(frame) < 0.08 and _cor_banner(frame) >= COR_MIN
    r = detect_round_end(frame, ocr)
    assert r is not None and r.resultado == "derrota"


def test_tela_vermelha_da_propria_morte_nao_e_fim_de_round(ocr):
    """A tela fica vermelha quando o jogador morre ("X matou você com..."): não é banner de round."""
    assert detect_round_end(_load("morte_tela_vermelha_1280x720.png"), ocr) is None


def test_detecta_a_propria_morte_em_varias_resolucoes(ocr):
    """Painel "<quem> matou você com: <arma>" (17/10/2026): lido de 640 a 1920 de largura; nenhum outro quadro de
    teste (banner, jogo normal) vira morte."""
    import cv2

    from cs2clip.roundend import detect_own_death
    f = _load("morte_tela_vermelha_1280x720.png")
    for w, h in ((1920, 1080), (1280, 720), (960, 540)):
        assert detect_own_death(cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA), ocr) is not None, w
    for nome in ("normal_1280x720.png", "derrota_1280x720.png", "derrota_banner_escuro_1280x720.png",
                 "vitoria_4x3_1920x1080.png"):
        assert detect_own_death(_load(nome), ocr) is None, nome


def test_juntar_varias_leituras_da_mesma_morte():
    from cs2clip.mortes import juntar
    assert juntar([100.0, 100.5, 101.0, 130.0, 131.5]) == [100.0, 130.0]
