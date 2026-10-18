"""Revisão final R2 (instalação, caches e arquivos de saída): cada teste falharia antes da correção.
Sem vídeo de verdade: ffmpeg, varredura, Whisper e o download do modelo são trocados por falsos."""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

import cs2clip.pipeline as pl
from cs2clip.config import Config
from cs2clip.indice import atualizar_indice, ler_indice
from cs2clip.rounds import carregar_rounds
from cs2clip.tools import gravar_atomico
from test_r1_pipeline import _analise_falsa, _cfg_render, _info, _jogada, _render_falso, _video

RAIZ = Path(__file__).resolve().parents[1]


# ── instalar.bat: o passo 4 quebrava em todo PC novo (load_config() sem o caminho) ──────────────

def test_baixar_modelo_usa_o_modelo_do_config(monkeypatch, tmp_path):
    import cs2clip.__main__ as main_mod
    pedidos = []
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(download_model=pedidos.append))
    cfg = tmp_path / "config.toml"
    cfg.write_text('[transcricao]\nmodelo = "small"\n', encoding="utf-8")
    assert main_mod.main(["--baixar-modelo", "--config", str(cfg)]) == 0
    assert pedidos == ["small"]


def test_baixar_modelo_que_falha_da_codigo_1_e_diz_o_motivo(monkeypatch, tmp_path, capsys):
    import cs2clip.__main__ as main_mod

    def sem_internet(nome):
        raise OSError("sem internet")
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(download_model=sem_internet))
    assert main_mod.main(["--baixar-modelo", "--config", str(tmp_path / "x.toml")]) == 1
    assert "sem internet" in capsys.readouterr().out


def _bat(nome: str) -> str:
    return (RAIZ / nome).read_text(encoding="utf-8")


def test_instalador_baixa_o_modelo_pelo_programa():
    texto = _bat("instalar.bat")
    assert "-m cs2clip --baixar-modelo" in texto
    assert "load_config()" not in texto


@pytest.mark.parametrize("versao,aceita", [((3, 10, 11), False), ((3, 11, 9), True), ((3, 12, 7), True),
                                           ((3, 13, 0), False), ((3, 14, 0), False)])
def test_instalador_so_aceita_python_3_11_ou_3_12(versao, aceita):
    # rapidocr-onnxruntime (banner de round) declara Requires-Python <3.13: com 3.13+ o pip install falhava
    codigo = re.search(r'set "VERSAO_OK=(.+)"', _bat("instalar.bat")).group(1)
    falso = types.SimpleNamespace(version_info=versao, exit=sys.exit)
    with pytest.raises(SystemExit) as fim:
        exec(codigo.replace("import sys; ", ""), {"sys": falso})
    assert (fim.value.code == 0) == aceita


@pytest.mark.parametrize("nome", ["instalar.bat", "processar.bat"])
def test_bat_da_raiz_tem_crlf(nome):
    conteudo = (RAIZ / nome).read_bytes()
    assert b"\r\n" in conteudo
    assert b"\n" not in conteudo.replace(b"\r\n", b"")


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe só existe no Windows")
def test_processar_sem_instalar_manda_rodar_o_instalador(tmp_path):
    shutil.copy(RAIZ / "processar.bat", tmp_path)
    (tmp_path / "saida").mkdir()
    (tmp_path / "saida" / "relatorio.html").write_text("velho", encoding="utf-8")
    r = subprocess.run(["cmd", "/c", str(tmp_path / "processar.bat")], cwd=tmp_path, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 1 and "instalar.bat" in r.stdout
    assert "Terminou com problemas" not in r.stdout       # antes: código 3 do cmd virava "veja o log"


# ── vídeo com trechos corrompidos (gravação que travou): a varredura rápida caía inteira ─────────

def test_varredura_rapida_pula_trecho_corrompido_e_le_ate_o_fim(tmp_path, capsys):
    from cs2clip.scan import keyframe_candidates, probe
    from cs2clip.tools import FFMPEG
    f = tmp_path / "gravacao.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=20",
                    "-c:v", "mpeg2video", "-g", "10", str(f)], check=True)
    dados = bytearray(f.read_bytes())
    for i in range(int(len(dados) * 0.3), int(len(dados) * 0.6), 7):
        dados[i] ^= 0x5A
    f.write_bytes(dados)
    tempos = []
    keyframe_candidates(probe(str(f)), on_frame=lambda t, img: tempos.append(t))   # antes: InvalidDataError
    assert tempos and tempos[-1] > 18.0                                             # leu até o fim
    assert "corrompido" in capsys.readouterr().out


