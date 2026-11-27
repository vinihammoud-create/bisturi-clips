"""A6 (dono, 08/11/2026): medir ENERGIA e DROP de uma música, para escolher faixas emocionantes e pôr o 1º abate no drop.

O dono disse que a música "não combinou": quer algo "mais emocionante e sincronizado com o tiro". Aqui só há números medidos
no áudio (nada de gosto):

  * energia (0 a 1): volume típico da faixa nos trechos mais fortes (percentil 90 do RMS de 0,5 s), de -30 dBFS (0) a -8 dBFS (1);
  * drop: o instante em que a energia dá o maior SALTO (média dos 3 s seguintes menos a dos 3 s anteriores), em dB;
  * impacto: o tamanho desse salto em dB. Sem salto de pelo menos 4 dB a faixa não tem drop (drop = None): é uma faixa "parelha".

O catálogo antigo guardava em `drop_s` o primeiro tempo do ritmo (~2,1 s em todas as faixas): não era um drop. Este módulo o substitui.
"""
from __future__ import annotations

import numpy as np

PASSO_S = 0.5                 # resolução da curva de energia
JANELA_S = 3.0                # média antes/depois do candidato a drop
IMPACTO_MIN_DB = 4.0          # salto menor que isto = faixa sem drop
BORDA_S = 4.0                 # não procura drop nos primeiros/últimos segundos (fade in/out)
PISO_DB, TETO_DB = -30.0, -8.0


def curva_rms_db(audio: np.ndarray, sr: int, passo_s: float = PASSO_S) -> np.ndarray:
    """RMS em dBFS a cada `passo_s` segundos (áudio mono, -1 a 1)."""
    n = max(1, int(sr * passo_s))
    quadros = len(audio) // n
    if quadros == 0:
        return np.zeros(0, np.float32)
    x = audio[: quadros * n].astype(np.float32).reshape(quadros, n)
    rms = np.sqrt(np.mean(x ** 2, axis=1))
    return (20.0 * np.log10(rms + 1e-9)).astype(np.float32)


def energia_da_faixa(db: np.ndarray) -> float:
    """0 a 1: percentil 90 do nível (os trechos fortes da faixa), do piso ao teto."""
    if not len(db):
        return 0.0
    p90 = float(np.percentile(db, 90))
    return round(float(np.clip((p90 - PISO_DB) / (TETO_DB - PISO_DB), 0.0, 1.0)), 3)


def achar_drop(db: np.ndarray, passo_s: float = PASSO_S) -> tuple[float | None, float]:
    """(instante do drop em s ou None, impacto em dB). O drop é onde a média dos próximos `JANELA_S` segundos mais sobe em
    relação à dos anteriores; o impacto é essa subida."""
    k = max(1, int(round(JANELA_S / passo_s)))
    b = int(round(BORDA_S / passo_s))
    if len(db) < 2 * k + 2 * b:
        return None, 0.0
    # energia média em potência (não em dB), para o salto não ser inflado por silêncio quase digital
    pot = 10 ** (db / 10.0)
    soma = np.concatenate([[0.0], np.cumsum(pot)])
    melhor_t, melhor = None, 0.0
    for i in range(max(k, b), len(db) - max(k, b) + 1):
        antes = (soma[i] - soma[i - k]) / k
        depois = (soma[i + k] - soma[i]) / k
        salto = 10.0 * np.log10((depois + 1e-12) / (antes + 1e-12))
        if salto > melhor:
            melhor_t, melhor = i * passo_s, float(salto)
    if melhor_t is None or melhor < IMPACTO_MIN_DB:
        return None, round(melhor, 1)
    return round(melhor_t, 2), round(melhor, 1)


def analisar(audio: np.ndarray, sr: int) -> dict:
    """{"energia", "drop_s" (None = sem drop), "impacto_db", "emocao"} de uma música (áudio mono, -1 a 1).
    `emocao` (0 a 1) = metade energia, metade impacto (12 dB de salto = máximo): é o que a escolha automática prefere."""
    db = curva_rms_db(audio, sr)
    energia = energia_da_faixa(db)
    drop, impacto = achar_drop(db)
    emocao = round(0.5 * energia + 0.5 * min(1.0, impacto / 12.0), 3)
    return {"energia": energia, "drop_s": drop, "impacto_db": impacto, "emocao": emocao}


def medir_arquivo(musica, sr: int = 11025) -> dict:
    """Decodifica a música (ffmpeg, mono) e devolve `analisar`. Levanta RuntimeError se o ffmpeg não conseguir ler."""
    import subprocess
    from pathlib import Path

    from .tools import FFMPEG, sem_janela
    r = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-i", str(Path(musica)), "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "pipe:1"], capture_output=True, **sem_janela())
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"não consegui ler a música {Path(musica).name}: {r.stderr.decode('utf-8', 'replace')[-200:]}")
    return analisar(np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0, sr)
