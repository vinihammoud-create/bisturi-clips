"""Cadeia de tratamento de voz (A2) e ducking automático por sidechain (A3) para gravações multipista do OBS.

Quando o vídeo tem faixas separadas (ex.: Faixa 2 = Microfone, Faixa 3 = Jogo) e o modo escolhido mantém a voz
(`criador_live`), permite:
  1. Limpar a faixa da voz (`filtro_tratamento_voz`): corte de graves abaixo de 90 Hz, redução suave de ruído de
     fundo (`afftdn`), compressor 3:1 e presença em 3 kHz;
  2. Baixar automaticamente o volume do jogo (`filtro_ducking_sidechain`) em 4 a 6 dB enquanto o streamer fala,
     voltando em 280 ms quando ele termina a frase.
"""
from __future__ import annotations


def filtro_tratamento_voz(ganho_db: float = 3.5) -> str:
    """Cadeia FFmpeg para faixa isolada de microfone (A2)."""
    return (
        "highpass=f=90,"
        "afftdn=nf=-25,"
        "acompressor=threshold=-18dB:ratio=3:attack=10:release=120:makeup=2,"
        "equalizer=f=3000:t=q:w=1.2:g=2.5,"
        "equalizer=f=6500:t=q:w=2.0:g=-2.0,"
        f"volume={ganho_db:.1f}dB"
    )


def filtro_mix_voz_e_jogo_simples(idx_voz: int, idx_jogo: int) -> str:
    """D-G1 (decisão do dono, 08/11/2026): o corte leva microfone + jogo SEM o Discord, sem limpar a voz e sem abaixar o
    jogo. É a mistura da live (faixa 1) menos a faixa do Discord. Saída em `[a_mix]`, como a versão com tratamento."""
    base = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    return (f"[0:a:{idx_voz}]{base}[voz_in];[0:a:{idx_jogo}]{base}[jogo_in];"
            "[voz_in][jogo_in]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[a_mix]")


def filtro_mix_voz_e_jogo_com_ducking(
    idx_voz: int = 1,
    idx_jogo: int = 2,
    ganho_voz_db: float = 3.5,
) -> str:
    """Monta o grafo `-filter_complex` que trata a voz (`0:a:{idx_voz}`) e aplica ducking sobre o jogo (`0:a:{idx_jogo}`)
    entregando a saída combinada em `[a_mix]` (A2 + A3)."""
    voz = filtro_tratamento_voz(ganho_voz_db)
    return (
        f"[0:a:{idx_voz}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,{voz},asplit=2[voz_out][voz_key];"
        f"[0:a:{idx_jogo}]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[jogo_in];"
        "[jogo_in][voz_key]sidechaincompress=threshold=0.03:ratio=4:attack=15:release=280:makeup=1[jogo_duck];"
        "[voz_out][jogo_duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[a_mix]"
    )

