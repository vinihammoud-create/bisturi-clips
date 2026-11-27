"""A3 da revisão do Claude (31/10/2026): o QC final passa a conferir o CONTEÚDO (tela preta e, se ligado, silêncio)
e o G-006 ganha uma ferramenta de linha de comando com âncora independente do OCR."""
import json
import subprocess
from pathlib import Path

import pytest

import cs2clip.pipeline as pl
from cs2clip import qc
from cs2clip.config import Config
from cs2clip.tools import FFMPEG

# Arquivo inteiro: toda a cobertura aqui depende de render/decodificacao real com o ffmpeg.
pytestmark = pytest.mark.lento


def _gerar(destino: Path, video: str, audio: str | None) -> Path:
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", video]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    subprocess.run(cmd + [str(destino)], check=True, capture_output=True)
    return destino


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("conteudo")
    return {
        "preto": _gerar(pasta / "preto.mp4", "color=c=black:s=320x180:r=30:d=3", "sine=frequency=440:d=3"),
        "bom": _gerar(pasta / "bom.mp4", "testsrc2=s=320x180:r=30:d=3", "sine=frequency=440:d=3"),
        "mudo": _gerar(pasta / "mudo.mp4", "testsrc2=s=320x180:r=30:d=3", "anullsrc=r=48000:cl=stereo:d=3"),
    }


# ── G-008 ligado ao QC final ──────────────────────────────────────────────────────────────────────

def test_qc_final_reprova_video_inteiramente_preto_quando_o_conteudo_esta_ligado(videos):
    esp = qc.Esperado(320, 180, None, True, None, conteudo=True)
    assert "vídeo inteiramente preto" in qc.verificar(videos["preto"], esp)
    with pytest.raises(qc.QcReprovou, match="inteiramente preto"):
        qc.exigir(videos["preto"], esp, "corte 01")
    assert qc.verificar(videos["bom"], esp) == []


def test_qc_final_nao_olha_o_conteudo_se_nao_pedido(videos):
    assert qc.verificar(videos["preto"], qc.Esperado(320, 180, None, True, None)) == []   # comportamento de antes


def test_silencio_so_reprova_quando_pedido_e_o_arquivo_tem_audio(videos):
    sem = qc.Esperado(320, 180, None, True, None, conteudo=True)
    com = qc.Esperado(320, 180, None, True, None, conteudo=True, silencio=True)
    assert qc.verificar(videos["mudo"], sem) == []                       # silêncio é opcional
    assert "áudio inteiramente mudo" in qc.verificar(videos["mudo"], com)
    assert qc.verificar(videos["bom"], com) == []                        # som de verdade passa


def test_pipeline_monta_o_esperado_com_as_opcoes_do_config():
    cfg = Config()
    assert cfg.qualidade.checar_conteudo is True and cfg.qualidade.checar_silencio is False   # padrões
    esp = pl._esperado(cfg, 1920, 1080, 12.0, True)
    assert (esp.largura, esp.altura, esp.duracao, esp.audio, esp.fps) == (1920, 1080, 12.0, True, 60.0)
    assert esp.conteudo is True and esp.silencio is False
    cfg.qualidade.checar_conteudo, cfg.qualidade.checar_silencio = False, True
    assert pl._esperado(cfg, 1080, 1920, None, True).conteudo is False
    assert pl._esperado(cfg, 1080, 1920, None, True).silencio is True
    assert pl._esperado(cfg, 1080, 1920, None, False).silencio is False   # sem áudio na origem não há o que cobrar


# ── G-006: ferramenta com âncora independente ─────────────────────────────────────────────────────

def _partida():
    from cs2clip.demo import Abate, PartidaDemo, RoundDemo
    return PartidaDemo(
        "de_inferno", {1: "Eu"}, 1, "Eu", [RoundDemo(1, 5.0, 60.0, "CT")],
        [Abate(10.0, 1, "v1", "ak47"), Abate(20.0, 1, "v2", "ak47"), Abate(30.0, 1, "v3", "ak47")],
        [], [], (1, 0), {1: "CT"})


def _momentos(pasta: Path, tempos: list[float]) -> Path:
    arq = pasta / "momentos.json"
    # o mesmo abate aparece em dois momentos (sequência + reação): conta uma vez só
    arq.write_text(json.dumps({"formato": 1, "momentos": [
        {"n": 1, "abates": [{"t": t, "icones": []} for t in tempos]},
        {"n": 2, "abates": [{"t": tempos[0] + 0.01, "icones": []}]}]}), encoding="utf-8")
    return arq


