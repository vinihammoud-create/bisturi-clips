"""Varre o vídeo inteiro procurando o banner de fim de round (`roundend.py`) e
monta uma linha do tempo de round ganho/perdido — a base do "contexto de jogo"
usado pra separar RAIVA de ALEGRIA sem demo (R-A7, ADR relacionado).

Não lê a demo nem depende do HUD do streamer (achado do lote P4 + issue #2):
o banner "» DERROTA «" / "» VITÓRIA «" é do próprio jogo.

A varredura é lenta (~6-7 min numa live de 50 min, medido em 11/10/2026,
porque o ffmpeg decodifica o vídeo inteiro) — por isso o resultado fica em
cache (`.cache/rounds.json`), igual aos outros passos do pipeline.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from .roundend import detect_round_end, pode_ser_banner
from .tools import FFMPEG, gravar_atomico

SCALE_W, SCALE_H = 960, 540  # mesma escala usada nos testes de roundend.py
TINT_MIN = 0.08              # mesmo limiar de detect_round_end: abaixo disso nem tenta o OCR
MIN_GAP = 20.0               # s: duas leituras a menos disso são o mesmo banner


@dataclass
class RoundTimelineEntry:
    t: float          # instante (s) em que o banner foi visto
    resultado: str     # "derrota" | "vitoria"
    confianca: float


def _dedupe(raw: list[tuple[float, str, float]], min_gap: float) -> list[RoundTimelineEntry]:
    """O banner fica ~3-4 s na tela: a varredura pega o mesmo round várias vezes
    seguidas. Agrupa detecções a menos de `min_gap` segundos da anterior numa
    só entrada (fica com a de maior confiança)."""
    out: list[RoundTimelineEntry] = []
    for t, resultado, conf in raw:
        if out and t - out[-1].t <= min_gap:
            if conf > out[-1].confianca:
                out[-1] = RoundTimelineEntry(out[-1].t, resultado, conf)
            continue
        out.append(RoundTimelineEntry(t, resultado, conf))
    return out


def scan_rounds(path: str, ocr, every: float = 3.0, min_gap: float = 20.0,
                progress=None, area=None) -> list[RoundTimelineEntry]:
    """Lê 1 quadro a cada `every` segundos (bem mais barato que os 10 fps do
    kill feed — o banner não precisa de precisão alta) e roda o detector de 2
    etapas (filtro de cor barato + OCR) em cada um.
    `area`: área do jogo (layout.area_cached); o banner é fração dela. None = quadro inteiro."""
    from .layout import como_esticado, filtro_area
    fps = 1.0 / every
    # área 4:3 (5:4, 16:10) ou sem área: 960x540 de sempre; outra área (ultrawide): 960 de largura sem deformar
    alt = SCALE_H if area is None or como_esticado(area) else max(2, round(SCALE_W * area[3] / area[2] / 2) * 2)
    vf = f"fps={fps},{filtro_area(area)}scale={SCALE_W}:{alt}"
    cmd = [FFMPEG, "-v", "error", "-nostdin", "-i", path, "-an", "-sn", "-vf", vf,
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=SCALE_W * alt * 3 * 4)
    frame_bytes = SCALE_W * alt * 3
    raw: list[tuple[float, str, float]] = []
    i = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            t = i * every
            img = np.frombuffer(buf, np.uint8).reshape(alt, SCALE_W, 3)
            r = detect_round_end(img, ocr)
            if r:
                raw.append((t, r.resultado, r.confianca))
            i += 1
            if progress:
                progress(t)
    finally:
        proc.stdout.close()
        codigo = proc.wait()
    if codigo != 0:  # leitura cortada no meio: não pode virar rounds.json "completo"
        raise RuntimeError("ffmpeg falhou lendo os banners de fim de round")
    return _dedupe(raw, min_gap)


def scan_rounds_cached(path: str, cache: Path, ocr, every: float = 3.0, min_gap: float = 20.0,
                       refazer: bool = False, progress=None, area=None) -> list[RoundTimelineEntry]:
    """`scan_rounds`, mas guardando/lendo o resultado em `cache/rounds.json`. `area`: ver `scan_rounds`."""
    if not refazer:
        guardados = carregar_rounds(cache)
        if guardados is not None:
            return guardados
    entries = scan_rounds(path, ocr, every=every, min_gap=min_gap, progress=progress, area=area)
    salvar_rounds(cache, entries)
    return entries


def criar_ocr():
    """RapidOCR (CPU). Carregar o modelo custa ~1 s: crie 1 vez e reaproveite.

    `det_limit_type="max"` e `det_limit_side_len=320`: o padrão (`min`, 736) AMPLIA recortes pequenos
    como o do banner (384x76) para ~1400 px antes de detectar — ~1 s por quadro. Com estes valores
    são ~50 ms e as mesmas leituras (medido em 17/10/2026 em 3 recortes reais)."""
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(det_limit_type="max", det_limit_side_len=320)


class RoundCollector:
    """Lê o banner de fim de round nos quadros-chave que a varredura do kill feed JÁ decodifica
    (`scan.keyframe_candidates(..., on_frame=coletor.feed)`): não custa uma 2ª passada no vídeo.

    Cada quadro passa pelo filtro de cor barato (em 1/4 dos pixels); só os tingidos (banner,
    mas também fogo/flash) vão para o OCR, que é criado só na primeira vez que precisa."""

    def __init__(self, ocr=None, min_gap: float = MIN_GAP, area=None):
        self._ocr = ocr
        self.min_gap = min_gap
        self.area = area  # área do jogo (sem barras pretas); None = quadro inteiro, como antes
        self.raw: list[tuple[float, str, float]] = []
        self.quadros = 0
        self.ocr_chamados = 0

    def feed(self, t: float, img_bgr: np.ndarray) -> None:
        self.quadros += 1
        if self.area is not None:   # só a área do jogo (as regiões do banner são frações dela)
            from .layout import recortar_area
            img_bgr = recortar_area(img_bgr, self.area)
        # tela tingida (banner clássico) OU letras/borda coloridas na caixa do banner (banner escuro, que não tinge)
        if not pode_ser_banner(img_bgr[::2, ::2], TINT_MIN):
            return
        if self._ocr is None:
            self._ocr = criar_ocr()
        small = self._reduzir(img_bgr)
        self.ocr_chamados += 1
        r = detect_round_end(small, self._ocr)
        if r:
            self.raw.append((t, r.resultado, r.confianca))

    def _reduzir(self, img_bgr: np.ndarray) -> np.ndarray:
        """Quadro na escala do detector. Área 4:3 (ou 5:4, 16:10) com barras vai SEMPRE para 960x540: o 4:3 com
        barras fica igual ao 4:3 esticado de sempre. Outra área (ultrawide, não medida) vai a 960 de largura na
        proporção dela, sem deformar. Sem área: o de sempre."""
        from .layout import como_esticado
        if self.area is None and img_bgr.shape[1] <= SCALE_W:
            return img_bgr
        if self.area is None or como_esticado(self.area):
            return cv2.resize(img_bgr, (SCALE_W, SCALE_H), interpolation=cv2.INTER_AREA)
        h, w = img_bgr.shape[:2]
        return cv2.resize(img_bgr, (SCALE_W, max(2, round(SCALE_W * h / w))), interpolation=cv2.INTER_AREA)

    def result(self) -> list[RoundTimelineEntry]:
        return _dedupe(self.raw, self.min_gap)


def salvar_rounds(cache: Path, entries: list[RoundTimelineEntry]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    gravar_atomico(cache / "rounds.json", json.dumps([asdict(e) for e in entries]))


def carregar_rounds(cache: Path) -> list[RoundTimelineEntry] | None:
    """None = sem rounds.json ou ilegível (gravação interrompida): quem chama varre de novo."""
    f = cache / "rounds.json"
    if not f.exists():
        return None
    try:
        return [RoundTimelineEntry(**d) for d in json.loads(f.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return None
