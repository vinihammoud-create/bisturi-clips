"""D7 (Plano D): editar a partida a partir do vídeo LIMPO gravado pela demo (e a voz da live). ffmpeg de verdade, clipes sintéticos pequenos.

Cada teste tem CONTROLE. Os que rodam o `render_all` inteiro são `lento`.
"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from cs2clip import editar_demo as ed
from cs2clip import video_limpo as vl
from cs2clip.config import Config
from cs2clip.demo import Abate, PartidaDemo, RoundDemo
from cs2clip.moments import Event, Moment
from cs2clip.tools import FFMPEG, FFPROBE
from cs2clip.video_limpo import ErroVideoLimpo, RoundGravado

T = 64.0


def _clip(destino: Path, dur: float, freq: int = 300, audios: int = 2) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=30:d={dur}"]
    for k in range(audios):
        cmd += ["-f", "lavfi", "-i", f"sine=f={freq + 100 * k}:d={dur}"]
    cmd += ["-map", "0:v"] + sum([["-map", f"{k + 1}:a"] for k in range(audios)], [])
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", str(destino)]   # estéreo, como o jogo gravado pelo HLAE
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


def _n_audios(arq: Path) -> int:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(arq)],
                       capture_output=True, text=True)
    return len([x for x in r.stdout.splitlines() if x.strip()])


def _cenario(tmp: Path, dur=8.0):
    """Dois rounds gravados: round 2 (demo 200 s) e round 5 (demo 500 s), com `registro.json` como o gravador deixa."""
    rounds_dir = tmp / "grav" / "rounds"
    trechos, resultados, rounds_demo, abates = [], [], [], []
    for n, ini, ks in ((2, 200.0, [202.0, 203.5, 205.0]), (5, 500.0, [502.0, 503.0, 504.0, 505.5])):
        f = _clip(rounds_dir / f"round_{n:02d}.mp4", dur + 5, 300 + n)
        rounds_demo.append(RoundDemo(n, ini, ini + dur, "CT", "x", tick_inicio=int(ini * T), tick_fim=int((ini + dur) * T)))
        abates += [Abate(t, n, "x", "ak47", tick=int(t * T)) for t in ks]
        trechos.append({"round": n, "tick_ini": int((ini - 2) * T), "tick_fim": int((ini + dur + 3) * T), "abates": len(ks), "ticks_abates": []})
        resultados.append({"round": n, "arquivo": str(f)})
    (tmp / "grav").mkdir(exist_ok=True)
    (tmp / "grav" / "registro.json").write_text(json.dumps({"trechos": trechos, "resultados": resultados}), encoding="utf-8")
    partida = PartidaDemo("de_mirage", {1: "eu"}, 1, "Fulano Exemplo", rounds_demo, abates=abates, accountid=1, user_id=5, patch_version="14191")
    partida.placar = (5, 13)
    return partida, tmp / "grav"


# ── leitura do registro e junção ──────────────────────────────────────────────────────────────────

@pytest.mark.lento
def test_rounds_gravados_le_o_registro_e_ignora_arquivo_sumido_ou_quebrado(tmp_path):
    partida, grav = _cenario(tmp_path)
    rs = vl.rounds_gravados(grav)
    assert [r.round for r in rs] == [2, 5] and rs[0].t0 == pytest.approx((200.0 - 2.0)) and rs[0].duracao == pytest.approx(13.0, abs=0.3)
    (grav / "rounds" / "round_05.mp4").write_bytes(b"quebrado")                                    # arquivo ilegível: ignorado
    assert [r.round for r in vl.rounds_gravados(grav)] == [2]
    (grav / "rounds" / "round_02.mp4").unlink()
    with pytest.raises(ErroVideoLimpo, match="Nenhum round"):
        vl.rounds_gravados(grav)
    with pytest.raises(ErroVideoLimpo, match="Não achei o registro"):
        vl.rounds_gravados(tmp_path / "nao_existe")


@pytest.mark.lento
def test_montar_partida_limpa_junta_sem_reencodar_e_da_o_deslocamento_de_cada_round(tmp_path):
    partida, grav = _cenario(tmp_path)
    rs = vl.rounds_gravados(grav)
    arq, desloc = vl.montar_partida_limpa(rs, tmp_path / "saida" / "PARTIDA_LIMPA.mp4")
    dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(arq)], capture_output=True, text=True).stdout)
    assert dur == pytest.approx(sum(r.duracao for r in rs), abs=0.5) and _n_audios(arq) == 2
    assert desloc[2] == pytest.approx(0.0 - rs[0].t0) and desloc[5] == pytest.approx(rs[0].duracao - rs[1].t0)
    # o tempo da demo vira o tempo do arquivo: o abate de 502 s do round 5 cai a (13 + 502 - 498) s do começo
    assert 502.0 + desloc[5] == pytest.approx(rs[0].duracao + 4.0)
    assert not list((tmp_path / "saida").glob("*.lista.txt")) and not list((tmp_path / "saida").glob("*.parcial*"))


def test_mapear_momentos_leva_os_tempos_e_deixa_de_fora_round_nao_gravado_e_trecho_que_passa_do_arquivo():
    rs = [RoundGravado(2, Path("a.mp4"), 198.0, 211.0, 13.0), RoundGravado(5, Path("b.mp4"), 498.0, 511.0, 13.0)]
    desloc = {2: -198.0, 5: 13.0 - 498.0}
    dentro = Moment(199.0, 207.0, "abate", kills=[Event(202.0, "kill", ["headshot"]), Event(203.5, "kill")], label="DOUBLE KILL", score=30, idx=1, round_n=2,
                    segmentos=[(199.0, 204.0), (206.0, 207.0)], pico=203.5)
    do_5 = Moment(500.0, 507.0, "abate", kills=[Event(502.0, "kill")], score=20, idx=2, round_n=5)
    nao_gravado = Moment(300.0, 306.0, "abate", kills=[Event(302.0, "kill")], score=20, idx=3, round_n=3)
    passa_do_fim = Moment(205.0, 215.0, "abate", kills=[Event(212.0, "kill")], score=20, idx=4, round_n=2)       # abate além do que foi gravado: fora
    passa_muito = Moment(205.0, 222.0, "abate", kills=[Event(206.0, "kill")], score=20, idx=5, round_n=2)        # 11 s além: fora
    aparavel = Moment(203.0, 214.0, "abate", kills=[Event(206.0, "kill"), Event(208.5, "kill")], score=40, idx=6, round_n=2,
                      segmentos=[(203.0, 207.0), (207.5, 214.0)], pico=213.0)                                       # 3 s além, abates dentro: aparado
    mapeados = vl.mapear_momentos([dentro, do_5, nao_gravado, passa_do_fim, passa_muito, aparavel], rs, desloc)
    assert [m.round_n for m in mapeados] == [2, 5, 2] and [m.idx for m in mapeados] == [1, 2, 3]
    ap = mapeados[2]                                                                                                 # fim aparado em t1 − 0,2 (= 210,8 → 12,8)
    assert ap.end == pytest.approx(12.8) and [round(x, 3) for s in ap.segmentos for x in s] == [5.0, 9.0, 9.5, 12.8] and ap.pico == pytest.approx(12.8)
    assert [k.t for k in ap.kills] == [8.0, 10.5]
    assert (aparavel.end, aparavel.pico) == (214.0, 213.0)                                                          # o original não mudou
    m = mapeados[0]
    assert (m.start, m.end) == (1.0, 9.0) and [k.t for k in m.kills] == [4.0, 5.5] and m.kills[0].hs and m.segmentos == [(1.0, 6.0), (8.0, 9.0)] and m.pico == 5.5
    assert mapeados[1].kills[0].t == pytest.approx(502.0 + 13.0 - 498.0)
    assert (dentro.start, dentro.kills[0].t) == (199.0, 202.0)                                                     # os momentos originais não mudaram


# ── a voz da live ─────────────────────────────────────────────────────────────────────────────────

def _rms_db(arq: Path, faixa: int, ini: float, dur: float) -> float:
    r = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-ss", f"{ini}", "-t", f"{dur}", "-i", str(arq), "-map", f"0:a:{faixa}", "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "pipe:1"], capture_output=True, check=True)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0
    return 20 * np.log10(float(np.sqrt(np.mean(x ** 2))) + 1e-9)


@pytest.mark.lento
def test_voz_da_live_entra_na_faixa_1_e_a_faixa_2_continua_so_o_jogo(tmp_path):
    clip = _clip(tmp_path / "clip.mp4", 6.0, 300)
    # live de 4 faixas (mistura, microfone, jogo, discord); o microfone (faixa 2) só fala de 10 a 14 s, em 800 Hz forte
    live = tmp_path / "live.mkv"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=30", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=30",
                    "-f", "lavfi", "-i", "sine=f=800:d=4,adelay=10000|10000,apad=whole_dur=30", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=30",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=30", "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a", "-map", "4:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-ac", "2", "-t", "30", str(live)], check=True, capture_output=True)
    out = vl.juntar_voz_da_live(clip, live, inicio_na_live=10.0, faixa_voz=2, destino=tmp_path / "com_voz.mp4")
    assert _n_audios(out) == 2
    # dentro do MESMO arquivo: com a voz (0 a 4 s) a faixa 1 é mais forte que a faixa 2 (só o jogo); depois que a voz acaba as duas se igualam
    assert _rms_db(out, 0, 0.5, 3.0) > _rms_db(out, 1, 0.5, 3.0) + 1.5
    assert abs(_rms_db(out, 0, 4.5, 1.2) - _rms_db(out, 1, 4.5, 1.2)) < 1.0
    # CONTROLE: começando num trecho em que o microfone está calado, as duas faixas ficam iguais o tempo todo
    mudo = vl.juntar_voz_da_live(clip, live, inicio_na_live=20.0, faixa_voz=2, destino=tmp_path / "mudo.mp4")
    assert abs(_rms_db(mudo, 0, 0.5, 3.0) - _rms_db(mudo, 1, 0.5, 3.0)) < 1.0
    with pytest.raises(ErroVideoLimpo, match="faixa 1"):
        vl.juntar_voz_da_live(clip, live, 10.0, 1, tmp_path / "x.mp4")
    with pytest.raises(ErroVideoLimpo, match="antes do início"):
        vl.juntar_voz_da_live(clip, live, -1.0, 2, tmp_path / "y.mp4")


def test_deslocamento_vem_da_analise_da_live_e_recusa_demo_nao_usada(tmp_path):
    (tmp_path / "sincronia.json").write_text(json.dumps([{"demo": "a.dem", "usada": True, "deslocamento": 125.3}, {"demo": "b.dem", "usada": False, "motivo": "x"},
                                                         {"demo": "c.dem", "usada": True, "deslocamento": None}]), encoding="utf-8")
    assert vl.deslocamento_da_analise(tmp_path, "a.dem") == 125.3
    for nome in ("b.dem", "c.dem", "d.dem"):
        with pytest.raises(ErroVideoLimpo, match="não usou"):
            vl.deslocamento_da_analise(tmp_path, nome)
    with pytest.raises(ErroVideoLimpo, match="sincronia.json"):
        vl.deslocamento_da_analise(tmp_path / "nao_existe", "a.dem")


# ── a edição inteira ──────────────────────────────────────────────────────────────────────────────

def test_config_do_video_limpo_usa_as_duas_faixas_do_jogo_sem_webcam_nem_discord_e_nao_mexe_na_original():
    cfg = Config()
    cfg.audio.faixa_jogo, cfg.audio.faixa_voz, cfg.audio.faixa_comms, cfg.audio.tratar_voz = 3, 2, 4, True
    cfg.gravacao_demo.hud = False
    c = ed.config_do_video_limpo(cfg)
    assert (c.audio.faixa_jogo, c.audio.faixa_voz, c.audio.faixa_comms, c.audio.tratar_voz, c.audio.fonte_limpa) == (2, 0, 0, False, True)
    assert c.shorts.mostrar_killfeed is False
    assert (cfg.audio.faixa_jogo, cfg.audio.faixa_voz, cfg.audio.faixa_comms, cfg.audio.fonte_limpa) == (3, 2, 4, False)      # a original intacta


@pytest.mark.lento
def test_edicao_do_video_limpo_gera_cortes_shorts_montagem_com_musica_capa_e_qualidade(tmp_path, monkeypatch):
    partida, grav = _cenario(tmp_path)
    monkeypatch.setattr(ed, "ler_demo", lambda caminho, quem=None: partida)
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    cfg.qualidade.garantir_pico = False
    cfg.shorts.versao_efeitos = False
    cfg.gravacao_demo.conta_principal_id = "76561198071376839"
    # música com drop no catálogo de teste (a pasta real de músicas não vai para o GitHub)
    demo = tmp_path / "match730_003849321203751714819_0599033075_201.dem"
    demo.write_bytes(b"PBDEMS2")
    mensagens = []
    out = ed.editar(demo, cfg, pasta_gravacao=grav, saida_raiz=tmp_path / "saida", imprimir=mensagens.append)
    assert out.name.endswith("_limpo") and (out / "PARTIDA_LIMPA.mp4").is_file()
    assert any("2 round(s) gravado(s)" in m for m in mensagens) and any("2 jogada(s)" in m for m in mensagens)
    cortes = sorted((out / "cortes").glob("*.mp4"))
    assert len(cortes) == 2 and len(list((out / "shorts").glob("*_vertical.mp4"))) == 2
    assert (out / f"{out.name}".join(["MONTAGEM_", ".mp4"])).is_file()
    assert (out / "qualidade.json").is_file() and (out / "relatorio.html").is_file() and list(out.glob("CAPA_*.jpg"))
    dados = json.loads((out / "momentos.json").read_text(encoding="utf-8"))["momentos"]
    assert {m["round"] for m in dados} == {2, 5} and all(m["arquivos"].get("corte") for m in dados)
    indice = json.loads((tmp_path / "saida" / "indice.json").read_text(encoding="utf-8"))
    assert indice[0]["id"] == out.name and indice[0]["abates"] == 7                                  # entra na biblioteca da interface
    assert not any("igual" in m for m in mensagens)                                                    # sem aviso de "faixa igual à faixa 1"
    # a config do dono não foi alterada pelo fluxo
    assert cfg.audio.fonte_limpa is False and cfg.audio.faixa_jogo == 0


@pytest.mark.lento
def test_edicao_recusa_quando_nenhuma_jogada_cai_nos_rounds_gravados_e_pede_a_pasta_da_analise(tmp_path, monkeypatch):
    partida, grav = _cenario(tmp_path)
    partida.abates = [Abate(900.0, 9, "x", "ak47", tick=int(900 * T))]
    partida.rounds.append(RoundDemo(9, 890.0, 920.0, "CT", "x", tick_inicio=int(890 * T), tick_fim=int(920 * T)))
    monkeypatch.setattr(ed, "ler_demo", lambda caminho, quem=None: partida)
    cfg = Config()
    demo = tmp_path / "match730_003849321203751714819_0599033075_201.dem"
    demo.write_bytes(b"PBDEMS2")
    with pytest.raises(ErroVideoLimpo, match="Nenhuma jogada sua"):
        ed.editar(demo, cfg, pasta_gravacao=grav, saida_raiz=tmp_path / "saida", imprimir=lambda *a: None)
    with pytest.raises(ErroVideoLimpo, match="pasta da análise"):
        ed.editar(demo, cfg, pasta_gravacao=grav, saida_raiz=tmp_path / "saida2", live=tmp_path / "live.mkv", imprimir=lambda *a: None)


@pytest.mark.lento
def test_rounds_gravados_descontam_os_segundos_parados_do_comeco(tmp_path):
    partida, grav = _cenario(tmp_path)
    reg = json.loads((grav / "registro.json").read_text(encoding="utf-8"))
    reg["resultados"][0]["congelado_inicio_s"] = 1.2                                              # a gravação começa pausada no tick_ini
    (grav / "registro.json").write_text(json.dumps(reg), encoding="utf-8")
    rs = vl.rounds_gravados(grav)
    assert rs[0].t0 == pytest.approx(200.0 - 2.0 - 1.2) and rs[1].t0 == pytest.approx(500.0 - 2.0)   # CONTROLE: sem o campo, nada muda


def _mp4(destino: Path, faixas: int) -> Path:
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=2"]
    for _ in range(faixas):
        cmd += ["-f", "lavfi", "-i", "sine=f=440:d=2"]
    cmd += ["-map", "0:v:0"] + [x for k in range(faixas) for x in ("-map", f"{k + 1}:a:0")] + ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


@pytest.mark.lento
def test_faixas_de_audio_conta_certo_e_devolve_zero_para_arquivo_ruim(tmp_path):
    assert vl.faixas_de_audio(_mp4(tmp_path / "vod.mp4", 1)) == 1 and vl.faixas_de_audio(_mp4(tmp_path / "obs.mp4", 4)) == 4
    assert vl.faixas_de_audio(_mp4(tmp_path / "mudo.mp4", 0)) == 0
    (tmp_path / "lixo.mp4").write_bytes(b"nao e video")
    assert vl.faixas_de_audio(tmp_path / "lixo.mp4") == 0 and vl.faixas_de_audio(tmp_path / "nao_existe.mp4") == 0


@pytest.mark.lento
def test_live_de_uma_faixa_com_faixa_voz_2_sai_sem_a_voz_e_avisa_em_vez_de_quebrar_no_fim(tmp_path, monkeypatch):
    """Achado A1 da revisão de 11/11: VOD da Twitch / vídeo do YouTube (1 faixa) + demo + audio.faixa_voz = 2 quebrava DEPOIS de regravar tudo."""
    partida, grav = _cenario(tmp_path)
    monkeypatch.setattr(ed, "ler_demo", lambda caminho, quem=None: partida)
    monkeypatch.setattr(vl, "deslocamento_da_analise", lambda pasta, nome: 12.5)
    chamadas = []
    import cs2clip.pipeline as pipeline
    monkeypatch.setattr(pipeline, "render_all", lambda *a, **k: chamadas.append(1) or ([], None, None))
    cfg = Config()
    cfg.audio.faixa_voz = 2
    cfg.gravacao_demo.conta_principal_id = "76561198071376839"
    demo = tmp_path / "match730_003849321203751714819_0599033075_201.dem"
    demo.write_bytes(b"PBDEMS2")
    msgs = []
    out = ed.editar(demo, cfg, pasta_gravacao=grav, saida_raiz=tmp_path / "saida", live=_mp4(tmp_path / "vod.mp4", 1), analise_da_live=tmp_path / "analise", imprimir=msgs.append)
    assert any("A live tem só 1 faixa(s) de áudio" in m and "SEM a sua voz" in m for m in msgs) and chamadas == [1]
    assert not (out / "_com_voz").exists() and not any("Voz da live" in m for m in msgs)                       # nada de voz foi misturado
    # CONTROLE: a gravação do OBS (4 faixas) continua juntando a voz
    msgs2 = []
    out2 = ed.editar(demo, cfg, pasta_gravacao=grav, saida_raiz=tmp_path / "saida2", live=_mp4(tmp_path / "obs.mp4", 4), analise_da_live=tmp_path / "analise", imprimir=msgs2.append)
    assert any("Voz da live (faixa 2) junta em 2 round(s)" in m for m in msgs2) and (out2 / "_com_voz").is_dir()

