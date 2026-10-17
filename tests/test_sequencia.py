"""Jogada do round inteira (cs2clip/sequencia.py) e o que depende dela: trechos no momento, render por trechos,
short compacto, EDL e cache. Regras decididas com o usuário em 17/10/2026 (docs/editing/REGRAS_DE_CORTE.md)."""
import json
from pathlib import Path

import pytest

from cs2clip.config import Config
from cs2clip.moments import Event, Moment
from cs2clip.partes import dividir_partes
from cs2clip.refazer import impressao
from cs2clip.rounds import RoundTimelineEntry as R
from cs2clip.sequencia import compactar, juntar_trechos, unir_sequencias


def partes_de_teste():
    # partida com rounds terminando em 100, 200, 300, 400 s
    return dividir_partes([R(100.0 * i, "vitoria", .9) for i in range(1, 5)], [], 500.0)


def jogada(ts, idx=0, score=20.0, hs=()):
    kills = [Event(t, "kill", ["headshot"] if t in hs else []) for t in ts]
    return Moment(max(0.0, ts[0] - 4.0), ts[-1] + 2.5, "abate", kills=kills, label="KILL", score=score, idx=idx)


def cortes(**kw):
    c = Config().cortes
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ── juntar_trechos ─────────────────────────────────────────────────────────────────────────────

def test_juntar_trechos_une_por_vao_e_sobreposicao():
    assert juntar_trechos([(10, 20), (24, 30), (45, 50)], 6) == [(10, 30), (45, 50)]
    assert juntar_trechos([(45, 50), (10, 20)], 6) == [(10, 20), (45, 50)]          # ordem não importa
    assert juntar_trechos([(10, 20), (15, 18)], 0) == [(10, 20)]                      # contido


# ── unir por round ─────────────────────────────────────────────────────────────────────────────

def test_dois_abates_longe_no_mesmo_round_viram_um_video_com_dois_trechos():
    a, b = jogada([110.0]), jogada([150.0])               # round 2 (100-200), 40 s de distância
    [m] = unir_sequencias([a, b], partes_de_teste(), cortes())
    assert m.label == "DOUBLE KILL" and len(m.kills) == 2
    assert m.janelas == [(106.0, 112.5), (146.0, 152.5)]  # tempo parado do meio fora
    assert m.duration == pytest.approx(13.0) and m.start == 106.0 and m.end == 152.5
    assert m.segmentos == m.janelas and m.idx == 1


def test_abates_de_rounds_diferentes_nao_se_juntam():
    a, b = jogada([195.0]), jogada([210.0])               # 15 s, mas o banner de 200 separa (210 já é round novo)
    ms = unir_sequencias([a, b], partes_de_teste(), cortes())
    assert len(ms) == 2 and all(not m.segmentos for m in ms)


def test_vao_pequeno_fica_continuo_e_sem_segmentos():
    a, b = jogada([110.0]), jogada([118.0])               # trechos 106-112.5 e 114-120.5: vão de 1,5 s
    [m] = unir_sequencias([a, b], partes_de_teste(), cortes())
    assert m.segmentos == [] and m.janelas == [(106.0, 120.5)]


def test_um_kill_pausa_e_depois_tres_vira_um_video_so_com_rotulo_do_total():
    um, tres = jogada([110.0]), jogada([160.0, 161.0, 163.0], score=50.0, hs=(161.0,))
    [m] = unir_sequencias([um, tres], partes_de_teste(), cortes())
    assert m.label == "QUADRA KILL" and len(m.kills) == 4 and len(m.janelas) == 2
    assert m.score > 50.0                                   # nota do melhor + bônus de 4 no round
    assert any("4 abates no mesmo round" in r for r in m.reasons)


def test_reacoes_e_fora_de_partida_passam_sem_mudar():
    reacao = Moment(300.0, 310.0, "reacao", pico=305.0)
    fora = jogada([450.0])                                  # depois do último banner: sem round
    ms = unir_sequencias([reacao, fora, jogada([110.0]), jogada([150.0])], partes_de_teste(), cortes())
    assert [m.kind for m in ms] == ["abate", "reacao", "abate"] and ms[2].kills[0].t == 450.0


def test_desligado_pelo_config_nao_muda_nada():
    a, b = jogada([110.0], idx=1), jogada([150.0], idx=2)
    assert unir_sequencias([a, b], partes_de_teste(), cortes(juntar_no_round=0)) == [a, b]


