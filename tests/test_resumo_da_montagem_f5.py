"""F5: o resumo da etapa Montagem dizia "N jogadas, 63s" somando a duração dos MOMENTOS, mas o arquivo é mais curto (cada corte perde as bordas
da transição e há mistura entre cortes): P-05 da prova de 65 min, 63 s no texto × 58,98 s no arquivo. O texto agora usa a duração REAL do arquivo."""
import shutil
import subprocess

import pytest

from cs2clip import pipeline as pl
from cs2clip.tools import FFMPEG


def _video(caminho, segundos):
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={segundos}:r=10", "-pix_fmt", "yuv420p", str(caminho)], check=True)


@pytest.mark.lento
@pytest.mark.skipif(shutil.which("ffprobe") is None and not FFMPEG, reason="precisa do ffmpeg")
def test_a_duracao_do_resumo_e_a_do_arquivo_e_nao_a_soma_dos_momentos(tmp_path):
    arq = tmp_path / "MONTAGEM_x.mp4"
    _video(arq, 3)
    real = pl.duracao_da_montagem(arq, soma_dos_momentos=63.0)
    assert 2.8 <= real <= 3.2                      # o arquivo tem 3 s, não os 63 s da soma


def test_sem_arquivo_legivel_cai_para_a_soma_dos_momentos(tmp_path):
    assert pl.duracao_da_montagem(tmp_path / "nao_existe.mp4", soma_dos_momentos=63.0) == 63.0
    lixo = tmp_path / "lixo.mp4"
    lixo.write_bytes(b"isto nao e video")
    assert pl.duracao_da_montagem(lixo, soma_dos_momentos=63.0) == 63.0


def test_o_resumo_da_etapa_usa_a_duracao_real(monkeypatch):
    import inspect
    fonte = inspect.getsource(pl.render_all)
    assert "duracao_da_montagem(" in fonte and "sum(m.duration for m in na_montagem):.0f}s" not in fonte


@pytest.mark.lento
def test_montagem_de_verdade_o_arquivo_e_mais_curto_que_a_soma_e_o_resumo_diz_o_do_arquivo(tmp_path):
    """Reproduz a dúvida do P-05: 3 cortes de 8 s (soma 24 s) viram um arquivo mais curto por causa das bordas e da mistura; o resumo tem de dizer o do arquivo."""
    from cs2clip.config import Config
    from cs2clip.montagem import render_montagem
    from cs2clip.scan import probe
    from test_montagem_esports import _gerar_corte
    cortes = [_gerar_corte(tmp_path / f"c{i}.mp4", dur=8.0, freq=300 + 100 * i) for i in range(3)]
    out = tmp_path / "MONTAGEM_x.mp4"
    render_montagem(cortes, out, Config())
    soma = 3 * 8.0
    real = pl.duracao_da_montagem(out, soma)
    assert abs(real - probe(str(out)).duration) < 0.01                    # é a duração do arquivo
    assert real < soma - 0.3, (real, soma)                                # e é MENOR que a soma dos momentos (a causa dos 63 s × 58,98 s)
