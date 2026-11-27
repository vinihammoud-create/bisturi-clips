"""G-019 (2), ajuste do orquestrador: conferência de conteúdo que não rodou pede revisão; defeito de verdade reprova."""
from pathlib import Path

import pytest

from cs2clip import qc, qualidade


@pytest.mark.parametrize("problemas, esperado", [
    (["não consegui conferir se o vídeo é preto: o ffmpeg falhou (código 1)"], "NEEDS_REVIEW"),
    (["não consegui conferir se o vídeo é preto: o ffmpeg passou de 120 s", "não consegui conferir se o áudio é mudo: o ffmpeg falhou (código 1)"], "NEEDS_REVIEW"),
    (["vídeo inteiramente preto"], "FAIL"),
    (["não consegui conferir se o vídeo é preto: o ffmpeg falhou (código 1)", "áudio inteiramente mudo"], "FAIL"),
    ([], "PASS"),
])
def test_conferencia_que_nao_rodou_pede_revisao_e_defeito_reprova(monkeypatch, problemas, esperado):
    monkeypatch.setattr(qc, "verificar_conteudo", lambda caminho, silencio=False: list(problemas))
    gate = qualidade.gate_conteudo(Path("qualquer.mp4"), True, True)
    assert gate.status == esperado
    assert qualidade.status_geral([gate]) == esperado


def test_no_render_a_conferencia_que_nao_rodou_avisa_mas_nao_rejeita_o_corte(monkeypatch, tmp_path, capsys):
    arq = tmp_path / "corte.mp4"
    arq.write_bytes(b"x" * (qc.TAMANHO_MIN + 1))
    monkeypatch.setattr(qc, "inspecionar", lambda c: {"video": {"width": 1920, "height": 1080, "pix_fmt": "yuv420p"}, "audio": True, "duracao": 10.0})
    monkeypatch.setattr(qc, "verificar_conteudo", lambda c, s, silencio=False: [f"{qc.NAO_CONFERIU} se o vídeo é preto: o ffmpeg falhou (código 1)"])
    esp = qc.Esperado(conteudo=True)
    assert qc.verificar(arq, esp) == []
    qc.exigir(arq, esp)                                              # não levanta
    assert "não consegui conferir" in capsys.readouterr().out
    monkeypatch.setattr(qc, "verificar_conteudo", lambda c, s, silencio=False: ["vídeo inteiramente preto"])
    with pytest.raises(qc.QcReprovou):
        qc.exigir(arq, esp)
