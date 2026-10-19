"""Entrada por demo (cs2clip/demo.py): regras de jogada, clutch e multikill com uma demo falsa (sempre roda) e
com a demo real do projeto (só roda se o arquivo existir: demos não vão para o GitHub)."""
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

from cs2clip import demo  # noqa: E402
from cs2clip.demo import Abate, Clutch, PartidaDemo, RoundDemo, ler_demo, momentos_da_demo  # noqa: E402

T = demo.TICKRATE
EU, A2, A3, A4, A5 = 1, 2, 3, 4, 5                 # meu time (T = 2)
B1, B2, B3, B4, B5 = 11, 12, 13, 14, 15            # adversários (CT = 3)
NOMES = {EU: "Eu", A2: "Aliado2", A3: "Aliado3", A4: "Aliado4", A5: "Aliado5",
         B1: "Bot1", B2: "Bot2", B3: "Bot3", B4: "Bot4", B5: "Bot5"}


def morte(seg, atk, vit, **kw):
    lado = lambda s: 2 if s < 10 else 3
    base = dict(tick=int(seg * T), attacker_steamid=atk, attacker_name=NOMES[atk], attacker_team_num=lado(atk),
                user_steamid=vit, user_name=NOMES[vit], user_team_num=lado(vit), weapon="ak47", headshot=False,
                penetrated=0, noscope=False, thrusmoke=False, attackerblind=False, attackerinair=False,
                distance=10.0)
    base.update(kw)
    return base


class ParserFalso:
    """Imita o DemoParser com 2 rounds. R1 (10–50 s): faço 1 HS, depois meus 4 aliados morrem e perco.
    R2 (70–150 s): meus 4 aliados morrem logo, fico 1v5 e mato 3 (com pausas > 8 s) e o round é meu."""

    def __init__(self, caminho):
        self.mortes = [
            morte(20, EU, B1, headshot=True),
            morte(30, B2, A2), morte(35, B3, A3), morte(40, B4, A4), morte(45, B5, A5),          # R1 perdido
            morte(80, B1, A2), morte(82, B2, A3), morte(84, B3, A4), morte(86, B4, A5),          # R2: fico sozinho contra 5
            morte(100, EU, B1), morte(115, EU, B2, thrusmoke=True), morte(130, EU, B3, penetrated=1),
        ]

    def parse_header(self):
        return {"map_name": "de_teste"}

    def parse_event(self, nome, **kw):
        if nome == "player_death":
            return pd.DataFrame(self.mortes)
        if nome == "round_freeze_end":
            return pd.DataFrame({"round": [1, 2], "tick": [int(10 * T), int(70 * T)]})
        if nome == "round_end":
            return pd.DataFrame({"round": [1, 2], "tick": [int(50 * T), int(150 * T)], "winner": ["CT", "T"],
                                 "reason": ["t_killed", "ct_killed"]})
        raise AssertionError(nome)

    def parse_ticks(self, props, ticks):
        linhas = []
        for t in ticks:
            for s in NOMES:
                linhas.append({"tick": t, "steamid": s, "name": NOMES[s], "team_num": 2 if s < 10 else 3})
        return pd.DataFrame(linhas)


@pytest.fixture
def partida(monkeypatch):
    monkeypatch.setattr(demo, "_abrir", lambda c: ParserFalso(c))
    return ler_demo(Path("falsa.dem"), "eu")


def test_rounds_placar_e_abates(partida):
    assert [r.n for r in partida.rounds] == [1, 2] and partida.mapa == "de_teste"
    assert partida.placar == (1, 1)                      # perdi o round 1, ganhei o 2
    assert len(partida.abates) == 4 and partida.abates[0].headshot and partida.abates[0].round == 1
    assert partida.lados == {1: "T", 2: "T"}


def test_clutch_usa_o_elenco_completo_do_round(partida):
    r1, r2 = sorted(partida.clutches, key=lambda x: x.round)
    assert (r1.round, r1.inimigos, r1.ganhou, r1.abates) == (1, 4, False, 0)   # matei 1 antes de ficar sozinho
    assert (r2.round, r2.inimigos, r2.ganhou, r2.abates) == (2, 5, True, 3)


def test_abates_de_aliado_e_fogo_amigo_nao_contam(monkeypatch):
    p0 = ParserFalso(None)
    p0.mortes.append(morte(25, EU, A2))                   # fogo amigo no R1
    monkeypatch.setattr(demo, "_abrir", lambda c: p0)
    p = ler_demo(Path("falsa.dem"), "eu")
    assert len(p.abates) == 4


