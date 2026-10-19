"""Demo + vídeo da mesma partida (cs2clip/sincronizar.py): o deslocamento, a fusão e o que ela muda na análise."""
import pytest

from cs2clip.demo import Abate, Clutch, PartidaDemo, RoundDemo
from cs2clip.moments import Event, Moment
from cs2clip.nota import pontuar
from cs2clip.partes import dividir_partes
from cs2clip.rounds import RoundTimelineEntry
from cs2clip.sincronizar import clutch_do_momento, estimar, integrar

DESLOC = 193.2


def _partida(n_rounds=4, abates=None, clutches=None):
    rounds = [RoundDemo(i + 1, 100.0 * i + 20, 100.0 * i + 90, "CT" if i % 2 == 0 else "T") for i in range(n_rounds)]
    return PartidaDemo("de_cache", {1: "eu"}, 1, "eu", rounds, abates or [], [60.0, 260.0], clutches or [], (2, 2),
                       {r.n: "CT" for r in rounds})


def _abate(t, rnd=1, **kw):
    return Abate(t, rnd, "inimigo", "ak47", **kw)


# ── estimar ──────────────────────────────────────────────────────────────────────────────────

def test_estima_o_deslocamento_mesmo_com_abate_de_menos_e_de_mais():
    demo = [40.0, 55.0, 120.0, 300.0, 310.0, 500.0]
    video = [t + DESLOC + 0.1 for t in demo if t != 120.0] + [900.0]      # 1 abate perdido + 1 leitura falsa
    s = estimar(demo, video)
    assert s.confiavel and s.casados == 5 and s.total_demo == 6
    assert s.deslocamento == pytest.approx(DESLOC + 0.1, abs=0.05) and s.residuo < 0.1


def test_abate_com_leitura_atrasada_pelo_flash_ainda_casa_no_pareamento_final():
    demo = [40.0, 55.0, 120.0, 300.0, 310.0]
    video = [t + DESLOC for t in demo]
    video[1] += 2.6                                      # atirador cego: kill feed lido atrasado
    assert estimar(demo, video).casados == 5


def test_sem_abates_em_comum_nao_sincroniza():
    s = estimar([10.0, 50.0, 90.0, 400.0], [7.0, 333.0, 701.0, 1000.0, 1500.0])
    assert s is None or not s.confiavel
    assert estimar([10.0, 20.0], [30.0, 40.0, 50.0]) is None              # menos de 3 abates: não confia


def test_duas_demos_no_mesmo_video_cada_uma_no_seu_lugar():
    a, b = [40.0, 55.0, 120.0, 300.0], [20.0, 80.0, 200.0, 260.0]
    video = [t + 193.0 for t in a] + [t + 2656.0 for t in b]
    assert estimar(a, video).deslocamento == pytest.approx(193.0, abs=0.1)
    assert estimar(b, video).deslocamento == pytest.approx(2656.0, abs=0.1)


# ── integrar ─────────────────────────────────────────────────────────────────────────────────

def _cenario():
    ab = [_abate(40.0, headshot=True), _abate(55.0, 1, parede=True, fumaca=True), _abate(120.0, 2),
          _abate(230.0, 3, sem_mira=True), _abate(250.0, 3)]
    p = _partida(4, ab, [Clutch(3, 225.0, 3, True, 2)])
    events = [Event(t + DESLOC + 0.05, "kill", ["headshot"] if t == 120.0 else []) for t in (40.0, 55.0, 230.0, 250.0)]
    events.append(Event(5000.0, "kill"))                 # fora da janela: não é mexido
    events.append(Event(40.0 + DESLOC + 30, "kill"))     # dentro da janela e sem abate na demo: leitura falsa
    events.append(Event(300.0, "assist"))
    return p, events


def test_fusao_corrige_icones_recupera_abate_e_tira_leitura_falsa():
    p, events = _cenario()
    f = integrar([(p, "a.dem")], events, [], [], 6000.0)
    o = f.relatorio[0]["deslocamento"]
    kills = [e for e in f.events if e.kind == "kill"]
    por_t = {round(e.t - o, 1): e for e in kills if e.t < 1000}
    assert set(por_t) == {40.0, 55.0, 120.0, 230.0, 250.0}               # o abate de 120 s entrou; a leitura falsa saiu
    assert por_t[40.0].icons == ["headshot"] and por_t[55.0].icons == ["wallbang", "smoke"]
    assert por_t[230.0].icons == ["noscope"] and por_t[120.0].icons == []
    assert any(e.t == 5000.0 for e in kills) and any(e.kind == "assist" for e in f.events)
    r = f.relatorio[0]
    assert r["usada"] and r["casados"] == 4 and r["abates_recuperados"] == 1 and r["abates_falsos_removidos"] == 1


