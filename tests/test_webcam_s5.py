"""S5 (D3 = sim): com a live junto da demo, o short de imagem limpa leva a webcam da LIVE no painel de cima. ffmpeg de verdade, vídeos sintéticos pequenos.

A "live" de teste é azul com um retângulo VERMELHO onde fica a webcam; o jogo gravado é VERDE. Assim, olhar a cor de um pixel do short diz de onde veio a imagem.
Cada teste tem CONTROLE (sem a opção / sem a webcam, o painel não aparece).
"""
import json
import subprocess
from pathlib import Path

import pytest

import cs2clip.pipeline as pipeline  # noqa: F401  (carrega o módulo antes de trocar partes dele)
from cs2clip import editar_demo as ed
from cs2clip import qc, render
from cs2clip import video_limpo as vl
from cs2clip.config import Config
from cs2clip.moments import Event, Moment
from cs2clip.scan import VideoInfo
from cs2clip.tools import FFMPEG, FFPROBE
from cs2clip.video_limpo import ErroVideoLimpo

from test_video_limpo_d7 import _cenario

COR = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
LW, LH = 640, 360
CAIXA = (50, 250, 160, 90)                    # onde a webcam está na live de teste (px)


@pytest.fixture(autouse=True)
def _x264_rapido(monkeypatch):
    monkeypatch.setattr(render, "video_args", lambda cfg: ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-g", "120", "-pix_fmt", "yuv420p", *COR])


def _verde(destino: Path, dur: float, w=640, h=360, fps=60, audio=True, faixas=1) -> Path:
    """Jogo gravado de mentira: tela verde; `faixas` = quantas trilhas de áudio (o jogo gravado pelo HLAE tem 2)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    n = faixas if audio else 0
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x20a020:s={w}x{h}:r={fps}:d={dur}"]
    for k in range(n):
        cmd += ["-f", "lavfi", "-i", f"sine=f={300 + 50 * k}:d={dur}"]
    cmd += ["-map", "0:v"] + [a for k in range(n) for a in ("-map", f"{k + 1}:a")] + ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += (["-c:a", "aac", "-ac", "2"] if n else []) + [str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


def _vermelho_cheio(destino: Path, dur: float, w=1080, h=608) -> Path:
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0xe01010:s={w}x{h}:r=60:d={dur}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


def _live(destino: Path, dur: float, faixas=4) -> Path:
    """Live 16:9 sintética (azul, webcam vermelha), baixa taxa de quadros para ser barata."""
    x, y, w, h = CAIXA
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x1030d0:s={LW}x{LH}:r=5:d={dur},drawbox=x={x}:y={y}:w={w}:h={h}:color=0xe01010:t=fill"]
    for k in range(faixas):
        cmd += ["-f", "lavfi", "-i", f"sine=f={440 + 100 * k}:d={dur}"]
    cmd += ["-map", "0:v"] + [a for i in range(faixas) for a in ("-map", f"{i + 1}:a")]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-pix_fmt", "yuv420p", "-c:a", "aac", str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


def _analise(tmp: Path, com_webcam=True) -> Path:
    """A pasta da análise da live com o `.cache/layout.json` que a análise grava (coordenadas relativas)."""
    cache = tmp / "analise" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    x, y, w, h = CAIXA
    cam = [x / LW, y / LH, w / LW, h / LH]
    face = [(x + w * 0.4) / LW, (y + h * 0.2) / LH, (w * 0.2) / LW, (h * 0.4) / LH]
    (cache / "layout.json").write_text(json.dumps({"cam": cam if com_webcam else None, "face": face if com_webcam else None, "conf": 0.9}), encoding="utf-8")
    return tmp / "analise"


def _rgb(arq: Path, t: float, x: int, y: int) -> tuple[int, int, int]:
    r = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(arq), "-frames:v", "1", "-vf", f"crop=8:8:{x}:{y},scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                       capture_output=True)
    assert len(r.stdout) >= 3, r.stderr[-200:]
    return tuple(r.stdout[:3])


def _vermelho(c) -> bool:
    return c[0] > 150 and c[1] < 90 and c[2] < 90


def _verde_ok(c) -> bool:
    return c[1] > 110 and c[0] < 90 and c[2] < 90


def _dims(arq: Path) -> tuple[int, int, float]:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(arq)], capture_output=True, text=True)
    d = json.loads(r.stdout)
    return d["streams"][0]["width"], d["streams"][0]["height"], float(d["format"]["duration"])


def _momento() -> Moment:
    m = Moment(2.0, 10.0, "abate", label="DOUBLE KILL", score=40.0, idx=1, kills=[Event(3.0, "kill"), Event(7.0, "kill", icons=["headshot"])])
    m.round_n = 12
    return m


def _cfg() -> Config:
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    return cfg


# ── onde está a webcam na live ──────────────────────────────────────────────────────────────────

def test_a_opcao_vem_ligada_por_padrao_como_o_dono_pediu():
    assert Config().gravacao_demo.webcam_no_short is True


def test_caixa_da_webcam_vem_do_layout_da_analise_com_a_proporcao_do_painel(tmp_path, monkeypatch):
    import cs2clip.scan as scan
    monkeypatch.setattr(scan, "probe", lambda caminho: VideoInfo(caminho, 100.0, LW, LH, 30.0))
    cfg = _cfg()
    x, y, w, h = vl.caixa_da_webcam(_analise(tmp_path), tmp_path / "live.mp4", cfg)
    assert abs(w / h - cfg.shorts.largura / cfg.shorts.camera_altura) < 0.06                              # proporção do painel do short
    bx, by, bw, bh = CAIXA
    assert bx - 4 <= x and x + w <= bx + bw + 4 and by - 4 <= y and y + h <= by + bh + 4                   # dentro do retângulo da webcam (±margem de arredondamento)
    # CONTROLEs: sem rosto, sem arquivo, live fora de 16:9 → None (o short sai sem painel, nunca com recorte errado)
    assert vl.caixa_da_webcam(_analise(tmp_path / "x", com_webcam=False), tmp_path / "live.mp4", cfg) is None
    assert vl.caixa_da_webcam(tmp_path / "nao_existe", tmp_path / "live.mp4", cfg) is None
    monkeypatch.setattr(scan, "probe", lambda caminho: VideoInfo(caminho, 100.0, 640, 480, 30.0))
    assert vl.caixa_da_webcam(_analise(tmp_path / "y"), tmp_path / "live.mp4", cfg) is None


def test_camera_manual_do_dono_vale_mais_que_o_layout_da_analise(tmp_path, monkeypatch):
    import cs2clip.scan as scan
    monkeypatch.setattr(scan, "probe", lambda caminho: VideoInfo(caminho, 100.0, LW, LH, 30.0))
    cfg = _cfg()
    cfg.shorts.camera_manual = [0.5, 0.1, 0.3, 0.3]
    x, y, w, h = vl.caixa_da_webcam(tmp_path / "sem_cache", tmp_path / "live.mp4", cfg)                      # nem precisa do layout.json
    assert x >= 0.5 * LW - 4 and y >= 0.1 * LH - 4


# ── recortar e juntar a webcam ──────────────────────────────────────────────────────────────────

@pytest.mark.lento
def test_webcam_da_live_recorta_a_regiao_certa_e_fica_com_a_duracao_do_round(tmp_path):
    clip = _verde(tmp_path / "round.mp4", 8.0)
    live = _live(tmp_path / "live.mp4", 40.0)
    cam = vl.webcam_da_live(clip, live, 3.0, CAIXA, tmp_path / "cam.mp4", 1080, 608)
    w, h, dur = _dims(cam)
    assert (w, h) == (1080, 608) and dur == pytest.approx(8.0, abs=0.15)
    assert _vermelho(_rgb(cam, 2.0, 540, 300))                                                             # a região da webcam (vermelha), não o fundo da live
    # CONTROLE: caixa em outro lugar da live (fundo azul) → não é vermelho
    fundo = vl.webcam_da_live(clip, live, 3.0, (400, 20, 160, 90), tmp_path / "fundo.mp4", 1080, 608)
    assert not _vermelho(_rgb(fundo, 2.0, 540, 300))
    # a live acaba antes do fim do round: o último quadro é repetido e a duração continua a do round (nada fica fora de tempo)
    curto = vl.webcam_da_live(clip, live, 38.5, CAIXA, tmp_path / "curto.mp4", 1080, 608)
    assert _dims(curto)[2] == pytest.approx(8.0, abs=0.15)
    with pytest.raises(ErroVideoLimpo, match="antes do início da live"):
        vl.webcam_da_live(clip, live, -1.0, CAIXA, tmp_path / "x.mp4", 1080, 608)


@pytest.mark.lento
def test_montar_webcam_tem_a_mesma_linha_do_tempo_da_partida_limpa_e_recusa_o_que_nao_alinha(tmp_path):
    rounds = [vl.RoundGravado(2, _verde(tmp_path / "r2.mp4", 6.0), 198.0, 204.0, 6.0), vl.RoundGravado(5, _verde(tmp_path / "r5.mp4", 9.0), 498.0, 507.0, 9.0)]
    cams = {2: _vermelho_cheio(tmp_path / "c2.mp4", 6.0), 5: _vermelho_cheio(tmp_path / "c5.mp4", 9.0)}
    arq = vl.montar_webcam(rounds, cams, tmp_path / "PARTIDA_WEBCAM.mp4")
    assert _dims(arq)[2] == pytest.approx(15.0, abs=0.3)
    # CONTROLE: uma webcam com a duração errada (3 s num round de 9 s) → erro claro, arquivo não fica para trás
    ruins = {2: cams[2], 5: _vermelho_cheio(tmp_path / "c5curto.mp4", 3.0)}
    with pytest.raises(ErroVideoLimpo, match="não dá para alinhar"):
        vl.montar_webcam(rounds, ruins, tmp_path / "ruim.mp4")
    assert not (tmp_path / "ruim.mp4").exists()
    with pytest.raises(ErroVideoLimpo, match="Falta a webcam"):
        vl.montar_webcam(rounds, {2: cams[2]}, tmp_path / "falta.mp4")


# ── o short ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.lento
def test_short_com_webcam_da_live_tem_o_painel_de_cima_vermelho_e_o_jogo_embaixo(tmp_path):
    src = _verde(tmp_path / "jogo.mp4", 12.0)
    cam = _vermelho_cheio(tmp_path / "cam.mp4", 12.0)
    out = tmp_path / "short.mp4"
    render.render_short(str(src), _momento(), out, _cfg(), None, None, cam_video=str(cam))
    assert qc.verificar(out, qc.Esperado(1080, 1920, 8.0, True, 60.0, conteudo=True)) == []
    assert _vermelho(_rgb(out, 1.0, 540, 300)) and _verde_ok(_rgb(out, 1.0, 540, 1500))                        # webcam em cima, jogo embaixo
    # CONTROLE: sem o vídeo da webcam (e sem recorte), o jogo ocupa o quadro todo: nada de painel vermelho no topo
    sem = tmp_path / "sem.mp4"
    render.render_short(str(src), _momento(), sem, _cfg(), None, None)
    assert qc.verificar(sem, qc.Esperado(1080, 1920, 8.0, True, 60.0, conteudo=True)) == []
    assert _verde_ok(_rgb(sem, 1.0, 540, 300)) and not _vermelho(_rgb(sem, 1.0, 540, 300))


@pytest.mark.lento
def test_a_webcam_acompanha_o_momento_no_tempo_e_nao_so_o_comeco(tmp_path):
    """A webcam usa o MESMO início e a mesma duração do jogo: com a câmera mudando de cor no meio (vermelho → azul em 6 s), o short do trecho 7–15 s só mostra azul."""
    src = _verde(tmp_path / "jogo.mp4", 16.0)
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0xe01010:s=1080x608:r=60:d=6", "-f", "lavfi", "-i", "color=c=0x1030d0:s=1080x608:r=60:d=10",
           "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(tmp_path / "cam.mp4")]
    subprocess.run(cmd, check=True, capture_output=True)
    m = Moment(7.0, 15.0, "abate", label="DOUBLE KILL", score=40.0, idx=1, kills=[Event(9.0, "kill"), Event(12.0, "kill")])
    m.round_n = 12
    out = tmp_path / "short.mp4"
    render.render_short(str(src), m, out, _cfg(), None, None, cam_video=str(tmp_path / "cam.mp4"))
    topo = _rgb(out, 1.0, 540, 300)
    assert topo[2] > 150 and topo[0] < 90                                                                      # azul (a câmera depois dos 6 s), não vermelho (o começo do arquivo)


# ── a edição inteira ─────────────────────────────────────────────────────────────────────────────

def test_webcam_dos_rounds_so_avisa_quando_nao_da_e_nunca_derruba_a_edicao(tmp_path, monkeypatch):
    msgs = []
    rounds = [vl.RoundGravado(2, tmp_path / "r2.mp4", 198.0, 204.0, 6.0)]
    monkeypatch.setattr(vl, "caixa_da_webcam", lambda *a, **k: None)
    assert ed._webcam_dos_rounds(rounds, tmp_path / "live.mp4", tmp_path, 0.0, _cfg(), tmp_path / "out", msgs.append) is None
    assert any("Não achei a webcam" in m for m in msgs)
    monkeypatch.setattr(vl, "caixa_da_webcam", lambda *a, **k: (0, 0, 100, 56))

    def quebra(*a, **k):
        raise ErroVideoLimpo("o ffmpeg não conseguiu recortar a webcam da live: x")

    monkeypatch.setattr(vl, "webcam_da_live", quebra)
    msgs.clear()
    (tmp_path / "out").mkdir()
    assert ed._webcam_dos_rounds(rounds, tmp_path / "live.mp4", tmp_path, 0.0, _cfg(), tmp_path / "out", msgs.append) is None
    assert any("sem a webcam da live" in m for m in msgs)


@pytest.mark.lento
def test_edicao_com_live_poe_a_webcam_da_live_nos_shorts_e_sem_ela_o_short_sai_como_antes(tmp_path, monkeypatch):
    partida, grav = _cenario(tmp_path)
    for f in sorted((grav / "rounds").glob("round_*.mp4")):                                                    # o jogo gravado vira verde (a cor diz de onde veio o pixel)
        _verde(f, 13.0, faixas=2)
    monkeypatch.setattr(ed, "ler_demo", lambda caminho, quem=None: partida)
    monkeypatch.setattr(vl, "deslocamento_da_analise", lambda pasta, nome: -196.0)                             # round 2 começa em 2 s da live, round 5 em 302 s
    live = _live(tmp_path / "live.mp4", 330.0)
    analise = _analise(tmp_path)
    demo = tmp_path / "match730_003849321203751714819_0599033075_201.dem"
    demo.write_bytes(b"PBDEMS2")

    def cfg_base() -> Config:
        cfg = Config()
        cfg.qualidade.encoder = "libx264"
        cfg.qualidade.garantir_pico = False
        cfg.shorts.versao_efeitos = False
        cfg.audio.faixa_voz = 2
        cfg.gravacao_demo.conta_principal_id = "76561198071376839"
        return cfg

    msgs = []
    out = ed.editar(demo, cfg_base(), pasta_gravacao=grav, saida_raiz=tmp_path / "saida", live=live, analise_da_live=analise, imprimir=msgs.append)
    assert any("Webcam da live no painel de cima dos shorts (2 round(s))" in m for m in msgs) and (out / "PARTIDA_WEBCAM.mp4").is_file()
    assert _dims(out / "PARTIDA_WEBCAM.mp4")[2] == pytest.approx(_dims(out / "PARTIDA_LIMPA.mp4")[2], abs=0.6)       # a MESMA linha do tempo da partida limpa
    shorts = sorted((out / "shorts").glob("*_vertical.mp4"))
    assert len(shorts) == 2 and all(_vermelho(_rgb(s, 1.0, 540, 300)) for s in shorts)                          # webcam da live em cima em todos os shorts
    assert all(_verde_ok(_rgb(s, 1.0, 540, 1500)) for s in shorts)                                              # jogo gravado embaixo
    # CONTROLE 1: a opção desligada → nada de webcam, short como antes (jogo na tela toda)
    cfg2 = cfg_base()
    cfg2.gravacao_demo.webcam_no_short = False
    msgs2 = []
    out2 = ed.editar(demo, cfg2, pasta_gravacao=grav, saida_raiz=tmp_path / "saida2", live=live, analise_da_live=analise, imprimir=msgs2.append)
    assert not (out2 / "PARTIDA_WEBCAM.mp4").exists() and not any("Webcam da live" in m for m in msgs2)
    assert all(not _vermelho(_rgb(s, 1.0, 540, 300)) for s in (out2 / "shorts").glob("*_vertical.mp4"))
    # CONTROLE 2: a análise não achou webcam → aviso claro, edição termina, shorts sem painel
    msgs3 = []
    out3 = ed.editar(demo, cfg_base(), pasta_gravacao=grav, saida_raiz=tmp_path / "saida3", live=live, analise_da_live=_analise(tmp_path / "sem", com_webcam=False), imprimir=msgs3.append)
    assert any("Não achei a webcam" in m for m in msgs3) and not (out3 / "PARTIDA_WEBCAM.mp4").exists()
    assert all(not _vermelho(_rgb(s, 1.0, 540, 300)) for s in (out3 / "shorts").glob("*_vertical.mp4"))


@pytest.mark.lento
@pytest.mark.parametrize("faixa_voz", [0, 1])
def test_sem_a_faixa_do_microfone_configurada_a_edicao_segue_sem_a_voz_avisa_e_ainda_poe_a_webcam(tmp_path, monkeypatch, faixa_voz):
    """R1 (achada ao refazer a edição): audio.faixa_voz = 0 (não configurado) ou 1 (a mistura da live) quebrava a edição com ErroVideoLimpo no meio. Agora o kit sai SEM a voz,
    com aviso, e a webcam (que não depende do áudio) continua."""
    partida, grav = _cenario(tmp_path)
    monkeypatch.setattr(ed, "ler_demo", lambda caminho, quem=None: partida)
    monkeypatch.setattr(vl, "deslocamento_da_analise", lambda pasta, nome: -196.0)
    recebido = {}
    monkeypatch.setattr(pipeline, "render_all", lambda *a, **k: recebido.update(k) or ([], None, None))
    cfg = Config()
    cfg.audio.faixa_voz = faixa_voz
    cfg.gravacao_demo.conta_principal_id = "76561198071376839"
    demo = tmp_path / "match730_003849321203751714819_0599033075_201.dem"
    demo.write_bytes(b"PBDEMS2")
    msgs = []
    out = ed.editar(demo, cfg, pasta_gravacao=grav, saida_raiz=tmp_path / "saida", live=_live(tmp_path / "live.mp4", 330.0), analise_da_live=_analise(tmp_path), imprimir=msgs.append)
    assert any("microfone da live não está configurado" in m and "SEM a sua voz" in m for m in msgs)
    assert not (out / "_com_voz").exists() and recebido.get("cam_video") is not None and (out / "PARTIDA_WEBCAM.mp4").is_file()
