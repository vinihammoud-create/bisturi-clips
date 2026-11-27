"""Testes da montagem estilo esports (E1 + A1 + C2 + A5): cs2clip/montagem.py."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from cs2clip import qc
from cs2clip.config import Config
from cs2clip.montagem import (
    alinhar_na_batida,
    batidas_do_audio,
    escolher_musica,
    n_faixas_audio,
    render_montagem,
    trilha_jogo,
)
from cs2clip.tools import FFMPEG

# Arquivo inteiro: toda a cobertura aqui depende de render/decodificacao real com o ffmpeg.
pytestmark = pytest.mark.lento


def _gerar_corte(dest: Path, dur: float = 2.0, freq: int = 440, duas_faixas: bool = False) -> Path:
    """Gera um corte 1920x1080 60fps com 1 ou 2 faixas de áudio diferentes."""
    cmd = [
        FFMPEG, "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=60:duration={dur}",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
    ]
    if duas_faixas:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq * 2}:duration={dur}"]
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0"]
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return dest


def _gerar_musica_120bpm(dest: Path, dur: float = 6.0, sr: int = 11025) -> Path:
    """Gera uma batida sintética exata a 120 BPM (um pulso a cada 0,5 s)."""
    t = np.arange(int(dur * sr)) / sr
    sinal = np.zeros_like(t, dtype=np.float32)
    for pulso in np.arange(0.25, dur, 0.5):
        env = np.exp(-np.maximum(0.0, t - pulso) * 35.0) * (t >= pulso)
        sinal += 0.7 * np.sin(2 * np.pi * 110.0 * t) * env
    pcm = np.clip(sinal * 32767, -32768, 32767).astype(np.int16)
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0", "-c:a", "pcm_s16le", str(dest)],
        input=pcm.tobytes(),
        capture_output=True,
    )
    assert r.returncode == 0
    return dest


def test_batidas_do_audio_acha_120_bpm_e_alinha_cortes():
    sr = 11025
    dur = 8.0
    t = np.arange(int(dur * sr)) / sr
    sinal = np.zeros_like(t, dtype=np.float32)
    for pulso in np.arange(0.25, dur, 0.5):   # 120 BPM -> período de 0,5 s
        env = np.exp(-np.maximum(0.0, t - pulso) * 40.0) * (t >= pulso)
        sinal += 0.8 * np.sin(2 * np.pi * 120.0 * t) * env
    b = batidas_do_audio(sinal, sr)
    assert len(b) >= 10
    periodo = float(np.median(np.diff(b)))
    assert abs(periodo - 0.5) < 0.03

    grade = np.arange(0.5, 10.0, 0.5)
    ajustadas = alinhar_na_batida([2.3, 2.2, 2.0], grade, mistura=0.0)
    # 1ª emenda cai exatamente em 2,0 s (múltiplo de 0,5 s); último corte não é encurtado
    assert abs(ajustadas[0] - 2.0) < 1e-6
    assert abs((ajustadas[0] + ajustadas[1]) - 4.0) < 1e-6
    assert abs(ajustadas[2] - 2.0) < 1e-6


def test_trilha_jogo_detecta_faixas_iguais_ou_separadas(tmp_path):
    # Vídeo onde faixa 1 e faixa 3 têm o MESMO áudio (armadilha do OBS)
    iguais = tmp_path / "iguais.mp4"
    subprocess.run([
        FFMPEG, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-map", "0:v:0", "-map", "1:a:0", "-map", "1:a:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(iguais),
    ], check=True, capture_output=True)
    cfg = Config()
    cfg.audio.faixa_jogo = 3
    idx, motivo = trilha_jogo(str(iguais), cfg)
    assert idx is None and "igual à faixa 1" in motivo

    # Vídeo onde faixa 3 tem som diferente da faixa 1
    separadas = tmp_path / "separadas.mp4"
    subprocess.run([
        FFMPEG, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
        "-map", "0:v:0", "-map", "1:a:0", "-map", "1:a:0", "-map", "2:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(separadas),
    ], check=True, capture_output=True)
    idx2, motivo2 = trilha_jogo(str(separadas), cfg)
    assert idx2 == 2 and motivo2 == ""


def test_render_montagem_corte_seco_e_com_musica(tmp_path):
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    cfg.montagem.transicao = 0.12
    cfg.montagem.transicao_tipo = "corte"

    c1 = _gerar_corte(tmp_path / "c1.mp4", dur=2.24, freq=440, duas_faixas=True)
    c2 = _gerar_corte(tmp_path / "c2.mp4", dur=2.24, freq=660, duas_faixas=True)
    assert n_faixas_audio(c1) == 2

    mus_dir = tmp_path / "musicas"
    mus_dir.mkdir()
    mus = _gerar_musica_120bpm(mus_dir / "teste.wav", dur=6.0)
    # A-012 (26/11/2026): sem catálogo nenhuma música é sorteada; a faixa do teste entra com a licença registrada
    (mus_dir / "catalogo.json").write_text('{"faixas": [{"arquivo": "teste.wav", "licenca": "CC0 1.0 (música sintética do teste)"}]}', encoding="utf-8")
    assert escolher_musica(cfg, "vod1", pasta=mus_dir) == mus

    out = tmp_path / "MONTAGEM_teste.mp4"
    resumo = render_montagem([c1, c2], out, cfg, musica=mus)
    assert "com música teste.wav" in resumo
    assert qc.verificar(out, qc.Esperado(1920, 1080, None, True, 60.0)) == []

    # Se os cortes tiverem só 1 faixa (vídeo antigo com voz misturada), ignora a música
    c1_antigo = _gerar_corte(tmp_path / "a1.mp4", dur=2.0, freq=440, duas_faixas=False)
    c2_antigo = _gerar_corte(tmp_path / "a2.mp4", dur=2.0, freq=660, duas_faixas=False)
    out_antigo = tmp_path / "MONTAGEM_antigo.mp4"
    resumo_antigo = render_montagem([c1_antigo, c2_antigo], out_antigo, cfg, musica=mus)
    assert "sem música" in resumo_antigo
    assert qc.verificar(out_antigo, qc.Esperado(1920, 1080, None, True, 60.0)) == []