# ── caches gravados pela metade (Cancelar = taskkill /F, queda de energia) ───────────────────

def test_gravar_atomico_troca_inteiro_e_preserva_o_antigo_se_cair(monkeypatch, tmp_path):
    import cs2clip.tools as tools
    arq = tmp_path / "falas.json"
    gravar_atomico(arq, "[1]")
    assert arq.read_text(encoding="utf-8") == "[1]" and list(tmp_path.iterdir()) == [arq]

    def queda(*a):
        raise OSError("energia")
    monkeypatch.setattr(tools.os, "replace", queda)
    with pytest.raises(OSError):
        gravar_atomico(arq, "[1, 2")
    assert arq.read_text(encoding="utf-8") == "[1]"


def test_caches_pela_metade_sao_refeitos_e_a_analise_segue(monkeypatch, tmp_path):
    cfg = _analise_falsa(monkeypatch, tmp_path, tem_som=True)
    cfg.chat.usar = False
    cfg.shorts.gerar = True
    refeito = []
    monkeypatch.setattr(pl.aud, "load_audio", lambda p: np.zeros(16000, np.float32))
    monkeypatch.setattr(pl.aud, "speech_segments", lambda a: (refeito.append("voz"), [[1.0, 2.0]])[1])
    monkeypatch.setattr(pl.aud, "voice_curve", lambda a, s: np.zeros(int(120 / pl.aud.HOP), np.float32))
    monkeypatch.setattr(pl, "transcribe_full", lambda *a: (refeito.append("falas"), [])[1])
    monkeypatch.setattr(pl, "liberar_modelo", lambda: None)
    monkeypatch.setattr(pl, "detect_layout", lambda *a: (refeito.append("webcam"), pl.Layout(None, None, 0.0))[1])
    monkeypatch.setattr(pl, "preview", lambda *a: None)
    cache = tmp_path / "saida" / "live" / ".cache"
    cache.mkdir(parents=True)
    (cache / "voz.npy").write_bytes(b"\x93NUMPY\x01\x00v\x00{'descr'")
    (cache / "fala_trechos.json").write_text("[[1.0, 2", encoding="utf-8")
    (cache / "falas.json").write_text('[{"w": "boa', encoding="utf-8")
    (cache / "falas.meta.json").write_text(json.dumps({"modelo": cfg.transcricao.modelo,
                                                       "idioma": cfg.transcricao.idioma}), encoding="utf-8")
    (cache / "layout.json").write_text('{"cam": [1, 2', encoding="utf-8")
    (cache / "rounds.json").write_text('[{"t": 3', encoding="utf-8")
    pl.analyze(_video(tmp_path / "in", "live.mp4"), cfg, tmp_path / "saida", tmp_path / "jogador")
    assert refeito == ["voz", "falas", "webcam"]
    np.load(cache / "voz.npy")                                       # agora legíveis
    assert json.loads((cache / "falas.json").read_text(encoding="utf-8")) == []
    assert json.loads((cache / "layout.json").read_text(encoding="utf-8"))["cam"] is None
    assert not list(cache.glob("*.tmp"))


class _ColetorFalso:
    def __init__(self, *a, **k):
        pass

    def feed(self, *a):
        pass

    def result(self):
        return []


@pytest.mark.parametrize("ruim", ["caixas.json", "rounds.json"])
def test_kill_feed_com_cache_ilegivel_varre_de_novo(monkeypatch, tmp_path, ruim):
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "caixas.json").write_text("[]", encoding="utf-8")
    (cache / "rounds.json").write_text("[]", encoding="utf-8")
    (cache / "detector.versao").write_text(pl._versao_cache(None), encoding="utf-8")
    varreu = []
    monkeypatch.setattr(pl, "RoundCollector", _ColetorFalso)
    monkeypatch.setattr(pl, "scan_video_fast", lambda *a, **k: (varreu.append(1), [])[1])
    assert pl._scan_cached(_info(), cache, Config(), False) == []
    assert varreu == []                                   # tudo legível: usa o cache
    (cache / ruim).write_text('[{"t": 1', encoding="utf-8")
    assert pl._scan_cached(_info(), cache, Config(), False) == []
    assert varreu == [1]
    assert carregar_rounds(cache) == [] and json.loads((cache / "caixas.json").read_text(encoding="utf-8")) == []


def test_rounds_ilegivel_vira_sem_rounds(tmp_path):
    (tmp_path / "rounds.json").write_text('[{"t": 1', encoding="utf-8")
    assert carregar_rounds(tmp_path) is None


