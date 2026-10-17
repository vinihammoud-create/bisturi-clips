"""Trilha de FAILS e momentos engraçados (pesquisa P7, R16; o usuário pediu em 17/10/2026, separada das jogadas).

Só com o que o vídeo mede com segurança:
  * a SUA morte (painel "matou você", cs2clip/mortes.py) seguida de palavra de frustração ou de risada nos
    segundos seguintes. Medido nas lives: grito alto depois de morrer quase sempre é callout ("meio, meio", "ali
    ali", "garagem"), então voz alta sozinha não basta;
  * trechos com 2+ risadas, longe das jogadas.
Uma morte logo depois de abates seus é parte da jogada (fica de fora). Os fails viram cortes próprios (tipo
"fail"), não entram na montagem nem nos shorts de jogadas, e ganham a compilação FAILS_<id>.mp4.
Com a demo da mesma partida (E6) entram os gatilhos objetivos (morte para faca/zeus/C4, team kill, etc.).
"""
from __future__ import annotations

import numpy as np

from .audio import HOP
from .emotion import RAIVA_TERMOS, _termos
from .moments import Moment, snap_to_speech
from .sequencia import sobreposicao
from .transcribe import Word, hype_events

ANTES, DEPOIS = 6.0, 6.0          # s em volta da morte
FALA_DEPOIS = 8.0                 # s depois da morte em que a reação conta
JOGADA_ANTES = 8.0                # abate seu até 8 s antes da morte: é jogada, não fail
RISADAS_MIN = 2
JANELA_RISADA = 10.0


def _pico(voz: np.ndarray | None, a: float, b: float) -> float:
    if voz is None or not len(voz):
        return 0.0
    i, j = max(0, int(a / HOP)), min(len(voz), int(b / HOP))
    return float(voz[i:j].max()) if j > i else 0.0


def _risadas(falas: list[Word], a: float, b: float) -> list[float]:
    return [s for s, _, _, termo in hype_events([w for w in falas if a <= w.start <= b]) if termo == "risada"]


def achar_fails(mortes: list[float], falas: list[Word], voz: np.ndarray | None, abates: list[float],
                existentes: list[Moment], duracao: float, quantidade: int = 8) -> list[Moment]:
    """Momentos do tipo "fail", do mais forte para o mais fraco (no máximo `quantidade`)."""
    if quantidade <= 0:
        return []
    candidatos: list[Moment] = []
    for t in sorted(mortes):
        if any(t - JOGADA_ANTES <= k <= t for k in abates):
            continue
        raiva = _termos(falas, t - 1.0, t + FALA_DEPOIS, RAIVA_TERMOS)
        risos = _risadas(falas, t - 1.0, t + FALA_DEPOIS)
        if not raiva and len(risos) < 1:
            continue
        m = Moment(max(0.0, t - ANTES), min(duracao, t + DEPOIS), "fail", pico=t,
                   label="RISADA" if risos else "FAIL")
        m.voice = _pico(voz, t, t + FALA_DEPOIS)
        m.reasons = ["você morreu"] + ([f"falou: {', '.join(raiva[:3])}"] if raiva else []) + (
            [f"{len(risos)} risada(s)"] if risos else [])
        m.score = 10 + 20 * m.voice + 8 * min(len(raiva), 2) + 10 * min(len(risos), 2)
        candidatos.append(m)
    # risadas longe das mortes (piada, fail de alguém do time)
    risos_todos = sorted(s for s, _, _, termo in hype_events(falas) if termo == "risada")
    i = 0
    while i < len(risos_todos):
        grupo = [r for r in risos_todos[i:] if r - risos_todos[i] <= JANELA_RISADA]
        if len(grupo) >= RISADAS_MIN:
            t = grupo[0]
            m = Moment(max(0.0, t - 6.0), min(duracao, grupo[-1] + 3.0), "fail", pico=t, label="RISADA")
            m.voice = _pico(voz, m.start, m.end)
            m.reasons = [f"{len(grupo)} risadas"]
            m.score = 10 + 20 * m.voice + 10 * min(len(grupo), 3)
            candidatos.append(m)
        i += len(grupo)
    ficam: list[Moment] = []
    for m in sorted(candidatos, key=lambda m: -m.score):
        if all(sobreposicao(m, o) <= 0.3 for o in existentes + ficam):
            snap_to_speech(m, falas, duracao)
            ficam.append(m)
        if len(ficam) >= quantidade:
            break
    return ficam
