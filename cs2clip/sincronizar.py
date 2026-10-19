"""Liga a demo (`.dem`) ao vídeo da MESMA partida e usa os fatos exatos da demo para melhorar a análise do vídeo.

O vídeo só estima; a demo sabe. Casando os abates do jogador (instante na demo x instante lido no kill feed) acha-se
o deslocamento `t_video = t_demo + deslocamento`, que em vídeo corrido (sem pausa nem corte) é fixo na partida inteira
(medido em 18/10/2026: 39 de 39 abates de duas partidas casaram, resíduo médio < 1 s).

Com o deslocamento, dentro da janela da partida:
  * abates: ganham os ícones certos (headshot, parede, noscope, fumaça, cego); os que o vídeo não viu entram;
    os que a demo não tem (leitura falsa do kill feed) saem;
  * mortes: o instante exato (o painel "matou você" só aparece 1 a 3 s depois);
  * rounds: a linha do tempo completa (o banner perdido na leitura deixa de faltar), com número, vencedor e placar;
  * clutch 1vX e o intervalo exato de cada partida.

Só LÊ a demo e o que o vídeo já tem. Sem acerto confiável (poucos abates casados), nada é mudado.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import numpy as np

from .demo import PartidaDemo
from .moments import Event
from .rounds import RoundTimelineEntry

TOLERANCIA = 2.0        # s: um abate do vídeo casa com o da demo se estiver a menos que isso (depois do deslocamento)
TOLERANCIA_FINAL = 4.0  # s: com o deslocamento já medido (resíduo ~0,05 s), o pareamento final aceita mais: o atirador
                        # cego (tela branca do flash) faz o kill feed ser lido 2 a 3 s atrasado
MIN_CASADOS = 3         # abates casados para confiar no deslocamento
ATRASO_BANNER = 2.3     # s entre o fim do round (demo) e o banner na tela (mediana medida em 18/10/2026: 2,0 a 3,0)
MARGEM_JANELA = 10.0    # s além do fim do último round que ainda contam como "dentro da partida"


@dataclass
class Sincronia:
    deslocamento: float                  # t_video = t_demo + deslocamento
    casados: int                         # abates da demo com um abate do vídeo
    total_demo: int
    total_video: int                     # abates do vídeo dentro da janela da partida
    residuo: float                       # erro médio absoluto (s) dos pares
    confianca: str                       # "alta" | "media" | "baixa"
    pares: list[tuple[int, int]] = field(default_factory=list)    # (índice na demo, índice no vídeo)

    @property
    def confiavel(self) -> bool:
        return self.confianca in ("alta", "media")


def _casar(demo: list[float], video: list[float], deslocamento: float,
           tol: float = TOLERANCIA) -> list[tuple[int, int, float]]:
    """Pares (i_demo, j_video, resíduo) um a um, do mais próximo para o mais distante."""
    candidatos = sorted((abs(v - (d + deslocamento)), i, j) for i, d in enumerate(demo)
                        for j, v in enumerate(video) if abs(v - (d + deslocamento)) <= tol)
    usados_i: set[int] = set()
    usados_j: set[int] = set()
    pares: list[tuple[int, int, float]] = []
    for dist, i, j in candidatos:
        if i in usados_i or j in usados_j:
            continue
        usados_i.add(i)
        usados_j.add(j)
        pares.append((i, j, video[j] - (demo[i] + deslocamento)))
    return sorted(pares)


def estimar(demo: list[float], video: list[float], tol: float = TOLERANCIA) -> Sincronia | None:
    """Acha o deslocamento que casa mais abates da demo com os do vídeo. None = sem abates para comparar."""
    if len(demo) < MIN_CASADOS or len(video) < MIN_CASADOS:
        return None
    d = np.array(sorted(demo))
    v = np.array(sorted(video))
    candidatos = np.unique(np.round((v[None, :] - d[:, None]).ravel(), 1))
    melhor: tuple[int, float, float] | None = None
    for c in candidatos:
        alvo = d + c
        pos = np.searchsorted(v, alvo)
        esq = np.abs(alvo - v[np.clip(pos - 1, 0, len(v) - 1)])
        dir_ = np.abs(alvo - v[np.clip(pos, 0, len(v) - 1)])
        dist = np.minimum(esq, dir_)
        n = int((dist <= tol).sum())
        erro = float(dist[dist <= tol].mean()) if n else 99.0
        if melhor is None or (n, -erro) > (melhor[0], -melhor[2]):
            melhor = (n, float(c), erro)
    desloc = melhor[1]
    for _ in range(2):      # afina com a mediana dos resíduos (o kill feed aparece uma fração de segundo depois)
        pares = _casar(sorted(demo), sorted(video), desloc, tol)
        if not pares:
            break
        desloc += statistics.median(r for _, _, r in pares)
    demo_o, video_o = sorted(demo), sorted(video)
    pares = _casar(demo_o, video_o, desloc, max(tol, TOLERANCIA_FINAL))
    if not pares:
        return None
    ini, fim = demo_o[0] + desloc - TOLERANCIA_FINAL, demo_o[-1] + desloc + TOLERANCIA_FINAL
    na_janela = sum(ini <= t <= fim for t in video_o)
    casados = len(pares)
    frac = casados / len(demo_o)
    confianca = ("alta" if casados >= 5 and frac >= 0.8 else "media" if casados >= MIN_CASADOS and frac >= 0.5
                 else "baixa")
    residuo = statistics.median(abs(r) for _, _, r in pares)
    return Sincronia(round(desloc, 3), casados, len(demo_o), na_janela, round(residuo, 3), confianca,
                     [(i, j) for i, j, _ in pares])


@dataclass
class Fusao:
    """O que a fusão devolve: as listas já corrigidas e o que foi mudado (para o relatório e a comparação)."""
    events: list[Event]
    mortes: list[float]
    rounds: list[RoundTimelineEntry]
    exatos: list[tuple[float, float]]                           # (início, fim) exatos de cada partida, em tempo de vídeo
    clutches: list[tuple[float, float, int, bool]]              # (t, fim do round, inimigos, ganhou), em tempo de vídeo
    relatorio: list[dict]


def _janela(partida: PartidaDemo, desloc: float) -> tuple[float, float]:
    return partida.rounds[0].inicio + desloc - 5.0, partida.rounds[-1].fim + desloc + MARGEM_JANELA


def integrar(demos: list[tuple[PartidaDemo, str]], events: list[Event], mortes: list[float],
             rounds: list[RoundTimelineEntry], duracao: float) -> Fusao:
    """`demos`: [(partida lida da demo, nome do arquivo)]. Cada demo é sincronizada com os abates do vídeo; só as de
    confiança alta ou média mexem na análise."""
    events = sorted(events, key=lambda e: e.t)   # os índices dos pares valem para as listas em ordem de tempo
    mortes = list(mortes)
    rounds = list(rounds)
    exatos: list[tuple[float, float]] = []
    clutches: list[tuple[float, float, int, bool]] = []
    relatorio: list[dict] = []
    for partida, arquivo in demos:
        item: dict = {"demo": arquivo, "mapa": partida.mapa, "jogador": partida.nome, "placar": list(partida.placar)}
        relatorio.append(item)
        if not partida.rounds or not partida.abates:
            item["usada"] = False
            item["motivo"] = "a demo não tem abates deste jogador"
            continue
        abates = sorted(partida.abates, key=lambda a: a.t)
        kills_v = [e.t for e in events if e.kind == "kill"]
        s = estimar([a.t for a in abates], kills_v)
        if s is None or not s.confiavel:
            item.update(usada=False, motivo="não achei esta partida no vídeo (poucos abates em comum)",
                        casados=s.casados if s else 0, total_demo=len(partida.abates))
            continue
        o = s.deslocamento
        ini, fim = _janela(partida, o)
        item.update(usada=True, deslocamento=o, confianca=s.confianca, casados=s.casados, total_demo=s.total_demo,
                    residuo=s.residuo)

        # abates: ícones certos nos casados, os que faltam entram, os falsos saem
        idx_kills = [k for k, e in enumerate(events) if e.kind == "kill"]
        casados_video = {idx_kills[j] for _, j in s.pares}
        for i, j in s.pares:
            ev = events[idx_kills[j]]
            ev.icons = list(abates[i].icones)
            ev.t = round(abates[i].t + o, 2)    # o instante da demo é exato (o kill feed pode atrasar)
        casados_demo = {i for i, _ in s.pares}
        novos = [Event(round(a.t + o, 2), "kill", list(a.icones), None)
                 for i, a in enumerate(abates) if i not in casados_demo]
        falsos = [k for k in idx_kills if ini <= events[k].t <= fim and k not in casados_video]
        item.update(abates_recuperados=len(novos), abates_falsos_removidos=len(falsos))
        events = [e for k, e in enumerate(events) if k not in set(falsos)] + novos
        events.sort(key=lambda e: e.t)

        # mortes: instante exato, no lugar das lidas no painel (que atrasa 1 a 3 s)
        antes = [m for m in mortes if ini <= m <= fim]
        item.update(mortes_demo=len(partida.mortes), mortes_video=len(antes))
        mortes = [m for m in mortes if not ini <= m <= fim] + [round(m + o, 2) for m in partida.mortes]
        mortes.sort()

        # rounds: linha do tempo completa; o atraso do banner vem dos banners que o vídeo leu
        meus = {r.n: ("vitoria" if r.vencedor == partida.lados.get(r.n) else "derrota")
                for r in partida.rounds if partida.lados.get(r.n)}
        lidos = [r for r in rounds if ini <= r.t <= fim + 5]
        atrasos = []
        for r in partida.rounds:
            perto = [b.t - (r.fim + o) for b in lidos if 0 <= b.t - (r.fim + o) <= 8]
            if perto:
                atrasos.append(min(perto))
        atraso = round(statistics.median(atrasos), 2) if len(atrasos) >= 3 else ATRASO_BANNER
        diferentes = 0
        for r in partida.rounds:
            quer = meus.get(r.n)
            b = next((b for b in lidos if 0 <= b.t - (r.fim + o) <= 8), None)
            if b is not None and quer is not None and b.resultado != quer:
                diferentes += 1
        item.update(rounds_demo=len(partida.rounds), rounds_video=len(lidos), banner_atraso=atraso,
                    banners_com_resultado_diferente=diferentes)
        sintetico = [RoundTimelineEntry(round(r.fim + o + atraso, 2), meus[r.n], 1.0)
                     for r in partida.rounds if r.n in meus]
        rounds = [r for r in rounds if not ini <= r.t <= fim + 5] + sintetico
        rounds.sort(key=lambda r: r.t)

        exatos.append((max(0.0, partida.rounds[0].inicio + o - 3.0), min(duracao, partida.rounds[-1].fim + o + 12.0)))
        for c in partida.clutches:
            rd = next((r for r in partida.rounds if r.n == c.round), None)
            if rd is not None:
                clutches.append((round(c.t + o, 2), round(rd.fim + o, 2), c.inimigos, c.ganhou))
    return Fusao(events, mortes, rounds, exatos, clutches, relatorio)


def clutch_do_momento(m_kills: list[Event], clutches: list[tuple[float, float, int, bool]]) -> tuple[int, bool] | None:
    """(inimigos, ganhou) se algum abate do momento foi feito DEPOIS de o jogador ficar sozinho num round de clutch."""
    for t, fim, inimigos, ganhou in clutches:
        if any(t - 0.5 <= k.t <= fim + 3.0 for k in m_kills):
            return inimigos, ganhou
    return None
