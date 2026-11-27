"""Comando falado na live: "clipa isso", "corta essa", "marca aí"... (pesquisa P7, R11; Eklipse, StreamLadder e
Medal têm o "clip that"). A jogada que terminou até 45 s antes do comando ganha prioridade (e vai para os shorts);
se não houver jogada, nasce um corte dos 30 s anteriores. A lista de frases é editável aqui."""
from __future__ import annotations

from .moments import Moment
from .transcribe import Word, _match_at

FRASES = ["clipa isso", "clipa essa", "clipa ai", "clipa", "corta essa", "corta isso", "marca ai", "marca essa",
          "salva essa", "clip that"]
JANELA = 45.0          # s antes do comando em que a jogada conta
SEM_JOGADA = (30.0, 5.0)


def achar_comandos(falas: list[Word]) -> list[float]:
    """Instantes em que uma frase de comando foi dita (uma por janela de 10 s)."""
    ts: list[float] = []
    for i, w in enumerate(falas):
        if any(_match_at(falas, i, f) for f in FRASES) and (not ts or w.start - ts[-1] > 10.0):
            ts.append(w.start)
    return ts


def _contem(m: Moment, c: float) -> bool:
    return any(a - 1.0 <= c <= b + 2.0 for a, b in m.janelas)


def aplicar_comandos(moments: list[Moment], comandos: list[float], duracao: float) -> list[Moment]:
    """Marca `pedido=True` na jogada atendida ou cria um momento novo. Devolve a lista (renumerada se cresceu).
    A jogada atendida é a que CONTÉM o comando (falado durante a reação) ou, se nenhuma, a que terminou até
    JANELA s antes. O fim da jogada não é esticado até a fala (revisão 17/10/2026: colava até 45 s parado).
    Um segundo comando perto de um CLIPA já criado só reforça o mesmo corte."""
    novos = list(moments)
    for c in comandos:
        # sequência com vão cortado: o comando dito no vão é da mesma jogada (revisão 17/10/2026: nascia um CLIPA
        # duplicado por cima dela, porque o fim da sequência fica depois do comando)
        candidatos = [m for m in novos if m.kind != "fail" and (_contem(m, c) or c - JANELA <= m.end <= c or (
            m.segmentos and m.start - 1.0 <= c <= m.end + 2.0))]
        if candidatos:
            dentro = [m for m in candidatos if _contem(m, c)]
            alvo = max(dentro or candidatos, key=lambda m: (m.kind == "abate", m.end))
            alvo.pedido = True
            continue
        m = Moment(max(0.0, c - SEM_JOGADA[0]), min(duracao, c + SEM_JOGADA[1]), "reacao", pico=c, label="CLIPA")
        m.pedido = True
        novos.append(m)
    if len(novos) != len(moments):
        novos.sort(key=lambda m: m.start)
        for i, m in enumerate(novos, 1):
            m.idx = i
    return novos
