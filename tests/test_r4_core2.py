"""R4 (grupo core2): um teste por achado corrigido. Cada teste falharia antes da correção."""
import numpy as np
import cv2
import pytest


# --- 3: area_bruta só aceita proporção conhecida --------------------------------------------------------

def _jogo(w, h, seed=0):
    return np.random.default_rng(seed).integers(40, 220, (h, w), dtype=np.uint8)


def _com_barras(W, H, x, y, w, h, n=8):
    out = []
    for k in range(n):
        f = np.full((H, W), 16, np.uint8)
        f[y:y + h, x:x + w] = _jogo(w, h, k)
        out.append(f)
    return out


def test_item3_overlay_escuro_simetrico_nao_vira_area_do_jogo():
    from cs2clip.layout import area_bruta
    assert area_bruta(_com_barras(1920, 1080, 0, 60, 1920, 960)) is None        # faixa de 60 px em cima e embaixo
    assert area_bruta(_com_barras(1920, 1080, 300, 0, 1320, 1080)) is None      # painel de 60 px de cada lado do 4:3


def test_item3_proporcoes_conhecidas_continuam_iguais():
    from cs2clip.layout import AREA_VERSAO, area_bruta
    assert area_bruta(_com_barras(1920, 1080, 240, 0, 1440, 1080)) == (240, 0, 1440, 1080)
    assert area_bruta(_com_barras(1920, 1080, 0, 128, 1920, 824)) is not None   # 21:9 dentro de 16:9
    assert area_bruta([_jogo(1920, 1080, k) for k in range(8)]) is None         # vídeo real sem barras
    assert AREA_VERSAO != "1"   # cache de area_jogo.json da regra antiga é refeito


# --- 13: "quase" e "aff" sozinhos não são raiva ---------------------------------------------------------

def test_item13_quase_e_aff_sem_contexto_nao_viram_raiva():
    from test_emotion import _fala, _voz
    from cs2clip.emotion import classificar
    assert classificar(40, 50, _voz({45: 0.9}), _fala(44, "quase dez horas de live"), [], None) is None
    assert classificar(40, 50, _voz({45: 0.9}), _fala(44, "aff que calor"), [], None) is None
    # com o round perdido continua sendo frustração
    assert classificar(40, 50, _voz({45: 0.9}), _fala(44, "aff"), [], "derrota").categoria == "RAIVA"


# --- 14: gabarito só com abate/clutch não avalia emoção --------------------------------------------------

@pytest.mark.parametrize("cats", [{"abate"}, {"clutch"}])
def test_item14_linha_so_com_abate_nao_gera_falso_positivo_de_emocao(cats):
    from cs2clip.medicao import Linha, medir
    momentos = [{"n": 1, "foco": 100.0, "inicio": 95.0, "fim": 108.0, "emocao": "HYPE", "abates": [{"t": 100.0}]}]
    rel = medir([Linha("v", 100, set(cats), "sim")], {"v": momentos})
    assert rel.metricas["alegria"].fp == 0
    rel = medir([Linha("v", 100, {"nenhuma"}, "")], {"v": momentos})   # quem diz "nenhuma" respondeu
    assert rel.metricas["alegria"].fp == 1


# --- 19: PlayerModel.load ignora arquivo fora do padrão ---------------------------------------------------

def test_item19_player_model_ignora_png_estranho(tmp_path, capsys):
    from cs2clip.classify import PlayerModel
    from cs2clip.tools import imwrite
    imwrite(tmp_path / "00_20.png", np.full((16, 20), 255, np.uint8))
    imwrite(tmp_path / "nome.png", np.full((16, 20), 255, np.uint8))        # nome fora do padrão
    (tmp_path / "01_30.png").write_bytes(b"nao e png")                       # truncado: imread -> None
    imwrite(tmp_path / "02_0.png", np.full((16, 20), 255, np.uint8))        # largura zero
    modelo = PlayerModel.load(tmp_path)
    assert modelo is not None and len(modelo.exemplars) == 1
    assert "ignorado" in capsys.readouterr().out
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    (vazia / "lixo.png").write_bytes(b"x")
    assert PlayerModel.load(vazia) is None


# --- 29: o corte pedido por "clipa isso" não perde a marca CLIPA ---------------------------------------

