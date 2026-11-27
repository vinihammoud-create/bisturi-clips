"""S1.2 (Plano Sonnet 04/11): `audio.faixa_comms` — a faixa do Discord nunca entra no áudio montado pelo programa."""
import subprocess

import numpy as np
import pytest

from cs2clip import faixas as fx
from cs2clip import render
from cs2clip.config import Config, load_config
from cs2clip.moments import Event, Moment
from cs2clip.tools import FFMPEG


def _cfg(**opcoes) -> Config:
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    for chave, valor in opcoes.items():
        secao, nome = chave.split("__")
        setattr(getattr(cfg, secao), nome, valor)
    return cfg


# ── configuração ──────────────────────────────────────────────────────────────────────────────────

def test_faixa_comms_nasce_desligada_e_aceita_de_2_a_6():
    assert Config().audio.faixa_comms == 0


@pytest.mark.parametrize("texto,trecho", [
    ("faixa_comms = 7", "faixa_comms"),
    ("faixa_comms = -1", "faixa_comms"),
    ("faixa_comms = 1", "mistura"),                                    # a faixa 1 é a mistura, não o Discord
    ("faixa_voz = 3\nfaixa_comms = 3", "faixa_voz"),
    ("faixa_jogo = 2\nfaixa_comms = 2", "faixa_jogo"),
])
def test_config_recusa_faixa_comms_ruim_com_motivo(tmp_path, texto, trecho):
    arq = tmp_path / "c.toml"
    arq.write_text(f"[audio]\n{texto}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=trecho):
        load_config(arq)


def test_config_aceita_as_tres_faixas_diferentes(tmp_path):
    arq = tmp_path / "c.toml"
    arq.write_text("[audio]\nfaixa_jogo = 2\nfaixa_voz = 3\nfaixa_comms = 4\n", encoding="utf-8")
    a = load_config(arq).audio
    assert (a.faixa_jogo, a.faixa_voz, a.faixa_comms) == (2, 3, 4)


def test_faixa_comms_esta_na_tela_configurar():
    from ui.configuracao import CAMPOS_POR_CHAVE
    assert ("audio", "faixa_comms") in CAMPOS_POR_CHAVE


# ── avisos de configuração de faixas ──────────────────────────────────────────────────────────────

def test_aviso_quando_comms_esta_marcada_mas_falta_voz_ou_jogo():
    """D-G1: com microfone + jogo marcados o corte já sai sem Discord (sem aviso); sem eles, a mistura pode ter Discord."""
    cfg = _cfg(audio__faixa_comms=4, audio__faixa_jogo=2)                           # falta a faixa do microfone
    avisos = fx.avisos_de_configuracao(cfg.audio, 4)
    assert any("faixa_voz" in a and "Discord" in a for a in avisos)
    cfg.audio.faixa_voz = 3
    assert fx.avisos_de_configuracao(cfg.audio, 4) == []                            # microfone + jogo: sem Discord por construção


def test_aviso_quando_uma_faixa_configurada_nao_existe_no_video():
    cfg = _cfg(audio__faixa_jogo=2, audio__faixa_voz=3, audio__faixa_comms=5, audio__tratar_voz=True)
    avisos = fx.avisos_de_configuracao(cfg.audio, 4)
    assert any("faixa_comms = 5" in a and "4 faixa" in a for a in avisos)


def test_sem_nada_configurado_nao_avisa():
    assert fx.avisos_de_configuracao(Config().audio, 3) == []


# ── papel das faixas usa o que o dono configurou ──────────────────────────────────────────────────

def _f(ordem, fala=None, rms=-30.0, igual=False):
    return fx.Faixa(ordem, rms_db=rms, fala=fala, igual_faixa1=igual)


def test_faixa_marcada_como_comms_vira_comms_e_nao_pede_confirmacao_do_microfone():
    lista = [_f(1, 0.4), _f(2, 0.02), _f(3, 0.5), _f(4, 0.4)]
    fx.sugerir_papeis(lista, jogo=2, voz=3, comms=4)
    assert [f.papel for f in lista] == ["mistura", "jogo", "voz", "comms"]
    assert not any("confirme qual é o microfone" in a for f in lista for a in f.avisos)


def test_duas_faixas_com_fala_e_so_o_microfone_marcado_a_outra_vira_comms_sugerida():
    lista = [_f(1, 0.4), _f(2, 0.02), _f(3, 0.5), _f(4, 0.4)]
    fx.sugerir_papeis(lista, voz=3)
    assert lista[2].papel == "voz" and lista[3].papel == "comms" and "sugest" in lista[3].pista


def test_sem_configuracao_o_comportamento_antigo_continua():
    lista = [_f(1, 0.4), _f(2, 0.02), _f(3, 0.5), _f(4, 0.4)]
    fx.sugerir_papeis(lista)
    assert [f.papel for f in lista] == ["mistura", "jogo", "voz", "voz"]


# ── a garantia que importa: o tom do Discord não aparece no corte com voz tratada ─────────────────

HZ_MISTURA, HZ_JOGO, HZ_MIC, HZ_DISCORD = 440, 880, 1320, 1760


def _origem_quatro_faixas(destino, dur=10):
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=60:d={dur}"]
    for hz in (HZ_MISTURA, HZ_JOGO, HZ_MIC, HZ_DISCORD):
        cmd += ["-f", "lavfi", "-i", f"sine=f={hz}:d={dur}"]
    cmd += ["-map", "0:v"] + [x for i in range(4) for x in ("-map", f"{i + 1}:a")]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


def _amplitude(arq, hz):
    r = subprocess.run([FFMPEG, "-v", "error", "-i", str(arq), "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
                       capture_output=True)
    a = np.frombuffer(r.stdout, np.int16).astype(np.float32)
    e = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    centro = int(hz * len(a) / 16000)
    return float(e[max(0, centro - 8): centro + 9].max())


@pytest.mark.lento
def test_corte_com_voz_tratada_leva_microfone_e_jogo_mas_nao_o_discord(tmp_path):
    src = _origem_quatro_faixas(tmp_path / "quatro.mp4")
    cfg = _cfg(audio__tratar_voz=True, audio__faixa_voz=3, audio__faixa_jogo=2, audio__faixa_comms=4)
    cfg._trilha_jogo = 1
    m = Moment(1.0, 9.0, "abate", kills=[Event(4.0, "kill")], label="KILL", score=30.0, idx=1)
    out = tmp_path / "corte.mp4"
    render.render_clip(str(src), m, out, cfg)
    mic, jogo, discord = _amplitude(out, HZ_MIC), _amplitude(out, HZ_JOGO), _amplitude(out, HZ_DISCORD)
    assert mic > 20 * max(discord, 1.0)                    # o microfone está lá e o Discord, ~ausente
    assert jogo > 20 * max(discord, 1.0)                   # o jogo está lá


# ── D-G1 (dono, 08/11/2026): com faixa_comms marcada, o corte sai de microfone + jogo mesmo sem `tratar_voz` ─────

def _momento_corte():
    return Moment(1.0, 9.0, "abate", kills=[Event(4.0, "kill")], label="KILL", score=30.0, idx=1)


@pytest.mark.lento
def test_corte_com_comms_marcada_leva_microfone_e_jogo_sem_o_discord_nem_a_mistura(tmp_path):
    src = _origem_quatro_faixas(tmp_path / "quatro.mp4")
    cfg = _cfg(audio__faixa_voz=3, audio__faixa_jogo=2, audio__faixa_comms=4)       # tratar_voz desligado (o padrão)
    cfg._trilha_jogo = 1
    out = tmp_path / "corte.mp4"
    render.render_clip(str(src), _momento_corte(), out, cfg)
    mic, jogo, discord, mistura = (_amplitude(out, hz) for hz in (HZ_MIC, HZ_JOGO, HZ_DISCORD, HZ_MISTURA))
    assert mic > 20 * max(discord, 1.0) and jogo > 20 * max(discord, 1.0)
    assert mistura < mic / 20                          # a faixa 1 (que teria o Discord) não é mais a base do corte

    # controle: sem faixa_comms o corte é a mistura da live, como antes (o defeito que o Gemini apontou)
    cfg0 = _cfg(audio__faixa_voz=3, audio__faixa_jogo=2)
    cfg0._trilha_jogo = 1
    antigo = tmp_path / "antigo.mp4"
    render.render_clip(str(src), _momento_corte(), antigo, cfg0)
    assert _amplitude(antigo, HZ_MISTURA) > 20 * max(_amplitude(antigo, HZ_MIC), 1.0)


@pytest.mark.lento
def test_short_com_comms_marcada_tambem_sai_sem_o_discord(tmp_path):
    src = _origem_quatro_faixas(tmp_path / "quatro_s.mp4")
    cfg = _cfg(audio__faixa_voz=3, audio__faixa_jogo=2, audio__faixa_comms=4)
    cfg._trilha_jogo = 1
    out = tmp_path / "short.mp4"
    render.render_short(str(src), _momento_corte(), out, cfg, None, None)
    mic, jogo, discord = _amplitude(out, HZ_MIC), _amplitude(out, HZ_JOGO), _amplitude(out, HZ_DISCORD)
    assert mic > 20 * max(discord, 1.0) and jogo > 20 * max(discord, 1.0)


def test_sem_faixa_comms_nada_muda_no_hash_do_corte_e_com_ela_muda():
    from cs2clip.refazer import hash_corte
    base = _cfg(audio__faixa_voz=3, audio__faixa_jogo=2)
    com = _cfg(audio__faixa_voz=3, audio__faixa_jogo=2, audio__faixa_comms=4)
    assert hash_corte(base) == hash_corte(_cfg(audio__faixa_voz=3, audio__faixa_jogo=2))
    assert hash_corte(com) != hash_corte(base)         # o cache antigo (com Discord) não é reaproveitado


def test_voz_ativa_so_com_comms_exige_voz_e_jogo_validos(monkeypatch):
    monkeypatch.setattr(render, "_AVISADOS_AUDIO", set())
    monkeypatch.setattr(render, "_n_faixas", lambda src: 4)
    monkeypatch.setattr(render, "_faixa_voz_valida", lambda src, k: (k - 1, ""))
    assert render._voz_ativa(_cfg(), "v.mp4", 1) is None                            # nada pedido: como sempre
    cfg = _cfg(audio__faixa_voz=3, audio__faixa_jogo=2, audio__faixa_comms=4)
    assert render._voz_ativa(cfg, "v.mp4", 1) == (2, 1)
    assert render._voz_ativa(cfg, "v.mp4", None) is None                            # faixa do jogo inválida: mistura, com aviso
