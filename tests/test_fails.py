"""Trilha de FAILS (cs2clip/fails.py): morte + frustração/risada; callout e jogada ficam de fora."""
import numpy as np

from cs2clip.fails import achar_fails
from cs2clip.moments import Event, Moment
from cs2clip.transcribe import Word


def fala(t, texto):
    return [Word(t + 0.3 * i, t + 0.3 * i + 0.25, w) for i, w in enumerate(texto.split())]


def voz(duracao=300.0):
    return np.full(int(duracao / 0.1) + 1, 0.9, np.float32)


def test_morte_com_palavrao_vira_fail_e_callout_nao():
    falas = fala(101, "meu deus que porra é essa") + fala(201, "meio meio ali ali garagem")
    fs = achar_fails([100.0, 200.0], falas, voz(), [], [], 300.0)
    assert [round(m.pico) for m in fs] == [100] and fs[0].kind == "fail" and "você morreu" in fs[0].reasons


def test_morte_logo_depois_de_abate_e_jogada_nao_fail():
    assert achar_fails([100.0], fala(101, "merda"), voz(), [95.0], [], 300.0) == []


def test_risadas_longe_das_mortes_viram_risada():
    falas = [Word(150 + i, 150.4 + i, "kkkk") for i in range(3)]
    fs = achar_fails([], falas, voz(), [], [], 300.0)
    assert len(fs) == 1 and fs[0].label == "RISADA"


def test_nao_repete_trecho_de_um_momento_que_ja_existe_e_respeita_a_quantidade():
    existente = Moment(95.0, 110.0, "abate", kills=[Event(96.0, "kill")])
    falas = fala(101, "merda") + fala(161, "porra") + fala(221, "caralho")
    fs = achar_fails([100.0, 160.0, 220.0], falas, voz(), [], [existente], 300.0, quantidade=1)
    assert len(fs) == 1 and round(fs[0].pico) in (160, 220)
