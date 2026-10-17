"""Revisão R1 (pipeline, main, chat, config, refazer): cada teste falharia antes da correção.
Sem vídeo de verdade: as etapas pesadas (ffmpeg, varredura, Whisper, GPU) são trocadas por falsas."""
import json
import types
from pathlib import Path

import numpy as np
import pytest

import cs2clip.chat as chat
import cs2clip.pipeline as pl
import cs2clip.qc as qc
from cs2clip.config import Config, load_config
from cs2clip.indice import atualizar_indice, ler_indice
from cs2clip.moments import Event, Moment
from cs2clip.transcribe import Word


def _info(dur=600.0, w=1920, h=1080):
    return types.SimpleNamespace(path="x", duration=dur, width=w, height=h, fps=60.0)


def _video(pasta: Path, nome: str, tamanho: int = 100) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    f = pasta / nome
    f.write_bytes(b"\0" * tamanho)
    return f


# ── #1/#27: nome de data não vira VOD; cache conferido pela origem do vídeo ─────────────────

def test_nome_de_data_do_celular_nao_vira_vod():
    assert chat.vod_id_from_name("20261017_201533.mp4") is None
    assert chat.vod_id_from_name("20261017_230000.mp4") is None
    assert chat.vod_id_from_name("2902813681-cs2.mp4") == "2902813681"      # VOD real (10 dígitos)
    assert chat.vod_id_from_name("v2902813681_live.mp4") == "2902813681"
    assert chat.vod_id_from_name("2902813681.mp4") == "2902813681"


def test_nome_so_com_caracteres_nao_latinos_nao_vira_a_pasta_saida(tmp_path):
    v = _video(tmp_path / "in", "日本.mp4")
    nome = pl._pasta_do_video(v, _info(), None, tmp_path / "saida")
    assert nome.startswith("video-") and len(nome) == len("video-") + 6


def test_video_trocado_com_o_mesmo_nome_apaga_o_cache(tmp_path):
    cache = tmp_path / "saida" / "live" / ".cache"
    pl._conferir_origem(cache, {"tamanho": 100, "duracao": 600.0, "largura": 1920, "altura": 1080})
    (cache / "caixas.json").write_text("[]", encoding="utf-8")
    (cache / "render_meta.json").write_text("{}", encoding="utf-8")
    # mesmo vídeo: cache fica
    pl._conferir_origem(cache, {"tamanho": 100, "duracao": 600.0, "largura": 1920, "altura": 1080})
    assert (cache / "caixas.json").exists()
    # outro vídeo com o mesmo nome (outro tamanho/duração): cache inteiro some
    pl._conferir_origem(cache, {"tamanho": 999, "duracao": 1200.0, "largura": 1920, "altura": 1080})
    assert not (cache / "caixas.json").exists() and not (cache / "render_meta.json").exists()
    assert json.loads((cache / "origem.json").read_text(encoding="utf-8"))["tamanho"] == 999


def test_cache_antigo_sem_origem_e_mantido(tmp_path):
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "caixas.json").write_text("[]", encoding="utf-8")
    pl._conferir_origem(cache, {"tamanho": 1, "duracao": 1.0, "largura": 1, "altura": 1})
    assert (cache / "caixas.json").exists() and (cache / "origem.json").exists()


def test_origem_nao_usa_data_de_modificacao(tmp_path):
    v = _video(tmp_path, "live.mp4")
    o = pl._origem(v, _info())
    assert set(o) == {"tamanho", "duracao", "largura", "altura"}


def test_dois_videos_existentes_com_o_mesmo_nome_nao_dividem_a_pasta(tmp_path):
    saida = tmp_path / "saida"
    a = _video(tmp_path / "dia1", "live.mp4", 100)
    b = _video(tmp_path / "dia2", "live.mp4", 200)
    assert pl._pasta_do_video(a, _info(), None, saida) == "live"
    pl._conferir_origem(saida / "live" / ".cache", pl._origem(a, _info()))
    atualizar_indice(saida, "live", a.name, 600.0, [], [], "2026-10-17 10:00", str(a.resolve()))
    nome_b = pl._pasta_do_video(b, _info(), None, saida)
    assert nome_b.startswith("live-") and nome_b != "live"
    assert pl._pasta_do_video(b, _info(), None, saida) == nome_b      # sempre a mesma pasta para o mesmo arquivo
    assert pl._pasta_do_video(a, _info(), None, saida) == "live"      # o primeiro continua na dele
    # o arquivo do índice foi trocado no mesmo caminho: mesma pasta (o cache é que é apagado)
    a.write_bytes(b"\0" * 300)
    assert pl._pasta_do_video(a, _info(), None, saida) == "live"


