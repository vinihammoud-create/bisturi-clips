"""S3: área do jogo (barras pretas) e regiões relativas a ela (kill feed, banner, painel da morte, mira do short)."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from cs2clip import layout, mortes, render
from cs2clip.detect import ROI_REL, ROI_SIZE, find_boxes
from cs2clip.layout import AREA_VERSAO, area_bruta, area_cached, filtro_area, ler_area, recortar_area
from cs2clip.tools import imread


def _jogo(w, h, seed=0):
    """'Jogo' com textura clara o bastante para não parecer barra."""
    rng = np.random.default_rng(seed)
    return rng.integers(40, 220, (h, w), dtype=np.uint8)


def _com_barras(W, H, x, y, w, h, n=8, borrar=False):
    out = []
    for k in range(n):
        f = np.full((H, W), 16, np.uint8)
        f[y:y + h, x:x + w] = _jogo(w, h, k)
        if borrar:   # escala do encoder: borda de 1-2 px borrada
            f = cv2.GaussianBlur(f, (5, 1), 0)
        out.append(f)
    return out


def test_sem_barras_e_none():
    assert area_bruta([_jogo(1280, 720, k) for k in range(8)]) is None


def test_4_3_com_barras_em_720p_e_1080p():
    assert area_bruta(_com_barras(1280, 720, 160, 0, 960, 720)) == (160, 0, 960, 720)
    assert area_bruta(_com_barras(1920, 1080, 240, 0, 1440, 1080)) == (240, 0, 1440, 1080)


def test_borda_borrada_acerta_o_4_3_exato():
    assert area_bruta(_com_barras(1920, 1080, 240, 0, 1440, 1080, borrar=True)) == (240, 0, 1440, 1080)


def test_faixa_em_cima_e_embaixo():
    # 21:9 dentro de 16:9: ~1920x823 centralizado (o ajuste à proporção pode mexer 2 px)
    x, y, w, h = area_bruta(_com_barras(1920, 1080, 0, 128, 1920, 824))
    assert (x, w) == (0, 1920) and abs(y - 128) <= 2 and abs(h - 824) <= 2


def test_webcam_pequena_na_barra_nao_atrapalha():
    fs = _com_barras(1280, 720, 160, 0, 960, 720)
    for f in fs:
        f[300:480, 10:150] = 200   # webcam na barra da esquerda (25% da altura)
    assert area_bruta(fs) == (160, 0, 960, 720)


def test_webcam_grande_e_chat_dentro_das_barras():
    # outros players põem a webcam e o chat nas barras do 4:3: a borda vem do meio para fora
    fs = _com_barras(1920, 1080, 240, 0, 1440, 1080)
    for k, f in enumerate(fs):
        f[100:700, 20:220] = _jogo(200, 600, 50 + k)        # webcam de 55% da altura na barra da esquerda
        f[300:1000:24, 1700:1890] = 230                      # linhas do chat na barra da direita
    assert area_bruta(fs) == (240, 0, 1440, 1080)


def test_canto_escuro_do_mapa_sem_barras_nao_vira_barra():
    fs = [_jogo(1280, 720, k) for k in range(8)]
    for f in fs[:3]:
        f[:, 300:340] = 10       # coluna escura no meio do jogo (porta, sombra): a faixa até a ponta não é preta
    for f in fs[3:5]:
        f[:, :200] = 10          # parede escura encostada na borda em 2 quadros: sem consenso
    assert area_bruta(fs) is None


def test_tela_fora_do_jogo_em_poucos_quadros():
    fs = _com_barras(1280, 720, 160, 0, 960, 720, n=10)
    fs[0] = _jogo(1280, 720, 99)       # "já volto" em 16:9 inteiro
    fs[1] = _jogo(1280, 720, 98)
    assert area_bruta(fs) == (160, 0, 960, 720)


def test_barra_so_de_um_lado_nao_arrisca():
    assert area_bruta(_com_barras(1280, 720, 160, 0, 1120, 720)) is None


def test_borda_fininha_de_captura_e_ignorada():
    assert area_bruta(_com_barras(1280, 720, 8, 0, 1264, 720)) is None


def test_quadros_pretos_nao_votam():
    fs = [np.full((720, 1280), 16, np.uint8) for _ in range(10)]
    assert area_bruta(fs) is None                                     # só tela preta: sem decisão
    fs += _com_barras(1280, 720, 160, 0, 960, 720, n=5)
    assert area_bruta(fs) == (160, 0, 960, 720)


def test_cena_escura_num_quadro_nao_muda_a_area():
    fs = _com_barras(1280, 720, 160, 0, 960, 720)
    fs[0][:, 160:400] = 10    # canto escuro do mapa em 1 quadro
    assert area_bruta(fs) == (160, 0, 960, 720)


def test_filtro_e_recorte():
    assert filtro_area(None) == ""
    assert filtro_area((160, 0, 960, 720)) == "crop=960:720:160:0,"
    img = np.zeros((720, 1280, 3), np.uint8)
    assert recortar_area(img, None) is img
    assert recortar_area(img, (160, 0, 960, 720)).shape == (720, 960, 3)


def test_cache_da_area(monkeypatch, tmp_path):
    info = type("I", (), {"path": "v.mp4", "duration": 60.0, "width": 1280, "height": 720})()
    chamadas = []

    def quadros(*a):
        chamadas.append(a)
        return _com_barras(1280, 720, 160, 0, 960, 720)
    monkeypatch.setattr(layout, "_quadros_cinza", quadros)
    assert area_cached(tmp_path, info) == (160, 0, 960, 720)
    assert area_cached(tmp_path, info) == (160, 0, 960, 720)
    assert len(chamadas) == 1                                          # 2ª vez veio do cache
    assert ler_area(tmp_path, 1920, 1080) == (False, None)            # outro tamanho de vídeo: não vale
    d = json.loads((tmp_path / "area_jogo.json").read_text(encoding="utf-8"))
    assert d["versao"] == AREA_VERSAO
    d["versao"] = "velha"
    (tmp_path / "area_jogo.json").write_text(json.dumps(d), encoding="utf-8")
    area_cached(tmp_path, info)
    assert len(chamadas) == 2                                          # versão antiga: mede de novo


def test_falha_na_leitura_nao_grava_cache(monkeypatch, tmp_path):
    info = type("I", (), {"path": "v.mp4", "duration": 60.0, "width": 1280, "height": 720})()
    monkeypatch.setattr(layout, "_quadros_cinza", lambda *a: [])
    assert area_cached(tmp_path, info) is None
    assert not (tmp_path / "area_jogo.json").exists()


# ── escala das regiões ───────────────────────────────────────────────────────────────────────────

CAIXA_REAL = Path(__file__).parent / "fixtures" / "caixas" / "2893023758_00165.22.png"


def _quadro_com_kill_feed(W, H):
    """Quadro 16:9 (4:3 esticado, como a live do usuário) com uma linha REAL do kill feed no canto superior
    direito: o recorte da fixture é colado na ROI normalizada, que volta para o tamanho do quadro."""
    caixa = imread(CAIXA_REAL)
    roi = np.full((ROI_SIZE[1], ROI_SIZE[0], 3), 50, np.uint8)
    ch, cw = caixa.shape[:2]
    roi[40:40 + ch, ROI_SIZE[0] - 20 - cw:ROI_SIZE[0] - 20] = caixa
    f = np.full((H, W, 3), 50, np.uint8)
    rx, ry, rw, rh = ROI_REL
    x0, x1, y1 = int(rx * W), int((rx + rw) * W), int((ry + rh) * H)
    f[0:y1, x0:x1] = cv2.resize(roi, (x1 - x0, y1), interpolation=cv2.INTER_AREA)
    return f


def _roi(img, area):
    """Mesma conta de scan.keyframe_candidates."""
    img = recortar_area(img, area)
    h, w = img.shape[:2]
    rx, ry, rw, rh = ROI_REL
    roi = img[int(ry * h):int((ry + rh) * h), int(rx * w):int((rx + rw) * w)]
    return cv2.resize(roi, ROI_SIZE, interpolation=cv2.INTER_CUBIC if h < 1080 else cv2.INTER_AREA)


@pytest.mark.parametrize("W,H", [(1280, 720), (1920, 1080)])
def test_kill_feed_com_barras_cai_no_mesmo_lugar_da_roi(W, H):
    esticado = _quadro_com_kill_feed(W, H)
    ref = find_boxes(_roi(esticado, None))
    assert len(ref) == 1
    # o mesmo jogo em 4:3 com barras: a imagem esticada "desesticada" para 4:3 e centralizada
    aw = H * 4 // 3
    barras = np.zeros_like(esticado)
    barras[:, (W - aw) // 2:(W + aw) // 2] = cv2.resize(esticado, (aw, H), interpolation=cv2.INTER_AREA)
    assert find_boxes(_roi(barras, None)) == [] or find_boxes(_roi(barras, None))[0].x != ref[0].x  # antes: errado
    novo = find_boxes(_roi(barras, ((W - aw) // 2, 0, aw, H)))
    assert len(novo) == 1
    b, r = novo[0], ref[0]
    assert abs(b.x - r.x) <= 4 and abs(b.y - r.y) <= 2 and abs(b.w - r.w) <= 6 and abs(b.h - r.h) <= 2


def test_altura_do_painel_da_morte():
    assert mortes.altura_painel(1280, 720) == 94
    assert mortes.altura_painel(1920, 1080) == 94
    assert mortes.altura_painel(1440, 1080) == 125            # 4:3 sem barras: igual a antes
    assert mortes.altura_painel(1280, 720, (160, 0, 960, 720)) == 94   # área com barras conta como 16:9


def test_mortes_cache_v1_vale_so_sem_barras(monkeypatch, tmp_path):
    (tmp_path / "mortes.json").write_text(json.dumps({"versao": "1", "mortes": [10.0]}), encoding="utf-8")
    lidas = []
    monkeypatch.setattr(mortes, "ler_mortes", lambda *a, **k: (lidas.append(a), [99.0])[1])
    info = type("I", (), {"duration": 60.0, "width": 1280, "height": 720})()
    assert mortes.mortes_cached(tmp_path, "v.mp4", info, 2, False) == [10.0]
    assert not lidas
    assert mortes.mortes_cached(tmp_path, "v.mp4", info, 2, False, (160, 0, 960, 720)) == [99.0]
    assert lidas and lidas[0][-1] == (160, 0, 960, 720)
    assert json.loads((tmp_path / "mortes.json").read_text(encoding="utf-8"))["versao"] == mortes.VERSAO


def test_versao_do_detector_sem_barras_continua_valendo():
    from cs2clip import pipeline as pl
    area = (160, 0, 960, 720)
    assert pl._versao_cache(None) == pl.DETECTOR_VERSAO                # sem barras: o arquivo de sempre
    assert pl._versao_vale(pl.DETECTOR_VERSAO, None) and pl._versao_vale(pl._versao_cache(area), area)
    assert not pl._versao_vale("5", None) and not pl._versao_vale("6", None)   # v7 lê a vitória azul: varre de novo
    assert not pl._versao_vale("5", area)
    assert not pl._versao_vale("4", None)
    assert not pl._versao_vale(pl.DETECTOR_VERSAO, area)               # varrido sem barras, agora com barras
    assert not pl._versao_vale(pl._versao_cache(area), None)           # e o contrário
    assert not pl._versao_vale(pl._versao_cache(area), (240, 0, 1440, 1080))


def test_mortes_cache_com_outra_area_le_de_novo(monkeypatch, tmp_path):
    monkeypatch.setattr(mortes, "ler_mortes", lambda *a, **k: [5.0])
    info = type("I", (), {"duration": 60.0, "width": 1280, "height": 720})()
    area = (160, 0, 960, 720)
    assert mortes.mortes_cached(tmp_path, "v.mp4", info, 2, False, area) == [5.0]
    monkeypatch.setattr(mortes, "ler_mortes", lambda *a, **k: [7.0])
    assert mortes.mortes_cached(tmp_path, "v.mp4", info, 2, False, area) == [5.0]   # mesma área: cache
    assert mortes.mortes_cached(tmp_path, "v.mp4", info, 2, False) == [7.0]         # sem barras agora: lê de novo
    assert json.loads((tmp_path / "mortes.json").read_text(encoding="utf-8"))["area"] is None
    assert mortes.mortes_cached(tmp_path, "v.mp4", info, 2, False) == [7.0]


def test_jogo_no_quadro_do_render(monkeypatch):
    monkeypatch.setattr(render, "_tamanho", lambda src: (1280, 720))
    assert render._jogo_no_quadro("v.mp4", None, None) is None
    assert render._jogo_no_quadro("v.mp4", (160, 0, 960, 720), None) == (240.0, 0.0, 1440.0, 1080.0)


def test_roundcollector_usa_a_area_em_960x540(monkeypatch):
    from cs2clip import rounds
    vistos = []
    monkeypatch.setattr(rounds, "detect_round_end", lambda img, ocr: vistos.append(img.shape))
    quadro = np.zeros((720, 1280, 3), np.uint8)
    quadro[:, 160:1120] = (20, 20, 200)    # área do jogo toda tingida de vermelho: passa no filtro de cor
    rounds.RoundCollector(ocr=object(), area=(160, 0, 960, 720)).feed(0.0, quadro)
    rounds.RoundCollector(ocr=object()).feed(0.0, quadro)
    assert vistos == [(540, 960, 3), (540, 960, 3)]
    pequeno = np.zeros((720, 960, 3), np.uint8)
    pequeno[:] = (20, 20, 200)
    rounds.RoundCollector(ocr=object()).feed(0.0, pequeno)
    assert vistos[-1] == (720, 960, 3)     # sem área: o de sempre (largura <= 960 não é redimensionada)


def test_ler_area_recusa_cache_corrompido(tmp_path):
    def gravar(area):
        (tmp_path / "area_jogo.json").write_text(json.dumps({"versao": AREA_VERSAO, "tamanho": [1280, 720],
                                                             "area": area}), encoding="utf-8")
    gravar([160, 0, 960, 720])
    assert ler_area(tmp_path, 1280, 720) == (True, (160, 0, 960, 720))
    gravar(None)
    assert ler_area(tmp_path, 1280, 720) == (True, None)
    for ruim in ([160, 0, 960], [160, 0, 960, 720, 1], [400, 0, 960, 720], [-2, 0, 960, 720], [160, 0, 0, 720],
                 [0, 100, 1280, 700], ["x", 0, 960, 720]):
        gravar(ruim)
        assert ler_area(tmp_path, 1280, 720) == (False, None), ruim   # mede de novo, não quebra o crop


def test_refazer_com_falha_apaga_area_velha(monkeypatch, tmp_path):
    info = type("I", (), {"path": "v.mp4", "duration": 60.0, "width": 1280, "height": 720})()
    monkeypatch.setattr(layout, "_quadros_cinza", lambda *a: _com_barras(1280, 720, 160, 0, 960, 720))
    assert area_cached(tmp_path, info) == (160, 0, 960, 720)
    monkeypatch.setattr(layout, "_quadros_cinza", lambda *a: [])
    assert area_cached(tmp_path, info, refazer=True) is None
    # o render (ler_area) não pode achar a área velha e cortar o short com barras que a varredura não usou
    assert ler_area(tmp_path, 1280, 720) == (False, None)


def test_como_esticado():
    assert layout.como_esticado((160, 0, 960, 720))          # 4:3
    assert layout.como_esticado((240, 0, 1440, 1080))
    assert layout.como_esticado((285, 0, 1350, 1080))        # 5:4
    assert layout.como_esticado((96, 0, 1728, 1080))         # 16:10
    assert not layout.como_esticado(None)
    assert not layout.como_esticado((0, 128, 1920, 824))     # 21:9
    assert not layout.como_esticado((0, 0, 1920, 1080))      # 16:9


def test_painel_da_morte_ultrawide_fica_na_proporcao_da_area():
    _, _, w, h = mortes.PAINEL_MORTE_REL
    uw = (0, 128, 1920, 824)
    assert mortes.altura_painel(1920, 1080, uw) == round(mortes.LARGURA * h * 824 / (w * 1920))   # 72
    assert mortes.altura_painel(1920, 1080, (240, 0, 1440, 1080)) == 94                           # 4:3: como 16:9


def test_roundcollector_ultrawide_nao_deforma(monkeypatch):
    from cs2clip import rounds
    vistos = []
    monkeypatch.setattr(rounds, "detect_round_end", lambda img, ocr: vistos.append(img.shape))
    quadro = np.zeros((1080, 1920, 3), np.uint8)
    quadro[128:952] = (20, 20, 200)
    rounds.RoundCollector(ocr=object(), area=(0, 128, 1920, 824)).feed(0.0, quadro)
    assert vistos == [(412, 960, 3)]


def test_scan_rounds_cached_repassa_a_area(monkeypatch, tmp_path):
    from cs2clip import rounds
    recebido = {}
    monkeypatch.setattr(rounds, "scan_rounds", lambda *a, **k: (recebido.update(k), [])[1])
    rounds.scan_rounds_cached("v.mp4", tmp_path, object(), area=(160, 0, 960, 720))
    assert recebido["area"] == (160, 0, 960, 720)
