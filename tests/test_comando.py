"""Comando falado "clipa isso" (cs2clip/comando.py)."""
from cs2clip.comando import achar_comandos, aplicar_comandos
from cs2clip.moments import Event, Moment
from cs2clip.nota import pontuar
from cs2clip.transcribe import Word


def fala(t, texto):
    return [Word(t + 0.3 * i, t + 0.3 * i + 0.25, w) for i, w in enumerate(texto.split())]


def test_acha_as_frases_sem_repetir_em_10_s():
    falas = fala(100, "clipa isso clipa isso") + fala(200, "Marca aí") + fala(300, "vou clicar ali")
    assert [round(t) for t in achar_comandos(falas)] == [100, 200]


def test_jogada_antes_do_comando_ganha_prioridade_e_sem_jogada_nasce_corte():
    j = Moment(80, 90, "abate", kills=[Event(85, "kill")], idx=1)
    ms = aplicar_comandos([j], [100.0, 500.0], 1000.0)
    assert j.pedido and len(ms) == 2 and ms[1].label == "CLIPA" and ms[1].start == 470.0
    pontuar(j, [])
    assert any("pediu" in r for r in j.reasons)
