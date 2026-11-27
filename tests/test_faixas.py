"""B1 (Plano V7 integrado): diagnóstico das faixas de áudio de uma gravação."""
import subprocess

import numpy as np
import pytest

from cs2clip import faixas as fx
from cs2clip.tools import FFMPEG


def _f(ordem, rms=-30.0, fala=None, igual=False):
    return fx.Faixa(ordem, rms_db=rms, fala=fala, igual_faixa1=igual)


def _papeis(lista):
    fx.sugerir_papeis(lista)
    return [f.papel for f in lista]


def test_faixa_igual_a_1_e_duplicada_e_muda_e_muda():
    assert _papeis([_f(1, fala=0.3), _f(2, igual=True, fala=0.3), _f(3, rms=-90.0)]) == ["mistura", "duplicada", "muda"]


def test_obs_bem_configurado_mistura_jogo_e_voz():
    assert _papeis([_f(1, fala=0.4), _f(2, fala=0.02), _f(3, fala=0.5)]) == ["mistura", "jogo", "voz"]


def test_duas_faixas_com_fala_pedem_confirmacao_de_qual_e_o_mic_e_o_discord():
    lista = [_f(1, fala=0.4), _f(2, fala=0.02), _f(3, fala=0.5), _f(4, fala=0.4)]
    fx.sugerir_papeis(lista)
    assert any("confirme qual é o microfone" in a for a in lista[2].avisos) and lista[1].avisos == []


def test_sem_faixa_de_jogo_avisa_que_a_montagem_com_musica_precisa_de_uma():
    lista = [_f(1, fala=0.4), _f(2, igual=True, fala=0.4)]
    fx.sugerir_papeis(lista)
    assert any("só o jogo" in a for a in lista[0].avisos)


def test_fala_nao_medida_nao_inventa_papel():
    assert _papeis([_f(1), _f(2, fala=None)]) == ["mistura", "desconhecida"]


def test_medir_volume_e_silencio():
    f = fx.Faixa(1)
    a = np.concatenate([np.zeros(fx.SR), 0.5 * np.sin(np.linspace(0, 2000, fx.SR))]).astype(np.float32)
    fx.medir(f, a)
    assert -13 < f.rms_db < -11 and f.pico_db > -7 and 0.4 < f.silencio < 0.6


def test_comparar_com_faixa1_acha_a_copia_exata_e_a_diferente():
    base = (0.3 * np.random.default_rng(1).standard_normal(fx.SR * 2)).astype(np.float32)
    copia, outra = fx.Faixa(2), fx.Faixa(3)
    fx.comparar_com_faixa1(copia, base.copy(), base)
    fx.comparar_com_faixa1(outra, (0.3 * np.random.default_rng(2).standard_normal(fx.SR * 2)).astype(np.float32), base)
    assert copia.igual_faixa1 and copia.corr_faixa1 > 0.999 and not outra.igual_faixa1 and abs(outra.corr_faixa1) < 0.2


def test_texto_em_portugues_lista_cada_faixa():
    lista = [_f(1, fala=0.4), _f(2, igual=True, fala=0.4)]
    fx.sugerir_papeis(lista)
    t = fx.texto("v.mp4", lista)
    assert "2 faixa(s)" in t and "DUPLICADA" in t and "IGUAL à faixa 1" in t and "pista, não uma certeza" in t
    assert "nenhuma faixa de áudio" in fx.texto("v.mp4", [])


@pytest.mark.lento
def test_video_de_verdade_com_tres_faixas_iguais_e_tres_diferentes(tmp_path):
    def video(nome, fontes):
        cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=8"]
        for f in fontes:
            cmd += ["-f", "lavfi", "-i", f]
        cmd += ["-map", "0:v"] + [x for i in range(len(fontes)) for x in ("-map", f"{i + 1}:a")]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(tmp_path / nome)]
        subprocess.run(cmd, check=True, capture_output=True)
        return str(tmp_path / nome)
    mesma = "sine=f=440:d=8"
    iguais = fx.analisar(video("iguais.mp4", [mesma, mesma, mesma]), janela_s=6, com_fala=False)
    assert [f.papel for f in iguais] == ["mistura", "duplicada", "duplicada"]
    dif = fx.analisar(video("dif.mp4", ["sine=f=440:d=8", "anoisesrc=a=0.2:d=8", "sine=f=900:d=8,volume=-90dB"]), janela_s=6, com_fala=False)
    assert not dif[1].igual_faixa1 and dif[2].papel == "muda"


