"""Primeira versão (simples, sem pesos calibrados ainda) de rotular RAIVA ou
ALEGRIA por contexto de round, sem demo: usa a linha do tempo de `rounds.py`.

Regra: olha o round que terminou **logo depois** do momento (dentro de
`janela` segundos) — é o round em que aquele momento aconteceu. Perdeu =
contexto de raiva; ganhou = contexto de alegria.

**Isto NÃO é o classificador final.** É o sinal de contexto mínimo pra já dar
pra ver o resultado funcionando; falta combinar com o léxico de falas e o
chat (proposto no lote P4) e calibrar os pesos com o gabarito do usuário
(issue #3) antes de confiar na nota final.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio import HOP
from .partes import Parte, ajustar_pos_round, round_de
from .rounds import RoundTimelineEntry
from .transcribe import Word, hype_events

CONTEXTO_RAIVA = "round_perdido"
CONTEXTO_ALEGRIA = "round_ganho"
CONTEXTO_INDEFINIDO = "indefinido"


def contexto_de_round(t: float, rounds: list[RoundTimelineEntry], janela: float = 90.0) -> str:
    """`t`: instante do momento (s). Acha o 1º fim-de-round que acontece
    depois de `t` e está a até `janela` segundos — esse é o round em que o
    momento ocorreu."""
    candidatos = [r for r in rounds if r.t >= t and r.t - t <= janela]
    if not candidatos:
        return CONTEXTO_INDEFINIDO
    prox = min(candidatos, key=lambda r: r.t)
    return CONTEXTO_RAIVA if prox.resultado == "derrota" else CONTEXTO_ALEGRIA


# ── E2-C: emoção do momento (RAIVA, HYPE, RISADA) e SEQUÊNCIA no Deathmatch ───────────────────
#
# HEURÍSTICA ainda SEM calibração: as regras vêm do plano (docs/PLANO_FINALIZACAO_V1.md, E2) e da
# definição de raiva do usuário (R-A7b: grito/aumento de voz). Os limiares só serão ajustados com o
# gabarito de ~20 momentos marcados por ele (issue #3). Por isso cada emoção sai com `confianca`
# ("alta" quando o contexto do jogo concorda, "media" quando só a voz/falas dizem).
LIMIAR_VOZ = 0.6      # 0 = fala normal ... 1 = grito (mesmo valor de cortes.limiar_reacao)
ANTES_ABATE = 5.0     # s: abate seu até 5 s antes do pico da voz = comemoração, não raiva
ANTES_MORTE = 12.0    # s: sua morte até 12 s antes do pico da voz = o sinal mais forte de raiva
MIN_RISADAS = 2       # risadas (kkkk/haha) nas falas do trecho para chamar de RISADA
SEQUENCIA_MIN = 3     # abates encadeados no Deathmatch para virar "SEQUÊNCIA xN"


@dataclass
class Emocao:
    categoria: str          # "RAIVA" | "HYPE" | "RISADA"
    confianca: str          # "alta" | "media"
    motivos: list[str]


def _pico(curva: np.ndarray | None, a: float, b: float) -> tuple[float, float]:
    """(valor, instante em s) do maior ponto da curva entre a e b."""
    if curva is None or not len(curva):
        return 0.0, a
    i, j = max(0, int(a / HOP)), min(len(curva), int(b / HOP))
    if j <= i:
        return 0.0, a
    k = i + int(np.argmax(curva[i:j]))
    return float(curva[k]), k * HOP


# Medido no gabarito do usuário (17/10/2026): voz alta SOZINHA não é emoção. As "raivas" erradas eram tática
# gritada ("bora rushar, confia", "só rush, vai vai vai"); depois de morrer, o grito quase sempre é callout ("meio,
# meio", "ali ali", "garagem"); os "HYPE" errados eram conversa ("e aí, boa noite"). A raiva verdadeira tinha palavra
# de frustração ("Nossa, quase, velho! Merda, hein!") e as alegrias vinham logo depois de abates.
RAIVA_TERMOS = ["merda", "porra", "caralho", "puta que pariu", "pqp", "vsf", "vai se foder", "aff", "nao acredito",
                "quase", "filho da puta", "lixo", "ridiculo", "roubado", "sacanagem", "absurdo", "inferno", "cacete",
                "desgraca", "que odio", "toma no cu", "palhacada", "nao e possivel", "como assim"]
# Callouts e tática: depois de um abate (ou de morrer) o jogador grita informação para o time. Medido nas lives
# (17/10/2026): "dois toyota, dois toyota", "deve ser B, B, B", "pegou areia, abriu o banco" vinham com voz alta.
CALLOUTS = ["b", "bomb", "meio", "mid", "rampa", "pilha", "base", "banco", "jungle", "fogo", "areia", "varanda",
            "garagem", "porta", "janela", "cat", "long", "curto", "toyota", "caixa", "ct", "direita", "esquerda",
            "ultimo", "rato", "atras", "saiu", "rush", "plantou", "plantando", "defusa", "smoke", "smokei", "flash",
            "molotov", "molly", "dois", "tres", "close", "pode jogar", "acelera", "fundo", "escada", "torre",
            "corredor", "tunel", "palco"]   # só termos de CS: palavras comuns ("a", "vai") pegavam qualquer frase
VOZ_SEM_PALAVRA = 0.85   # sem fala de empolgação, só voz MUITO alta (e sem callout) conta como comemoração
HYPE_FORTE = ["que tiro", "clutch", "ace", "que isso", "inacreditavel", "puta que pariu", "meu deus", "clipa",
              "clipa isso", "insano", "monstro", "absurdo", "olha isso", "que jogada"]


# Servem para raiva E para comemoração ("caralho, que tiro!"): sozinhos só viram RAIVA com o contexto do jogo
# (você tinha morrido ou o round foi perdido). Revisão 17/10/2026: "absurdo"/"puta que pariu" sem abate viravam
# raiva até em comemoração; no gabarito, "Manito, caralho" (conversa, round ganho) era uma raiva falsa.
AMBIGUOS = {"caralho", "porra", "puta que pariu", "pqp", "absurdo", "nao acredito", "como assim", "cacete", "nossa", "quase", "aff"}


def _termos(falas: list[Word], a: float, b: float, lista: list[str]) -> list[str]:
    from .transcribe import _match_at
    trecho = [w for w in falas if a <= w.start <= b]
    achados = []
    for i in range(len(trecho)):
        for termo in lista:
            if termo not in achados and _match_at(trecho, i, termo):
                achados.append(termo)
    return achados


def classificar(inicio: float, fim: float, voz: np.ndarray | None, falas: list[Word],
                abates: list[float], resultado_round: str | None,
                limiar: float = LIMIAR_VOZ, mortes: list[float] = (),
                janelas: list[tuple[float, float]] | None = None) -> Emocao | None:
    """Emoção de um trecho. Prioridade: RISADA > RAIVA > HYPE.
      * RISADA: >= MIN_RISADAS risadas nas falas;
      * RAIVA: palavra de frustração no trecho e nenhum abate seu logo antes (alta: morreu antes ou round perdido);
      * HYPE: abate seu logo antes + voz alta ou fala de empolgação (alta: round ganho); sem abate, só com
        fala forte ("que tiro", "clutch"...) e voz alta (média).
    `janelas`: os trechos que o vídeo mostra (sequência com o tempo parado cortado). Só eles contam: risada ou
    grito no vão cortado não aparece no vídeo (revisão 17/10/2026). Sem janelas, vale [inicio, fim]."""
    janelas = list(janelas) if janelas else [(inicio, fim)]
    falas = [w for w in falas if any(a <= w.start <= b for a, b in janelas)]
    v, t_pico = max((_pico(voz, a, b) for a, b in janelas), key=lambda p: p[0])
    alta = v >= limiar
    risadas = sum(1 for s, _, _, termo in hype_events([w for w in falas if inicio <= w.start <= fim])
                  if termo == "risada")
    if risadas >= MIN_RISADAS:
        return Emocao("RISADA", "alta" if alta else "media", [f"{risadas} risadas nas falas"])
    abate_antes = any(t_pico - ANTES_ABATE <= k <= t_pico + 0.5 for k in abates) or any(
        a <= k <= b for a, b in janelas for k in abates)
    raiva = _termos(falas, inicio, fim, RAIVA_TERMOS)
    morte_antes = any(inicio - ANTES_MORTE <= k <= fim for k in mortes)
    so_comemoracao = _termos(falas, inicio, fim, [t for t in HYPE_FORTE if t not in AMBIGUOS])
    exclusivos = [t for t in raiva if t not in AMBIGUOS]
    contexto_ruim = morte_antes or resultado_round == "derrota"
    if raiva and not (exclusivos or contexto_ruim) or (raiva and so_comemoracao and not exclusivos):
        raiva = []
    if raiva and not abate_antes:
        mot = [f"falou: {', '.join(raiva[:3])}"]
        if morte_antes:
            mot.append("você tinha morrido")
        if resultado_round == "derrota":
            mot.append("round perdido")
        return Emocao("RAIVA", "alta" if (morte_antes or resultado_round == "derrota") else "media", mot)
    hype = _termos(falas, inicio, fim, HYPE_FORTE + ["nossa", "caralho", "porra", "vamo", "vamos", "toma",
                                                     "uhul", "eita", "caraca", "rapaz"])
    callout = _termos(falas, inicio, fim, CALLOUTS)
    so_voz = v >= VOZ_SEM_PALAVRA and not callout
    if abate_antes and (hype or so_voz):
        mot = (["voz alta (%.2f)" % v] if alta else []) + ([f"falou: {', '.join(hype[:3])}"] if hype else []) +               ["abate seu logo antes"] + (["round ganho"] if resultado_round == "vitoria" else [])
        return Emocao("HYPE", "alta" if resultado_round == "vitoria" else "media", mot)
    if so_comemoracao and alta and not abate_antes:   # sem abate: só palavra que é SÓ de comemoração
        return Emocao("HYPE", "media", ["voz alta (%.2f)" % v, f"falou: {', '.join(so_comemoracao[:3])}"])
    return None


def anotar(moments, partes: list[Parte], voz: np.ndarray | None, falas: list[Word],
           abates: list[float], limiar: float = LIMIAR_VOZ, mortes: list[float] = ()) -> None:
    """Preenche em cada `Moment` a parte do vídeo, o round (e se foi ganho ou perdido), a emoção e,
    no Deathmatch, o rótulo "SEQUÊNCIA xN". Reação com emoção troca o rótulo genérico "REAÇÃO" (menos o CLIPA, pedido pelo streamer)."""
    for m in moments:
        # abate logo depois do banner é do round que acabou (o mesmo ajuste de sequencia.unir_sequencias)
        t = ajustar_pos_round(m.focus, partes) if m.kills else m.focus
        parte, round_n, resultado = round_de(t, partes)
        if parte is not None:
            m.parte, m.parte_tipo = parte.titulo, parte.tipo
        m.round_n, m.round_resultado = round_n, resultado
        if m.kind == "abate" and m.parte_tipo == "deathmatch" and len(m.kills) >= SEQUENCIA_MIN:
            m.label = f"SEQUÊNCIA x{len(m.kills)}"
        e = classificar(m.start, m.end, voz, falas, abates, resultado, limiar, mortes, janelas=m.janelas)
        if e is not None:
            m.emocao, m.confianca, m.emocao_motivos = e.categoria, e.confianca, e.motivos
            if m.kind == "reacao" and m.label != "CLIPA":   # o corte pedido na live ("clipa isso") mantém a marca
                m.label = e.categoria
