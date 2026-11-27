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

from . import modelo_voz


_DLLS_REGISTRADAS: set[str] = set()


def _add_cuda_dlls() -> None:
    """No Windows, a DLL do cuBLAS vem no pacote pip nvidia-cublas-cu12; registra a pasta. (cuDNN e nvrtc não são mais instalados: o Whisper não os usa;
    se alguém os tiver de uma instalação antiga, a pasta existe e também é registrada, sem mal nenhum.)
    Cada pasta é registrada UMA vez: chamar de novo (o modelo recarregado) não acumula entradas repetidas no PATH (G-019)."""
    if sys.platform != "win32":
        return
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for sub in ("cublas", "cudnn", "cuda_nvrtc"):
        d = base / sub / "bin"
        if d.exists() and str(d) not in _DLLS_REGISTRADAS:
            _DLLS_REGISTRADAS.add(str(d))
            os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


@dataclass
class Word:
    start: float
    end: float
    text: str


_MODEL = None
_MODEL_NA_CPU = False
_MODEL_CHAVE: tuple[str, bool] | None = None   # (nome, cpu) com que `_MODEL` foi pedido

MODELO_LEVE = "small"   # P3.9: para quem a GPU falha e pediu um modelo maior que ele (a CPU com o turbo leva tempo demais)
_TROCA: dict[str, str | None] = {}   # só quando a fala caiu para o modelo leve: o que o dono vê no tempos.json; liberar_modelo NÃO apaga


class FalaIndisponivel(RuntimeError):
    """A GPU falhou e o modelo leve também não pôde ser carregado (ex.: sem internet para baixar o `small`): a transcrição é pulada."""


def _eh_modelo_grande(nome: str) -> bool:
    """Maior que o `small` (turbo, medium, large*, distil-large): a CPU com ele é lenta demais. tiny/base/small e nomes que não conhecemos: não."""
    n = nome.lower()
    return any(k in n for k in ("large", "medium", "turbo"))


def troca_de_modelo() -> dict[str, str | None] | None:
    """Se nesta análise a fala caiu para o modelo leve: {"modelo_pedido", "modelo_usado" (None = nenhum), "motivo"}. Senão None."""
    return dict(_TROCA) if _TROCA else None


def zerar_troca_de_modelo() -> None:
    _TROCA.clear()


def _avisar_queda(erro: Exception, quando: str) -> None:
    print(f"  [aviso] a placa de vídeo falhou {quando} ({str(erro)[:150]}). O programa vai usar o modelo leve ({MODELO_LEVE}) na CPU: "
          "a transcrição fica menos precisa e mais lenta. Atualize o driver da NVIDIA ou feche o que está usando a placa.")


def _onde_esta(nome: str) -> str:
    """P4.2: a pasta do modelo na revisão fixada no componentes.json, se ela já está inteira no PC (o faster-whisper a carrega sem consultar
    a rede). Senão o próprio nome, exatamente como antes: modelo que o `modelo_voz` não conhece, que falta, ou que está em outra revisão."""
    pasta = modelo_voz.caminho_do_modelo(nome)
    return str(pasta) if pasta is not None else nome


def _carregar_na_cpu(whisper_model, real: str, pedido: str, chave: tuple[str, bool]):
    """Carrega `real` na CPU e guarda como o modelo de `chave`. Se `real` é o leve no lugar do `pedido`, registra a troca;
    se nem o leve carregar (download sem internet), avisa e levanta FalaIndisponivel."""
    global _MODEL, _MODEL_NA_CPU, _MODEL_CHAVE
    troca = real != pedido
    try:
        _MODEL = whisper_model(_onde_esta(real), device="cpu", compute_type="int8")
    except Exception as e:  # noqa: BLE001
        if not troca:
            raise
        _TROCA.update(modelo_pedido=pedido, modelo_usado=None, motivo=str(e)[:150])
        print(f"  [aviso] não consegui preparar o modelo leve ({real}): {str(e)[:150]}. Se ele ainda não foi baixado, precisa de internet. "
              "A transcrição foi pulada: as falas não entram na nota dos momentos.")
        raise FalaIndisponivel(str(e)) from e
    _MODEL_NA_CPU = True
    _MODEL_CHAVE = chave
    if troca:
        _TROCA.update(modelo_pedido=pedido, modelo_usado=real, motivo="a placa de vídeo falhou")
    return _MODEL


