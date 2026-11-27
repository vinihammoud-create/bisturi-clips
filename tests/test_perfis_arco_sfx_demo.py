"""Testes dos itens Q1 (perfis/questionário), M6 (Kill-Sync), M8 (ordem em arco), V6 (SFX CC0), D2/S1 (entry/no_ar) e L3/L4."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from cs2clip import qc
from cs2clip.config import Config, load_config
from cs2clip.demo import Abate, Clutch, PartidaDemo, RoundDemo, momentos_da_demo
from cs2clip.moments import Event, Moment
from cs2clip.montagem import alinhar_tiros_e_emendas, ordenar_montagem, render_montagem
from cs2clip.perfis import PERFIS, aplicar_perfil, aplicar_questionario
from cs2clip.sfx import garantir_sfx
from cs2clip.tools import FFMPEG

# Arquivo inteiro: toda a cobertura aqui depende de render/decodificacao real com o ffmpeg.
pytestmark = pytest.mark.lento


def test_q1_perfis_e_questionario_ajustam_config(tmp_path):
    for nome in PERFIS:
        cfg = aplicar_perfil(Config(), nome)
        assert cfg.montagem.ordem in ("cronologica", "melhores", "arco")

    # Questionário: Fragmovie Musical (2), só jogo + música (1), ritmo rápido (1), ordem em arco (1)
    cfg_q = aplicar_questionario(Config(), destino="A", estilo=2, audio=1, ritmo=1, ordem=1)
    assert cfg_q.montagem.gerar is True and cfg_q.shorts.gerar is False
    assert cfg_q.montagem.ordem == "arco" and cfg_q.montagem.kill_sync is True and cfg_q.montagem.sfx is True
    assert cfg_q.cortes.pular_parado == 3.5

    # Perfil no config.toml com override manual do usuário
    p = tmp_path / "config.toml"
    p.write_text('[montagem]\nperfil = "fragmovie_musical"\nsfx = false\n', encoding="utf-8")
    lido = load_config(p)
    assert lido.montagem.ordem == "arco" and lido.montagem.sfx is False


def test_m8_ordenar_montagem_em_arco_abre_com_top2_e_fecha_com_top1():
    jogadas = [
        Moment(10, 15, "abate", score=40, idx=1),
        Moment(20, 25, "abate", score=95, idx=2),   # Top 1 (clímax)
        Moment(30, 35, "abate", score=75, idx=3),   # Top 2 (gancho)
        Moment(40, 45, "abate", score=55, idx=4),
        Moment(50, 55, "abate", score=25, idx=5),
    ]
    arco = ordenar_montagem(jogadas, "arco")
    # Abre com a Top 2 (score 75), cresce pelo miolo (25 -> 40 -> 55) e termina na Top 1 (score 95)
    assert [m.score for m in arco] == [75, 25, 40, 55, 95]


def test_m6_kill_sync_alinha_primeiro_tiro_na_batida():
    # Grade de batidas a cada 0,5 s (0.5, 1.0, 1.5, 2.0, 2.5, 3.0...)
    grade = np.arange(0.5, 15.0, 0.5)
    # Corte 0 começa em ini=0.12, tem duração 4.0 e o 1º tiro está em tiro_rel=2.37 s (ou seja, t_tiro = 2.25 s).
    # Recuando o início em +0.25 s (ini = 0.37), o tiro cai cravado na batida 2.00 s!
    inicios, durs = alinhar_tiros_e_emendas([0.12, 0.12], [4.0, 4.0], [2.37, 2.30], grade, mistura=0.0)
    t_tiro_0 = 2.37 - inicios[0]
    assert abs(t_tiro_0 - 2.0) < 1e-6
    # E a emenda para o 2º corte também cai num múltiplo de 0,5 s
    assert abs((durs[0] % 0.5)) < 1e-6


def test_v6_sfx_cc0_gera_wavs_e_entra_na_montagem(tmp_path):
    arqs = garantir_sfx(tmp_path / "sfx")
    assert arqs["whoosh"].is_file() and arqs["whoosh"].stat().st_size > 1000
    assert arqs["impacto"].is_file() and arqs["impacto"].stat().st_size > 1000

    # Renderiza uma montagem curta com sfx = True
    c1, c2 = tmp_path / "c1.mp4", tmp_path / "c2.mp4"
    for dest, f in ((c1, 440), (c2, 660)):
        subprocess.run([
            FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60:duration=2",
            "-f", "lavfi", "-i", f"sine=frequency={f}:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest),
        ], check=True, capture_output=True)
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    cfg.montagem.sfx = True
    out = tmp_path / "MONTAGEM_sfx.mp4"
    render_montagem([c1, c2], out, cfg, musica=None)
    assert qc.verificar(out, qc.Esperado(1920, 1080, None, True, 60.0)) == []


def test_d2_nota_valoriza_abate_de_abertura_e_no_ar():
    p_normal = PartidaDemo("de_mirage", {1: "Eu"}, 1, "Eu", [RoundDemo(1, 0.0, 100.0, "T")],
                           [Abate(15.0, 1, "v", "ak47", abertura=False, no_ar=False)], [], [], (1, 0), {1: "T"})
    p_entry_air = PartidaDemo("de_mirage", {1: "Eu"}, 1, "Eu", [RoundDemo(1, 0.0, 100.0, "T")],
                              [Abate(15.0, 1, "v", "ssg08", abertura=True, no_ar=True)], [], [], (1, 0), {1: "T"})
    [m0] = momentos_da_demo(p_normal)
    [m1] = momentos_da_demo(p_entry_air)
    assert m1.abertura is True and "inair" in m1.kills[0].icons
    assert m1.score - m0.score == 10
    assert any("abertura" in r for r in m1.reasons) and any("no ar" in r for r in m1.reasons)


def test_l3_e_l4_ferramentas_de_estudo_e_mineracao(tmp_path):
    from tools.estudar_referencia import estudar_video
    from tools.treinar_demos import estatisticas_partidas

    vid = tmp_path / "ref.mp4"
    subprocess.run([
        FFMPEG, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(vid),
    ], check=True, capture_output=True)
    rel = estudar_video(vid)
    assert rel["duracao_s"] >= 2.8 and "sugestao_config" in rel

    p = PartidaDemo("de_dust2", {1: "Eu"}, 1, "Eu", [RoundDemo(1, 0.0, 60.0, "T")],
                    [Abate(10.0, 1, "v", "ak47", headshot=True, abertura=True)], [], [Clutch(1, 20.0, 2, True, 1)],
                    (1, 0), {1: "T"})
    est = estatisticas_partidas([p])
    assert est["partidas_lidas"] == 1 and est["abates_totais"] == 1 and est["clutches_ganhos"] == 1
    assert "headshot" in est["pesos_raridade_sugeridos"]


def test_m1_editplan_pydantic_valida_e_salva(tmp_path):
    from pydantic import ValidationError
    from cs2clip.plano import EditPlan, TrechoPlano, criar_plano_momento

    m = Moment(10.0, 18.0, "abate", kills=[Event(14.0, "kill", ["headshot"])], label="HEADSHOT", score=65.0, idx=1, round_n=5)
    cfg = Config()
    plano = criar_plano_momento(m, cfg, formato="16:9")
    assert plano.duracao_total == 8.0
    assert plano.trechos[0].primeiro_tiro_s == 14.0
    assert plano.camadas_texto[0].texto == "ROUND 5 · HEADSHOT"

    arq = plano.salvar(tmp_path / "planos" / "01.json")
    assert arq.is_file()

    # Garante que fim <= inicio ou velocidade absurda são bloqueados pelo Pydantic
    with pytest.raises(ValidationError):
        TrechoPlano(inicio=10.0, fim=5.0)
    with pytest.raises(ValidationError):
        TrechoPlano(inicio=0.0, fim=5.0, velocidade=10.0)
    with pytest.raises(ValidationError):
        EditPlan(id_momento="x", formato="formato_invalido", trechos=[TrechoPlano(inicio=0.0, fim=2.0)])


def test_v4_cartao_tatico_a2_a3_ducking_e_s2_diversidade(tmp_path):
    from cs2clip.audio_mix import filtro_mix_voz_e_jogo_com_ducking, filtro_tratamento_voz
    from cs2clip.cartao import gerar_ass_cartao, texto_cartao
    from cs2clip.moments import diversificar_shorts

    # V4: cartão tático broadcast
    m = Moment(10.0, 20.0, "abate", kills=[Event(12.0, "kill", ["headshot"]), Event(14.0, "kill", ["headshot"])],
               label="DOUBLE KILL", round_n=7, clutch=(3, True), abertura=True)
    txt = texto_cartao(m)
    assert "ROUND 7" in txt and "CLUTCH 1v3 (2 HS)" in txt and "ENTRY" in txt
    ass = gerar_ass_cartao(tmp_path / "cartao.ass", m)
    assert ass is not None and ass.is_file() and "\\fad(150,250)" in ass.read_text(encoding="utf-8")

    # A2 + A3: tratamento de voz + ducking sidechain válido no FFmpeg
    assert "highpass=f=90" in filtro_tratamento_voz()
    fc = filtro_mix_voz_e_jogo_com_ducking(idx_voz=1, idx_jogo=2)
    assert "sidechaincompress" in fc
    vid = tmp_path / "multi.mp4"
    out = tmp_path / "duck.mp4"
    subprocess.run([
        FFMPEG, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1.5",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=1.5",
        "-f", "lavfi", "-i", "sine=frequency=600:duration=1.5",
        "-map", "0:v:0", "-map", "1:a:0", "-map", "1:a:0", "-map", "2:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(vid),
    ], check=True, capture_output=True)
    r = subprocess.run([
        FFMPEG, "-v", "error", "-y", "-i", str(vid),
        "-filter_complex", fc, "-map", "0:v:0", "-map", "[a_mix]",
        "-c:v", "copy", "-c:a", "aac", str(out),
    ], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    # S2: diversificar_shorts evita repetir o mesmo rótulo quando há jogada variada com nota próxima
    ms = [
        Moment(10, 15, "abate", label="DOUBLE KILL", score=50, idx=1),
        Moment(20, 25, "abate", label="DOUBLE KILL", score=49, idx=2),
        Moment(30, 35, "abate", label="DOUBLE KILL", score=48, idx=3),
        Moment(40, 45, "abate", label="CLUTCH 1v2", score=46, idx=4),
    ]
    div = diversificar_shorts(ms, nota_short=30, quantidade=2)
    assert [x.label for x in div] == ["DOUBLE KILL", "CLUTCH 1v2"]


def test_v9_capa_youtube_m5_davinci_fcpxml_e_u1_arco_ui(tmp_path):
    from cs2clip.capa import gerar_capa
    from cs2clip.davinci import exportar_fcpxml
    from ui.configuracao import CAMPOS_POR_CHAVE

    # U1: "arco" disponível na interface web
    assert "arco" in CAMPOS_POR_CHAVE[("montagem", "ordem")].opcoes

    # V9: gera CAPA_<video>.jpg em 1280x720
    vid = tmp_path / "jogo.mp4"
    subprocess.run([
        FFMPEG, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(vid),
    ], check=True, capture_output=True)
    m = Moment(0.5, 2.5, "abate", kills=[Event(1.5, "kill", ["headshot"])], label="ACE", score=92.0, idx=1, round_n=12)
    capa = gerar_capa(str(vid), [m], tmp_path / "CAPA_jogo.jpg", duracao_video=3.0)
    assert capa is not None and capa.is_file() and capa.stat().st_size > 2000

    # M5: exporta timeline FCPXML 1.9 para DaVinci Resolve com marcadores de abate e batida
    fcpxml = exportar_fcpxml(tmp_path / "timeline.fcpxml", [m], [vid], fps=60, batidas_s=[0.5, 1.0, 1.5])
    conteudo = fcpxml.read_text(encoding="utf-8")
    assert '<fcpxml version="1.9">' in conteudo and 'value="HEADSHOT"' in conteudo and 'value="BATIDA"' in conteudo


def test_v1_killfeed_short_v2_pan_v3_gancho_m7_speed_ramp_v8_freeze_e_a4(tmp_path):
    from cs2clip import render
    from cs2clip.captions import texto_gancho_short
    from cs2clip.efeitos import (
        expressao_pan_suave,
        extrair_jogo_de_faixa_unica,
        gerar_trechos_speed_ramp,
        render_clip_speed_ramp,
        render_freeze_headshot,
        rife_executavel,
    )

    # V3: ganchos variados mantendo o rótulo original
    m_hs = Moment(10.0, 20.0, "abate", kills=[Event(12.0, "kill", ["headshot"]), Event(14.0, "kill", ["headshot"])],
                  label="DOUBLE KILL")
    assert texto_gancho_short(m_hs) == "DOUBLE KILL · 100% HS"
    m_ns = Moment(10.0, 20.0, "abate", kills=[Event(12.0, "kill", ["noscope"])], label="KILL")
    assert texto_gancho_short(m_ns) == "KILL · NOSCOPE"

    # V2: expressão de pan suave dentro dos limites
    expr = expressao_pan_suave(600.0, 1920, 608.0, [2.0, 4.0])
    assert expr.startswith("clip(")

    # M7: speed-ramp divide abates afastados em 1,0x -> 1,75x -> 1,0x
    m_ramp = Moment(0.0, 6.0, "abate", kills=[Event(1.0, "kill", ["headshot"]), Event(4.8, "kill", ["headshot"])],
                    label="DOUBLE KILL")
    trs = gerar_trechos_speed_ramp(m_ramp, vel_rapida=1.75)
    assert len(trs) == 3 and [t.velocidade for t in trs] == [1.0, 1.75, 1.0]

    # Cria vídeo base 1920x1080 60fps para testar V1 (short com kill feed), M7 (speed ramp), V8 (freeze HS) e A4
    src = tmp_path / "base.mp4"
    subprocess.run([
        FFMPEG, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src),
    ], check=True, capture_output=True)

    cfg = Config()
    cfg.qualidade.encoder = "libx264"

    # V1: Short 9:16 com tira do kill feed ativada
    out_short = tmp_path / "short_kf.mp4"
    render.render_short(str(src), Moment(0.0, 2.0, "abate", kills=[Event(1.0, "kill", ["headshot"])]), out_short, cfg, None, None)
    assert qc.verificar(out_short, qc.Esperado(1080, 1920, 2.0, True, 60.0)) == []

    # M7: Render corte com speed-ramp
    out_ramp = tmp_path / "ramp.mp4"
    render_clip_speed_ramp(str(src), m_ramp, out_ramp, cfg, vel_rapida=1.75)
    assert qc.verificar(out_ramp, qc.Esperado(1920, 1080, None, True, 60.0)) == []

    # V8: Render corte com freeze-frame no headshot decisivo
    out_freeze = tmp_path / "freeze.mp4"
    render_freeze_headshot(str(src), Moment(0.0, 2.0, "abate", kills=[Event(1.0, "kill", ["headshot"])]), out_freeze, cfg)
    assert qc.verificar(out_freeze, qc.Esperado(1920, 1080, 2.25, True, 60.0)) == []

    # A4: Extração de som do jogo atenuando voz em faixa única
    wav_jogo, metodo = extrair_jogo_de_faixa_unica(str(src), tmp_path / "jogo_sem_voz.wav", ini=0.0, dur=2.0)
    assert wav_jogo.is_file() and wav_jogo.stat().st_size > 10_000 and metodo

    # V7: rife_executavel retorna Path ou None sem erro
    assert rife_executavel() is None or isinstance(rife_executavel(), Path)


def test_heatmap_youtube_catalogo_musicas_e_biblioteca_pro(tmp_path):
    import json
    from cs2clip.montagem import escolher_musica_por_ritmo
    from cs2clip.sfx import garantir_sfx
    from tools.estudar_referencia import (
        PASTA_PERFIS_APRENDIDOS,
        analisar_padrao_titulo,
        fundir_picos_heatmap,
        limpar_vtt_para_texto,
        salvar_estilo_aprendido,
    )

    # 1. Fusão de picos de retenção ("Most Replayed" do YouTube, estilo SpikeClip / yt-heatmap-clipper)
    heatmap = [
        {"start_time": 0.0, "end_time": 10.0, "value": 0.2},
        {"start_time": 10.0, "end_time": 15.0, "value": 0.85},
        {"start_time": 17.0, "end_time": 22.0, "value": 0.95},
        {"start_time": 60.0, "end_time": 65.0, "value": 0.75},
    ]
    picos = fundir_picos_heatmap(heatmap, limiar=0.70, gap_max_s=5.0)
    assert len(picos) == 2
    assert picos[0]["inicio_s"] == 10.0 and picos[0]["fim_s"] == 22.0 and picos[0]["intensidade_pico"] == 1.0

    # 2. Análise de padrão de título e limpeza de legenda .vtt (estilo Agent-Reach)
    pad = analisar_padrao_titulo("CLUTCH 1v4 IMPOSSÍVEL NA MIRAGE! | Highlights CS2")
    assert "CLUTCH" in pad["palavras_destaque_caixa_alta"] and "1v4" in pad["numeros_e_placares"]
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<c>Corte</c> na batida\n\n00:00:02.000 --> 00:00:03.000\nCorte na batida\n"
    assert limpar_vtt_para_texto(vtt) == "Corte na batida"

    # 3. Biblioteca de perfis profissionais + salvamento de estilo aprendido
    cat_pro = PASTA_PERFIS_APRENDIDOS / "catalogo_pro_cs2.json"
    assert cat_pro.is_file()
    arq_est = salvar_estilo_aprendido("Meu Canal Teste", {"cortes_por_minuto": 15.0}, pasta=tmp_path)
    assert arq_est.name == "meu_canal_teste.json"

    # 4. SFX CC0 completo (4 efeitos) + Seleção de música CC0 por ritmo (BPM + segundo do Drop)
    sfxs = garantir_sfx(tmp_path / "sfx")
    assert set(sfxs.keys()) == {"whoosh", "impacto", "riser", "bass_drop"}
    pasta_m = tmp_path / "musicas"
    pasta_m.mkdir(parents=True, exist_ok=True)
    (pasta_m / "faixa_rapida.wav").write_bytes(b"RIFF")
    (pasta_m / "catalogo.json").write_text(
        json.dumps({"faixas": [{"arquivo": "faixa_rapida.wav", "licenca": "CC0 1.0", "bpm": 132.0, "drop_s": 3.4}]}),
        encoding="utf-8",
    )
    mus, drop_s = escolher_musica_por_ritmo(cortes_por_min=14.0, pasta=pasta_m)
    assert mus is not None and mus.name == "faixa_rapida.wav" and drop_s == 3.4


def test_lote1_g004_unc_fila_g005_deps_e_g007_url_ytdlp(tmp_path):
    from fastapi.testclient import TestClient
    from tools.estudar_referencia import validar_url_referencia
    from ui.seguranca import validar_caminho_sem_unc
    from ui.servidor import create_app

    # G-004: Bloqueia caminhos UNC antes de acessar rede/disco
    for unc in (r"\\127.0.0.1\c$\video.mp4", "//servidor/share/partida.mp4", r"\\?\UNC\srv\sh\a.mp4"):
        with pytest.raises(PermissionError):
            validar_caminho_sem_unc(unc)

    # G-004: POST /api/fila recusa UNC e recusa arquivos fora da raiz que não vieram do seletor nativo
    raiz = tmp_path / "proj"
    (raiz / "entrada").mkdir(parents=True)
    (raiz / "saida").mkdir(parents=True)
    externo = tmp_path / "fora_do_projeto.mp4"
    externo.write_bytes(b"video")

    escolhidos_mock = []
    app = create_app(root_dir=raiz, port=8765, chooser=lambda: escolhidos_mock)
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        headers = {"X-Token": app.state.token}
        r_unc = c.post("/api/fila", headers=headers, json={"arquivos": [r"\\10.0.0.1\share\a.mp4"]})
        assert r_unc.status_code == 403
        r_fora = c.post("/api/fila", headers=headers, json={"arquivos": [str(externo)]})
        assert r_fora.status_code == 403
        # Depois que o usuário escolhe pelo seletor nativo (/api/escolher-arquivo), o arquivo externo passa a ser autorizado
        escolhidos_mock.append({"caminho": str(externo.resolve()), "nome": externo.name, "tamanho": 5, "tipo": "video"})
        assert c.post("/api/escolher-arquivo", headers=headers).status_code == 200

    # G-005: requirements.txt declara pydantic, av e pandas
    req_txt = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
    for pac in ("pydantic", "av", "pandas", "opencv-python-headless", "demoparser2"):
        assert pac in req_txt

    # G-007: validar_url_referencia aceita apenas HTTPS de YouTube/Twitch/TikTok e bloqueia SSRF/flags
    assert validar_url_referencia("https://www.youtube.com/watch?v=dQw4w9WgXcQ").startswith("https://")
    for url_ruim in (
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://127.0.0.1:8080/video.mp4",
        "https://localhost/video.mp4",
        "file:///C:/Windows/win.ini",
        "--exec=calc.exe",
        "https://evil.example.com/watch?v=1",
    ):
        with pytest.raises(ValueError):
            validar_url_referencia(url_ruim)


def test_lote2_g001_g002_g003_integracao_pipeline_e_ui(tmp_path, monkeypatch):
    import ui.fila as fila_mod
    from cs2clip import render
    from cs2clip.config import Config

    # G-003: construir_argumentos aceita --jogador e --perfil para vídeos
    video = tmp_path / "partida.mp4"
    video.write_bytes(b"video")
    args = fila_mod.construir_argumentos(tmp_path, [video], {"jogador": "Fallen", "perfil": "esports_blast"})
    assert "--jogador" in args and "Fallen" in args
    assert "--perfil" in args and "esports_blast" in args

    with pytest.raises(ValueError, match="desconhecido"):
        fila_mod.construir_argumentos(tmp_path, [video], {"perfil": "perfil_inexistente"})

    # G-002: render.concat repassa tiros_rel para render_montagem
    chamadas_montagem = []

    def mock_render_montagem(files, out, cfg, musica=None, tiros_rel=None, abates_rel=None):
        chamadas_montagem.append({"files": files, "out": out, "musica": musica, "tiros_rel": tiros_rel})
        out.write_bytes(b"montagem")
        return "ok"

    monkeypatch.setattr("cs2clip.montagem.render_montagem", mock_render_montagem)
    c1 = tmp_path / "c1.mp4"
    c2 = tmp_path / "c2.mp4"
    c1.write_bytes(b"1")
    c2.write_bytes(b"2")
    cfg = Config()
    cfg.montagem.musica = ""  # sem música
    out_montagem = tmp_path / "MONTAGEM_teste.mp4"
    render.concat([c1, c2], out_montagem, cfg, tiros_rel=[1.5, 2.0])

    assert len(chamadas_montagem) == 1
    assert chamadas_montagem[0]["tiros_rel"] == [1.5, 2.0]


def test_lote3_g006_ancora_demo_e_g008_qc_vmaf_conteudo(tmp_path):
    from cs2clip import qc
    from cs2clip.demo import Abate, PartidaDemo, RoundDemo
    from cs2clip.moments import Event
    from cs2clip.sincronizar import avaliar_contra_demo_ancorada
    from cs2clip.tools import FFMPEG

    # G-006: Avaliação de OCR contra demo com deslocamento âncora independente
    p = PartidaDemo(
        "de_inferno", {1: "Eu"}, 1, "Eu",
        [RoundDemo(1, 0.0, 60.0, "CT")],
        [Abate(10.0, 1, "v1", "ak47"), Abate(20.0, 1, "v2", "ak47"), Abate(30.0, 1, "v3", "ak47")],
        [], [], (1, 0), {1: "CT"},
    )
    # Deslocamento âncora externo: +100.0s
    # Abates reais no vídeo estão em 110.0, 120.0, 130.0
    # O OCR detectou 110.1 (TP), 120.05 (TP), 150.0 (FP fora de hora) e perdeu o de 130.0 (FN)
    events_ocr = [
        Event(110.1, "kill"),
        Event(120.05, "kill"),
        Event(150.0, "kill"),
    ]
    res = avaliar_contra_demo_ancorada(p, events_ocr, deslocamento_ancora=100.0, tol=0.5)
    assert res["tp"] == 2
    assert res["fp"] == 1
    assert res["fn"] == 1
    assert round(res["precisao"], 2) == 0.67
    assert round(res["recall"], 2) == 0.67
    assert res["erro_mediano_s"] > 0.0

    # G-008: qc.verificar_conteudo e medir_vmaf
    preto = tmp_path / "preto.mp4"
    colorido = tmp_path / "colorido.mp4"
    for dest, cor in ((preto, "color=c=black:s=320x180:r=30:d=2"), (colorido, "testsrc2=size=320x180:rate=30:duration=2")):
        subprocess.run([
            FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", cor,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(dest),
        ], check=True, capture_output=True)

    prob_preto = qc.verificar_conteudo(preto, max_segundos=2.0)
    assert "vídeo inteiramente preto" in prob_preto

    prob_cor = qc.verificar_conteudo(colorido, max_segundos=2.0)
    assert prob_cor == []
    # medir_vmaf tem teste próprio (tests/test_revisao_claude_fase4_a2.py): precisa de ffmpeg com libvmaf