# ── #11/#30 (sem som) e #13 (chat corrompido): a análise segue ──────────────────────────────

def _analise_falsa(monkeypatch, tmp_path, tem_som: bool):
    monkeypatch.setattr(pl, "probe", lambda p: _info(120.0))
    monkeypatch.setattr(pl, "_scan_cached", lambda *a, **k: [])
    monkeypatch.setattr(pl, "mortes_cached", lambda *a, **k: [])
    monkeypatch.setattr(qc, "tem_audio", lambda v: tem_som)

    def sem_ffmpeg(*a, **k):
        raise AssertionError("não pode extrair o áudio de um vídeo sem som")
    monkeypatch.setattr(pl.aud, "load_audio", sem_ffmpeg)
    monkeypatch.setattr(pl, "transcribe_full", sem_ffmpeg)
    cfg = Config()
    cfg.shorts.gerar = False
    return cfg


def test_video_sem_som_e_analisado_so_com_abates(monkeypatch, tmp_path):
    cfg = _analise_falsa(monkeypatch, tmp_path, tem_som=False)
    cfg.chat.usar = False
    v = _video(tmp_path / "in", "sem-som.mp4")
    info, name, out, moments, words, lay = pl.analyze(v, cfg, tmp_path / "saida", tmp_path / "jogador")
    assert words == []
    assert not (out / ".cache" / "voz.npy").exists()       # cache vazio não fica guardado
    assert not (out / ".cache" / "falas.json").exists()


def test_chat_corrompido_e_apagado_e_a_analise_segue(monkeypatch, tmp_path):
    cfg = _analise_falsa(monkeypatch, tmp_path, tem_som=False)
    v = _video(tmp_path / "in", "2902813681-live.mp4")
    cj = tmp_path / "saida" / "2902813681" / ".cache" / "chat.json"
    cj.parent.mkdir(parents=True)
    cj.write_text('{"comments": [{"content_offset_seconds": 1, "message": {"body": "' + "x" * 200,
                  encoding="utf-8")                          # download interrompido: JSON pela metade
    monkeypatch.setattr(pl, "download_chat", lambda vod, f: f.exists())
    pl.analyze(v, cfg, tmp_path / "saida", tmp_path / "jogador")
    assert not cj.exists()


def test_download_do_chat_pela_metade_nao_fica_com_o_nome_final(monkeypatch, tmp_path):
    cli = tmp_path / "TwitchDownloaderCLI.exe"
    cli.write_bytes(b"")
    monkeypatch.setattr(chat, "CLI", cli)
    destino = tmp_path / "chat.json"

    def cli_morre(cmd, **k):
        Path(cmd[cmd.index("-o") + 1]).write_text('{"comments": [' + "x" * 300, encoding="utf-8")
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(chat.subprocess, "run", cli_morre)
    assert not chat.download_chat("2902813681", destino)
    assert not destino.exists() and not (tmp_path / "chat.tmp.json").exists()

    def cli_ok(cmd, **k):
        Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps({"comments": [], "x": "y" * 200}), encoding="utf-8")
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(chat.subprocess, "run", cli_ok)
    assert chat.download_chat("2902813681", destino)
    assert json.loads(destino.read_text(encoding="utf-8"))["comments"] == []


# ── #10: termos de empolgação só das janelas que o render mostra ─────────────────────────────

def test_risada_no_vao_cortado_da_sequencia_nao_conta():
    m = Moment(106.0, 152.5, "abate", segmentos=[(106.0, 112.5), (146.0, 152.5)])
    words = [Word(130.0, 130.5, "kkkkk"), Word(130.6, 131.0, "kkkk")]
    assert pl._termos_nas_janelas(words, m) == []
    words.append(Word(147.0, 147.5, "kkkkk"))
    assert pl._termos_nas_janelas(words, m) == ["risada"]
    simples = Moment(100.0, 140.0, "abate")
    assert pl._termos_nas_janelas([Word(130.0, 130.5, "kkkkk")], simples) == ["risada"]


# ── #34: miniatura que falha não impede o corte ──────────────────────────────────────────────