def test_item29_anotar_nao_troca_o_rotulo_clipa():
    from test_emotion import _fala, _partes_demo, _voz
    from cs2clip.emotion import anotar
    from cs2clip.moments import Moment
    m = Moment(440.0, 452.0, "reacao", label="CLIPA", pedido=True)
    anotar([m], _partes_demo(), _voz({446.0: 0.9}, 800.0), _fala(445.0, "que merda velho"), [])
    assert m.label == "CLIPA" and m.emocao == "RAIVA"      # a emoção segue em `emocao`
    r = Moment(440.0, 452.0, "reacao", label="REAÇÃO")      # reação comum continua ganhando o rótulo da emoção
    anotar([r], _partes_demo(), _voz({446.0: 0.9}, 800.0), _fala(445.0, "que merda velho"), [])
    assert r.label == "RAIVA"


# --- 30: reação sem emoção não vira short ---------------------------------------------------------------

def test_item30_reacao_sem_emocao_nao_vira_short():
    from cs2clip.moments import Moment, escolher_shorts
    r = Moment(0.0, 12.0, "reacao", score=35, idx=1)
    assert escolher_shorts([r], nota_short=30, quantidade=5) == []
    r.emocao = "HYPE"
    assert escolher_shorts([r], nota_short=30, quantidade=5) == [r]
    a = Moment(20.0, 30.0, "abate", score=35, idx=2)             # abate continua pela nota
    assert escolher_shorts([a], nota_short=30, quantidade=5) == [a]
    p = Moment(40.0, 52.0, "reacao", score=5, idx=3, pedido=True)   # pedido na live tem vaga garantida
    assert escolher_shorts([p], nota_short=30, quantidade=5) == [p]


# --- 31: abates sem round (banner perdido) a poucos segundos se juntam ----------------------------------

def test_item31_abates_na_mesma_partida_sem_round_se_juntam_se_o_vao_e_curto():
    from test_sequencia import cortes, jogada
    from cs2clip.partes import dividir_partes
    from cs2clip.rounds import RoundTimelineEntry as R
    from cs2clip.sequencia import unir_sequencias
    # banners em 100, 200, 400, 500 (o de 300 não foi lido): 230 e 242 ficam longe do banner de 400
    partes = dividir_partes([R(t, "vitoria", .9) for t in (100.0, 200.0, 400.0, 500.0)], [], 600.0)
    assert len(unir_sequencias([jogada([230.0]), jogada([242.0])], partes, cortes())) == 1
    assert len(unir_sequencias([jogada([230.0]), jogada([290.0])], partes, cortes())) == 2
    assert len(unir_sequencias([jogada([230.0]), jogada([242.0])], partes, cortes(), mortes=[236.0])) == 2


# --- 32: vídeo curto não perde as reações na margem fixa -------------------------------------------------

def test_item32_margem_de_abertura_encolhe_em_video_curto():
    from cs2clip.config import Config
    from cs2clip.moments import build_moments
    n = int(60.0 / 0.1) + 1
    V = np.zeros(n)
    V[300:320] = 1.0                                      # grito em t ~ 30-32 s de um clipe de 60 s
    ms = build_moments([], V, None, None, [], 60.0, Config())
    assert [m.kind for m in ms] == ["reacao"]
    # numa live longa a margem de 90/30 s continua valendo
    n = int(1800.0 / 0.1) + 1
    V = np.zeros(n)
    V[600:620] = 1.0                                      # t = 60 s
    assert build_moments([], V, None, None, [], 1800.0, Config()) == []


# --- 33: o download do chat tem tempo limite ------------------------------------------------------------

def test_item33_download_do_chat_que_trava_e_abandonado(monkeypatch, tmp_path, capsys):
    import subprocess
    import cs2clip.chat as chat
    cli = tmp_path / "TwitchDownloaderCLI.exe"
    cli.write_bytes(b"")
    monkeypatch.setattr(chat, "CLI", cli)
    destino = tmp_path / "chat.json"
    visto = []

    def trava(cmd, **k):
        visto.append(k.get("timeout"))
        (tmp_path / "chat.tmp.json").write_text("{", encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd, k["timeout"])
    monkeypatch.setattr(chat.subprocess, "run", trava)
    assert not chat.download_chat("2902813681", destino)
    assert visto == [chat.CHAT_TIMEOUT_S]                       # sem segunda tentativa: a API travou
    assert not destino.exists() and not (tmp_path / "chat.tmp.json").exists()
    assert "indisponível" in capsys.readouterr().out


