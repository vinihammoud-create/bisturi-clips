"""Áudio: extração 16 kHz mono e curva de empolgação da VOZ do streamer (0–1).

O som do jogo (tiros, explosões) domina o volume da live, então medir só volume
engana. A curva usa:
  1. VAD Silero (do faster-whisper) -> só trechos com fala contam;
  2. energia na banda de voz (300–3400 Hz) acima do nível típico de fala do
     próprio streamer (mediana móvel de ±2,5 min só dos trechos falados);
  3. peso de "tonalidade" (voz é harmônica; tiro é ruído de espectro plano).
0 = fala normal (ou silêncio), 1 = grito (~+12 dB acima do normal).
"""
from __future__ import annotations

import subprocess

import numpy as np

from .tools import FFMPEG

SR = 16000
HOP = 0.1  # resolução das curvas (s)


def load_audio(path: str, sr: int = SR) -> np.ndarray:
    cmd = [FFMPEG, "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
           "-f", "s16le", "pipe:1"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16)


def window(audio: np.ndarray, start: float, end: float, sr: int = SR) -> np.ndarray:
    a = audio[max(0, int(start * sr)):max(0, int(end * sr))]
    return a.astype(np.float32) / 32768.0


def speech_segments(audio: np.ndarray, sr: int = SR) -> list[tuple[float, float]]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    ts = get_speech_timestamps(audio.astype(np.float32) / 32768.0,
                               VadOptions(threshold=0.4, min_speech_duration_ms=200,
                                          min_silence_duration_ms=300, speech_pad_ms=100))
    return [(s["start"] / sr, s["end"] / sr) for s in ts]


def _band_db(audio: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    hop = int(sr * HOP)
    n = len(audio) // hop
    db = np.zeros(n, np.float32)
    tonal = np.zeros(n, np.float32)
    win = np.hanning(hop).astype(np.float32)
    freqs = np.fft.rfftfreq(hop, 1 / sr)
    band = (freqs >= 300) & (freqs <= 3400)
    step = 6000  # blocos para não estourar memória em VODs longos
    for a in range(0, n, step):
        b = min(n, a + step)
        fr = audio[a * hop:b * hop].astype(np.float32).reshape(b - a, hop) / 32768.0 * win
        p = np.abs(np.fft.rfft(fr, axis=1))[:, band] ** 2 + 1e-10
        db[a:b] = 10 * np.log10(p.sum(axis=1))
        flat = np.exp(np.mean(np.log(p), axis=1)) / np.mean(p, axis=1)
        tonal[a:b] = np.clip(1.0 - 2.5 * flat, 0.0, 1.0)
    return db, tonal


def voice_curve(audio: np.ndarray, segments: list[tuple[float, float]], sr: int = SR) -> np.ndarray:
    db, tonal = _band_db(audio, sr)
    n = len(db)
    speech = np.zeros(n, bool)
    for s, e in segments:
        speech[int(s / HOP):min(n, int(e / HOP) + 1)] = True
    if speech.sum() < 50:
        return np.zeros(n, np.float32)
    # nível típico de fala: mediana dos frames falados numa janela de ±2,5 min
    idx = np.nonzero(speech)[0]
    sdb = db[idx]
    centers = np.arange(0, n, int(15 / HOP))
    half = int(150 / HOP)
    glob_med = float(np.median(sdb))
    base_pts = []
    for c in centers:
        lo, hi = np.searchsorted(idx, c - half), np.searchsorted(idx, c + half)
        base_pts.append(float(np.median(sdb[lo:hi])) if hi - lo >= 30 else glob_med)
    base = np.interp(np.arange(n), centers, base_pts)
    excess = (db - base) * (0.5 + 0.5 * tonal)
    v = np.clip((excess - 3.0) / 9.0, 0.0, 1.0) * speech
    k = max(1, int(0.5 / HOP))
    v = np.convolve(v, np.ones(k) / k, mode="same")
    return v.astype(np.float32)


def peak_in(curve: np.ndarray | None, start: float, end: float) -> float:
    if curve is None or not len(curve):
        return 0.0
    a, b = max(0, int(start / HOP)), min(len(curve), int(end / HOP))
    return float(curve[a:b].max()) if b > a else 0.0


def find_peaks(curve: np.ndarray, thresh: float, min_len: float = 0.5,
               min_gap: float = 20.0) -> list[tuple[float, float]]:
    """Trechos sustentados acima do limiar -> (tempo do pico, valor), separados por min_gap."""
    above = curve > thresh
    peaks: list[tuple[float, float]] = []
    i, n = 0, len(curve)
    while i < n:
        if above[i]:
            j = i
            while j < n and above[j]:
                j += 1
            if (j - i) * HOP >= min_len:
                k = i + int(np.argmax(curve[i:j]))
                peaks.append((k * HOP, float(curve[k])))
            i = j
        else:
            i += 1
    peaks.sort(key=lambda p: -p[1])
    chosen: list[tuple[float, float]] = []
    for t, v in peaks:
        if all(abs(t - c[0]) >= min_gap for c in chosen):
            chosen.append((t, v))
    return sorted(chosen)