def test_escolher_jogador_por_nome_parte_do_nome_e_steamid():
    js = {10: "Dollar The D0G", 20: "Dollar"}
    assert demo._escolher(js, {}, "dollar the") == 10
    assert demo._escolher(js, {}, "Dollar") == 20          # nome exato desempata
    assert demo._escolher(js, {}, "20") == 20
    assert demo._escolher(js, {}, "") is None and demo._escolher(js, {}, "ninguem") is None


def test_sem_jogador_nao_adivinha(monkeypatch):
    monkeypatch.setattr(demo, "_abrir", lambda c: ParserFalso(c))
    assert ler_demo(Path("falsa.dem")).jogador is None


def _p(abates, rounds=None, clutches=()):
    rounds = rounds or [RoundDemo(1, 0.0, 100.0, "T")]
    return PartidaDemo("de_x", {1: "Eu"}, 1, "Eu", rounds, abates, [], list(clutches), (1, 0), {1: "T"})


def ab(t, rnd=1, **kw):
    return Abate(t, rnd, "v", "ak47", **kw)


def test_abates_proximos_viram_uma_jogada_e_round_diferente_separa():
    ms = momentos_da_demo(_p([ab(10), ab(14), ab(18)]))
    assert len(ms) == 1 and ms[0].label == "TRIPLE KILL" and len(ms[0].kills) == 3
    rs = [RoundDemo(1, 0, 30, "T"), RoundDemo(2, 31, 60, "T")]
    ms = momentos_da_demo(_p([ab(28), ab(32, rnd=2)], rs))
    assert len(ms) == 2                                  # 4 s de diferença, mas rounds diferentes


def test_icones_e_pontos_dos_abates_especiais():
    m = momentos_da_demo(_p([ab(10, headshot=True, parede=True, sem_mira=True, fumaca=True)]))[0]
    assert m.kills[0].icons == ["headshot", "wallbang", "noscope", "smoke"]
    assert m.score > momentos_da_demo(_p([ab(10)]))[0].score + 25


def test_ace_com_pausas_vira_um_video_so_com_os_trechos_dos_abates():
    [m] = momentos_da_demo(_p([ab(10), ab(30), ab(50), ab(70), ab(90)]))
    assert m.label == "ACE" and len(m.kills) == 5
    assert m.janelas == [(6.0, 12.5), (26.0, 32.5), (46.0, 52.5), (66.0, 72.5), (86.0, 92.5)]
    assert m.score >= momentos_da_demo(_p([ab(10)]))[0].score + 65 and m.score <= 99   # nota 0-99 (cs2clip/nota.py)


def test_clutch_ganho_marca_a_jogada_do_round_e_o_resultado():
    k = Clutch(1, 40.0, 4, True, 1)
    [m] = momentos_da_demo(_p([ab(10), ab(50)], clutches=[k]))
    assert m.label == "CLUTCH 1v4" and m.round_resultado == "vitoria"
    assert any("clutch 1v4 ganho" in r for r in m.reasons)
    [sem] = momentos_da_demo(_p([ab(10), ab(50)]))
    assert m.score > sem.score


def test_deslocamento_move_todos_os_tempos():
    m = momentos_da_demo(_p([ab(100)]), antes=4, depois=2.5, deslocamento=30.0)[0]
    assert m.kills[0].t == 130.0 and m.start == 126.0 and m.end == 132.5


def test_demo_invalida_da_erro_claro(tmp_path):
    pytest.importorskip("demoparser2")
    ruim = tmp_path / "x.dem"
    ruim.write_bytes(b"isto nao e uma demo")
    with pytest.raises(demo.DemoInvalida):
        ler_demo(ruim, "eu")


# ── com a demo real (Inferno, 17 rounds; jogador "Dollar The D0G") ───────────────────────────────

REAL = Path(__file__).resolve().parents[1] / "entrada" / "demos" / "match730_003846488745834446879_2134712485_201.dem"


@pytest.mark.skipif(not REAL.exists(), reason="demo real não está nesta máquina")
def test_demo_real_do_projeto():
    pytest.importorskip("demoparser2")
    p = ler_demo(REAL, "dollar")
    assert p.mapa == "de_inferno" and len(p.rounds) == 17
    assert p.nome == "Dollar The D0G" and len(p.abates) == 16 and len(p.mortes) == 15
    assert p.placar == (4, 13)
    [c] = [x for x in p.clutches if x.round == 12]
    assert c.inimigos == 4 and c.ganhou and c.abates == 4
    ms = momentos_da_demo(p)
    assert any(m.label == "ACE" and m.round_n == 12 for m in ms)
