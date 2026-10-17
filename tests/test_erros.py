"""E3: erros claros. Um vídeo corrompido gera mensagem em português e NUNCA "Pronto"; o detalhe vai
para log.txt; a saída do programa diz se terminou com problemas."""
import shutil
import subprocess

import pytest

from cs2clip.__main__ import main
from cs2clip.registro import Registro, resumo_erro
from cs2clip.scan import VideoInvalido, probe
from cs2clip.tools import FFMPEG


def test_resumo_erro_pega_a_ultima_linha_com_conteudo():
    txt = "ffmpeg falhou:\nffmpeg -i x\n\nInvalid data found when processing input\n\n"
    assert resumo_erro(txt) == "Invalid data found when processing input"
    assert resumo_erro("") == "erro sem mensagem"
    assert len(resumo_erro("x" * 500, limite=50)) == 50


def test_registro_conta_grava_e_devolve_linha_curta(tmp_path):
    r = Registro(tmp_path / "pasta" / "log.txt")
    linha = r.erro("corte 03", "ffmpeg falhou:\ncomando enorme\nDiscard of Stream 0")
    assert r.n == 1
    assert linha == "[erro] corte 03: Discard of Stream 0"
    log = (tmp_path / "pasta" / "log.txt").read_text(encoding="utf-8")
    assert "comando enorme" in log and "[erro] corte 03" in log     # o detalhe completo fica no log


def test_registro_com_excecao_grava_o_traceback(tmp_path):
    r = Registro(tmp_path / "log.txt")
    try:
        raise ValueError("deu ruim")
    except ValueError as e:
        r.erro("análise", e)
    assert "Traceback" in (tmp_path / "log.txt").read_text(encoding="utf-8")


def test_probe_arquivo_inexistente(tmp_path):
    with pytest.raises(VideoInvalido, match="não encontrado"):
        probe(str(tmp_path / "nao_existe.mp4"))


def test_probe_arquivo_que_nao_e_video(tmp_path):
    f = tmp_path / "falso.mp4"
    f.write_text("isto não é um vídeo", encoding="utf-8")
    with pytest.raises(VideoInvalido, match="não é um vídeo válido ou está corrompido"):
        probe(str(f))


def test_probe_video_cortado_no_meio_nao_passa(tmp_path):
    """Um mp4 de verdade truncado (sem o índice no fim) tem que dar erro claro, não traceback."""
    if not shutil.which(FFMPEG) and not FFMPEG:
        pytest.skip("ffmpeg indisponível")
    ok = tmp_path / "ok.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=2:r=30", "-pix_fmt", "yuv420p",
                    str(ok)], check=True)
    info = probe(str(ok))
    assert info.width == 320 and info.height == 240 and 1.5 < info.duration < 2.5
    cortado = tmp_path / "cortado.mp4"
    cortado.write_bytes(ok.read_bytes()[: ok.stat().st_size // 4])
    with pytest.raises(VideoInvalido):
        probe(str(cortado))


def test_main_com_video_corrompido_nao_diz_pronto(tmp_path, capsys):
    f = tmp_path / "quebrado.mp4"
    f.write_text("lixo", encoding="utf-8")
    saida = tmp_path / "saida"
    codigo = main([str(f), "--saida", str(saida), "--so-analise"])
    out = capsys.readouterr().out
    assert codigo == 1
    assert "não é um vídeo válido ou está corrompido" in out
    assert "Pronto" not in out and "Nenhum vídeo foi processado" in out
    assert "quebrado.mp4" in (saida / "log.txt").read_text(encoding="utf-8")
    assert not (saida / "relatorio.html").exists()


def test_main_com_formato_nao_aceito_explica_e_nao_diz_pronto(tmp_path, capsys):
    f = tmp_path / "anotacoes.txt"
    f.write_text("oi", encoding="utf-8")
    codigo = main([str(f), "--saida", str(tmp_path / "saida")])
    out = capsys.readouterr().out
    assert codigo == 1 and "formato não aceito" in out and "Pronto" not in out


def test_main_com_arquivo_que_nao_existe(tmp_path, capsys):
    codigo = main([str(tmp_path / "fantasma.mp4"), "--saida", str(tmp_path / "saida")])
    out = capsys.readouterr().out
    assert codigo == 1 and "não encontrado" in out and "Pronto" not in out


def test_main_refazer_com_etapa_invalida_da_erro_claro(capsys):
    with pytest.raises(SystemExit):
        main(["x.mp4", "--refazer", "bobagem"])
    assert "desconhecida" in capsys.readouterr().err


def test_encoder_desconhecido_avisa_e_usa_libx264(capsys):
    from cs2clip.config import Config
    from cs2clip import render
    render._AVISADOS.clear()
    cfg = Config()
    cfg.qualidade.encoder = "x264_errado"
    args = render.video_args(cfg)
    assert "libx264" in args
    assert "desconhecido" in capsys.readouterr().out
    render.video_args(cfg)                                   # 2ª vez: não repete o aviso
    assert capsys.readouterr().out == ""