def test_indice_ilegivel_fica_guardado_em_vez_de_apagar_a_biblioteca(tmp_path, capsys):
    (tmp_path / "indice.json").write_text('[{"id": "a", "arq', encoding="utf-8")
    atualizar_indice(tmp_path, "b", "b.mp4", 10.0, [], [], "d")
    assert (tmp_path / "indice.json.corrompido").read_text(encoding="utf-8").startswith('[{"id": "a"')
    assert [x["id"] for x in ler_indice(tmp_path)] == ["b"]
    assert "ilegível" in capsys.readouterr().out


# ── montagem, junções e MELHORES_DE_TODOS ────────────────────────────────────────────────────

def test_montagem_sem_nenhum_corte_pronto_apaga_a_antiga_e_nao_chama_o_ffmpeg(monkeypatch, tmp_path):
    _render_falso(monkeypatch)

    def disco_cheio(m, tmp, fn):
        raise RuntimeError("disco cheio")
    monkeypatch.setattr(pl, "render_por_janelas", disco_cheio)
    juntou = []
    monkeypatch.setattr(pl, "concat", lambda parts, out, cfg=None: juntou.append(parts))
    out = tmp_path / "saida" / "live"
    out.mkdir(parents=True)
    (out / "MONTAGEM_live.mp4").write_bytes(b"de outra analise")
    cfg = _cfg_render()
    cfg.shorts.gerar = False
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [_jogada()], [], None)
    assert juntou == [] and not (out / "MONTAGEM_live.mp4").exists()


def test_concat_sem_trechos_da_erro_claro(tmp_path):
    from cs2clip.render import concat
    with pytest.raises(ValueError, match="nada para juntar"):
        concat([], tmp_path / "x.mp4")


def test_juncoes_usam_o_encoder_do_config(monkeypatch, tmp_path):
    _render_falso(monkeypatch)
    recebidos = []
    monkeypatch.setattr(pl, "concat", lambda parts, out, cfg=None: (recebidos.append(cfg),
                                                                     Path(out).write_bytes(b"v")))
    cfg = _cfg_render()
    cfg.shorts.gerar = False
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", tmp_path / "saida" / "live", [_jogada()], [], None)
    assert recebidos and all(c is cfg for c in recebidos)


def test_melhores_de_todos_interrompido_nao_fica_pela_metade(monkeypatch, tmp_path):
    import cs2clip.__main__ as main_mod
    import cs2clip.checagem as checagem
    import cs2clip.render as render
    a = _video(tmp_path / "in", "a.mp4")
    b = _video(tmp_path / "in", "b.mp4")
    corte = _video(tmp_path, "corte.mp4")
    monkeypatch.setattr(pl, "analyze", lambda p, cfg, o, pd, R: (_info(60.0), p.stem, o / p.stem, [], [], None))
    monkeypatch.setattr(pl, "render_all", lambda p, *a, **k: ([], [], [(_jogada(), corte)]))
    monkeypatch.setattr(checagem, "gpu_ocupada", lambda: [])

    def cancelado_no_meio(files, out, cfg=None):
        Path(out).write_bytes(b"pela metade")
        raise RuntimeError("cancelado")
    monkeypatch.setattr(render, "concat", cancelado_no_meio)
    saida = tmp_path / "saida"
    cod = main_mod.main([str(a), str(b), "--saida", str(saida), "--config", str(tmp_path / "x.toml")])
    assert cod == 3 and not list(saida.glob("MELHORES_DE_TODOS*"))


# ── exportar_publico.py: apagava tudo no destino sem conferir ────────────────────────────────

def _exportador():
    arq = RAIZ / "tools" / "exportar_publico.py"
    if not arq.exists():
        pytest.skip("o exportador só existe no repositório de trabalho")
    spec = importlib.util.spec_from_file_location("exportar_publico", arq)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_exportador_recusa_destino_perigoso(tmp_path):
    exp = _exportador()
    for ruim in (exp.RAIZ, exp.RAIZ.parent, exp.RAIZ / "saida"):
        with pytest.raises(SystemExit, match="recusado"):
            exp.conferir_destino(ruim)
    cheia = tmp_path / "Projetos"
    cheia.mkdir()
    (cheia / "algo.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit, match="recusado"):
        exp.conferir_destino(cheia)
    assert (cheia / "algo.txt").exists()
    exp.conferir_destino(tmp_path / "nova")
    (tmp_path / "vazia").mkdir()
    exp.conferir_destino(tmp_path / "vazia")
