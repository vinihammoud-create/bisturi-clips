"""Correções da revisão R1 na detecção (scan.py, rounds.py, layout.py, roundend.py).

Os vídeos usados aqui são sintéticos e minúsculos, gerados com o ffmpeg numa pasta temporária."""
import subprocess

import numpy as np
import pytest

from cs2clip.tools import FFMPEG


def _video(tmp_path, nome: str, *extra: str, dur: float = 3.0) -> str:
    f = tmp_path / nome
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=10:duration={dur}",
                    "-c:v", "mpeg2video", "-g", "10", *extra, str(f)], check=True)
    return str(f)


# ── achado 0: tempo dos quadros-chave na mesma escala do ffmpeg -ss ─────────────────────────────────────────────

def test_quadros_chave_descontam_start_time_do_arquivo(tmp_path):
    from cs2clip.scan import keyframe_candidates, probe
    path = _video(tmp_path, "offset.ts", "-output_ts_offset", "10")
    tempos = []
    keyframe_candidates(probe(path), on_frame=lambda t, img: tempos.append(t))
    assert tempos, "nenhum quadro-chave lido"
    assert tempos[0] < 0.5, tempos  # antes vinha ~10 s (tempo absoluto do PyAV)
    assert tempos[-1] < 3.0, tempos


# ── achado 2: ffmpeg que falha não pode virar varredura "completa" ─────────────────────────────────────────────

def test_scan_range_levanta_erro_se_ffmpeg_falha(tmp_path):
    from cs2clip.scan import _scan_range
    with pytest.raises(RuntimeError):
        _scan_range(str(tmp_path / "nao_existe.mp4"), 0.0, 10.0, 10.0, 0.0, 10.0)


def test_frames_tracks_levanta_erro_se_ffmpeg_falha(tmp_path):
    from cs2clip.scan import _frames_tracks
    with pytest.raises(RuntimeError):
        _frames_tracks(str(tmp_path / "nao_existe.mp4"), 0.0, 10.0, 10.0)


def test_scan_rounds_levanta_erro_se_ffmpeg_falha(tmp_path):
    from cs2clip.rounds import scan_rounds
    with pytest.raises(RuntimeError):
        scan_rounds(str(tmp_path / "nao_existe.mp4"), ocr=None)


def test_scan_range_ok_em_video_valido(tmp_path):
    from cs2clip.scan import _frames_tracks, _scan_range
    path = _video(tmp_path, "ok.mp4")
    assert _scan_range(path, 0.0, 3.0, 10.0, 0.0, 4.0) == []
    assert _frames_tracks(path, 0.0, 3.0, 10.0) == []


# ── achado 3: preview com menos de 3 quadros ───────────────────────────────────────────────────────────────────

def test_preview_com_um_quadro_so(tmp_path, monkeypatch):
    import cs2clip.layout as layout
    um = np.zeros((layout.SH, layout.SW, 3), np.uint8)
    monkeypatch.setattr(layout, "sample_frames", lambda path, duration, n=40: [um])
    salvos = []
    monkeypatch.setattr(layout, "imwrite", lambda f, img: salvos.append(img.shape))
    lay = layout.Layout((0.1, 0.1, 0.2, 0.2), (0.15, 0.15, 0.05, 0.05), 0.9)
    layout.preview("x.mp4", 10.0, lay, str(tmp_path / "p.png"))  # antes: IndexError em frames[1]
    assert salvos == [(layout.SH, layout.SW, 3)]


# ── achado 5 (S1): banner e painel de morte em outros idiomas ──────────────────────────────────────────────────

class _OcrFalso:
    def __init__(self, *textos: str):
        self.textos = textos

    def __call__(self, img, use_cls=False):
        return [[None, t, 0.9] for t in self.textos], None


def _quadro_banner():
    # tela tingida de vermelho: passa o pré-filtro de cor do banner
    q = np.zeros((540, 960, 3), np.uint8)
    q[..., 2] = 200
    return q


@pytest.mark.parametrize("texto, resultado", [
    ("» ROUND WON «", "vitoria"),
    ("» ROUND LOST «", "derrota"),
    ("RONDA GANADA", "vitoria"),
    ("RONDA PERDIDA", "derrota"),
    ("RONDA VENCIDA", "vitoria"),
    ("» VITÓRIA «", "vitoria"),
    ("» DERROTA «", "derrota"),
])
def test_banner_em_varios_idiomas(texto, resultado):
    from cs2clip.roundend import detect_round_end
    r = detect_round_end(_quadro_banner(), _OcrFalso(texto))
    assert r is not None and r.resultado == resultado


def test_banner_em_dois_pedacos_do_ocr():
    from cs2clip.roundend import detect_round_end
    r = detect_round_end(_quadro_banner(), _OcrFalso("ROUND", "LOST"))
    assert r is not None and r.resultado == "derrota"


def test_banner_texto_qualquer_nao_casa():
    from cs2clip.roundend import detect_round_end
    assert detect_round_end(_quadro_banner(), _OcrFalso("ROUND", "STARTED")) is None


def _quadro_morte():
    # uma linha vermelha fina no meio do painel "matou você com" (quadro 1280x720): passa os filtros de cor
    q = np.zeros((720, 1280, 3), np.uint8)
    q[380:386, 498:640] = (0, 0, 230)
    return q


@pytest.mark.parametrize("texto", [
    "killed you with the AK-47",
    "te ha matado con el arma AK-47",
    "te han matado con el arma AK-47",
    "te mató con AK-47",
    "matou-te com: AK-47",
    "matou você com: AK-47",
])
def test_painel_de_morte_em_varios_idiomas(texto):
    from cs2clip.roundend import detect_own_death
    assert detect_own_death(_quadro_morte(), _OcrFalso(texto)) is not None


def test_painel_de_morte_texto_qualquer_nao_casa():
    from cs2clip.roundend import detect_own_death
    assert detect_own_death(_quadro_morte(), _OcrFalso("AK-47")) is None