def get_model(name: str = "large-v3-turbo", cpu: bool = False):
    """O modelo carregado, guardado por (nome, cpu): pedir outro nome ou outro dispositivo carrega o certo (antes devolvia
    o primeiro modelo carregado, qualquer que fosse o pedido) e solta o anterior (G-019).
    Se a GPU falhar ao carregar um modelo maior que o `small`, carrega o `small` na CPU (P3.9); `cpu=True` é pedido explícito: não troca."""
    global _MODEL, _MODEL_NA_CPU, _MODEL_CHAVE
    chave = (name, cpu)
    ja_na_cpu = _MODEL_NA_CPU and _MODEL_CHAVE is not None and _MODEL_CHAVE[0] == name   # o que caiu na CPU serve a quem pede CPU
    if _MODEL is not None and _MODEL_CHAVE not in (None, chave) and not (cpu and ja_na_cpu):
        liberar_modelo()
    if _MODEL is None:
        _add_cuda_dlls()
        from faster_whisper import WhisperModel
        real = name
        if not cpu:
            try:
                _MODEL = WhisperModel(_onde_esta(name), device="cuda", compute_type="int8_float16")
                _MODEL_NA_CPU = False
            except Exception as e:  # sem GPU/CUDA: CPU (mais lento)
                if _eh_modelo_grande(name):
                    _avisar_queda(e, "ao carregar o modelo")
                    real = MODELO_LEVE
                else:
                    print(f"  [aviso] GPU indisponível para o Whisper ({e}); usando CPU.")
        if _MODEL is None:
            _carregar_na_cpu(WhisperModel, real, name, chave)
        _MODEL_CHAVE = chave
    return _MODEL


def liberar_modelo() -> None:
    """Solta o Whisper da memória de vídeo. A RTX 3050 tem 6 GB: o render (NVENC) e o jogo precisam dela."""
    global _MODEL, _MODEL_NA_CPU, _MODEL_CHAVE
    if _MODEL is not None:
        _MODEL = None
        _MODEL_NA_CPU = False
        _MODEL_CHAVE = None
        import gc
        gc.collect()


def transcribe(audio: np.ndarray, offset: float, model_name: str = "large-v3-turbo",
               idioma: str = "pt") -> list[Word]:
    """audio: float32 16 kHz. Retorna palavras com tempo absoluto (offset somado). Se a GPU falhar no meio da
    transcrição (VRAM ocupada por outro programa, driver), repete uma vez na CPU em vez de derrubar a análise: com o
    `small` quando o pedido era um modelo maior (P3.9), com o mesmo modelo nos demais. Se nem o `small` carregar: devolve []."""
    try:
        model = get_model(model_name)
    except FalaIndisponivel:
        return []
    try:
        return _palavras(model, audio, offset, idioma)
    except Exception as e:  # noqa: BLE001  (CUDA/OOM/driver durante a transcrição)
        if _MODEL_NA_CPU:
            raise   # já estava na CPU: não há para onde cair
        liberar_modelo()
        if not _eh_modelo_grande(model_name):
            print(f"  [aviso] a GPU falhou na transcrição ({e}); repetindo na CPU (mais lento).")
            return _palavras(get_model(model_name, cpu=True), audio, offset, idioma)
        _avisar_queda(e, "durante a transcrição")
        _add_cuda_dlls()
        from faster_whisper import WhisperModel
        try:
            leve = _carregar_na_cpu(WhisperModel, MODELO_LEVE, model_name, (model_name, False))
        except FalaIndisponivel:
            return []
        return _palavras(leve, audio, offset, idioma)


def _palavras(model, audio: np.ndarray, offset: float, idioma: str) -> list[Word]:
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
        achados: list[str] = []
        for lst, wt in ((STRONG, 1.0), (MEDIUM, 0.5)):
            for ph in sorted(lst, key=lambda p: -len(p.split())):   # a frase mais longa primeiro
                if any(a.startswith(ph + " ") for a in achados):    # "clipa" dentro de "clipa isso": um evento só
                    continue
                if _match_at(words, i, ph):
                    achados.append(ph)
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
