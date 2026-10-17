"""Formatos de vídeo (E3b): o `probe` lê pelo conteúdo, não pela extensão, e entende taxa variável, capa
embutida e resoluções diferentes. Os vídeos de teste são gerados na hora pelo ffmpeg (2 s cada)."""
import shutil
import subprocess

import pytest

from cs2clip.__main__ import _parece_video
from cs2clip.scan import VideoInvalido, probe
from cs2clip.tools import FFMPEG

pytestmark = pytest.mark.skipif(not (shutil.which(FFMPEG) or shutil.which("ffmpeg")), reason="ffmpeg indisponível")


def gerar(destino, tam="1280x720", fps="60", extra=(), filtro=None, codec="mpeg4"):
    vf = [] if filtro is None else ["-vf", filtro]
    if destino.suffix == ".flv":
        codec = "flv1"
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={tam}:rate={fps}:duration=2",
           *vf, "-c:v", codec, "-pix_fmt", "yuv420p", *extra, str(destino)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"este ffmpeg não gera {destino.suffix}: {r.stderr.strip()[-80:]}")
    return str(destino)


@pytest.mark.parametrize("ext,tam,fps,esperado", [
    (".mkv", "1280x720", "60", 60.0),
    (".ts", "2560x1440", "30", 30.0),
    (".mp4", "1920x1080", "60000/1001", 59.94),
    (".flv", "1280x720", "30", 30.0),
    (".mov", "3840x2160", "24", 24.0),
    (".avi", "960x720", "60", 60.0),
])
def test_probe_le_resolucao_e_fps_de_varios_formatos(tmp_path, ext, tam, fps, esperado):
    info = probe(gerar(tmp_path / f"v{ext}", tam, fps))
    w, h = map(int, tam.split("x"))
    assert (info.width, info.height) == (w, h)
    assert info.fps == pytest.approx(esperado, abs=0.1) and info.duration == pytest.approx(2.0, abs=0.3)


def test_taxa_variavel_usa_a_media_e_nao_o_valor_nominal_enganoso(tmp_path):
    # joga fora ~30% dos quadros mantendo o tempo deles: OBS/mkv com VFR (o r_frame_rate vira 1000)
    v = gerar(tmp_path / "vfr.mkv", fps="60", filtro=r"select='gt(random(1)\,0.3)'",
              extra=("-fps_mode", "passthrough"))
    info = probe(v)
    assert 20 <= info.fps <= 60, info.fps


def test_capa_embutida_nao_vira_a_faixa_de_video(tmp_path):
    video = gerar(tmp_path / "base.mp4", "1280x720", "30")
    capa = tmp_path / "capa.png"
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:size=64x64", "-frames:v", "1",
                        str(capa)], capture_output=True)
    assert r.returncode == 0
    saida = tmp_path / "com_capa.mp4"
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", capa, "-i", video, "-map", "0", "-map", "1",
                        "-c", "copy", "-disposition:v:0", "attached_pic", str(saida)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("este ffmpeg não grava capa embutida")
    info = probe(str(saida))
    assert (info.width, info.height) == (1280, 720)


def test_extensao_desconhecida_vale_pelo_conteudo(tmp_path):
    v = gerar(tmp_path / "v.mkv", "1280x720", "30")
    estranho = tmp_path / "gravacao.xyz"
    shutil.copy(v, estranho)
    assert _parece_video(estranho)
    texto = tmp_path / "notas.txt"
    texto.write_text("não sou vídeo", encoding="utf-8")
    assert not _parece_video(texto)


def test_arquivo_sem_cabecalho_da_erro_claro(tmp_path):
    v = gerar(tmp_path / "v.mkv", "1280x720", "30")
    quebrado = tmp_path / "sem_cabecalho.mkv"
    quebrado.write_bytes(open(v, "rb").read()[:60])
    with pytest.raises(VideoInvalido):
        probe(str(quebrado))


def test_gravacao_cortada_no_meio_ainda_abre(tmp_path):
    """OBS que travou: cabeçalho ok, fim faltando. O programa lê o que tem em vez de recusar."""
    v = gerar(tmp_path / "v.ts", "1280x720", "30")
    dados = open(v, "rb").read()
    cortado = tmp_path / "cortado.ts"
    cortado.write_bytes(dados[: len(dados) // 2])
    assert probe(str(cortado)).width == 1280
