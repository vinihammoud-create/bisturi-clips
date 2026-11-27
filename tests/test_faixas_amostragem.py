"""Correção de 06/11/2026 (achada nas gravações reais do OBS): 'muda' não pode ser julgada por uma janela só do meio.
Nas gravações de 02–05/11 o microfone tinha sinal em apenas 1 de 14 trechos quando o dono jogou calado; com uma janela só,
o programa trataria a faixa de voz como muda e voltaria à mistura por engano. Também: o nome que o dono deu à faixa no OBS
("Mix (live)", "Voz", "Jogo", "Discord") é a melhor pista de papel."""
import subprocess

import numpy as np
import pytest

from cs2clip import faixas as fx
from cs2clip.tools import FFMPEG


# ── papel pelo nome da faixa no OBS ───────────────────────────────────────────────────────────────

def _f(ordem, titulo="", rms=-30.0, igual=False, fala=None):
    return fx.Faixa(ordem, titulo=titulo, rms_db=rms, igual_faixa1=igual, fala=fala)


def test_nomes_do_obs_do_dono_viram_os_papeis_certos():
    lista = [_f(1, "Mix (live)"), _f(2, "Voz", fala=0.02), _f(3, "Jogo", fala=0.5), _f(4, "Discord", rms=-120.0)]
    fx.sugerir_papeis(lista)
    assert [f.papel for f in lista] == ["mistura", "voz", "jogo", "comms"]            # o nome vale mais que a fala medida
    assert "nome da faixa no OBS" in lista[1].pista and "Voz" in lista[1].pista


@pytest.mark.parametrize("nome,papel", [("Microfone", "voz"), ("MIC", "voz"), ("Game Audio", "jogo"), ("Gameplay", "jogo"),
                                        ("Comunicação", "comms"), ("Chat de voz Discord", "comms"), ("Áudio do Jogo", "jogo")])
def test_variacoes_de_nome_sao_reconhecidas(nome, papel):
    lista = [_f(1, "Mix"), _f(2, nome)]
    fx.sugerir_papeis(lista)
    assert lista[1].papel == papel


def test_nome_desconhecido_cai_na_adivinhacao_pela_fala():
    lista = [_f(1), _f(2, "Faixa 2", fala=0.02), _f(3, "Track3", fala=0.6)]
    fx.sugerir_papeis(lista)
    assert [f.papel for f in lista] == ["mistura", "jogo", "voz"]


def test_faixa_de_nome_voz_que_e_copia_da_mistura_e_duplicada_e_nao_voz():
    lista = [_f(1, "Mix"), _f(2, "Voz", igual=True)]
    fx.sugerir_papeis(lista)
    assert lista[1].papel == "duplicada"


def test_configuracao_do_dono_continua_valendo_mais_que_o_nome():
    lista = [_f(1, "Mix"), _f(2, "Voz"), _f(3, "Jogo")]
    fx.sugerir_papeis(lista, jogo=2, voz=3)                                           # o dono inverteu de propósito
    assert (lista[1].papel, lista[2].papel) == ("jogo", "voz")


def test_faixa_com_nome_mas_sem_sinal_mantem_o_papel_e_avisa():
    f = _f(4, "Discord", rms=-120.0)
    f.janelas, f.janelas_com_sinal = 8, 0
    lista = [_f(1, "Mix"), f]
    fx.sugerir_papeis(lista)
    assert f.papel == "comms" and any("nenhum dos 8 trechos" in a for a in f.avisos)


# ── 'muda' só quando não há sinal em NENHUM trecho amostrado ──────────────────────────────────────

def test_estado_muda_so_sem_sinal_em_todos_os_trechos():
    ok = _f(2, rms=-45.0)
    ok.janelas, ok.janelas_com_sinal = 8, 1                                             # falou em 1 de 8: faixa viva
    assert fx._estado(ok) == "ok"
    sem = _f(3, rms=-120.0)
    sem.janelas, sem.janelas_com_sinal = 8, 0
    assert fx._estado(sem) == "muda"
    antigo = _f(4, rms=-120.0)                                                          # sem amostragem: vale o volume, como antes
    assert fx._estado(antigo) == "muda"


