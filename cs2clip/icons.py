"""Ícones extras do kill feed (headshot, através da fumaça, wallbang, noscope, cego...).

Ficam entre a arma e o nome da vítima. Cada ícone é comparado com modelos em
assets/icons/<nome>_<n>.png (máscaras binárias com altura ICON_H).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .classify import Parsed, _runs
from .tools import imread

ICON_H = 20
ASSETS = Path(__file__).resolve().parent / "assets" / "icons"

LABELS = {
    "headshot": "HS",
    "smoke": "fumaça",
    "wallbang": "wallbang",
    "noscope": "noscope",
    "blind": "cego",
    "inair": "no ar",
}


def _norm(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return np.zeros((ICON_H, ICON_H), np.uint8)
    m = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    w = max(1, round(m.shape[1] * ICON_H / m.shape[0]))
    return cv2.resize(m, (w, ICON_H), interpolation=cv2.INTER_AREA)


def extract_icons(p: Parsed) -> list[np.ndarray]:
    """Máscaras dos ícones à direita da arma (antes da vítima)."""
    if p.weapon is None:
        return []
    hsv = cv2.cvtColor(p.inner, cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] < 70) & (hsv[..., 2] > 170)).astype(np.uint8) * 255
    x0, x1 = p.weapon[1] + 1, p.right[0].x0 - 1
    if x1 - x0 < 6:
        return []
    zone = white[:, x0:x1]
    out = []
    for a, b in _runs(zone.sum(0) >= 255 * 2, gap=2):
        if b - a < 5:
            continue
        blob = zone[:, a:b + 1]
        ys = np.nonzero(blob.any(1))[0]
        if ys.size == 0 or ys.max() - ys.min() < 9:  # linha fina, não ícone
            continue
        out.append(_norm(blob))
    return out


@lru_cache(maxsize=1)
def _templates() -> list[tuple[str, np.ndarray]]:
    out = []
    for f in sorted(ASSETS.glob("*.png")):
        m = imread(f, cv2.IMREAD_GRAYSCALE)
        if m is not None:
            out.append((f.stem.rsplit("_", 1)[0], m))
    return out


def _sim(a: np.ndarray, b: np.ndarray) -> float:
    if abs(a.shape[1] - b.shape[1]) > max(4, 0.25 * b.shape[1]):
        return 0.0
    w = b.shape[1]
    fa = cv2.resize(a, (w, ICON_H)).astype(np.float32).ravel()
    fb = b.astype(np.float32).ravel()
    fa -= fa.mean()
    fb -= fb.mean()
    d = np.linalg.norm(fa) * np.linalg.norm(fb)
    return float(fa @ fb / d) if d > 0 else 0.0


def identify(mask: np.ndarray) -> str:
    best, best_s = "outro", 0.55
    for name, tpl in _templates():
        s = _sim(mask, tpl)
        if s > best_s:
            best, best_s = name, s
    return best


def icons_of(p: Parsed) -> list[str]:
    return [identify(m) for m in extract_icons(p)]