def test_fusao_usa_o_instante_exato_da_demo():
    p, events = _cenario()
    f = integrar([(p, "a.dem")], events, [], [], 6000.0)
    o = f.relatorio[0]["deslocamento"]
    assert any(abs(e.t - (40.0 + o)) < 0.011 for e in f.events)         # o 40 s do vídeo estava 0,05 s fora


def test_fusao_troca_mortes_e_completa_os_rounds():
    p, events = _cenario()
    lidos = [RoundTimelineEntry(90.0 + DESLOC + 2.0, "vitoria", 0.9)]    # o vídeo só leu 1 dos 4 banners
    f = integrar([(p, "a.dem")], events, [62.0 + DESLOC, 9999.0], lidos, 6000.0)
    o = f.relatorio[0]["deslocamento"]
    assert [round(m - o, 1) for m in f.mortes if m < 5000] == [60.0, 260.0] and 9999.0 in f.mortes
    assert len(f.rounds) == 4 and [r.resultado for r in f.rounds] == ["vitoria", "derrota", "vitoria", "derrota"]
    assert f.relatorio[0]["rounds_demo"] == 4 and f.relatorio[0]["rounds_video"] == 1
    assert f.relatorio[0]["banners_com_resultado_diferente"] == 0
    assert f.exatos[0][0] == pytest.approx(20.0 + o - 3.0) and f.exatos[0][1] == pytest.approx(390.0 + o + 12.0)


def test_demo_de_outra_partida_nao_mexe_em_nada():
    p, _ = _cenario()
    estranhos = [Event(t, "kill") for t in (11.0, 345.0, 1234.0, 2000.0, 2999.0)]
    f = integrar([(p, "outra.dem")], estranhos, [5.0], [RoundTimelineEntry(30.0, "vitoria", 1.0)], 6000.0)
    assert f.relatorio[0]["usada"] is False and "não achei" in f.relatorio[0]["motivo"]
    assert [e.t for e in f.events] == [e.t for e in estranhos] and f.mortes == [5.0] and len(f.rounds) == 1


def test_resultado_do_round_vem_do_lado_do_jogador():
    p = _partida(2, [_abate(40.0), _abate(55.0), _abate(130.0)])
    p.lados = {1: "CT", 2: "CT"}                         # round 1: CT venceu; round 2: T venceu
    events = [Event(t + DESLOC, "kill") for t in (40.0, 55.0, 130.0)]
    f = integrar([(p, "a.dem")], events, [], [], 1000.0)
    assert [r.resultado for r in f.rounds] == ["vitoria", "derrota"]


# ── clutch e nota ────────────────────────────────────────────────────────────────────────────

def test_clutch_so_conta_para_abates_depois_de_ficar_sozinho():
    cl = [(225.0 + DESLOC, 290.0 + DESLOC, 3, True)]
    antes = [Event(100.0 + DESLOC, "kill")]
    depois = [Event(100.0 + DESLOC, "kill"), Event(230.0 + DESLOC, "kill")]
    assert clutch_do_momento(antes, cl) is None and clutch_do_momento(depois, cl) == (3, True)


def test_nota_valoriza_clutch_parede_e_noscope():
    def nota(icons=(), clutch=None):
        m = Moment(0, 10, "abate", kills=[Event(5.0, "kill", list(icons))], clutch=clutch)
        pontuar(m, [])
        return m.score, m.reasons
    base, _ = nota()
    n_clutch, motivos = nota(clutch=(3, True))
    assert n_clutch - base == 26 and any("clutch 1v3 ganho" in r for r in motivos)
    assert nota(clutch=(3, False))[0] - base == 4
    assert nota(icons=["wallbang"])[0] - base == 6 and nota(icons=["noscope"])[0] - base == 12


# ── partes com a demo ────────────────────────────────────────────────────────────────────────

def test_partida_exata_tem_limites_e_numeracao_da_demo_mesmo_com_round_longo():
    # rounds de 50 a 110 s: a regra dos banners acharia "banner perdido" nos longos e bagunçaria a numeração
    ts = [100.0, 150.0, 260.0, 310.0, 420.0, 470.0]
    rounds = [RoundTimelineEntry(t, "vitoria" if i % 2 == 0 else "derrota", 1.0) for i, t in enumerate(ts)]
    partes = dividir_partes(rounds, [120.0], 1000.0, exatos=[(80.0, 500.0)])
    p = next(x for x in partes if x.tipo == "partida")
    assert (p.inicio, p.fim) == (80.0, 500.0) and p.numeros == [1, 2, 3, 4, 5, 6] and p.rounds_faltando == 0
    assert p.placar == "3x3"


def test_duas_partidas_coladas_nao_viram_uma():
    a = [RoundTimelineEntry(100.0 + 60 * i, "vitoria", 1.0) for i in range(4)]
    b = [RoundTimelineEntry(420.0 + 60 * i, "derrota", 1.0) for i in range(4)]
    partes = dividir_partes(a + b, [], 1000.0, exatos=[(80.0, 340.0), (400.0, 680.0)])
    assert [p.placar for p in partes if p.tipo == "partida"] == ["4x0", "0x4"]


