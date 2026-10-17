"""R1: correções da revisão no render, legendas, publicar, checagem e medição."""
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from cs2clip import captions, checagem, render, tools
from cs2clip.config import Config
from cs2clip.medicao import ler_gabarito, medir
from cs2clip.moments import Event, Moment
from cs2clip.publicar import gerar_publicar, hashtags, titulos

RAIZ = Path(__file__).resolve().parents[1]


def _tem_ffmpeg() -> bool:
    try:
        return bool(tools.FFMPEG)
    except SystemExit:
        return False


precisa_ffmpeg = pytest.mark.skipif(not _tem_ffmpeg(), reason="ffmpeg indisponível")


def _video(destino: Path, tam="320x180", dur=1, perfil="high", cor=None):
    fonte = f"color=c={cor}:size={tam}:rate=60:duration={dur}" if cor else f"testsrc2=size={tam}:rate=60:duration={dur}"
    subprocess.run([tools.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", fonte,
                    "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={dur}",
                    "-c:v", "libx264", "-profile:v", perfil, "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                    "-ac", "2", "-shortest", str(destino)], check=True, capture_output=True)
    return destino


def _x264():
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    return cfg


# ── 16/32: apóstrofo no caminho do concat ─────────────────────────────────────────────────────

def test_lista_do_concat_escapa_o_apostrofo(tmp_path):
    p = tmp_path / "D'Avila" / "seg0.mp4"
    linha = render._lista_concat(p)
    assert linha == "file '" + p.resolve().as_posix().replace("'", "'\\''") + "'\n"
    assert "D'\\''Avila" in linha


@precisa_ffmpeg
def test_concat_funciona_em_pasta_com_apostrofo(tmp_path):
    pasta = tmp_path / "D'Avila"
    pasta.mkdir()
    a, b = _video(pasta / "a.mp4"), _video(pasta / "b.mp4")
    out = pasta / "junto.mp4"
    render.concat([a, b], out)
    from cs2clip import qc
    assert out.exists() and qc.inspecionar(out)["duracao"] == pytest.approx(2.0, abs=0.15)
    assert not (pasta / "junto.txt").exists()


# ── 20/33: formatos diferentes juntam reencodando, não com "-c copy" ──────────────────────────

@precisa_ffmpeg
def test_codec_compara_perfil_e_marca_do_x264(tmp_path):
    a = _video(tmp_path / "a.mp4", perfil="high")
    b = _video(tmp_path / "b.mp4", perfil="baseline")
    assert "x264" in render._codec(a) and render._codec(a) != render._codec(b)   # antes: os dois 'h264,yuv420p'


@precisa_ffmpeg
def test_concat_de_formatos_diferentes_reencoda_e_nao_falha(tmp_path, monkeypatch):
    a = _video(tmp_path / "a.mp4", perfil="high")
    b = _video(tmp_path / "b.mp4", perfil="baseline")
    chamadas = []
    original = render._run_enc
    monkeypatch.setattr(render, "_run_enc", lambda f, cwd=None: (chamadas.append(f()), original(f, cwd)))
    out = tmp_path / "junto.mp4"
    render.concat([a, b], out, _x264())
    assert chamadas and "-filter_complex" in chamadas[0] and "copy" not in chamadas[0]
    from cs2clip import qc
    assert qc.inspecionar(out)["duracao"] == pytest.approx(2.0, abs=0.15) and qc.tem_audio(str(out))


def test_encoder_efetivo_resolve_auto_e_queda_da_gpu(monkeypatch):
    cfg = Config()
    cfg.qualidade.encoder = "hevc_nvenc"
    monkeypatch.setattr(render, "_NVENC_QUEBROU", False)
    assert render.encoder_efetivo(cfg) == "hevc_nvenc"
    monkeypatch.setattr(render, "_NVENC_QUEBROU", True)
    assert render.encoder_efetivo(cfg) == "libx264" and "libx264" in render.video_args(cfg)


def test_render_por_janelas_refaz_em_x264_os_trechos_feitos_antes_da_gpu_cair(tmp_path, monkeypatch):
    m = Moment(100, 160, "abate", kills=[Event(110.0, "kill"), Event(150.0, "kill")])
    m.segmentos = [(106.0, 112.5), (146.0, 152.5)]
    monkeypatch.setattr(render, "_NVENC_QUEBROU", False)
    monkeypatch.setattr(render, "concat", lambda files, out: out.write_bytes(b"ok"))
    vistos = []

    def render_um(mm, dest):
        vistos.append(mm.start)
        if mm.start > 140:                 # a GPU cai no 2º trecho
            monkeypatch.setattr(render, "_NVENC_QUEBROU", True)
        dest.write_bytes(b"x")

    render.render_por_janelas(m, tmp_path / "c.mp4", render_um)
    assert vistos == [106.0, 146.0, 106.0, 146.0]


# ── 17: o gancho (nome da jogada) só no 1º trecho ────────────────────────────────────────────

def _quadra():
    m = Moment(100, 160, "abate", kills=[Event(t, "kill") for t in (108.0, 110.0, 148.0, 150.0)])
    m.label = "QUADRA KILL"
    m.segmentos = [(106.0, 112.5), (146.0, 152.5)]
    return m


def _dialogos(tmp_path, m, a, b, **kw):
    ass = tmp_path / "x.ass"
    captions.build_ass(ass, [], m, a, b, 1080, 1920, 768, False, True, **kw)
    return [ln for ln in ass.read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue")]


def test_gancho_nao_se_repete_no_segundo_trecho(tmp_path, monkeypatch):
    import dataclasses
    m = _quadra()
    feitos = {}
    monkeypatch.setattr(render, "concat", lambda files, out: out.write_bytes(b"ok"))

    def render_um(mm, dest):
        feitos[mm.start] = _dialogos(tmp_path, mm, mm.start, mm.end)
        dest.write_bytes(b"x")

    render.render_por_janelas(m, tmp_path / "s.mp4", render_um)
    assert sum("QUADRA KILL" in d for d in feitos[106.0]) == 1      # 1º trecho: gancho
    segundo = feitos[146.0]
    assert not any("QUADRA KILL" in d and "0:00:00.00" in d for d in segundo)   # antes: gancho de novo
    assert not any("DOUBLE KILL" in d for d in segundo)              # selo do abate do 1º trecho não sobra
    assert any("TRIPLE KILL" in d for d in segundo) and any("QUADRA KILL" in d for d in segundo)
    # short de um trecho só continua com o gancho
    assert any("QUADRA KILL" in d and "0:00:00.00" in d
               for d in _dialogos(tmp_path, dataclasses.replace(m, segmentos=[]), 100, 160))


def test_gancho_pode_ser_desligado_explicitamente(tmp_path):
    m = _quadra()
    assert not any("0:00:00.00" in d for d in _dialogos(tmp_path, m, 146.0, 152.5, gancho=False))


def test_marca_vazia_ou_so_arroba_nao_gera_faixa(tmp_path):
    m = _quadra()
    for marca in ("", "  ", "@", " @ "):
        ass = tmp_path / "m.ass"
        captions.build_ass(ass, [], m, 100, 160, 1080, 1920, 768, False, False, marca, 160)
        assert "Marca" not in ass.read_text(encoding="utf-8")
    captions.build_ass(ass, [], m, 100, 160, 1080, 1920, 768, False, False, "@canal", 160)
    assert "Marca,,0,0,0,," in ass.read_text(encoding="utf-8")


# ── 22: escape inválido na f-string da tremida ───────────────────────────────────────────────

def test_render_compila_sem_aviso_de_escape_invalido():
    fonte = (RAIZ / "cs2clip" / "render.py").read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(fonte, "render.py", "exec")


# ── 18: sem ffmpeg, o --checar ainda mostra a lista inteira ──────────────────────────────────

def test_importar_tools_sem_ffmpeg_nao_derruba_o_programa():
    codigo = ("import shutil, pathlib; shutil.which = lambda *a, **k: None; pathlib.Path.exists = lambda self: False\n"
              "from cs2clip.tools import ROOT\n"
              "try:\n    from cs2clip.tools import FFMPEG\nexcept SystemExit as e:\n    print('SAIU', e)\n")
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, encoding="utf-8",
                       cwd=RAIZ, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0 and "SAIU ffmpeg não encontrado" in r.stdout


def test_checar_sem_ffmpeg_mostra_os_outros_itens(tmp_path, monkeypatch):
    def nao_acha(nome):
        raise SystemExit(f"{nome} não encontrado.")
    monkeypatch.setattr(tools, "_find", nao_acha)
    monkeypatch.delattr(tools, "FFMPEG", raising=False)
    monkeypatch.delattr(tools, "FFPROBE", raising=False)
    itens = {i.nome: i for i in checagem.checar(tmp_path, tmp_path, ativos=set())}
    assert not itens["FFmpeg"].ok and "não encontrado" in itens["FFmpeg"].detalhe
    assert itens["Python"].ok and "Espaço em disco" in itens and "OpenCV" in itens


# ── 19: gabarito com só o abate respondido ───────────────────────────────────────────────────

def test_linha_so_com_abate_conta_sem_aviso(tmp_path):
    f = tmp_path / "g.csv"
    f.write_text("vod_id,tempo,categoria,observacao,abate_certo\nv,01:40,,,sim\nv,05:00,,,\n", encoding="utf-8")
    linhas, avisos = ler_gabarito(f)
    assert len(linhas) == 1 and avisos == []
    momentos = [{"foco": 100.0, "inicio": 95.0, "fim": 108.0, "emocao": "HYPE", "abates": [{"t": 100.0}]}]
    rel = medir(linhas, {"v": momentos})
    assert rel.abate_sim == 1 and rel.metricas["alegria"].fp == 0   # sem resposta de emoção: não vira falso


# ── 23/24: títulos e descrição ───────────────────────────────────────────────────────────────

def _jogada(intervalo):
    m = Moment(10, 20, "abate", kills=[Event(10 + i * intervalo / 2, "kill") for i in range(3)], idx=1)
    return m


def test_contexto_de_tempo_curto_sem_zero_nem_singular_errado():
    assert titulos(_jogada(0.4))[0] == "TRIPLE KILL em 0,4 s"
    assert titulos(_jogada(1.2))[0] == "TRIPLE KILL em 1,2 s"
    assert titulos(_jogada(3.0))[0] == "TRIPLE KILL em 3 segundos"


def test_marca_vazia_nao_gera_hashtag_nem_link(tmp_path):
    m = _jogada(3.0)
    for marca in ("", "@", "  "):
        assert "#" not in hashtags(m, marca) and all(len(t) > 1 for t in hashtags(m, marca))
        gerar_publicar(tmp_path, "v", [m], {1: {"short": "s.mp4"}}, marca)
        texto = (tmp_path / "publicar.txt").read_text(encoding="utf-8")
        assert "twitch.tv" not in texto and "Live completa" not in texto


# ── 26: vídeo que não é 16:9 mantém a proporção ──────────────────────────────────────────────

def test_area_16x9_nao_muda_nada(monkeypatch):
    monkeypatch.setattr(render, "_tamanho", lambda src: (1920, 1080))
    assert render._area("x") is None and render._escala16(None) == "scale=1920:1080:flags=lanczos"
    monkeypatch.setattr(render, "_tamanho", lambda src: (1280, 720))
    assert render._area("x") is None


def test_area_4x3_e_ultrawide_ganham_faixa_preta(monkeypatch):
    monkeypatch.setattr(render, "_tamanho", lambda src: (1440, 1080))
    assert render._area("x") == (1440, 1080, 240, 0)
    assert "pad=1920:1080:240:0" in render._escala16(render._area("x"))
    monkeypatch.setattr(render, "_tamanho", lambda src: (2560, 1080))
    aw, ah, ax, ay = render._area("x")
    assert (aw, ax) == (1920, 0) and ah == 810 and ay % 2 == 0


def test_camera_vai_para_a_area_sem_deformar():
    x, y, w, h = render._cam_na_area((1600, 40, 300, 200), (1440, 1080, 240, 0))
    assert 240 <= x and x + w <= 1680 and abs(w / h - 1.5) < 0.05     # mesma proporção, dentro da imagem


@precisa_ffmpeg
def test_corte_de_video_4x3_sai_com_faixas_pretas(tmp_path):
    src = _video(tmp_path / "quatro_tercos.mp4", tam="320x240", cor="white")
    render._tamanho.cache_clear()
    out = tmp_path / "corte.mp4"
    cfg = _x264()
    cfg.montagem.transicao = 0
    render.render_clip(str(src), Moment(0.0, 0.5, "abate"), out, cfg)
    raw = subprocess.run([tools.FFMPEG, "-v", "error", "-i", str(out), "-frames:v", "1", "-vf", "scale=192:108",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    linha = raw[54 * 192:55 * 192]
    assert linha[5] < 40 and linha[96] > 200 and linha[186] < 40         # preto nas bordas, imagem no meio
