"""R4 (revisão do Opus, item 7 do auditor): arquivo de TEXTO com cara de vídeo (lista HLS, script ffconcat, DASH, SDP) não entra.

Conferido com o ffprobe real (FFmpeg 9.0.2): uma lista HLS vinda de arquivo local NÃO alcança a rede (o ffmpeg já limita a 'file,crypto,data'), mas um script `ffconcat`
faz o ffmpeg abrir OUTROS arquivos do computador. Como o ffmpeg reconhece o formato pelo conteúdo e não pela extensão, um `.mp4` de texto passava. Agora o `probe` (por onde
todo vídeo de fora entra) recusa texto antes de chamar o ffmpeg, e o ffprobe roda com `-protocol_whitelist file`.
"""
import subprocess
from pathlib import Path

import pytest

from cs2clip import scan
from cs2clip.scan import VideoInvalido, e_arquivo_de_texto, probe
from cs2clip.tools import FFMPEG

HOSTIS = {
    "hls.mp4": b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10,\nhttp://127.0.0.1:9/x.ts\n#EXT-X-ENDLIST\n",
    "concat.mkv": b"ffconcat version 1.0\nfile 'segredo.mp4'\n",
    "concat_com_bom.mp4": b"\xef\xbb\xbf  \r\nffconcat version 1.0\nfile '../outro.mp4'\n",
    "dash.mp4": b'<?xml version="1.0"?>\n<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"></MPD>',
    "sdp.mov": b"v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=x\r\nm=video 5004 RTP/AVP 96\r\n",
    "minusculo.mp4": b"#extm3u\n#extinf:1,\nfile:///c:/x.ts\n",
    "texto_qualquer.mp4": b"isto e apenas um texto comprido que alguem renomeou para mp4 " * 12,
    "html.mp4": b"<html><body>nao sou video</body></html>",
}


@pytest.mark.parametrize("nome", sorted(HOSTIS))
def test_texto_com_cara_de_video_e_recusado_antes_de_chamar_o_ffmpeg(tmp_path, monkeypatch, nome):
    arq = tmp_path / nome
    arq.write_bytes(HOSTIS[nome])
    assert e_arquivo_de_texto(arq) is True
    monkeypatch.setattr(scan.subprocess, "run", lambda *a, **k: pytest.fail("chamou o ffprobe com um arquivo de texto"))
    with pytest.raises(VideoInvalido, match="arquivo de texto"):
        probe(str(arq))


def _video(destino: Path, codec: str, extra=()):
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=1", "-f", "lavfi", "-i", "sine=f=440:d=1", "-c:v", codec, "-pix_fmt", "yuv420p", "-c:a", "aac",
           *extra, str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


@pytest.mark.lento
@pytest.mark.parametrize("nome,codec,extra", [("a.mp4", "libx264", []), ("b.mkv", "libx264", []), ("c.mov", "libx264", []), ("d.ts", "libx264", []),
                                              ("e.avi", "mpeg4", []), ("f.flv", "libx264", []), ("g.m4v", "libx264", []), ("h.wmv", "wmv2", [])])
def test_video_de_verdade_em_cada_formato_comum_continua_abrindo_o_controle(tmp_path, nome, codec, extra):
    """O risco do filtro é recusar vídeo bom: todo contêiner que o dono pode ter (OBS grava mkv/mp4/flv, Twitch/YouTube mp4) tem de passar."""
    v = _video(tmp_path / nome, codec, extra)
    assert e_arquivo_de_texto(v) is False
    info = probe(str(v))
    assert info.width == 160 and info.height == 90 and info.duration > 0.5


def test_arquivo_vazio_ou_ilegivel_nao_quebra_e_segue_o_erro_de_sempre(tmp_path):
    vazio = tmp_path / "vazio.mp4"
    vazio.write_bytes(b"")
    assert e_arquivo_de_texto(vazio) is False and e_arquivo_de_texto(tmp_path / "nao_existe.mp4") is False
    with pytest.raises(VideoInvalido, match="não é um vídeo válido"):
        probe(str(vazio))                                                                                     # a mensagem de sempre (não a do texto)
    binario = tmp_path / "lixo.mp4"
    binario.write_bytes(bytes(range(256)) * 8)
    assert e_arquivo_de_texto(binario) is False


@pytest.mark.lento
def test_o_ffprobe_roda_so_com_arquivo_local(tmp_path, monkeypatch):
    v = _video(tmp_path / "ok.mp4", "libx264")
    visto = {}
    real = scan.subprocess.run

    def espia(cmd, *a, **k):
        visto["cmd"] = list(cmd)
        return real(cmd, *a, **k)

    monkeypatch.setattr(scan.subprocess, "run", espia)
    probe(str(v))
    assert visto["cmd"][visto["cmd"].index("-protocol_whitelist") + 1] == "file" and visto["cmd"][-1] == str(v)