def test_sem_exatos_a_regra_dos_banners_continua_igual():
    rounds = [RoundTimelineEntry(100.0 + 80 * i, "vitoria", 1.0) for i in range(4)]
    partes = dividir_partes(rounds, [], 600.0)
    assert [p.tipo for p in partes].count("partida") == 1 and partes[0].tipo != "partida" or partes[0].tipo == "partida"


# ── linha de comando: vídeo + demos ──────────────────────────────────────────────────────────

def _main_com_video_e_demo(monkeypatch, tmp_path, argv_extra, config=""):
    import types
    import cs2clip.__main__ as main_mod
    import cs2clip.checagem as checagem
    import cs2clip.pipeline as pl
    (tmp_path / "pasta").mkdir()
    (tmp_path / "pasta" / "live.mp4").write_bytes(b"x")
    (tmp_path / "pasta" / "partida.dem").write_bytes(b"x")
    (tmp_path / "x.toml").write_text(config, encoding="utf-8")
    vistos = {}

    def analise(p, cfg, o, pd, R, **kw):
        vistos.update(kw)
        info = types.SimpleNamespace(path="x", duration=60.0, width=1920, height=1080, fps=60.0)
        return info, "live", o / "live", [], [], None
    monkeypatch.setattr(pl, "analyze", analise)
    monkeypatch.setattr(pl, "render_all", lambda *a, **k: ([], [], []))
    monkeypatch.setattr(checagem, "gpu_ocupada", lambda: [])
    lidas = []
    monkeypatch.setattr(main_mod, "_ler_demo_do_video",
                        lambda demo, quem: (lidas.append((demo.name, quem)), ("PARTIDA", demo.name))[1])
    main_mod.main([str(tmp_path / "pasta" / "live.mp4"), "--so-analise", "--saida", str(tmp_path / "saida"),
                   "--config", str(tmp_path / "x.toml"), *argv_extra])
    return vistos, lidas


def test_video_com_demo_ao_lado_usa_a_demo_quando_o_nome_e_conhecido(monkeypatch, tmp_path):
    vistos, lidas = _main_com_video_e_demo(monkeypatch, tmp_path, ["--jogador", "Dollar"])
    assert lidas == [("partida.dem", "Dollar")] and vistos["demos"] == [("PARTIDA", "partida.dem")]


def test_nome_no_config_toml_tambem_vale(monkeypatch, tmp_path):
    vistos, lidas = _main_com_video_e_demo(monkeypatch, tmp_path, [], '[demo]\njogador = "Dollar"\n')
    assert lidas == [("partida.dem", "Dollar")] and "demos" in vistos


def test_sem_o_nome_a_demo_ao_lado_nao_e_lida(monkeypatch, tmp_path):
    vistos, lidas = _main_com_video_e_demo(monkeypatch, tmp_path, [])
    assert lidas == [] and "demos" not in vistos


def test_demo_pedida_na_linha_de_comando_junto_com_o_video(monkeypatch, tmp_path):
    import cs2clip.__main__ as main_mod
    import cs2clip.checagem as checagem
    import cs2clip.pipeline as pl
    import types
    (tmp_path / "live.mp4").write_bytes(b"x")
    (tmp_path / "outra.dem").write_bytes(b"x")
    vistos = {}
    monkeypatch.setattr(pl, "analyze", lambda p, cfg, o, pd, R, **kw: (vistos.update(kw), (
        types.SimpleNamespace(path="x", duration=60.0, width=1920, height=1080, fps=60.0), "live", o / "live", [], [], None))[1])
    monkeypatch.setattr(pl, "render_all", lambda *a, **k: ([], [], []))
    monkeypatch.setattr(checagem, "gpu_ocupada", lambda: [])
    monkeypatch.setattr(main_mod, "_ler_demo_do_video", lambda demo, quem: ("P", demo.name))
    main_mod.main([str(tmp_path / "live.mp4"), str(tmp_path / "outra.dem"), "--jogador", "x", "--so-analise",
                   "--saida", str(tmp_path / "saida"), "--config", str(tmp_path / "x.toml")])
    assert vistos["demos"] == [("P", "outra.dem")]     # antes: só a demo era analisada e o vídeo ficava de fora


def test_texto_das_partes_diz_quando_o_placar_vem_da_demo():
    from cs2clip.partes import texto_partes
    rounds = [RoundTimelineEntry(100.0 + 70 * i, "vitoria", 1.0) for i in range(4)]
    com = texto_partes(dividir_partes(rounds, [], 600.0, exatos=[(80.0, 400.0)]), "v", 600.0)
    sem = texto_partes(dividir_partes(rounds, [], 600.0), "v", 600.0)
    assert "placar exato (da demo)" in com and "placar aproximado" in sem
