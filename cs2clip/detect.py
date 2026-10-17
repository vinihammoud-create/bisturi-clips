"""Detecção das linhas do kill feed do CS2 com borda vermelha (abates do jogador local).

Trabalha numa ROI normalizada para a escala 1080p: canto superior direito
(40% da largura x 45% da altura) redimensionado para 768x486.

A ROI é fração da ÁREA DO JOGO (`layout.area_cached`: o quadro sem as barras pretas; sem barras, o quadro
inteiro). Como ela é sempre levada para 768x486, os limites em pixels daqui (MIN_H, MAX_W, `_thin`,
`_h_segments`, x1 >= 0,7*W...) escalam com a altura da área na vertical, mas com a LARGURA dela na horizontal:
foram medidos em 720p/1080p com o jogo em 4:3 esticado ocupando a tela, e o 4:3 com barras vira a mesma imagem.
16:9 nativo (o HUD sai 0,75x mais estreito que no esticado): medido em 17/10/2026 espremendo só o kill feed de 2
trechos reais (720p, 4 min cada) — mesmos 6 + 3 abates da live inteira; a menor caixa ficou com 212 px na ROI
(MIN_W = 110, segmento mínimo 50). Normalizar a largura pela altura (4/3 x altura) NÃO serve: pela imagem não dá
para saber se o 16:9 sem barras é nativo ou o 4:3 esticado do usuário, e no esticado as caixas sairiam 1,33x
mais largas (linha longa cortada pela ROI). Ultrawide (área 21:9): pela conta ~0,57x da largura do esticado
(menor caixa medida ~160 px), não medido com vídeo.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

# ROI relativa ao frame inteiro (x, y, w, h) e tamanho normalizado (escala 1080p).
# 45% da altura: no Deathmatch o kill feed chega a ~8 linhas.
ROI_REL = (0.60, 0.0, 0.40, 0.45)
ROI_SIZE = (768, 486)

# Limites geométricos da caixa (em pixels da escala 1080p)
MIN_W, MAX_W = 110, 760
MIN_H, MAX_H = 20, 46


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    profile: np.ndarray = field(repr=False)  # assinatura do conteúdo (texto/ícones)
    crop: np.ndarray | None = field(default=None, repr=False)
    mask: np.ndarray | None = field(default=None, repr=False)  # assinatura fina (máscara do texto)


def red_mask(bgr: np.ndarray, loose: bool = False) -> np.ndarray:
    """Vermelho da borda. 'loose' pega também a borda em fade/rolagem (mais escura/misturada)."""
    b = bgr[..., 0].astype(np.int16)
    g = bgr[..., 1].astype(np.int16)
    r = bgr[..., 2].astype(np.int16)
    if loose:
        mx = np.maximum(g, b)
        m = (r >= 80) & (r >= 1.9 * g) & (r >= 1.7 * b) & (r - mx >= 35)
    else:
        m = (r > 140) & (g < 70) & (b < 80) & (r > 2.6 * g) & (r > 2.2 * b)
    return m.astype(np.uint8) * 255


def _thin(mask: np.ndarray) -> np.ndarray:
    """Remove regiões vermelhas grossas (parede, sangue): a borda tem 2–4 px de espessura."""
    thick_v = cv2.erode(mask, np.ones((7, 1), np.uint8))
    thick_h = cv2.erode(mask, np.ones((1, 9), np.uint8))
    thick = cv2.dilate(cv2.bitwise_and(thick_v, thick_h), np.ones((9, 9), np.uint8))
    return cv2.bitwise_and(mask, cv2.bitwise_not(thick))


def _coverage(line: np.ndarray) -> float:
    return float(np.count_nonzero(line)) / max(1, line.size)


SIG_W = 768  # largura fixa (px 1080p) da assinatura, ancorada à esquerda


def _anchored(gray_interior: np.ndarray) -> np.ndarray:
    """Máscara do texto claro numa tela de largura fixa, alinhada à esquerda.

    Assim a mesma linha do kill feed tem a mesma assinatura mesmo que a largura
    detectada da caixa oscile alguns pixels entre quadros.
    """
    bright = (gray_interior > 150).astype(np.float32)
    h, w = bright.shape
    canvas = np.zeros((h, SIG_W), np.float32)
    canvas[:, :min(w, SIG_W)] = bright[:, :SIG_W]
    return canvas


def signature(gray_interior: np.ndarray) -> np.ndarray:
    """Perfil horizontal do texto claro: robusto a mudança do fundo semitransparente."""
    prof = _anchored(gray_interior).sum(axis=0)
    prof = cv2.resize(prof.reshape(1, -1), (SIG_W // 6, 1), interpolation=cv2.INTER_AREA).ravel()
    n = np.linalg.norm(prof)
    return prof / n if n > 0 else prof


def fine_signature(gray_interior: np.ndarray) -> np.ndarray:
    """Máscara 2D do texto em resolução maior — distingue entradas quase iguais."""
    m = cv2.resize(_anchored(gray_interior), (SIG_W // 3, 12), interpolation=cv2.INTER_AREA).ravel()
    m = m - m.mean()
    n = np.linalg.norm(m)
    return m / n if n > 0 else m


def _snap(mask: np.ndarray, y0: int, y1: int, lo: int, hi: int, rightmost: bool) -> int | None:
    """Coluna da borda vertical (maior cobertura de vermelho) dentro de [lo, hi)."""
    lo, hi = max(0, lo), min(mask.shape[1], hi)
    if hi - lo < 2 or y1 - y0 < 6:
        return None
    cov = (mask[y0:y1, lo:hi] > 0).mean(axis=0)
    if cov.max() < 0.4:
        return None
    idx = np.nonzero(cov >= cov.max() - 0.1)[0]
    return lo + int(idx.max() if rightmost else idx.min())


def _h_segments(mask: np.ndarray) -> list[list[int]]:
    """Traços horizontais longos: [x0, x1, y0, y1] (linhas de 2–4 px juntadas; buracos curtos unidos)."""
    h = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (30, 1)))
    h = cv2.morphologyEx(h, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1)))
    n, _, st, _ = cv2.connectedComponentsWithStats(h, connectivity=8)
    segs = [[int(x), int(x + w - 1), int(y), int(y + hh - 1)] for x, y, w, hh, _ in st[1:]
            if w >= 50 and hh <= 8]
    segs.sort(key=lambda s: (s[2], s[0]))
    # une pedaços da mesma linha separados por falhas (fade/compressão)
    merged: list[list[int]] = []
    for s in segs:
        for m in merged:
            if abs(m[2] - s[2]) <= 3 and s[0] - m[1] <= 60 and m[0] - s[1] <= 60:
                m[0], m[1] = min(m[0], s[0]), max(m[1], s[1])
                m[2], m[3] = min(m[2], s[2]), max(m[3], s[3])
                break
        else:
            merged.append(s)
    return merged


def find_boxes(roi_bgr: np.ndarray, keep_crop: bool = False) -> list[Box]:
    """Caixas = par de traços vermelhos (topo/base) alinhados, na altura de uma linha do kill feed.

    Funciona com caixas empilhadas e encostadas (Deathmatch), com o fade de entrada/saída
    e com o "flash" claro de quando a linha aparece.
    """
    H, W = roi_bgr.shape[:2]
    mask = _thin(red_mask(roi_bgr, loose=True))
    segs = _h_segments(mask)
    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    boxes: list[Box] = []
    taken: list[tuple[int, int]] = []
    for i, t in enumerate(segs):
        best = None
        for b in segs[i + 1:]:
            dy = b[2] - t[2]
            if dy < MIN_H - 2:
                continue
            if dy > MAX_H:
                break
            x0, x1 = min(t[0], b[0]), max(t[1], b[1])
            w = x1 - x0 + 1
            if not (MIN_W <= w <= MAX_W) or x1 < 0.7 * W:  # kill feed é alinhado à direita
                continue
            if (t[1] - t[0] + 1) < 0.6 * w or (b[1] - b[0] + 1) < 0.6 * w:
                continue
            best = (x0, x1, t[2], b[3])
            break
        if best is None:
            continue
        x0, x1, y0, y1 = best
        # encaixa as laterais na borda vertical real (a linha horizontal às vezes "vaza")
        r = _snap(mask, y0 + 3, y1 - 2, x1 - 25, x1 + 4, rightmost=True)
        l_ = _snap(mask, y0 + 3, y1 - 2, x0 - 4, x0 + 25, rightmost=False)
        x0, x1 = (l_ if l_ is not None else x0), (r if r is not None else x1)
        if x1 - x0 + 1 < MIN_W:
            continue
        x, y, w, h = x0, y0, x1 - x0 + 1, y1 - y0 + 1
        side = lambda a, c: _coverage(mask[y + 3:y + h - 3, max(0, a):min(W, c)].max(axis=1))  # noqa: E731
        if max(side(x - 2, x + 6), side(x + w - 6, x + w + 2)) < 0.5:
            continue
        inner = roi_bgr[y + 5:y + h - 5, x + 6:x + w - 6]
        if inner.size == 0:
            continue
        # interior: fundo do kill feed (escuro, ou claro só no flash de entrada), sem vermelho
        if np.median(cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)[..., 2]) > 150 or _coverage(red_mask(inner)) > 0.2:
            continue
        if any(abs(y - ty) < 10 for ty, _ in taken):
            continue
        taken.append((y, x))
        g_in = gray[y + 5:y + h - 5, x + 6:x + w - 6]
        boxes.append(Box(int(x), int(y), int(w), int(h), signature(g_in),
                         roi_bgr[y:y + h, x:x + w].copy() if keep_crop else None, fine_signature(g_in)))
    boxes.sort(key=lambda b: b.y)
    return boxes


@dataclass
class Track:
    id: int
    first: float
    last: float
    w: int
    profile: np.ndarray = field(repr=False)
    hits: int = 1
    best_crop: np.ndarray | None = field(default=None, repr=False)
    box: tuple[int, int, int, int] = (0, 0, 0, 0)
    mask: np.ndarray | None = field(default=None, repr=False)
    frames: set[int] = field(default_factory=set, repr=False)  # quadros (t*10) em que apareceu
    best_hits: int = 0  # nº de acertos quando best_crop foi salvo (desempate na fusão)


def _track_sim(a: Track, b: Track) -> float:
    coarse = float(np.dot(a.profile, b.profile))
    fine = float(np.dot(a.mask, b.mask)) if a.mask is not None and b.mask is not None else coarse
    return min(coarse, fine)


def merge_duplicates(tracks: list[Track], window: float = 2.0) -> list[Track]:
    """Une trilhas da MESMA linha que se alternaram (nunca no mesmo quadro, mesmo conteúdo)."""
    out: list[Track] = []
    for tr in sorted(tracks, key=lambda x: x.first):
        dup = None
        for o in out:
            if abs(tr.first - o.first) > window or _track_sim(o, tr) <= 0.85:
                continue
            both = len(o.frames & tr.frames)
            if both <= 0.1 * max(1, min(len(o.frames), len(tr.frames))):
                dup = o
                break
        if dup is None:
            out.append(tr)
            continue
        main, other = (dup, tr) if dup.hits >= tr.hits else (tr, dup)
        main.first = min(dup.first, tr.first)
        main.last = max(dup.last, tr.last)
        main.hits = dup.hits + tr.hits
        main.frames = dup.frames | tr.frames
        if main is tr:
            out[out.index(dup)] = tr
    return out


class KillFeedTracker:
    """Associa caixas entre frames; cada caixa nova e persistente = um evento."""

    def __init__(self, gap: float = 2.5, min_hits: int = 3, revive: float = 12.0):
        self.gap = gap
        self.min_hits = min_hits
        self.revive = revive  # caixa que some (placar aberto etc.) e volta = mesma entrada
        self.active: list[Track] = []
        self.done: list[Track] = []
        # trilhas curtas (ainda sem confirmação): guardadas para não perder o instante real
        # do abate quando a borda falha por alguns quadros (fade/rolagem do kill feed)
        self.tentative: list[Track] = []
        self._next = 0

    @staticmethod
    def _sim(tr: Track, b: Box) -> float:
        coarse = float(np.dot(tr.profile, b.profile))
        fine = float(np.dot(tr.mask, b.mask)) if tr.mask is not None and b.mask is not None else coarse
        return min(coarse, fine)

    def _revive(self, t: float, b: Box) -> Track | None:
        """Mesma entrada reaparecendo (placar/menu cobriu o kill feed): exige conteúdo idêntico."""
        best, best_s, src = None, 0.9, None
        for lst in (self.done, self.tentative):
            for tr in lst:
                if t - tr.last > self.revive or abs(tr.w - b.w) > 30:
                    continue
                s = self._sim(tr, b)
                if s > best_s:
                    best, best_s, src = tr, s, lst
        if best is not None:
            src.remove(best)
            self.active.append(best)
        return best

    def _hit(self, tr: Track, t: float, b: Box) -> None:
        tr.last = t
        tr.hits += 1
        tr.frames.add(int(round(t * 10)))
        tr.profile = 0.8 * tr.profile + 0.2 * b.profile
        if b.mask is not None:
            tr.mask = b.mask if tr.mask is None else 0.8 * tr.mask + 0.2 * b.mask
            n = np.linalg.norm(tr.mask)
            if n > 0:
                tr.mask = tr.mask / n
        if b.crop is not None and tr.hits == 3:
            tr.best_crop = b.crop

    def update(self, t: float, boxes: list[Box]) -> None:
        # Associação global gulosa pelo maior score (não depende da ordem das caixas na tela)
        pairs = []
        for bi, b in enumerate(boxes):
            for tr in self.active:
                if abs(tr.w - b.w) <= 30:  # assinatura ancorada à esquerda tolera largura oscilando
                    s = self._sim(tr, b)
                    if s > 0.75:
                        pairs.append((s, bi, tr))
        pairs.sort(key=lambda p: -p[0])
        used_b: set[int] = set()
        used_t: set[int] = set()
        for s, bi, tr in pairs:
            if bi in used_b or tr.id in used_t:
                continue
            self._hit(tr, t, boxes[bi])
            used_b.add(bi)
            used_t.add(tr.id)
        for bi, b in enumerate(boxes):
            if bi in used_b:
                continue
            tr = self._revive(t, b)
            if tr is not None and tr.id not in used_t:
                self._hit(tr, t, b)
            else:
                tr = Track(self._next, t, t, b.w, b.profile, box=(b.x, b.y, b.w, b.h), mask=b.mask)
                tr.frames.add(int(round(t * 10)))
                tr.best_crop = b.crop
                self._next += 1
                self.active.append(tr)
            used_t.add(tr.id)
        still = []
        for tr in self.active:
            if t - tr.last > self.gap:
                (self.done if tr.hits >= self.min_hits else self.tentative).append(tr)
            else:
                still.append(tr)
        self.active = still
        self.tentative = [tr for tr in self.tentative if t - tr.last <= self.revive]

    def finish(self) -> list[Track]:
        for tr in self.active:
            if tr.hits >= self.min_hits:
                self.done.append(tr)
        self.active = []
        return sorted(merge_duplicates(self.done), key=lambda tr: tr.first)
