"""Fusão de sinais -> momentos (jogadas e reações) com nota.

Pontuação (inspirada em ferramentas de CS2 e de clipes de live do GitHub):
  * Jogada (abates no kill feed): base {1:6, 2:18, 3:40, 4:70, 5:100}; +3 por HS;
    +10 se todos HS (2+ abates); +5 por abate através da fumaça; +4 por outro ícone
    especial; +10 se os abates saem em sequência rápida.
  * Empolgação E = noisy-OR de voz (V), chat (C) e falas (T):
        E = 1 - (1 - 0.6·V)(1 - 0.9·C)(1 - 0.35·T)     (cada sinal 0–1)
    jogada: +30·E;  reação sem abate: 5 + 35·E (exige voz alterada ou chat).
  * Concordância: ×1,2 se 2+ sinais >= 0,5.
Cortes: 4 s antes do 1º abate, 2,5 s depois do último (estende enquanto a voz segue
empolgada) e ajuste fino para não cortar no meio de uma fala.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .audio import HOP, find_peaks, peak_in
from .config import Config

LABELS = {1: "KILL", 2: "DOUBLE KILL", 3: "TRIPLE KILL", 4: "QUADRA KILL", 5: "ACE"}
BASE = {1: 6, 2: 18, 3: 40, 4: 70, 5: 100}
TETO_GRUPO = 25.0  # s: uma sequência de abates encadeados não passa disso (senão um Deathmatch vira 1 corte de minutos)


@dataclass
class Event:
    t: float
    kind: str  # kill | assist | death | unknown
    icons: list[str] = field(default_factory=list)
    crop_file: str | None = None

    @property
    def hs(self) -> bool:
        return "headshot" in self.icons


@dataclass
class Moment:
    start: float
    end: float
    kind: str  # "abate" | "reacao"
    kills: list[Event] = field(default_factory=list)
    assists: list[Event] = field(default_factory=list)
    label: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    voice: float = 0.0
    chat: float = 0.0
    talk: float = 0.0
    hype_words: list[str] = field(default_factory=list)
    idx: int = 0
    # E2 (partidas, rounds e emoção; preenchidos em `emotion.anotar`, vazios se não houver banners)
    parte: str = ""                  # título da parte do vídeo ("Partida 1 (10x6)", "Deathmatch/aquecimento")
    parte_tipo: str = ""             # "partida" | "deathmatch" | "fora"
    round_n: int | None = None       # nº do round dentro da partida (o que termina logo depois do momento)
    round_resultado: str | None = None   # "vitoria" | "derrota" | None
    emocao: str | None = None        # "RAIVA" | "HYPE" | "RISADA" | None (heurística, sem gabarito ainda)
    confianca: str = ""              # "alta" | "media" (só quando há emoção)
    emocao_motivos: list[str] = field(default_factory=list)
    pico: float | None = None        # instante do pico da reação (estável: não muda com o ajuste às falas)
    # Sequência do round: trechos que entram no vídeo, sem o tempo parado do meio. Vazio = um trecho só [start, end].
    segmentos: list[tuple[float, float]] = field(default_factory=list)
    faixa: str = ""                  # DESTAQUE | PADRAO | RELATORIO (cs2clip/nota.py)
    pedido: bool = False             # o streamer falou "clipa isso" (cs2clip/comando.py)
    clutch: tuple[int, bool] | None = None   # (inimigos, ganhou): só com a demo da partida (cs2clip/sincronizar.py)

    @property
    def janelas(self) -> list[tuple[float, float]]:
        """Os trechos do vídeo que compõem este momento (um só, na maioria dos casos)."""
        return list(self.segmentos) if self.segmentos else [(self.start, self.end)]

    @property
    def duration(self) -> float:
        return sum(b - a for a, b in self.janelas)

    @property
    def focus(self) -> float:
        """Instante principal (último abate ou pico da reação) — usado na miniatura."""
        if self.kills:
            return self.kills[-1].t
        return self.pico if self.pico is not None else self.start + 0.65 * self.duration

    def add(self, pts: float, why: str) -> None:
        if pts >= 0.5:
            self.score += pts
            self.reasons.append(f"{why} (+{pts:.0f})")


def _label(kills: list[Event]) -> str:
    n = len(kills)
    if n >= 5:
        return LABELS[5]
    if n == 1 and kills[0].hs:
        return "HEADSHOT"
    return LABELS.get(n, "KILL")


def _excitement(m: Moment, V, C, T, a: float, b: float) -> float:
    m.voice, m.chat, m.talk = peak_in(V, a, b), peak_in(C, a, b), peak_in(T, a, b)
    return 1 - (1 - 0.6 * m.voice) * (1 - 0.9 * m.chat) * (1 - 0.35 * m.talk)


def _agreement(m: Moment) -> None:
    strong = sum(x >= 0.5 for x in (m.voice, m.chat, m.talk)) + (1 if m.kills else 0)
    if strong >= 2:
        m.score *= 1.2
        m.reasons.append("sinais concordam (×1,2)")


def snap_to_speech(m: Moment, words, duration: float) -> None:
    """Não corta no meio de palavra/frase: recua o início e estende o fim (até +3 s)."""
    if not words:
        return
    for w in words:
        if w.start < m.start < w.end and m.start - w.start <= 1.5:
            m.start = max(0.0, w.start - 0.05)
            break
    end, limit = m.end, min(duration, m.end + 3.0)
    for w in words:
        if w.end <= end - 0.35 or w.start >= limit:
            continue
        if w.start <= end + 0.35:  # fala em andamento no ponto de corte: segue até a pausa
            end = min(limit, w.end + 0.3)
    m.end = max(m.end, end)


def build_moments(events: list[Event], V: np.ndarray, C: np.ndarray | None, T: np.ndarray | None,
                  words, duration: float, cfg: Config) -> list[Moment]:
    c = cfg.cortes
    kills = sorted([e for e in events if e.kind == "kill"], key=lambda e: e.t)
    assists = sorted([e for e in events if e.kind == "assist"], key=lambda e: e.t)

    groups: list[list[Event]] = []
    for k in kills:
        if groups and k.t - groups[-1][-1].t <= c.juntar_intervalo and k.t - groups[-1][0].t <= TETO_GRUPO:
            groups[-1].append(k)
        else:
            groups.append([k])

    moments: list[Moment] = []
    for g in groups:
        start = max(0.0, g[0].t - c.antes)
        end = min(duration, g[-1].t + c.depois)
        if c.estender_reacao:
            limit = min(duration, end + 4.0)
            while end < limit and peak_in(V, end, end + 0.5) > 0.35:
                end += 0.5
        m = Moment(start, end, "abate", kills=g, label=_label(g))
        n = min(len(g), 5)
        m.add(BASE[n], f"{len(g)} abate(s)")
        hs = sum(e.hs for e in g)
        m.add(3 * hs, f"{hs} headshot(s)")
        if len(g) >= 2 and hs == len(g):
            m.add(10, "todos de headshot")
        m.add(5 * sum("smoke" in e.icons for e in g), "através da fumaça")
        m.add(4 * sum(sum(i not in ("headshot", "smoke") for i in e.icons) for e in g), "ícone especial")
        if len(g) >= 2 and (g[-1].t - g[0].t) <= 2.5 * (len(g) - 1):
            m.add(10, "sequência rápida")
        e = _excitement(m, V, C, T, g[0].t - 1.0, end)
        m.add(30 * e, "empolgação (voz/chat/falas)")
        _agreement(m)
        moments.append(m)

    # Reações sem abate: picos de voz, chat ou falas fortes longe das jogadas
    if c.reacoes_sem_abate > 0:
        busy = [(m.start - 8, m.end + 8) for m in moments]
        cands: list[tuple[float, float]] = []
        for curve, th, w in ((V, c.limiar_reacao, 1.0), (C, 0.5, 0.9), (T, 0.99, 0.6)):
            if curve is not None and len(curve):
                cands += [(t, v * w) for t, v in find_peaks(curve, th, 0.3, 20.0)]
        reac: list[Moment] = []
        for t, _ in sorted(cands, key=lambda p: -p[1]):
            if (t < min(90.0, 0.05 * duration) or t > duration - min(30.0, 0.03 * duration)
                    or any(a <= t <= b for a, b in busy)):
                continue  # ignora abertura/encerramento da live (a margem encolhe em vídeo curto)
            m = Moment(max(0.0, t - 8.0), min(duration, t + 4.0), "reacao", label="REAÇÃO", pico=t)
            e = _excitement(m, V, C, T, m.start, m.end)
            if m.voice < 0.3 and m.chat < 0.3:
                continue  # só a fala, sem voz alterada nem chat: provavelmente comentário comum
            m.add(5 + 35 * e, "empolgação (voz/chat/falas)")
            _agreement(m)
            reac.append(m)
            busy.append((m.start - 8, m.end + 8))
            if len(reac) >= c.reacoes_sem_abate:
                break
        moments += reac

    for m in moments:
        m.assists = [a for a in assists if m.start <= a.t <= m.end]
        snap_to_speech(m, words, duration)
        if m.duration < c.duracao_minima:
            m.start = max(0.0, m.end - c.duracao_minima)
    moments.sort(key=lambda m: m.start)
    for i, m in enumerate(moments, 1):
        m.idx = i
    return moments


RESERVA_EMOCAO = 2     # vagas de short guardadas para momentos com emoção (RAIVA/HYPE/RISADA)
NOTA_MIN_EMOCAO = 20   # nota mínima (0-99) para um momento com emoção usar uma vaga reservada


def escolher_shorts(moments: list[Moment], nota_short: float, quantidade: int,
                    reserva_emocao: int = RESERVA_EMOCAO) -> list[Moment]:
    """Quais momentos viram short: as melhores notas (>= `nota_short`), mas `reserva_emocao` das
    `quantidade` vagas ficam para os melhores momentos com emoção (confiança alta primeiro), mesmo
    com nota abaixo do corte (desde que >= NOTA_MIN_EMOCAO). Sem emoção suficiente, as vagas
    reservadas voltam para as melhores notas. Reação sem emoção não vira short (mesma regra da montagem).
    Devolve em ordem de nota, maior primeiro."""
    if quantidade <= 0:
        return []
    # "clipa isso" falado na live: vaga garantida (antes das reservas de emoção)
    pedidos = sorted((m for m in moments if getattr(m, "pedido", False)), key=lambda m: -m.score)[:quantidade]
    nomes = {id(m) for m in pedidos}
    emocionais = sorted((m for m in moments if m.emocao and m.score >= NOTA_MIN_EMOCAO and id(m) not in nomes),
                        key=lambda m: (m.confianca != "alta", -m.score))
    reservados = pedidos + emocionais[:max(0, min(reserva_emocao, quantidade - len(pedidos)))]
    nomes = {id(m) for m in reservados}
    # reação sem emoção é conversa: não vira short (mesma regra da montagem, `keep` de pipeline.render_all)
    resto = sorted((m for m in moments if m.score >= nota_short and id(m) not in nomes
                    and (m.kind == "abate" or m.emocao)), key=lambda m: -m.score)
    escolhidos = reservados + resto[:quantidade - len(reservados)]
    return sorted(escolhidos, key=lambda m: -m.score)


def elegiveis(moments: list[Moment], so_partidas: bool = True) -> list[Moment]:
    """Momentos que viram corte, montagem e short. O usuário só edita partidas competitivas:
    com `so_partidas`, ficam só os momentos dentro de uma partida (Deathmatch/aquecimento e trechos
    fora de partida só aparecem na análise). Se nenhuma partida foi detectada (nenhum banner lido),
    não dá para distinguir e todos os momentos continuam elegíveis, como antes."""
    if not so_partidas or not any(m.parte_tipo == "partida" for m in moments):
        return list(moments)
    return [m for m in moments if m.parte_tipo == "partida"]