def test_miniatura_no_fim_do_video_fica_presa_antes_do_fim(monkeypatch, tmp_path):
    pedidos = []

    def thumb(src, t, out, width=480):
        pedidos.append(t)
        Path(out).write_bytes(b"jpg")
    monkeypatch.setattr(pl, "thumbnail", thumb)
    pl._miniatura("v.mp4", 599.9, 600.0, tmp_path / "a.jpg")
    assert pedidos[-1] == pytest.approx(599.9)


def test_miniatura_sem_quadro_da_erro_claro(monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "thumbnail", lambda *a, **k: None)   # ffmpeg não gravou nada
    with pytest.raises(RuntimeError, match="sem quadro"):
        pl._miniatura("v.mp4", 10.0, 600.0, tmp_path / "a.jpg")


# ── render_all com render falso ──────────────────────────────────────────────────────────────

def _render_falso(monkeypatch, thumb_falha=False):
    def escreve(m, dest, *a, **k):
        Path(dest).write_bytes(b"video")
    monkeypatch.setattr(pl, "render_por_janelas", lambda m, tmp, fn: escreve(m, tmp))
    monkeypatch.setattr(pl, "concat", lambda parts, out: Path(out).write_bytes(b"video"))
    monkeypatch.setattr(qc, "exigir", lambda *a, **k: None)
    monkeypatch.setattr(qc, "tem_audio", lambda v: True)

    def thumb(src, t, out, width=480):
        if thumb_falha:
            raise RuntimeError("ffmpeg sem quadro")
        Path(out).write_bytes(b"jpg")
    monkeypatch.setattr(pl, "thumbnail", thumb)


def _jogada(idx=1, t=50.0):
    m = Moment(t - 4, t + 3, "abate", kills=[Event(t, "kill"), Event(t + 1, "kill")], label="DOUBLE KILL",
               score=40.0, idx=idx, parte_tipo="partida")
    return m


def _cfg_render():
    cfg = Config()
    cfg.qualidade.renders_paralelos = 1
    return cfg


def test_miniatura_que_falha_nao_impede_o_corte(monkeypatch, tmp_path):
    _render_falso(monkeypatch, thumb_falha=True)
    out = tmp_path / "saida" / "live"
    m = _jogada()
    cfg = _cfg_render()
    cfg.shorts.gerar = False
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [m], [], None)
    assert list((out / "cortes").glob("*.mp4"))


def test_sem_shorts_e_sem_montagem_nao_apagam_o_que_ja_existe(monkeypatch, tmp_path):
    _render_falso(monkeypatch)
    out = tmp_path / "saida" / "live"
    m = _jogada()
    cfg = _cfg_render()
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [m], [], None)   # tudo ligado
    shorts = sorted((out / "shorts").glob("*.mp4"))
    assert shorts and (out / "MONTAGEM_live.mp4").exists()
    cfg.shorts.gerar = False      # --sem-shorts
    cfg.montagem.gerar = False    # --sem-montagem
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [m], [], None)
    assert sorted((out / "shorts").glob("*.mp4")) == shorts
    assert (out / "MONTAGEM_live.mp4").exists()
    dados = json.loads((out / "momentos.json").read_text(encoding="utf-8"))
    assert dados["momentos"][0]["arquivos"]["short"]          # o momento continua apontando para o short


def test_desligar_shorts_nao_rerenderiza_os_cortes(monkeypatch, tmp_path):
    _render_falso(monkeypatch)
    feitos = []
    monkeypatch.setattr(pl, "render_por_janelas", lambda m, tmp, fn: (feitos.append(tmp.name),
                                                                       Path(tmp).write_bytes(b"v")))
    out = tmp_path / "saida" / "live"
    cfg = _cfg_render()
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [_jogada()], [], None)
    feitos.clear()
    cfg.shorts.gerar = False
    cfg.qualidade.renders_paralelos = 2
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [_jogada()], [], None)
    assert feitos == []                                     # o corte 16:9 continua valendo


def test_revisao_velha_some_quando_nao_ha_cortes(monkeypatch, tmp_path):
    _render_falso(monkeypatch)
    out = tmp_path / "saida" / "live"
    out.mkdir(parents=True)
    (out / "revisao.html").write_text("velha", encoding="utf-8")
    pl.render_all(tmp_path / "v.mp4", _cfg_render(), _info(), "live", out, [], [], None)
    assert not (out / "revisao.html").exists()


