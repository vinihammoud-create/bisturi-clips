"""E4: checagem dos arquivos gerados (qc.py) e reserva em x264 quando o NVENC falha (render.py)."""
import subprocess

import pytest

from cs2clip import qc, render
from cs2clip.config import Config
from cs2clip.pipeline import _atomico, _renderizar_e_checar
from cs2clip.tools import FFMPEG


def gerar(destino, tam="1920x1080", fps="60", dur=2, audio=True, codec="mpeg4"):
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={tam}:rate={fps}:duration={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}"]
    cmd += ["-c:v", codec, "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if audio else []) + [str(destino)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return destino


def test_arquivo_certo_passa(tmp_path):
    v = gerar(tmp_path / "ok.mp4")
    assert qc.verificar(v, qc.Esperado(1920, 1080, 2.0, True, 60.0)) == []


@pytest.mark.parametrize("esp,trecho", [
    (qc.Esperado(1280, 720), "tamanho 1920x1080 em vez de 1280x720"),
    (qc.Esperado(duracao=10.0), "duração"),
    (qc.Esperado(fps=30.0), "60 fps em vez de 30"),
])
def test_reprova_tamanho_duracao_e_fps_errados(tmp_path, esp, trecho):
    v = gerar(tmp_path / "v.mp4")
    assert any(trecho in p for p in qc.verificar(v, esp))


def test_reprova_corte_sem_som_so_quando_a_entrada_tinha_som(tmp_path):
    mudo = gerar(tmp_path / "mudo.mp4", audio=False)
    assert any("sem som" in p for p in qc.verificar(mudo, qc.Esperado(audio=True)))
    assert qc.verificar(mudo, qc.Esperado(audio=False)) == []
    assert qc.tem_audio(str(mudo)) is False and qc.tem_audio(str(gerar(tmp_path / "com.mp4"))) is True


def test_reprova_inexistente_vazio_e_lixo(tmp_path):
    assert qc.verificar(tmp_path / "nao.mp4", qc.Esperado()) == ["o arquivo não foi criado"]
    (tmp_path / "vazio.mp4").write_bytes(b"")
    assert "vazio" in qc.verificar(tmp_path / "vazio.mp4", qc.Esperado())[0]
    (tmp_path / "lixo.mp4").write_bytes(b"isto nao e um video" * 400)
    assert qc.verificar(tmp_path / "lixo.mp4", qc.Esperado())


def test_corte_reprovado_nao_troca_o_bom_que_ja_existia(tmp_path):
    destino = tmp_path / "corte.mp4"
    bom = gerar(tmp_path / "bom.mp4")
    destino.write_bytes(bom.read_bytes())
    original = destino.read_bytes()

    def render_ruim():
        gerar(tmp_path / "corte.tmp.mp4", tam="640x360")      # sai com o tamanho errado

    with pytest.raises(qc.QcReprovou, match="reprovou na checagem"):
        _atomico(destino, lambda tmp: _renderizar_e_checar(tmp, render_ruim, qc.Esperado(1920, 1080), "corte 01"))
    assert destino.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp.*"))


# ── reserva em x264 ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def nvenc_ligado(monkeypatch):
    monkeypatch.setattr(render, "_NVENC_QUEBROU", False)
    monkeypatch.setattr(render, "_has_nvenc", lambda: True)


def _cfg():
    c = Config()
    c.qualidade.encoder = "auto"
    return c


def test_nvenc_que_falha_refaz_em_x264_e_o_resto_do_lote_ja_usa_x264(nvenc_ligado, monkeypatch, capsys):
    chamadas = []

    def rodar(cmd, cwd=None):
        chamadas.append(cmd)
        if "h264_nvenc" in cmd:
            raise render.FfmpegErro(cmd, "[h264_nvenc] OpenEncodeSessionEx failed: out of memory (10)")

    monkeypatch.setattr(render, "_run", rodar)
    cfg = _cfg()
    render._run_enc(lambda: ["ffmpeg", *render.video_args(cfg), "saida.mp4"])
    assert "h264_nvenc" in chamadas[0] and "libx264" in chamadas[1] and render.nvenc_quebrou()
    assert "NVENC" in capsys.readouterr().out
    assert "libx264" in render.video_args(cfg)                 # os próximos cortes já saem em x264
    render._run_enc(lambda: ["ffmpeg", *render.video_args(cfg), "outro.mp4"])
    assert len(chamadas) == 3 and "libx264" in chamadas[2]
    monkeypatch.setattr(render, "_NVENC_QUEBROU", False)


def test_erro_que_nao_e_do_nvenc_nao_tenta_de_novo(nvenc_ligado, monkeypatch):
    chamadas = []

    def rodar(cmd, cwd=None):
        chamadas.append(cmd)
        raise render.FfmpegErro(cmd, "Invalid argument: filtro quebrado")

    monkeypatch.setattr(render, "_run", rodar)
    cfg = _cfg()
    with pytest.raises(render.FfmpegErro):
        render._run_enc(lambda: ["ffmpeg", *render.video_args(cfg), "x.mp4"])
    assert len(chamadas) == 1 and not render.nvenc_quebrou()


def test_x264_que_falha_nao_entra_em_loop(monkeypatch):
    monkeypatch.setattr(render, "_NVENC_QUEBROU", False)
    cfg = _cfg()
    cfg.qualidade.encoder = "libx264"
    chamadas = []

    def rodar(cmd, cwd=None):
        chamadas.append(cmd)
        raise render.FfmpegErro(cmd, "cuda qualquer coisa")

    monkeypatch.setattr(render, "_run", rodar)
    with pytest.raises(render.FfmpegErro):
        render._run_enc(lambda: ["ffmpeg", *render.video_args(cfg), "x.mp4"])
    assert len(chamadas) == 1


def test_whisper_e_liberado_da_memoria():
    from cs2clip import transcribe
    transcribe._MODEL = object()
    transcribe.liberar_modelo()
    assert transcribe._MODEL is None
    transcribe.liberar_modelo()                                  # chamar de novo não quebra


def test_padrao_de_renders_paralelos_e_2():
    assert Config().qualidade.renders_paralelos == 2
