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
from .transcribe import Word, _match_at, hype_events

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


DEPOIS_DA_FALA_S = 1.5            # o corte segue isto depois do último termo da reação (raiva ou risada)


def marcar_reacao_na_morte(moments: list[Moment], falas: list[Word], duracao: float = 0.0) -> int:
    """23/11/2026: na jogada em que você morreu logo depois do último abate (`morreu_no_fim`), procura a REAÇÃO nos `FALA_DEPOIS` s seguintes: palavra de raiva
    ou risada (voz alta sozinha não vale: depois de morrer o grito quase sempre é callout, medido nas lives). Com reação: `m.reacao_na_morte` ("RISADA" ganha de
    "RAIVA") e o fim do corte vai até `DEPOIS_DA_FALA_S` depois do último termo, no máximo `FALA_DEPOIS` s depois da morte (e nunca além do fim do vídeo).
    Só cresce o corte. Devolve quantas jogadas ganharam a marca."""
    n = 0
    for m in moments:
        if not (m.kind == "abate" and m.kills and m.morreu_no_fim and m.morte_t is not None):
            continue
        t = m.morte_t
        trecho = [w for w in falas if t - 0.5 <= w.start <= t + FALA_DEPOIS]
        if not trecho:
            continue
        raiva = _termos(trecho, t - 0.5, t + FALA_DEPOIS, RAIVA_TERMOS)
        risos = [(s, e) for s, e, _, termo in hype_events(trecho) if termo == "risada"]
        if not raiva and not risos:
            continue
        m.reacao_na_morte = "RISADA" if risos else "RAIVA"
        fim_termos = [e for s, e in risos]
        for i in range(len(trecho)):                       # fim da ÚLTIMA palavra de cada termo de raiva ("nao acredito" termina em "acredito")
            for termo in raiva:
                if _match_at(trecho, i, termo):
                    fim_termos.append(trecho[min(len(trecho) - 1, i + len(termo.split()) - 1)].end)
        ate = max(fim_termos, default=t) + DEPOIS_DA_FALA_S
        ate = min(ate, t + FALA_DEPOIS, duracao if duracao > 0 else ate)
        if m.segmentos and ate > m.segmentos[-1][1]:
            a, b = m.segmentos[-1]
            m.segmentos = [*m.segmentos[:-1], (a, ate)]
        if ate > m.end:
            m.end = ate
        n += 1
    return n


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
        m.categoria = "morte_com_risada" if risos else "morte_com_raiva"
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
            m.categoria = "risada_solta"   # sem morte sua: piada, fail de alguém do time... o dono confirma antes de publicar
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
