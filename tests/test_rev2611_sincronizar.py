"""Revisão de 26/11/2026, lote A1: G-002 (a demo só entra no intervalo do vídeo)."""
from __future__ import annotations

import numpy as np

from cs2clip.config import Config
from cs2clip.demo import Abate, PartidaDemo, RoundDemo
from cs2clip.moments import Event, build_moments
from cs2clip.sincronizar import integrar


def _partida() -> PartidaDemo:
    return PartidaDemo(
        mapa="de_mirage", jogadores={76561198000000001: "Tester"}, jogador=76561198000000001, nome="Tester",
        abates=[Abate(t=20.0, tick=1280, round=1, headshot=True, arma="ak47", vitima="A"),      # no vídeo: -80 s
                Abate(t=150.0, tick=9600, round=2, headshot=False, arma="ak47", vitima="B"),    # 50 s
                Abate(t=180.0, tick=11520, round=2, headshot=False, arma="ak47", vitima="C"),   # 80 s
                Abate(t=210.0, tick=13440, round=3, headshot=False, arma="ak47", vitima="D"),   # 110 s
                Abate(t=240.0, tick=15360, round=3, headshot=False, arma="ak47", vitima="E"),   # 140 s
                Abate(t=350.0, tick=22400, round=4, headshot=True, arma="ak47", vitima="F")],   # 250 s (vídeo tem 200 s)
        mortes=[10.0, 360.0],
        rounds=[RoundDemo(1, 0.0, 80.0, "CT"), RoundDemo(2, 80.0, 190.0, "CT"),
                RoundDemo(3, 190.0, 260.0, "T"), RoundDemo(4, 260.0, 380.0, "CT")],
        clutches=[], lados={1: "CT", 2: "CT", 3: "CT", 4: "CT"})


def _fusao():
    video = [Event(50.0, "kill"), Event(80.0, "kill"), Event(110.0, "kill"), Event(140.0, "kill")]
    return integrar([(_partida(), "x.dem")], video, [], [], duracao=200.0)


def test_g002_nada_da_demo_cai_fora_do_video():
    f = _fusao()
    assert f.relatorio[0]["usada"] and f.relatorio[0]["deslocamento"] == -100.0   # o vídeo começou aos 100 s da partida
    assert f.events and all(0.0 <= e.t <= 200.0 for e in f.events)
    assert f.mortes == [] or all(0.0 <= m <= 200.0 for m in f.mortes)
    assert f.rounds and all(0.0 <= r.t <= 200.0 for r in f.rounds)


def test_g002_o_que_cai_dentro_continua_entrando():
    f = _fusao()
    assert [e.t for e in f.events] == [50.0, 80.0, 110.0, 140.0]
    assert f.mortes == []                                  # as duas mortes da demo (-90 s e 260 s) estão fora do vídeo
    assert [r.t for r in f.rounds if r.t > 0]              # banners dos rounds 2 e 3 (fim em 90 s e 160 s) entram
    assert len(f.rounds) == 2


def test_g002_nenhum_momento_tem_duracao_negativa():
    f = _fusao()
    moms = build_moments(f.events, np.zeros(0, np.float32), None, None, [], 200.0, Config())
    assert moms and all(m.end > m.start >= 0.0 for m in moms)


def test_g002_build_moments_ignora_abate_fora_do_video():
    eventos = [Event(-80.0, "kill"), Event(50.0, "kill"), Event(250.0, "kill")]
    moms = build_moments(eventos, np.zeros(0, np.float32), None, None, [], 200.0, Config())
    assert all(m.end > m.start for m in moms)
    assert any(m.kills and m.kills[0].t == 50.0 for m in moms)