def _pcm_falso(monkeypatch, onde_a_voz_fala):
    """Mistura sempre com som; a faixa 3 só tem sinal depois de `onde_a_voz_fala` segundos da gravação (200 s)."""
    rng = np.random.default_rng(0)
    monkeypatch.setattr(fx, "n_faixas_audio", lambda src: 3)
    monkeypatch.setattr(fx, "_duracao", lambda src: 200.0)

    def pcm(src, faixa, ini, dur, sr=8000):
        n = int(dur * sr)
        if faixa == 0:
            return (0.1 * rng.standard_normal(n)).astype(np.float32)
        if faixa == 2:
            return (0.1 * rng.standard_normal(n)).astype(np.float32) if ini >= onde_a_voz_fala else np.zeros(n, np.float32)
        return (0.1 * rng.standard_normal(n)).astype(np.float32)
    monkeypatch.setattr(fx, "_pcm", pcm)


def test_amostrar_acha_o_sinal_que_so_aparece_no_fim_da_gravacao(monkeypatch):
    _pcm_falso(monkeypatch, onde_a_voz_fala=150.0)
    a, com_sinal, total = fx.amostrar("v.mp4", 2, 200.0)
    assert total == fx.N_JANELAS and 1 <= com_sinal < total and len(a) > 0


def test_faixa_de_voz_nao_cai_na_mistura_so_porque_o_meio_estava_calado(monkeypatch):
    _pcm_falso(monkeypatch, onde_a_voz_fala=150.0)                                      # calado no meio, fala no fim
    idx, aviso = fx.faixa_de_voz("v.mp4", 3)
    assert idx == 2 and aviso == ""


def test_faixa_de_voz_realmente_muda_em_todos_os_trechos_cai_na_mistura(monkeypatch):
    _pcm_falso(monkeypatch, onde_a_voz_fala=10_000.0)                                   # nunca fala
    idx, aviso = fx.faixa_de_voz("v.mp4", 3)
    assert idx is None and "está muda" in aviso


def test_gravacao_curta_e_lida_inteira_numa_janela(monkeypatch):
    chamadas = []
    monkeypatch.setattr(fx, "_pcm", lambda src, faixa, ini, dur, sr=8000: chamadas.append((ini, dur)) or np.full(int(dur * sr), 0.1, np.float32))
    a, com_sinal, total = fx.amostrar("v.mp4", 0, 20.0)
    assert total == 1 and com_sinal == 1 and chamadas == [(0.0, 20.0)]


def test_texto_mostra_em_quantos_trechos_ha_sinal():
    f = _f(2, "Voz", rms=-40.0)
    f.janelas, f.janelas_com_sinal = 8, 3
    lista = [_f(1, "Mix"), f]
    fx.sugerir_papeis(lista)
    assert "sinal em 3 de 8 trechos" in fx.texto("v.mp4", lista)


@pytest.mark.lento
def test_video_de_verdade_microfone_so_fala_no_fim_nao_e_muda(tmp_path):
    v = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=5:d=200",
                    "-f", "lavfi", "-i", "sine=f=440:d=200",
                    "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*660*t)*gte(t,190)':s=48000:d=200",
                    "-map", "0:v", "-map", "1:a", "-map", "2:a", "-metadata:s:a:0", "title=Mix", "-metadata:s:a:1", "title=Voz",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(v)], check=True, capture_output=True)
    lista = fx.analisar(str(v), com_fala=False)
    voz = lista[1]
    assert voz.titulo == "Voz" and voz.papel == "voz" and fx._estado(voz) == "ok"        # a janela única do meio diria "muda"
    assert voz.janelas == fx.N_JANELAS and 1 <= voz.janelas_com_sinal < voz.janelas
