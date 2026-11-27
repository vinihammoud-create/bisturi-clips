"""S1.3 (Plano Sonnet 04/11): perfil de faixas de cada vídeo registrado; gravação seguinte diferente = aviso, não silêncio."""
import json
import subprocess

import pytest

from cs2clip import faixas as fx
from cs2clip.config import Config
from cs2clip.tools import FFMPEG


def _f(ordem, estado="ok"):
    f = fx.Faixa(ordem, rms_db=-120.0 if estado == "muda" else -30.0)
    if estado == "duplicada":
        f.igual_faixa1 = True
    return f


def test_perfil_guarda_numero_de_faixas_estado_de_cada_uma_e_o_que_o_dono_marcou():
    cfg = Config()
    cfg.audio.faixa_jogo, cfg.audio.faixa_voz, cfg.audio.faixa_comms = 2, 3, 4
    p = fx.perfil_de_faixas([_f(1), _f(2), _f(3), _f(4, "muda")], cfg.audio)
    assert p["n"] == 4 and p["marcadas"] == {"jogo": 2, "voz": 3, "comms": 4}
    assert p["estado"] == {"1": "ok", "2": "ok", "3": "ok", "4": "muda"}


def test_gravacao_igual_a_anterior_nao_avisa():
    a = fx.perfil_de_faixas([_f(1), _f(2), _f(3)], Config().audio)
    assert fx.comparar_perfis(a, a) == []


def test_primeiro_video_sem_perfil_anterior_nao_avisa():
    assert fx.comparar_perfis(None, fx.perfil_de_faixas([_f(1)], Config().audio)) == []


def test_menos_faixas_que_a_gravacao_anterior_avisa():
    ant = fx.perfil_de_faixas([_f(1), _f(2), _f(3), _f(4)], Config().audio)
    atual = fx.perfil_de_faixas([_f(1), _f(2)], Config().audio)
    avisos = fx.comparar_perfis(ant, atual)
    assert any("4 faixa" in a and "2 faixa" in a for a in avisos)


def test_faixa_que_estava_boa_e_agora_esta_muda_ou_duplicada_avisa_qual():
    ant = fx.perfil_de_faixas([_f(1), _f(2), _f(3)], Config().audio)
    atual = fx.perfil_de_faixas([_f(1), _f(2, "duplicada"), _f(3, "muda")], Config().audio)
    avisos = " | ".join(fx.comparar_perfis(ant, atual))
    assert "faixa 2" in avisos and "igual à faixa 1" in avisos and "faixa 3" in avisos and "muda" in avisos


def test_faixa_que_melhorou_nao_avisa():
    ant = fx.perfil_de_faixas([_f(1), _f(2, "duplicada")], Config().audio)
    assert fx.comparar_perfis(ant, fx.perfil_de_faixas([_f(1), _f(2)], Config().audio)) == []


def test_faixa_marcada_pelo_dono_que_esta_muda_ou_duplicada_avisa_mesmo_sem_perfil_anterior():
    cfg = Config()
    cfg.audio.faixa_jogo, cfg.audio.faixa_voz = 2, 3
    atual = fx.perfil_de_faixas([_f(1), _f(2, "duplicada"), _f(3, "muda")], cfg.audio)
    avisos = " | ".join(fx.comparar_perfis(None, atual))
    assert "audio.faixa_jogo = 2" in avisos and "audio.faixa_voz = 3" in avisos


def test_registrar_grava_no_video_e_no_global_e_devolve_avisos(tmp_path):
    from cs2clip.pipeline import registrar_perfil_de_faixas
    saida = tmp_path / "saida"
    out = saida / "v1" / ".cache"
    out.mkdir(parents=True)
    cfg = Config()
    bom = [_f(1), _f(2), _f(3)]
    assert registrar_perfil_de_faixas(bom, cfg.audio, out, saida) == []
    assert json.loads((out / "faixas.json").read_text(encoding="utf-8"))["n"] == 3
    assert json.loads((saida / "perfil_faixas.json").read_text(encoding="utf-8"))["n"] == 3
    avisos = registrar_perfil_de_faixas([_f(1), _f(2, "muda"), _f(3)], cfg.audio, out, saida)
    assert any("faixa 2" in a for a in avisos)                                  # comparou com o do vídeo anterior
    # a comparação seguinte já parte do perfil novo (o problema foi avisado uma vez)
    assert registrar_perfil_de_faixas([_f(1), _f(2, "muda"), _f(3)], cfg.audio, out, saida) == []


def test_registrar_nunca_derruba_o_processamento(tmp_path):
    from cs2clip.pipeline import registrar_perfil_de_faixas
    assert registrar_perfil_de_faixas(None, Config().audio, tmp_path / "nao_existe", tmp_path / "x") == []


@pytest.mark.lento
def test_diagnostico_rapido_sem_fala_de_video_de_verdade(tmp_path):
    v = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=8", "-f", "lavfi", "-i", "sine=f=440:d=8",
                    "-f", "lavfi", "-i", "sine=f=440:d=8", "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(v)], check=True, capture_output=True)
    lista = fx.analisar(str(v), janela_s=6, com_fala=False)
    p = fx.perfil_de_faixas(lista, Config().audio)
    assert p["n"] == 2 and p["estado"]["2"] == "duplicada"
