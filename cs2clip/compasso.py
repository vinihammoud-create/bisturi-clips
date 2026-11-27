"""Compasso e frases da música (C1–C3, Plano V7 integrado): além da batida, qual batida é o "1" do compasso (downbeat)
e onde começam as frases (4 ou 8 compassos). A montagem pode então alinhar tiros e emendas ao compasso, em vez de a
qualquer batida.

Honestidade: o estimador automático só SUGERE. Em música eletrônica com bumbo em todas as batidas o "1" é ambíguo, e
aí a confiança sai baixa e o programa volta para a grade de batidas de sempre. O dono pode fixar o "1" à mão no
`musicas/catalogo.json` (campo `downbeat_s`, com `downbeat_fonte = "manual"`), que vale mais que a estimativa."""
from __future__ import annotations

from pathlib import Path

import numpy as np

CONFIANCA_MINIMA = 0.30      # abaixo disso a estimativa não é usada (volta para a grade de batidas)
JANELA_ANTES = 0.02          # s antes da batida na medida de energia
JANELA_DEPOIS = 0.12         # s depois da batida (o bumbo e o ataque vivem aqui)


def _energia_grave(a: np.ndarray, sr: int) -> np.ndarray:
    """Envelope de energia da região grave (< 150 Hz, onde mora o bumbo): filtro por FFT + média curta."""
    x = a.astype(np.float32)
    espectro = np.fft.rfft(x)
    espectro[int(150.0 * len(x) / sr) + 1:] = 0
    grave = np.fft.irfft(espectro, n=len(x)).astype(np.float32)
    k = max(1, int(0.01 * sr))
    return np.convolve(grave * grave, np.ones(k, np.float32) / k, mode="same")


def _ataque(a: np.ndarray, sr: int) -> np.ndarray:
    """Envelope de ataque amplo (derivada positiva da energia suavizada)."""
    k = max(1, int(0.01 * sr))
    e = np.convolve(a.astype(np.float32) ** 2, np.ones(k, np.float32) / k, mode="same")
    return np.maximum(np.diff(e, prepend=e[:1]), 0.0)


def estimar_fase(a: np.ndarray, sr: int, batidas_s: np.ndarray, por_compasso: int = 4) -> tuple[int, float]:
    """(fase, confiança 0–1). `fase` = quantas batidas depois da primeira cai o "1" do compasso (0 a por_compasso-1).
    Pontua cada fase pela energia grave + ataque média nas batidas dela; a fase vencedora precisa se destacar."""
    n = len(batidas_s)
    if por_compasso < 2 or n < por_compasso * 4 or len(a) < sr:
        return 0, 0.0
    grave, ataque = _energia_grave(a, sr), _ataque(a, sr)
    forca = np.zeros(n, np.float64)
    for i, t in enumerate(batidas_s):
        ini, fim = max(0, int((t - JANELA_ANTES) * sr)), min(len(a), int((t + JANELA_DEPOIS) * sr))
        if fim > ini:
            forca[i] = float(grave[ini:fim].mean()) + 0.5 * float(ataque[ini:fim].mean())
    if forca.max() <= 0:
        return 0, 0.0
    forca /= forca.max()
    notas = np.array([forca[p::por_compasso].mean() for p in range(por_compasso)])
    ordem = np.argsort(notas)[::-1]
    melhor, segundo = float(notas[ordem[0]]), float(notas[ordem[1]])
    return int(ordem[0]), float(np.clip((melhor - segundo) / max(melhor, 1e-9), 0.0, 1.0))


def tempos_fortes(batidas_s: np.ndarray, fase: int, por_compasso: int = 4) -> np.ndarray:
    """Os instantes dos "1" de cada compasso (downbeats)."""
    return np.asarray(batidas_s)[fase % max(1, por_compasso)::max(1, por_compasso)]


def inicios_de_frase(downbeats: np.ndarray, compassos_por_frase: int = 4) -> np.ndarray:
    """Os instantes em que uma frase de 4 (ou 8) compassos recomeça."""
    return np.asarray(downbeats)[::max(1, compassos_por_frase)]


def fase_pelo_catalogo(batidas_s: np.ndarray, downbeat_s: float, por_compasso: int = 4) -> int | None:
    """A fase que faz o "1" cair mais perto de `downbeat_s` (marcado à mão no catálogo)."""
    if not len(batidas_s) or por_compasso < 1:
        return None
    mais_perto = int(np.argmin(np.abs(np.asarray(batidas_s) - downbeat_s)))
    return mais_perto % por_compasso


def entrada_do_catalogo(musica: Path, pasta: Path | None = None, pasta_pessoa: Path | None = None) -> dict | None:
    """A linha do catálogo efetivo desta música (fábrica com os ajustes da pessoa, mais as faixas da pessoa), ou None.
    `pasta`: a de fábrica (sem ela, as pastas saem do lugar da música: `musicas_catalogo.pastas_da_musica`)."""
    from .musicas_catalogo import catalogo_efetivo, pasta_da_pessoa, pastas_da_musica
    fabrica, pessoa = (Path(pasta), pasta_da_pessoa(pasta, pasta_pessoa)) if pasta is not None else pastas_da_musica(musica)
    try:
        faixas = catalogo_efetivo(fabrica, pessoa).get("faixas", [])
    except (OSError, ValueError, AttributeError):
        return None
    return next((f for f in faixas if isinstance(f, dict) and f.get("arquivo") == Path(musica).name), None)


def grade_de_compasso(musica: Path, a: np.ndarray, sr: int, batidas_s: np.ndarray) -> tuple[np.ndarray, str]:
    """(downbeats, fonte). Ordem: `downbeat_s` manual do catálogo > estimativa com confiança suficiente > vazio
    (a montagem então usa a grade de batidas). `fonte` explica de onde veio, em português curto."""
    ent = entrada_do_catalogo(musica) or {}
    por = int(ent.get("beats_per_bar") or 4)
    if ent.get("downbeat_fonte") == "manual" and ent.get("downbeat_s") is not None:
        fase = fase_pelo_catalogo(batidas_s, float(ent["downbeat_s"]), por)
        if fase is not None:
            return tempos_fortes(batidas_s, fase, por), "compasso marcado à mão"
    fase, conf = estimar_fase(a, sr, batidas_s, por)
    if conf >= CONFIANCA_MINIMA:
        return tempos_fortes(batidas_s, fase, por), f"compasso estimado (confiança {conf:.0%})"
    return np.zeros(0), f"compasso incerto (confiança {conf:.0%}): usei a grade de batidas"