def test_jogada_maior_que_o_maximo_fica_com_o_final():
    ms = [jogada([110.0 + 20 * i]) for i in range(4)]      # 4 trechos de 6,5 s = 26 s
    [m] = unir_sequencias(ms, partes_de_teste(), cortes(sequencia_max=15.0))
    assert m.duration == pytest.approx(15.0) and m.end == 172.5
    assert [k.t for k in m.kills] == [150.0, 170.0] and m.label == "DOUBLE KILL"   # os que ficaram fora não contam
    assert m.janelas == [(130.5, 132.5), (146.0, 152.5), (166.0, 172.5)]   # o começo foi cortado


# ── short compacto ─────────────────────────────────────────────────────────────────────────────

def test_compactar_so_quando_passa_do_limite_e_nao_altera_o_original():
    m = jogada([110.0, 150.0, 190.0])
    m.segmentos = [(106.0, 125.0), (140.0, 165.0), (180.0, 192.5)]      # 56,5 s
    assert compactar(m, 60.0, 2.0, 1.5, 6.0) is m
    curto = compactar(m, 30.0, 2.0, 1.5, 6.0)
    assert curto is not m and curto.janelas == [(108.0, 111.5), (148.0, 151.5), (188.0, 191.5)]
    assert curto.duration == pytest.approx(10.5) and m.duration == pytest.approx(56.5)


def test_compactar_que_ainda_passa_fica_com_o_final():
    m = jogada([100.0 + 10 * i for i in range(10)])        # 10 abates; compacto = 10 × 3,5 s = 35 s
    curto = compactar(m, 20.0, 2.0, 1.5, 1.0)
    assert curto.duration == pytest.approx(20.0) and curto.end == 191.5       # último abate + 1,5 s


# ── o resto do programa entende trechos ────────────────────────────────────────────────────────

def test_impressao_muda_quando_os_trechos_mudam():
    a = jogada([110.0, 150.0])
    b = jogada([110.0, 150.0])
    b.segmentos = [(106.0, 112.5), (146.0, 152.5)]
    assert impressao(a) != impressao(b)


def test_momentos_json_grava_os_segmentos(tmp_path):
    from cs2clip.momentos_io import gravar_momentos
    m = jogada([110.0, 150.0])
    m.segmentos = [(106.0, 112.5), (146.0, 152.5)]
    gravar_momentos(tmp_path, [m], "v", 500.0, "3")
    d = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]
    assert d["segmentos"] == [[106.0, 112.5], [146.0, 152.5]] and d["inicio"] == 106.0 and d["fim"] == 152.5


def test_render_por_janelas_renderiza_cada_trecho_e_junta(tmp_path, monkeypatch):
    from cs2clip import render
    m = jogada([110.0, 150.0])
    m.segmentos = [(106.0, 112.5), (146.0, 152.5)]
    vistos, juntados = [], []
    monkeypatch.setattr(render, "concat", lambda files, out: (juntados.append([f.name for f in files]), out.write_bytes(b"ok")))

    def render_um(mm, dest):
        vistos.append((mm.start, mm.end, mm.segmentos))
        dest.write_bytes(b"x")

    out = tmp_path / "corte.mp4"
    render.render_por_janelas(m, out, render_um)
    assert vistos == [(106.0, 112.5, []), (146.0, 152.5, [])]
    assert juntados == [["corte.seg0.mp4", "corte.seg1.mp4"]] and out.read_bytes() == b"ok"
    assert not list(tmp_path.glob("*.seg*"))                 # as partes são apagadas


def test_render_por_janelas_de_um_trecho_so_nao_junta_nada(tmp_path, monkeypatch):
    from cs2clip import render
    monkeypatch.setattr(render, "concat", lambda *a: pytest.fail("não devia juntar"))
    m = jogada([110.0])
    render.render_por_janelas(m, tmp_path / "c.mp4", lambda mm, dest: dest.write_bytes(b"1"))
    assert (tmp_path / "c.mp4").read_bytes() == b"1"


def test_render_por_janelas_limpa_as_partes_quando_falha(tmp_path, monkeypatch):
    from cs2clip import render
    m = jogada([110.0, 150.0])
    m.segmentos = [(106.0, 112.5), (146.0, 152.5)]

    def quebra(mm, dest):
        dest.write_bytes(b"x")
        if mm.start > 140:
            raise RuntimeError("ffmpeg falhou")

    with pytest.raises(RuntimeError):
        render.render_por_janelas(m, tmp_path / "c.mp4", quebra)
    assert not list(tmp_path.glob("*.seg*"))


def test_edl_tem_um_evento_por_trecho(tmp_path):
    from cs2clip.pipeline import _write_edl
    m = jogada([110.0, 150.0])
    m.segmentos = [(106.0, 112.5), (146.0, 152.5)]
    _write_edl(tmp_path / "m.edl", [m, jogada([300.0])], "v.mp4", 60)
    texto = (tmp_path / "m.edl").read_text(encoding="utf-8")
    assert texto.count("* FROM CLIP NAME") == 3 and "001  AX" in texto and "003  AX" in texto