# ── #31: configuração que derrubaria o render; erro no render não derruba o lote ─────────────

@pytest.mark.parametrize("texto", ["[qualidade]\nrenders_paralelos = 0\n", "[deteccao]\nprocessos = 0\n",
                                   "[deteccao]\nfps_analise = 0\n", "[shorts]\nquantidade = -1\n"])
def test_config_invalida_da_erro_na_leitura(tmp_path, texto):
    p = tmp_path / "config.toml"
    p.write_text(texto, encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(p)


def test_erro_no_render_de_um_video_nao_derruba_o_lote(monkeypatch, tmp_path):
    import cs2clip.__main__ as main_mod
    import cs2clip.checagem as checagem
    a = _video(tmp_path / "in", "a.mp4")
    b = _video(tmp_path / "in", "b.mp4")
    vistos = []

    def analise(p, cfg, out_root, player_dir, R):
        return _info(60.0), p.stem, out_root / p.stem, [], [], None

    def render(p, *a, **k):
        vistos.append(p.name)
        if p.name == "a.mp4":
            raise RuntimeError("falhou")
        return [], [], []
    monkeypatch.setattr(pl, "analyze", analise)
    monkeypatch.setattr(pl, "render_all", render)
    monkeypatch.setattr(checagem, "gpu_ocupada", lambda: [])
    cod = main_mod.main([str(a), str(b), "--saida", str(tmp_path / "saida"), "--config", str(tmp_path / "x.toml")])
    assert vistos == ["a.mp4", "b.mp4"]
    assert cod == 3 and (tmp_path / "saida" / "relatorio.html").exists()


# ── #38: pasta só com demos; #44: demo entra no índice ──────────────────────────────────────

def _demo_falsa(monkeypatch):
    import cs2clip.demo as demo
    vistas = []

    def analisar(d, cfg, out_root, jogador):
        vistas.append(d.name)
        out = out_root / d.stem
        out.mkdir(parents=True, exist_ok=True)
        partida = types.SimpleNamespace(abates=[], mortes=[], clutches=[], titulo="Mirage 13x7", nome="eu",
                                        rounds=[types.SimpleNamespace(fim=1800.0)])
        return partida, [Moment(10, 20, "abate", kills=[Event(15, "kill")])], out
    monkeypatch.setattr(demo, "analisar_demo", analisar)
    return vistas


def test_pasta_so_com_demo_analisa_a_demo_e_registra_no_indice(monkeypatch, tmp_path):
    import cs2clip.__main__ as main_mod
    vistas = _demo_falsa(monkeypatch)
    _video(tmp_path / "entrada", "partida.dem")
    saida = tmp_path / "saida"
    cod = main_mod.main([str(tmp_path / "entrada"), "--saida", str(saida), "--config", str(tmp_path / "x.toml")])
    assert cod == 0 and vistas == ["partida.dem"]
    linha = next(x for x in ler_indice(saida) if x["id"] == "partida")
    assert linha["arquivo"] == "partida.dem" and linha["duracao"] == 1800.0 and linha["abates"] == 1


def test_pasta_com_video_e_demo_processa_so_o_video(monkeypatch, tmp_path, capsys):
    import cs2clip.__main__ as main_mod
    import cs2clip.checagem as checagem
    vistas = _demo_falsa(monkeypatch)
    _video(tmp_path / "entrada", "partida.dem")
    _video(tmp_path / "entrada", "live.mp4")
    analisados = []
    monkeypatch.setattr(pl, "analyze", lambda p, *a: (analisados.append(p.name), (_info(60.0), "live",
                                                     tmp_path / "saida" / "live", [], [], None))[1])
    monkeypatch.setattr(pl, "render_all", lambda *a, **k: ([], [], []))
    monkeypatch.setattr(checagem, "gpu_ocupada", lambda: [])
    main_mod.main([str(tmp_path / "entrada"), "--saida", str(tmp_path / "saida"), "--config", str(tmp_path / "x.toml")])
    assert analisados == ["live.mp4"] and vistas == []
    assert "demo(s) na pasta ignorada(s)" in capsys.readouterr().out


# ── #7/#15/#35: a marca padrão é vazia ───────────────────────────────────────────────────────

def test_marca_padrao_e_vazia():
    assert Config().shorts.marca == ""
