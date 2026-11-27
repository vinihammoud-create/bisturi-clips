"""R2 (revisão do Opus de 13/11): com a webcam da live, TODOS os shorts eram renderizados de novo a cada edição, mesmo sem nada mudar, porque a impressão do short usava a data
do `PARTIDA_WEBCAM.mp4`, que a própria edição recriava. Agora vale a assinatura da ORIGEM (live, caixa, deslocamento, rounds), gravada ao lado, e o vídeo da webcam só é
refeito se essa assinatura mudar."""
import os
import time
from pathlib import Path

import pytest

import cs2clip.pipeline as pipeline
from cs2clip import editar_demo as ed
from cs2clip import video_limpo as vl
from cs2clip.config import Config
from cs2clip.video_limpo import RoundGravado

from test_video_limpo_d7 import _cenario
from test_webcam_s5 import _analise, _live, _rgb, _verde, _vermelho  # noqa: F401


def _rounds(tmp_path, duracao=13.0):
    return [RoundGravado(2, tmp_path / "r2.mp4", 198.0, 198.0 + duracao, duracao), RoundGravado(5, tmp_path / "r5.mp4", 498.0, 498.0 + duracao, duracao)]


@pytest.fixture
def falsos(tmp_path, monkeypatch):
    """ffmpeg trocado por arquivos de mentira: conta quantas vezes a webcam foi de fato refeita."""
    chamadas = {"cortes": 0, "montagens": 0}
    monkeypatch.setattr(vl, "caixa_da_webcam", lambda *a, **k: (10, 20, 160, 90))

    def corta(clip, live, inicio, caixa, destino, largura, altura, fps=60):
        chamadas["cortes"] += 1
        Path(destino).write_bytes(b"cam")
        return destino

    def monta(rounds, cams, destino, tolerancia_s=0.5):
        chamadas["montagens"] += 1
        Path(destino).write_bytes(b"partida webcam")
        return destino

    monkeypatch.setattr(vl, "webcam_da_live", corta)
    monkeypatch.setattr(vl, "montar_webcam", monta)
    live = tmp_path / "live.mp4"
    live.write_bytes(b"x" * 1000)
    out = tmp_path / "out"
    out.mkdir()
    return chamadas, live, out


def _chama(tmp_path, live, out, desloc=-196.0, rounds=None, msgs=None):
    return ed._webcam_dos_rounds(rounds or _rounds(tmp_path), live, tmp_path / "analise", desloc, Config(), out, (msgs if msgs is not None else []).append)


def test_a_mesma_origem_reaproveita_o_video_da_webcam_e_a_data_dele_nao_muda(tmp_path, falsos):
    chamadas, live, out = falsos
    msgs = []
    primeiro = _chama(tmp_path, live, out, msgs=msgs)
    assert chamadas == {"cortes": 2, "montagens": 1} and (out / "PARTIDA_WEBCAM.json").is_file()
    m1 = primeiro.stat().st_mtime_ns
    time.sleep(0.05)
    segundo = _chama(tmp_path, live, out, msgs=msgs)
    assert segundo == primeiro and chamadas == {"cortes": 2, "montagens": 1}                                  # nada foi refeito
    assert segundo.stat().st_mtime_ns == m1 and any("reaproveitada" in m for m in msgs)


@pytest.mark.parametrize("o_que_muda", ["deslocamento", "live", "round", "caixa"])
def test_mudou_a_origem_refaz_a_webcam(tmp_path, falsos, monkeypatch, o_que_muda):
    chamadas, live, out = falsos
    _chama(tmp_path, live, out)
    assert chamadas["montagens"] == 1
    rounds, desloc = _rounds(tmp_path), -196.0
    if o_que_muda == "deslocamento":
        desloc = -190.0                                                                                       # a sincronia mudou
    elif o_que_muda == "live":
        live.write_bytes(b"y" * 2000)                                                                         # outra live (ou a mesma regravada)
    elif o_que_muda == "round":
        rounds = _rounds(tmp_path, duracao=14.0)                                                              # o round foi regravado com outra duração
    else:
        monkeypatch.setattr(vl, "caixa_da_webcam", lambda *a, **k: (30, 40, 160, 90))                          # o dono mexeu no recorte
    _chama(tmp_path, live, out, desloc=desloc, rounds=rounds)
    assert chamadas["montagens"] == 2, o_que_muda


