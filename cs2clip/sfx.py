"""Efeitos sonoros abertos CC0 1.0 (V6) para transições e headshots decisivos.

Os arquivos `.wav` (48 kHz, estéreo, 16 bits) são gerados matematicamente com NumPy em `cs2clip/assets/sfx/`
na primeira vez que são pedidos (domínio público CC0 1.0, sem risco de Content ID). Se a pasta do programa não for
gravável, o plano B usa `<BISTURI_DADOS>/cache/sfx` (mesmo conteúdo):
  * `whoosh_transicao.wav` (150 ms): varredura suave de espectro para emendas rápidas
  * `impacto_hs.wav` (220 ms): sub-bass cinematográfico (62 Hz -> 32 Hz) + transiente curto para abate decisivo
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np
from .tools import substituir_arquivo

PASTA_SFX = Path(__file__).resolve().parent / "assets" / "sfx"
SR_SFX = 48000


class SfxIndisponivelError(RuntimeError):
    """Nenhuma pasta onde gravar (ou ler) os efeitos sonoros: a do programa e a de dados falharam ou não existem."""


def _pasta_dados() -> Path | None:
    """Pasta de dados da pessoa (`BISTURI_DADOS`), ou None se a variável não existe ou está vazia."""
    bruto = os.environ.get("BISTURI_DADOS", "").strip()
    return Path(bruto).resolve() if bruto else None


def _gravar_wav_estereo(caminho: Path, esq: np.ndarray, dir_: np.ndarray, sr: int = SR_SFX) -> Path:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    inter = np.empty(len(esq) * 2, dtype=np.int16)
    inter[0::2] = np.clip(esq * 32767.0, -32768, 32767).astype(np.int16)
    inter[1::2] = np.clip(dir_ * 32767.0, -32768, 32767).astype(np.int16)
    tmp = caminho.with_suffix(".wav.tmp")
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(inter.tobytes())
    substituir_arquivo(tmp, caminho)
    return caminho


def garantir_sfx(pasta: Path | None = None) -> dict[str, Path]:
    """Garante os 4 `.wav` (`whoosh_transicao`, `impacto_hs`, `riser_clutch`, `bass_drop`) e devolve onde estão.

    Com `pasta` explícita, usa só ela (erro de escrita sobe como está). Sem `pasta`: tenta `PASTA_SFX` (a do programa);
    se não der para gravar lá (instalado em pasta só de leitura), usa `<BISTURI_DADOS>/cache/sfx`. Sem essa variável,
    ou se também falhar, levanta `SfxIndisponivelError`. O conteúdo é o mesmo nos dois lugares (determinístico)."""
    if pasta is not None:
        return _garantir_em(pasta)
    try:
        return _garantir_em(PASTA_SFX)
    except OSError as erro_programa:
        dados = _pasta_dados()
        if dados is None:
            raise SfxIndisponivelError(
                f"Não consegui gravar os efeitos sonoros na pasta do programa ({PASTA_SFX}): {erro_programa}. "
                "Defina a variável de ambiente BISTURI_DADOS com uma pasta onde o programa possa gravar."
            ) from erro_programa
        alternativa = dados / "cache" / "sfx"
        try:
            return _garantir_em(alternativa)
        except OSError as erro_dados:
            raise SfxIndisponivelError(
                f"Não consegui gravar os efeitos sonoros nem na pasta do programa ({PASTA_SFX}) "
                f"nem em {alternativa}: {erro_dados}."
            ) from erro_dados


def _garantir_em(pasta: Path) -> dict[str, Path]:
    """Gera em `pasta` o que faltar dos 4 `.wav` (os que já existem não são reescritos)."""
    pasta.mkdir(parents=True, exist_ok=True)
    whoosh = pasta / "whoosh_transicao.wav"
    impacto = pasta / "impacto_hs.wav"
    riser = pasta / "riser_clutch.wav"
    bass_drop = pasta / "bass_drop.wav"

    if not whoosh.is_file():
        dur = 0.15
        n = int(dur * SR_SFX)
        t = np.linspace(0.0, dur, n, endpoint=False)
        rng = np.random.default_rng(42)
        ruido = rng.standard_normal(n).astype(np.float32)
        # Filtro passa-faixa dinâmico + varredura tonal suave (280 Hz -> 90 Hz)
        env = np.sin(np.pi * (t / dur)) ** 2.2
        fase = 2.0 * np.pi * (280.0 * t - (95.0 / dur) * (t ** 2))
        sinal = (0.35 * np.sin(fase) + 0.25 * np.convolve(ruido, np.ones(32) / 32.0, mode="same")) * env
        # Leve movimento estéreo (esquerda -> direita)
        pan = np.linspace(0.35, 0.65, n)
        _gravar_wav_estereo(whoosh, sinal * (1.0 - pan), sinal * pan)

    if not impacto.is_file():
        dur = 0.22
        n = int(dur * SR_SFX)
        t = np.linspace(0.0, dur, n, endpoint=False)
        # Sub-bass drop (64 Hz -> 30 Hz) + ataque curto em 1.8 kHz
        fase_sub = 2.0 * np.pi * (64.0 * t - (75.0 * (t ** 2)))
        sub = np.sin(fase_sub) * np.exp(-t * 14.0)
        clique = np.sin(2.0 * np.pi * 1800.0 * t) * np.exp(-t * 95.0)
        sinal = 0.75 * sub + 0.20 * clique
        _gravar_wav_estereo(impacto, sinal, sinal)

    if not riser.is_file():
        dur = 1.20
        n = int(dur * SR_SFX)
        t = np.linspace(0.0, dur, n, endpoint=False)
        # Subida de tensão exponencial (110 Hz -> 780 Hz) para abertura de clutch / momento decisivo
        fase = 2.0 * np.pi * (110.0 * t + (280.0 / dur) * (t ** 2.4))
        env = ((t / dur) ** 2.0) * np.sin(np.pi * np.minimum(1.0, (dur - t) * 20.0 + 0.05))
        sinal = 0.55 * np.sin(fase) * env
        _gravar_wav_estereo(riser, sinal * 0.9, sinal)

    if not bass_drop.is_file():
        dur = 0.60
        n = int(dur * SR_SFX)
        t = np.linspace(0.0, dur, n, endpoint=False)
        # Queda sub-grave cinematográfica (82 Hz -> 28 Hz) para início de montagem ou clímax Top 1
        fase = 2.0 * np.pi * (82.0 * t - (45.0 / dur) * (t ** 2))
        sinal = 0.80 * np.sin(fase) * np.exp(-t * 4.5)
        _gravar_wav_estereo(bass_drop, sinal, sinal)

    return {"whoosh": whoosh, "impacto": impacto, "riser": riser, "bass_drop": bass_drop}