def test_duracao_esperada_do_qc_usa_a_soma_dos_trechos():
    import types

    from cs2clip.pipeline import _dur_esperada
    m = jogada([110.0, 150.0])
    m.segmentos = [(106.0, 112.5), (146.0, 152.5)]
    assert _dur_esperada(m, types.SimpleNamespace(duration=500.0)) == pytest.approx(13.0)


def test_demo_agrupa_todos_os_abates_do_round(tmp_path):
    pytest.importorskip("pandas")
    from cs2clip.demo import Abate, PartidaDemo, RoundDemo, momentos_da_demo
    rs = [RoundDemo(1, 0.0, 100.0, "T")]
    ab = [Abate(10.0, 1, "v", "ak47"), Abate(40.0, 1, "v", "ak47", headshot=True), Abate(43.0, 1, "v", "ak47")]
    p = PartidaDemo("de_x", {1: "Eu"}, 1, "Eu", rs, ab, [60.0], [], (1, 0), {1: "T"})
    [m] = momentos_da_demo(p, 4.0, 2.5, 6.0)
    assert m.label == "TRIPLE KILL" and m.janelas == [(6.0, 12.5), (36.0, 45.5)] and m.round_resultado == "vitoria"
    p.mortes = [44.0]                                          # morreu 1 s depois do último abate
    [m] = momentos_da_demo(p, 4.0, 2.5, 6.0)
    assert m.janelas[-1][1] == 45.0                            # o trecho não passa de 1 s depois da morte


def test_limpar_velhos_apaga_so_os_arquivos_do_programa_que_nao_valem_mais(tmp_path):
    from cs2clip.pipeline import _limpar_velhos
    cortes = tmp_path / "cortes"
    cortes.mkdir()
    for nome in ("05_double-kill_05m59s.mp4", "05_headshot_05m59s.mp4", "06_kill_10m00s_vertical.mp4",
                 "meu_video_favorito.mp4", "notas.txt", "07_ace_20m00s.jpg"):
        (cortes / nome).write_bytes(b"x")
    n = _limpar_velhos([cortes], {cortes / "05_double-kill_05m59s.mp4"})
    assert n == 3
    assert sorted(f.name for f in cortes.iterdir()) == ["05_double-kill_05m59s.mp4", "meu_video_favorito.mp4", "notas.txt"]


def test_remover_repetidos_fica_com_o_abate_e_renumera():
    from cs2clip.sequencia import remover_repetidos, sobreposicao
    abate = jogada([110.0], score=10.0)                               # 106 - 112.5
    reacao = Moment(107.0, 113.0, "reacao", score=40.0, pico=110.0)  # quase o mesmo trecho
    longe = Moment(300.0, 310.0, "reacao", score=5.0, pico=305.0)
    assert sobreposicao(abate, reacao) > 0.5 and sobreposicao(abate, longe) == 0
    ficam = remover_repetidos([reacao, longe, abate])
    assert [m.kind for m in ficam] == ["abate", "reacao"] and [m.idx for m in ficam] == [1, 2]


def test_morte_entre_dois_abates_separa_os_rounds():
    a, b = jogada([110.0]), jogada([150.0])
    assert len(unir_sequencias([a, b], partes_de_teste(), cortes(), mortes=[130.0])) == 2


def test_banner_perdido_no_meio_nao_junta_abates_distantes():
    """Live 2903084427: TRIPLE + 1 abate do round seguinte viraram QUADRA porque o banner entre eles não foi lido."""
    # banners a cada 100 s, mas o de 300 não foi lido: o intervalo 200-400 esconde 2 rounds
    partes = dividir_partes([R(100.0, "vitoria", .9), R(200.0, "vitoria", .9), R(400.0, "vitoria", .9),
                             R(500.0, "vitoria", .9)], [], 600.0)
    longe = unir_sequencias([jogada([270.0]), jogada([360.0])], partes, cortes())   # 90 s: rounds diferentes
    perto = unir_sequencias([jogada([290.0]), jogada([300.0])], partes, cortes())   # 10 s: mesmo round
    assert len(longe) == 2 and len(perto) == 1


def test_abate_logo_depois_do_banner_e_pos_round_do_round_que_acabou():
    """Live 2903084427: abate 1 s depois do banner foi unido aos abates do round seguinte, 96 s depois."""
    pos, seguinte = jogada([201.0]), jogada([290.0])     # banner em 200
    antes = jogada([190.0])
    ms = unir_sequencias([antes, pos, seguinte], partes_de_teste(), cortes())
    assert [len(m.kills) for m in ms] == [2, 1]           # 190 + 201 juntos; 290 sozinho
