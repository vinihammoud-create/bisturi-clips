"""23/11/2026: short em pé no formato que funcionou — webcam INTEIRA em cima (com o letreiro), jogo embaixo
com a mira no centro, fundo desfocado do próprio jogo, nada importante nos ~150 px de cima. Opção `shorts.layout = "webcam_inteira"` (DESLIGADA: o padrão
continua "classico", CONSTITUICAO §3.2). ffmpeg de verdade em vídeo sintético pequeno: jogo VERDE, webcam VERMELHA; a cor do pixel diz de onde veio a imagem."""
import subprocess
from pathlib import Path

import pytest

import cs2clip.pipeline as pipeline  # noqa: F401  (carrega o módulo antes de trocar partes dele)
from cs2clip import qc, render
from cs2clip.config import Config, retangulo_da_webcam
from cs2clip.moments import Event, Moment
from cs2clip.tools import FFMPEG

COR = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
CAIXA = (30, 429, 456, 276)          # a webcam do dono (x 32-488, y 428-704), arredondada para caber na escala 1/3 do vídeo de teste


@pytest.fixture(autouse=True)
def _x264_rapido(monkeypatch):
    monkeypatch.setattr(render, "video_args", lambda cfg: ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-g", "120", "-pix_fmt", "yuv420p", *COR])


def _live(destino: Path, dur: float) -> Path:
    """Live 640x360 (1/3 de 1920x1080): jogo verde, webcam vermelha onde a do dono fica, kill feed AZUL no canto de cima à direita."""
    x, y, w, h = (v // 3 for v in CAIXA)
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x20a020:s=640x360:r=30:d={dur},drawbox=x={x}:y={y}:w={w}:h={h}:color=0xe01010:t=fill,drawbox=x=470:y=20:w=170:h=50:color=0x1030d0:t=fill",
           "-f", "lavfi", "-i", f"sine=f=440:d={dur}", "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


def _rgb(arq: Path, t: float, x: int, y: int) -> tuple[int, int, int]:
    r = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(arq), "-frames:v", "1", "-vf", f"crop=8:8:{x}:{y},scale=1:1", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "pipe:1"], capture_output=True)
    assert len(r.stdout) >= 3, r.stderr[-200:]
    return r.stdout[0], r.stdout[1], r.stdout[2]


def _vermelho(c) -> bool:
    return c[0] > 150 and c[1] < 90 and c[2] < 90


def _verde(c) -> bool:
    return c[1] > 90 and c[0] < 90 and c[2] < 90


def _escuro(c) -> bool:
    return max(c) < 40


def _momento() -> Moment:
    return Moment(2.0, 8.0, "abate", label="DOUBLE KILL", score=40.0, idx=1, kills=[Event(3.0, "kill"), Event(6.0, "kill")])


def _cfg(layout: str) -> Config:
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    cfg.shorts.layout = layout
    cfg.shorts.zoom_no_abate = False
    return cfg


# ── geometria (sem ffmpeg) ───────────────────────────────────────────────────────────────────────

def test_geometria_da_webcam_inteira_segue_as_areas_seguras():
    g = render.geometria_do_short(_cfg("webcam_inteira").shorts, (32, 428, 456, 276), False)
    assert g["inteira"] and g["cam_x"] == 60 and g["cam_w"] == 960                  # margem de 60 px dos lados
    assert g["cam_h"] == 580 and g["topo"] >= 150                                    # 960 x 276/456 = 581 -> par; nada nos ~150 px de cima
    assert g["jogo_y"] == g["topo"] + g["cam_h"] + render.VAO_WEBCAM_JOGO and g["jogo_y"] + g["jogo_h"] == 1920
    classico = render.geometria_do_short(_cfg("classico").shorts, (32, 428, 456, 276), False)
    assert not classico["inteira"] and classico["cam_w"] == 1080 and classico["cam_h"] == 608      # CONTROLE: o formato de sempre, igual a antes


def test_retangulo_da_webcam_em_pixels():
    assert retangulo_da_webcam("32,428,456,276") == (32, 428, 456, 276)
    assert retangulo_da_webcam(" 32, 428 , 456,276 ") == (32, 428, 456, 276)
    assert retangulo_da_webcam("") is None
    for ruim in ("32,428,456", "a,b,c,d", "32,428,0,276", "1800,428,456,276", "32,900,456,276", "-1,428,456,276"):
        with pytest.raises(ValueError):
            retangulo_da_webcam(ruim)


def test_config_nasce_no_formato_de_sempre_e_valida_o_novo():
    from cs2clip.config import _validar as validar
    cfg = Config()
    assert cfg.shorts.layout == "classico" and cfg.shorts.webcam_retangulo == ""
    cfg.shorts.layout = "outro"
    with pytest.raises(ValueError, match="layout"):
        validar(cfg)
    cfg.shorts.layout, cfg.shorts.webcam_retangulo = "webcam_inteira", "32,428"
    with pytest.raises(ValueError, match="webcam_retangulo"):
        validar(cfg)
    from ui.configuracao import CAMPOS_POR_CHAVE
    assert ("shorts", "layout") in CAMPOS_POR_CHAVE and ("shorts", "webcam_retangulo") in CAMPOS_POR_CHAVE


def test_caixa_da_webcam_inteira_vem_do_retangulo_ou_da_deteccao():
    from cs2clip.layout import Layout
    cfg = _cfg("webcam_inteira")
    cfg.shorts.webcam_retangulo = "32,428,456,276"
    lay = Layout((0.1, 0.4, 0.25, 0.25), (0.15, 0.45, 0.05, 0.08), 0.9)
    assert pipeline.caixa_da_webcam_inteira(cfg.shorts, lay) == (32, 428, 456, 276)           # com webcam achada, o retângulo dado vale mais que a detecção
    cfg.shorts.webcam_retangulo = ""
    assert pipeline.caixa_da_webcam_inteira(cfg.shorts, lay) == (192, 432, 480, 270)            # a câmera achada pela análise, inteira (sem zoom no rosto)
    assert pipeline.caixa_da_webcam_inteira(cfg.shorts, None) is None