# --- 34: a GPU que falha NA transcrição cai para a CPU, uma vez -----------------------------------------

def test_item34_gpu_que_falha_durante_a_transcricao_repete_na_cpu(monkeypatch, capsys):
    import sys
    import types
    import cs2clip.transcribe as tr
    modelos = []

    class FalsoWhisper:
        def __init__(self, nome, device, compute_type):
            self.device = device
            modelos.append(device)

        def transcribe(self, audio, **k):
            if self.device == "cuda":
                raise RuntimeError("CUDA out of memory")
            seg = types.SimpleNamespace(words=[types.SimpleNamespace(start=0.0, end=0.5, word=" oi ")])
            return iter([seg]), None
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FalsoWhisper))
    monkeypatch.setattr(tr, "_MODEL", None)
    monkeypatch.setattr(tr, "_MODEL_NA_CPU", False)
    palavras = tr.transcribe(np.zeros(16000, np.float32), 10.0)
    assert [(w.start, w.text) for w in palavras] == [(10.0, "oi")]
    assert modelos == ["cuda", "cpu"]
    assert "CPU" in capsys.readouterr().out
    tr.liberar_modelo()


def test_item34_erro_na_cpu_sobe_sem_repetir(monkeypatch):
    import sys
    import types
    import cs2clip.transcribe as tr
    chamadas = []

    class Quebrado:
        def __init__(self, nome, device, compute_type):
            if device == "cuda":
                raise RuntimeError("sem GPU")

        def transcribe(self, audio, **k):
            chamadas.append(1)
            raise RuntimeError("falhou de verdade")
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Quebrado))
    monkeypatch.setattr(tr, "_MODEL", None)
    monkeypatch.setattr(tr, "_MODEL_NA_CPU", False)
    with pytest.raises(RuntimeError, match="falhou de verdade"):
        tr.transcribe(np.zeros(16000, np.float32), 0.0)
    assert chamadas == [1]
    tr.liberar_modelo()


# --- 35: erro do ffmpeg no áudio diz o motivo ----------------------------------------------------------

def _precisa_ffmpeg():
    from cs2clip import tools
    try:
        tools.FFMPEG
    except SystemExit:
        pytest.skip("ffmpeg indisponível")


def test_item35_load_audio_com_arquivo_ruim_diz_o_motivo(tmp_path):
    from cs2clip import audio
    _precisa_ffmpeg()
    ruim = tmp_path / "ruim.mp4"
    ruim.write_bytes(bytes(range(256)) * 20)
    with pytest.raises(RuntimeError, match="ffmpeg não conseguiu ler o áudio: .+"):
        audio.load_audio(str(ruim))


# --- 36: "clipa isso" é um termo só ---------------------------------------------------------------------

def test_item36_frase_longa_nao_duplica_a_curta():
    from cs2clip.transcribe import Word, hype_events, hype_terms
    assert hype_terms([Word(0, .4, "clipa"), Word(.4, .8, "isso")], 0, 5) == ["clipa isso"]
    assert hype_terms([Word(0, .4, "clipa")], 0, 5) == ["clipa"]
    assert [(s, e, w) for s, e, w, _ in hype_events([Word(0, .4, "Clipa"), Word(.4, .8, "isso!")])] == [(0, .8, 1.0)]
    assert hype_terms([Word(0, .4, "clipa"), Word(.5, .9, "nossa")], 0, 5) == ["clipa", "nossa"]


# --- 37: trecho mudo na junção ganha silêncio em vez de derrubar o som dos outros ------------------------