# ── B2: voz, Whisper e emoção leem a faixa do microfone quando ela existe e presta ───────────────────

def _pcm_falso(monkeypatch, mix, voz, n=3):
    monkeypatch.setattr(fx, "n_faixas_audio", lambda src: n)
    monkeypatch.setattr(fx, "_duracao", lambda src: 120.0)
    monkeypatch.setattr(fx, "_pcm", lambda src, faixa, ini, dur, sr=8000: mix if faixa == 0 else voz)


def test_faixa_de_voz_desligada_ou_mistura_le_a_mistura(monkeypatch):
    _pcm_falso(monkeypatch, np.ones(100, np.float32), np.ones(100, np.float32))
    assert fx.faixa_de_voz("v.mp4", 0) == (None, "") and fx.faixa_de_voz("v.mp4", 1) == (None, "")


def test_faixa_de_voz_valida_usa_a_faixa_pedida(monkeypatch):
    rng = np.random.default_rng(3)
    _pcm_falso(monkeypatch, 0.3 * rng.standard_normal(8000).astype(np.float32), 0.3 * rng.standard_normal(8000).astype(np.float32))
    assert fx.faixa_de_voz("v.mp4", 3) == (2, "")                 # faixa 3 = índice 2


@pytest.mark.parametrize("n,mix,voz,trecho", [
    (2, np.ones(100, np.float32), np.ones(100, np.float32), "não tem a faixa 3"),
    (3, np.ones(100, np.float32), np.zeros(100, np.float32), "está muda"),
    (3, np.full(100, 0.3, np.float32), np.full(100, 0.3, np.float32), "igual à faixa 1"),
])
def test_faixa_de_voz_ruim_volta_para_a_mistura_e_explica(monkeypatch, n, mix, voz, trecho):
    _pcm_falso(monkeypatch, mix, voz, n)
    idx, aviso = fx.faixa_de_voz("v.mp4", 3)
    assert idx is None and trecho in aviso and "a voz vem da mistura" in aviso


def test_cache_de_voz_so_vale_se_foi_feito_com_a_mesma_faixa(tmp_path):
    import json
    from cs2clip.pipeline import _faixa_do_cache_confere
    meta = tmp_path / "voz.meta.json"
    assert _faixa_do_cache_confere(meta, None)                    # cache antigo, sem meta = mistura: continua valendo
    assert not _faixa_do_cache_confere(meta, 2)                   # agora lê a faixa 3: precisa refazer
    meta.write_text(json.dumps({"faixa": 2}), encoding="utf-8")
    assert _faixa_do_cache_confere(meta, 2) and not _faixa_do_cache_confere(meta, None) and not _faixa_do_cache_confere(meta, 1)
    meta.write_text("{ilegivel", encoding="utf-8")
    assert not _faixa_do_cache_confere(meta, None)                # meta estragado: refaz por segurança


@pytest.mark.lento
def test_load_audio_le_a_faixa_pedida(tmp_path):
    from cs2clip import audio as aud
    arq = tmp_path / "duas.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=4",
                    "-f", "lavfi", "-i", "sine=f=500:d=4", "-f", "lavfi", "-i", "sine=f=2000:d=4",
                    "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(arq)], check=True, capture_output=True)

    def freq(a):
        e = np.abs(np.fft.rfft(a.astype(np.float32)))
        return float(np.argmax(e)) * aud.SR / len(a)
    assert abs(freq(aud.load_audio(str(arq))) - 500) < 30                    # padrão = 1ª faixa
    assert abs(freq(aud.load_audio(str(arq), faixa=0)) - 500) < 30
    assert abs(freq(aud.load_audio(str(arq), faixa=1)) - 2000) < 30          # a 2ª faixa de verdade