@pytest.mark.parametrize("texto,esperado", [("754.5", 754.5), ("12:34.5", 754.5), ("1:02:03", 3723.0),
                                            ("12:34,5", 754.5), (" 90 ", 90.0)])
def test_tempo_para_segundos(texto, esperado):
    from tools.avaliar_ocr_demo import tempo_para_segundos
    assert tempo_para_segundos(texto) == pytest.approx(esperado)


@pytest.mark.parametrize("ruim", ["", "a:b", "1:2:3:4", "-5"])
def test_tempo_invalido_e_recusado(ruim):
    from tools.avaliar_ocr_demo import tempo_para_segundos
    with pytest.raises(ValueError):
        tempo_para_segundos(ruim)


def test_abates_do_video_nao_conta_o_mesmo_abate_duas_vezes(tmp_path):
    from tools.avaliar_ocr_demo import abates_do_video
    assert abates_do_video(_momentos(tmp_path, [105.1, 115.0, 135.0])) == [105.1, 115.0, 135.0]


def test_avaliar_usa_a_ancora_e_nao_o_ocr_para_o_deslocamento():
    from tools.avaliar_ocr_demo import avaliar
    # round 1 começa em 5 s na demo e a pessoa viu que começa em 105 s no vídeo -> deslocamento 100 s
    r = avaliar(_partida(), [110.1, 120.05, 150.0], ancora_video=105.0, tolerancia=0.5)
    assert r["deslocamento_ancora_s"] == pytest.approx(100.0)
    assert (r["tp"], r["fp"], r["fn"]) == (2, 1, 1)
    assert r["precisao"] == pytest.approx(0.6667, abs=1e-3) and r["recall"] == pytest.approx(0.6667, abs=1e-3)
    assert 0 < r["erro_mediano_s"] <= 0.1
    # uma âncora errada em 3 s derruba o acerto: prova que o deslocamento NÃO é ajustado aos abates do OCR
    errada = avaliar(_partida(), [110.1, 120.05, 150.0], ancora_video=108.0, tolerancia=0.5)
    assert errada["tp"] == 0


def test_avaliar_sem_rounds_na_demo_da_erro():
    from cs2clip.demo import PartidaDemo
    from tools.avaliar_ocr_demo import avaliar
    with pytest.raises(ValueError, match="rounds"):
        avaliar(PartidaDemo("de_x", {1: "Eu"}, 1, "Eu", [], [], [], [], (0, 0), {}), [1.0], 0.0)


def test_main_imprime_o_resultado_e_grava_o_json(monkeypatch, tmp_path, capsys):
    import tools.avaliar_ocr_demo as ferramenta
    monkeypatch.setattr(ferramenta, "ler_demo", lambda caminho, jogador=None: _partida())
    mom = _momentos(tmp_path, [110.1, 120.05, 150.0])
    saida = tmp_path / "metricas.json"
    cod = ferramenta.main([str(mom), "x.dem", "--jogador", "Eu", "--ancora", "1:45", "--tolerancia", "0.5",
                           "--json", str(saida)])
    assert cod == 0
    txt = capsys.readouterr().out
    assert "precisão 67%" in txt and "recall 67%" in txt and "100.00 s" in txt
    assert json.loads(saida.read_text(encoding="utf-8"))["tp"] == 2


def test_main_diz_o_motivo_quando_algo_falha(monkeypatch, tmp_path, capsys):
    import tools.avaliar_ocr_demo as ferramenta
    from cs2clip.demo import PartidaDemo
    mom = _momentos(tmp_path, [110.0])
    assert ferramenta.main([str(mom), "x.dem", "--jogador", "Eu", "--ancora", "abc"]) == 1      # âncora inválida
    assert "tempo inválido" in capsys.readouterr().out
    assert ferramenta.main([str(tmp_path / "nao_existe.json"), "x.dem", "--jogador", "Eu", "--ancora", "1"]) == 1
    sem_jogador = PartidaDemo("de_x", {1: "Fulano"}, None, "", [], [], [], [], (0, 0), {})
    monkeypatch.setattr(ferramenta, "ler_demo", lambda caminho, jogador=None: sem_jogador)
    assert ferramenta.main([str(mom), "x.dem", "--jogador", "Eu", "--ancora", "1"]) == 1
    assert "Fulano" in capsys.readouterr().out                                                  # lista os jogadores