# ── o short de verdade ───────────────────────────────────────────────────────────────────────────

@pytest.mark.lento
def test_short_webcam_inteira_tem_fundo_desfocado_webcam_com_margem_e_jogo_embaixo(tmp_path):
    src = _live(tmp_path / "live.mp4", 10.0)
    out = tmp_path / "short.mp4"
    cfg = _cfg("webcam_inteira")
    render.render_short(str(src), _momento(), out, cfg, CAIXA, None)
    assert qc.verificar(out, qc.Esperado(1080, 1920, 6.0, True, 60.0, conteudo=True)) == []
    g = render.geometria_do_short(cfg.shorts, CAIXA, False)
    assert _vermelho(_rgb(out, 1.0, 540, g["topo"] + g["cam_h"] // 2))                    # a webcam inteira, no meio do painel de cima
    assert _verde(_rgb(out, 1.0, 540, g["jogo_y"] + g["jogo_h"] // 2))                    # o jogo embaixo
    assert _verde(_rgb(out, 1.0, 540, 60))                                                # topo: fundo do próprio jogo (desfocado), não faixa preta
    assert not _vermelho(_rgb(out, 1.0, 16, g["topo"] + g["cam_h"] // 2))                # a margem ao lado da webcam é fundo, não webcam


@pytest.mark.lento
def test_controle_formato_classico_continua_com_a_faixa_escura_no_topo(tmp_path):
    src = _live(tmp_path / "live.mp4", 10.0)
    out = tmp_path / "short.mp4"
    cfg = _cfg("classico")
    render.render_short(str(src), _momento(), out, cfg, CAIXA, None)
    assert qc.verificar(out, qc.Esperado(1080, 1920, 6.0, True, 60.0, conteudo=True)) == []
    assert _escuro(_rgb(out, 1.0, 540, 60))                                               # a faixa do topo de sempre (cor 0x0b0d10)


@pytest.mark.lento
def test_o_jogo_embaixo_nunca_mostra_um_pedaco_da_webcam_da_live_nem_o_recorte_do_kill_feed(tmp_path):
    # prévia real de 23/11 (VOD 1234573000): com zoom 1, o recorte do jogo (998 px de largura) pegava 27 px da webcam (x 461-488) na borda esquerda,
    # e o recorte do kill feed aparecia colado (o formato pedido é "sem efeito"; MARCA_E_VOZ §7: recorte solto de HUD fica estranho)
    src = _live(tmp_path / "live.mp4", 10.0)
    out = tmp_path / "short.mp4"
    cfg = _cfg("webcam_inteira")
    cfg.shorts.zoom_gameplay = 1.0
    render.render_short(str(src), _momento(), out, cfg, CAIXA, None)
    g = render.geometria_do_short(cfg.shorts, CAIXA, False)
    for y in range(g["jogo_y"] + 20, 1900, 60):
        assert not _vermelho(_rgb(out, 1.0, 0, y)), y                                       # a borda esquerda do jogo nunca é a webcam
    assert _verde(_rgb(out, 3.2, 1000, g["jogo_y"] + 40))                                   # canto de cima à direita do jogo, logo depois do abate: jogo, sem recorte colado


def test_webcam_alta_nao_quebra_o_short_e_o_jogo_continua_com_espaco():
    g = render.geometria_do_short(_cfg("webcam_inteira").shorts, (100, 100, 300, 600), False)
    assert g["jogo_h"] >= 800 and g["cam_h"] <= int(0.42 * 1920) and g["cam_w"] % 2 == 0 and g["cam_x"] >= 60
    assert abs(g["cam_w"] / g["cam_h"] - 0.5) < 0.02                                     # a proporção da webcam não deforma


def test_retangulo_dado_so_vale_quando_a_analise_achou_uma_webcam():
    # revisão de 23/11 (alto): no kit de imagem limpa ou num vídeo do OBS só do jogo, o retângulo fixo virava um pedaço do jogo no lugar da webcam
    from cs2clip.layout import Layout
    from cs2clip.editar_demo import config_do_video_limpo
    cfg = _cfg("webcam_inteira")
    cfg.shorts.webcam_retangulo = "32,428,456,276"
    assert pipeline.caixa_da_webcam_inteira(cfg.shorts, None) is None
    assert pipeline.caixa_da_webcam_inteira(cfg.shorts, Layout(None, None, 0.1)) is None
    com_rosto = Layout((0.0, 0.4, 0.25, 0.25), (0.05, 0.45, 0.05, 0.08), 0.9)
    assert pipeline.caixa_da_webcam_inteira(cfg.shorts, com_rosto) == (32, 428, 456, 276)
    assert config_do_video_limpo(cfg).shorts.webcam_retangulo == ""                      # o vídeo limpo nunca tem a webcam da live


def test_trocar_o_formato_do_short_refaz_o_short_e_o_padrao_nao_muda_a_impressao():
    from cs2clip.refazer import hash_short
    base = hash_short(_cfg("classico"))
    assert hash_short(Config()) != "" and hash_short(_cfg("classico")) == base
    novo = _cfg("webcam_inteira")
    assert hash_short(novo) != base
    novo2 = _cfg("webcam_inteira")
    novo2.shorts.webcam_retangulo = "32,428,456,276"
    assert hash_short(novo2) != hash_short(novo)