def test_sem_o_arquivo_ou_com_o_lado_estragado_refaz_em_vez_de_quebrar(tmp_path, falsos):
    chamadas, live, out = falsos
    _chama(tmp_path, live, out)
    (out / "PARTIDA_WEBCAM.json").write_text("{lixo", encoding="utf-8")
    _chama(tmp_path, live, out)
    assert chamadas["montagens"] == 2
    (out / "PARTIDA_WEBCAM.mp4").unlink()
    _chama(tmp_path, live, out)
    assert chamadas["montagens"] == 3


def test_a_impressao_do_short_ignora_a_data_do_arquivo_e_segue_a_assinatura_da_origem(tmp_path, falsos):
    chamadas, live, out = falsos
    cam = _chama(tmp_path, live, out)
    antes = pipeline._assinatura_cam(cam)
    novo = time.time() + 100
    os.utime(cam, (novo, novo))                                                                               # a data muda (como acontecia a cada edição): a impressão NÃO
    cam.write_bytes(b"partida webcam refeita com outro conteudo")
    assert pipeline._assinatura_cam(cam) == antes
    _chama(tmp_path, live, out, desloc=-100.0)                                                                # a origem mudou: a impressão muda
    assert pipeline._assinatura_cam(cam) != antes
    cam.with_suffix(".json").unlink()                                                                         # sem o lado: volta ao critério antigo (nunca quebra)
    assert pipeline._assinatura_cam(cam).startswith("cam:PARTIDA_WEBCAM.mp4")


@pytest.mark.lento
def test_editar_duas_vezes_sem_mudar_nada_nao_renderiza_os_shorts_de_novo(tmp_path, monkeypatch):
    """Ponta a ponta com ffmpeg: a 2ª edição reaproveita a webcam E os shorts (mesma data de arquivo); a mudança do deslocamento refaz."""
    partida, grav = _cenario(tmp_path)
    for f in sorted((grav / "rounds").glob("round_*.mp4")):
        _verde(f, 13.0, faixas=2)
    monkeypatch.setattr(ed, "ler_demo", lambda caminho, quem=None: partida)
    desloc = {"v": -196.0}
    monkeypatch.setattr(vl, "deslocamento_da_analise", lambda pasta, nome: desloc["v"])
    live = _live(tmp_path / "live.mp4", 330.0)
    analise = _analise(tmp_path)
    demo = tmp_path / "match730_003849321203751714819_0599033075_201.dem"
    demo.write_bytes(b"PBDEMS2")
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    cfg.qualidade.garantir_pico = False
    cfg.shorts.versao_efeitos = False
    cfg.audio.faixa_voz = 2
    cfg.gravacao_demo.conta_principal_id = "76561198071376839"

    def edita(msgs):
        return ed.editar(demo, cfg, pasta_gravacao=grav, saida_raiz=tmp_path / "saida", live=live, analise_da_live=analise, imprimir=msgs.append)

    m1 = []
    out = edita(m1)
    shorts = sorted((out / "shorts").glob("*_vertical.mp4"))
    assert len(shorts) == 2 and any("Webcam da live no painel" in m for m in m1)
    antes = [s.stat().st_mtime_ns for s in shorts]
    webcam_antes = (out / "PARTIDA_WEBCAM.mp4").stat().st_mtime_ns
    time.sleep(1.1)
    m2 = []
    edita(m2)
    assert any("reaproveitada" in m for m in m2)
    assert (out / "PARTIDA_WEBCAM.mp4").stat().st_mtime_ns == webcam_antes
    assert [s.stat().st_mtime_ns for s in shorts] == antes                                                    # os shorts NÃO foram renderizados de novo
    assert all(_vermelho(_rgb(s, 1.0, 540, 300)) for s in shorts)                                             # e continuam com a webcam em cima
    # CONTROLE: o deslocamento mudou (ex.: outra sincronia) → webcam e shorts são refeitos
    desloc["v"] = -190.0
    time.sleep(1.1)
    m3 = []
    edita(m3)
    assert any("Webcam da live no painel" in m for m in m3) and not any("reaproveitada" in m for m in m3)
    assert [s.stat().st_mtime_ns for s in shorts] != antes
