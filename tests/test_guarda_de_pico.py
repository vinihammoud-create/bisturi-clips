"""S1.4b (descoberto na prova real de 05/11): o limitador sozinho não segura o pico real depois do AAC. 5 de 11 arquivos da
gravação de 23/10 passavam de -1 dBTP (um em +0,2). O guarda mede o arquivo PRONTO e baixa só o áudio até ficar <= -1 dBTP."""
import subprocess

import pytest

from cs2clip import qc, render
from cs2clip.config import Config
from cs2clip.moments import Event, Moment
from cs2clip.tools import FFMPEG, FFPROBE

pytestmark = pytest.mark.lento


def _arquivo_estourado(destino, dois_audios=False):
    """Seno a ~0 dBFS (o `sine` do lavfi nasce a -18 dBFS; volume 7,9 = +18 dB) em AAC: o pico real depois da codificação passa de -1 dBTP com folga."""
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=6", "-f", "lavfi", "-i", "sine=f=1000:d=6,volume=7.9"]
    if dois_audios:
        cmd += ["-f", "lavfi", "-i", "sine=f=500:d=6,volume=7.9"]
    cmd += ["-map", "0:v", "-map", "1:a"] + (["-map", "2:a", "-metadata:s:a:1", "title=jogo"] if dois_audios else [])
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


def _info(arq):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height:format=duration", "-of", "json", str(arq)],
                       capture_output=True, text=True)
    import json
    d = json.loads(r.stdout)
    return [(s["codec_type"], s.get("codec_name")) for s in d["streams"]], float(d["format"]["duration"])


def test_guarda_baixa_o_pico_para_menos_de_1_dbtp_e_preserva_video_faixas_e_duracao(tmp_path):
    arq = _arquivo_estourado(tmp_path / "a.mp4", dois_audios=True)
    antes = qc.medir_loudness(arq)[1]
    assert antes > -1.0                                                     # controle: o arquivo realmente estoura
    streams_antes, dur_antes = _info(arq)
    depois = render.garantir_pico(arq)
    assert depois is not None and depois <= -1.0 + 1e-6
    assert qc.medir_loudness(arq)[1] <= -1.0 + 1e-6                          # medido de novo, no arquivo final
    streams, dur = _info(arq)
    assert streams == streams_antes and abs(dur - dur_antes) < 0.2           # mesmo vídeo, mesmas 2 faixas, mesma duração


def test_guarda_nao_mexe_em_arquivo_que_ja_esta_abaixo(tmp_path):
    arq = _arquivo_estourado(tmp_path / "b.mp4")
    render.garantir_pico(arq)
    assinatura = arq.read_bytes()
    assert render.garantir_pico(arq) is not None and arq.read_bytes() == assinatura   # 2ª vez: nada a fazer, arquivo intacto


def test_guarda_em_arquivo_sem_audio_nao_faz_nada(tmp_path):
    mudo = tmp_path / "mudo.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=2", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(mudo)], check=True, capture_output=True)
    assinatura = mudo.read_bytes()
    assert render.garantir_pico(mudo) is None and mudo.read_bytes() == assinatura


def test_guarda_desligado_pela_configuracao_nao_mexe(tmp_path):
    arq = _arquivo_estourado(tmp_path / "c.mp4")
    assinatura = arq.read_bytes()
    cfg = Config()
    cfg.qualidade.garantir_pico = False
    render.garantir_pico(arq, cfg)
    assert arq.read_bytes() == assinatura


def test_corte_renderizado_sai_com_pico_no_limite(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "video_args", lambda cfg: ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p"])
    origem = _arquivo_estourado(tmp_path / "o.mp4")
    m = Moment(1.0, 5.0, "abate", kills=[Event(3.0, "kill")], label="KILL", score=10.0, idx=1)
    out = tmp_path / "corte.mp4"
    render.render_clip(str(origem), m, out, Config())
    assert qc.medir_loudness(out)[1] <= -1.0 + 1e-6


def test_config_guarda_de_pico_nasce_ligado_e_esta_na_tela():
    from ui.configuracao import CAMPOS_POR_CHAVE
    assert Config().qualidade.garantir_pico is True and ("qualidade", "garantir_pico") in CAMPOS_POR_CHAVE
