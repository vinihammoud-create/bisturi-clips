"""Transcrição PT-BR com faster-whisper na GPU (timestamps por palavra).

Usada para as legendas animadas dos shorts e para pontuar falas de empolgação.
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def _add_cuda_dlls() -> None:
    """No Windows, as DLLs do cuBLAS/cuDNN vêm nos pacotes pip nvidia-*; registra as pastas."""
    if sys.platform != "win32":
        return
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for sub in ("cublas", "cudnn", "cuda_nvrtc"):
        d = base / sub / "bin"
        if d.exists():
            os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


@dataclass
class Word:
    start: float
    end: float
    text: str


_MODEL = None


def get_model(name: str = "large-v3-turbo"):
    global _MODEL
    if _MODEL is None:
        _add_cuda_dlls()
        from faster_whisper import WhisperModel
        try:
            _MODEL = WhisperModel(name, device="cuda", compute_type="int8_float16")
        except Exception as e:  # sem GPU/CUDA: CPU (mais lento)
            print(f"  [aviso] GPU indisponível para o Whisper ({e}); usando CPU.")
            _MODEL = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODEL


def liberar_modelo() -> None:
    """Solta o Whisper da memória de vídeo. A RTX 3050 tem 6 GB: o render (NVENC) e o jogo precisam dela."""
    global _MODEL
    if _MODEL is not None:
        _MODEL = None
        import gc
        gc.collect()


def transcribe(audio: np.ndarray, offset: float, model_name: str = "large-v3-turbo",
               idioma: str = "pt") -> list[Word]:
    """audio: float32 16 kHz. Retorna palavras com tempo absoluto (offset somado)."""
    model = get_model(model_name)
    segments, _ = model.transcribe(
        audio, language=idioma, word_timestamps=True, beam_size=5,
        condition_on_previous_text=False, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        no_speech_threshold=0.5,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            txt = w.word.strip()
            if txt:
                words.append(Word(w.start + offset, w.end + offset, txt))
    return words


def transcribe_full(audio_i16: np.ndarray, model_name: str = "large-v3-turbo", idioma: str = "pt") -> list[Word]:
    """Transcrição do vídeo inteiro (VAD pula silêncio/jogo: ~1–1,5 min por hora na RTX 3050)."""
    return transcribe(audio_i16.astype(np.float32) / 32768.0, 0.0, model_name, idioma)


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"(.)\1{2,}", r"\1\1", s)  # "nossaaaa" -> "nossaa"


# Falas de empolgação (PT-BR): fortes valem 1,0; médias 0,5
STRONG = ["caralho", "puta que pariu", "pqp", "meu deus", "nao acredito", "que isso", "ace", "clutch",
          "clipa", "clipa isso", "inacreditavel", "absurdo", "socorro", "que tiro"]
MEDIUM = ["nossa", "olha isso", "toma", "amassou", "amassei", "de capa", "rapaz", "insano", "monstro",
          "porra", "caraca", "eita", "brabo", "sinistro", "surreal", "uau", "mitou", "na cabeca", "vamo",
          "bora", "lindo", "que jogada"]
# _plain colapsa letras repetidas ("kkkk" -> "kk"): por isso k{2,}. Com k{3,} a risada nunca casava
# (bug corrigido em 17/10/2026). Mesmo assim o Whisper quase não escreve risada: ver docs/STATUS.md.
_LAUGH = re.compile(r"\b(h[aeiu]){2,}\b|\bk{2,}\b|\b(rs){2,}\b", re.I)


def _sem_repeticao(s: str) -> str:
    """Letras repetidas viram uma ("porraaa", "porra" -> "pora"): palavra gritada e esticada casa com o termo."""
    return re.sub(r"(.)\1+", r"\1", s)


def _match_at(words: list[Word], i: int, phrase: str) -> bool:
    parts = phrase.split()
    if i + len(parts) > len(words):
        return False
    return all(_sem_repeticao(re.sub(r"\W", "", _plain(words[i + j].text))) == _sem_repeticao(p)
               for j, p in enumerate(parts))


def hype_events(words: list[Word]) -> list[tuple[float, float, float, str]]:
    """(início, fim, peso, termo) de cada fala de empolgação/risada."""
    out = []
    for i, w in enumerate(words):
        for lst, wt in ((STRONG, 1.0), (MEDIUM, 0.5)):
            for ph in lst:
                if _match_at(words, i, ph):
                    out.append((w.start, words[min(len(words) - 1, i + len(ph.split()) - 1)].end, wt, ph))
        if _LAUGH.search(_plain(w.text)):
            out.append((w.start, w.end, 0.8, "risada"))
    return out


def hype_curve(words: list[Word], duration: float, hop: float) -> np.ndarray:
    n = int(duration / hop) + 1
    c = np.zeros(n, np.float32)
    for s, e, wt, _ in hype_events(words):
        a, b = max(0, int((s - 0.5) / hop)), min(n, int((e + 1.0) / hop) + 1)
        c[a:b] = np.maximum(c[a:b], wt)
    return c


def hype_terms(words: list[Word], start: float, end: float) -> list[str]:
    seen = []
    for s, _, _, term in hype_events([w for w in words if start <= w.start <= end]):
        if term not in seen:
            seen.append(term)
    return seen