def _clip(destino, com_som, dur=1, tam="320x180"):
    import subprocess
    from cs2clip import tools
    cmd = [tools.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={tam}:rate=60:duration={dur}"]
    if com_som:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={dur}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


def _x264():
    from cs2clip.config import Config
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    return cfg


@pytest.mark.parametrize("ordem", [(False, True), (True, False), (False, True, False)])
def test_item37_concat_com_trecho_mudo_mantem_o_som_dos_outros(tmp_path, ordem):
    from cs2clip import render
    _precisa_ffmpeg()
    arqs = [_clip(tmp_path / f"c{i}.mp4", som) for i, som in enumerate(ordem)]
    out = tmp_path / "junto.mp4"
    render.concat(arqs, out, _x264())
    assert render._faixa(out, "a:0", "codec_name")                       # tem trilha de som
    assert abs(render._duracao(out) - len(ordem)) < 0.25                  # e o silêncio completou o tempo
    assert abs(float(render._faixa(out, "a:0", "duration") or 0) - len(ordem)) < 0.3


def test_item37_so_mudos_continua_sem_audio(tmp_path):
    from cs2clip import render
    _precisa_ffmpeg()
    a = _clip(tmp_path / "a.mp4", False)
    b = _clip(tmp_path / "b.mp4", False, tam="160x90")        # formato diferente força o reencode
    out = tmp_path / "junto.mp4"
    render.concat([a, b], out, _x264())
    assert render._faixa(out, "a:0", "codec_name") == ""


# --- 39: cópia das fontes em paralelo ------------------------------------------------------------------

def test_item39_render_short_copia_fontes_sem_corrida(tmp_path, monkeypatch):
    import threading
    import time
    from cs2clip import render
    from cs2clip.config import Config
    from cs2clip.moments import Moment
    fontes = tmp_path / "D'Angelo" / "fontes"
    fontes.mkdir(parents=True)
    for n in range(4):
        (fontes / f"f{n}.ttf").write_bytes(b"x" * 200_000)
    monkeypatch.setattr(render, "FONTES", fontes)
    monkeypatch.setattr(render, "measure_loudness", lambda *a, **k: {})
    monkeypatch.setattr(render, "audio_filter", lambda *a, **k: "anull")
    monkeypatch.setattr(render, "_run_enc", lambda mk, **k: mk())
    ativas, maximo, trava = [0], [0], threading.Lock()
    copiar = render.shutil.copy2

    def copia_lenta(a, b):   # alarga a janela da corrida: duas cópias ao mesmo tempo = o defeito
        with trava:
            ativas[0] += 1
            maximo[0] = max(maximo[0], ativas[0])
        time.sleep(0.05)
        copiar(a, b)
        with trava:
            ativas[0] -= 1
    monkeypatch.setattr(render.shutil, "copy2", copia_lenta)
    monkeypatch.setattr(render, "video_args", lambda cfg: [])
    ass = tmp_path / "shorts" / "a.ass"
    ass.parent.mkdir()
    ass.write_text("", encoding="utf-8")
    erros = []

    def uma():
        try:
            render.render_short("x.mp4", Moment(0.0, 10.0, "abate"), tmp_path / "s.mp4", Config(), None, ass_file=ass)
        except Exception as e:   # noqa: BLE001
            erros.append(e)
    ts = [threading.Thread(target=uma) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not erros, erros
    assert maximo[0] == 1
    copia = ass.parent / "_fontes"
    assert sorted(p.name for p in copia.iterdir()) == [f"f{n}.ttf" for n in range(4)]   # nada de .tmp sobrando
    assert all(p.stat().st_size == 200_000 for p in copia.iterdir())


# --- 40: imread de arquivo que não existe devolve None ---------------------------------------------------

def test_item40_imread_de_arquivo_inexistente_devolve_none(tmp_path):
    from cs2clip.tools import imread, imwrite
    assert imread(tmp_path / "nao_existe.png") is None
    assert imread(tmp_path / "nao_existe.png", cv2.IMREAD_GRAYSCALE) is None
    imwrite(tmp_path / "ok.png", np.full((4, 4), 200, np.uint8))
    assert imread(tmp_path / "ok.png", cv2.IMREAD_GRAYSCALE).shape == (4, 4)


# --- 43: revisao.html tem doctype, idioma e charset ----------------------------------------------------

def test_item43_pagina_de_revisao_tem_doctype_idioma_e_charset():
    from cs2clip.revisao import PAGE
    topo = PAGE[:400].lower()
    assert topo.lstrip().startswith("<!doctype html>")
    assert '<html lang="pt-br">' in topo and '<meta charset="utf-8">' in topo
    assert "__VIDEO__" in PAGE and "__DATA__" in PAGE
