"""Nota explicável de cada momento (0 a 99) e a faixa dele: DESTAQUE, PADRÃO ou só no relatório.

Por que (pesquisa P7, docs/research/PLANO_REFERENCIAS_P7.md, item R3): Opus/StreamLadder/Vizard ordenam por nota
e mostram os componentes; a Steam separa clipes em 3 classes (Featured/Standard/None). Os PESOS abaixo são uma
PROPOSTA inicial (a revisão cética lembrou que valores de jogo profissional não valem direto para Premier): são
ajustados com os seus votos (R4) e medidos contra o gabarito.

Entra o que dá para medir NO VÍDEO. Clutch 1vX, parede e noscope só existem quando a demo da mesma partida foi
sincronizada (cs2clip/sincronizar.py). Economia, bomba e abertura do round ainda não entram.
"""
from __future__ import annotations

from .moments import Moment
from .partes import Parte, ajustar_pos_round, round_de

# ── pesos (proposta, a calibrar) ───────────────────────────────────────────────────────────────
PESOS = {
    "abate": 5,                  # cada abate
    "multi": {2: 8, 3: 18, 4: 32, 5: 50},   # bônus pelo total de abates no corte (cresce mais que linear)
    "rapido": 4,                 # cada abate a até 1,5 s do anterior
    "tudo_rapido": 15,           # 4+ abates em até 5 s
    "headshot": 3,
    "fumaca": 5,
    "parede": 6,                 # através da parede (só a demo sabe; o kill feed mostra o ícone, mas raramente se lê)
    "sem_mira": 12,              # noscope
    "cego": 3,                   # atirando cego
    "no_ar": 6,                  # abate pulando/no ar (inair)
    "abertura": 4,               # 1º abate do round (entry kill 5v5 -> 5v4)
    "clutch_ganho": 10,          # + clutch_por_inimigo x (inimigos - 1): clutch 1v3 ganho = 10 + 2 x 8 = 26
    "clutch_por_inimigo": 8,
    "clutch_perdido": 4,         # tentou e perdeu: ainda é uma jogada
    "pistol_ganho": 5,           # round 1 ou 13 (MR12) ganho
    "match_point": 10,           # alguém a 1 round de ganhar (12 no tempo normal; 15, 18... na prorrogação)
    "doze_a_doze": 8,
    "voz": 10,                   # grito/voz alta (>= limiar)
    "falas": 5,                  # falas de empolgação no trecho
    "chat": 10,                  # pico de chat (nunca é gatilho sozinho)
    "emocao_alta": 5,            # emoção com o contexto do jogo concordando
    "pedido": 30,                # falou "clipa isso" na live: vai para os shorts
    "reacao_base": 5,            # momento só de reação (sem abate): sozinho fica no relatório
    "reacao_voz": 15,            # x força da voz (0-1)
    "reacao_emocao": 10,         # reação com emoção detectada pelas PALAVRAS (gabarito: voz sem palavra era conversa)
    "falha_reacao": 20,          # 23/11 (opção cortes.falha_com_reacao): começou bem, você morreu logo depois do último abate e reagiu (raiva/risada)
}
DESTAQUE, PADRAO = 60, 30
MAXIMO = 99
RAPIDO_S, TUDO_RAPIDO_S = 1.5, 5.0


def faixa(nota: float) -> str:
    return "DESTAQUE" if nota >= DESTAQUE else "PADRAO" if nota >= PADRAO else "RELATORIO"


def _placar_antes(parte: Parte, numero: int) -> tuple[int, int, int] | None:
    """(ganhos, perdidos, desconhecidos) antes do round `numero`. `numero` conta os banners perdidos, então
    `numero - 1` rounds foram jogados; os que não têm banner lido entram como desconhecidos."""
    numeros = parte.numeros or list(range(1, len(parte.rounds) + 1))
    if numero not in numeros:
        return None
    i = numeros.index(numero)
    ganhos = sum(r.resultado == "vitoria" for r in parte.rounds[:i])
    return ganhos, i - ganhos, max(0, numero - 1 - i)


def _decisivo(g: int, d: int) -> str | None:
    """M-004: o placar (g x d, antes do round) é um empate de 12x12 ("empate") ou um match point ("match point")? Vale o tempo normal (MR12:
    quem tem 12 e o outro menos que 12) e as prorrogações (MR3: cada uma começa empatada em 12, 15, 18... e vence quem chega a 4 rounds
    nela, então o match point é ter 15, 18, 21... contra o adversário que está na mesma prorrogação, a até 3 rounds). Antes só o 12x12
    e "alguém com 12" valiam: 12x13 ganhava match point indevido e o match point de verdade da prorrogação (15x14) nada."""
    maior, menor = max(g, d), min(g, d)
    if maior < 12 or (maior - 12) % 3:
        return None
    if maior == menor:
        return "empate"
    return "match point" if maior == 12 or menor >= maior - 3 else None


def pontuar(m: Moment, partes: list[Parte], limiar_voz: float = 0.6, falha_com_reacao: bool = False) -> None:
    """Recalcula `m.score` (0-99), `m.reasons` (cada motivo com seus pontos) e `m.faixa`."""
    if m.kind == "fail" or m.categoria == "resenha":   # a nota do fail / da resenha vem da força da reação (cs2clip/fails.py, resenha.py)
        m.score = float(min(MAXIMO, m.score))
        m.faixa = faixa(m.score)
        return
    m.score, m.reasons = 0.0, []
    p = PESOS
    if m.kills:
        n = len(m.kills)
        m.add(p["abate"] * n, f"{n} abate(s)")
        if n >= 2:
            m.add(p["multi"][min(n, 5)], {2: "DOUBLE", 3: "TRIPLE", 4: "QUADRA", 5: "ACE"}[min(n, 5)])
        ts = [k.t for k in m.kills]
        rapidos = sum(1 for a, b in zip(ts, ts[1:]) if b - a <= RAPIDO_S)
        m.add(p["rapido"] * rapidos, f"{rapidos} abate(s) em sequência rápida")
        if n >= 4 and ts[-1] - ts[0] <= TUDO_RAPIDO_S:
            m.add(p["tudo_rapido"], f"{n} abates em {ts[-1] - ts[0]:.1f} s")
        m.add(p["headshot"] * sum(k.hs for k in m.kills), "headshot(s)")
        m.add(p["fumaca"] * sum("smoke" in k.icons for k in m.kills), "através da fumaça")
        m.add(p["parede"] * sum("wallbang" in k.icons for k in m.kills), "através da parede")
        m.add(p["sem_mira"] * sum("noscope" in k.icons for k in m.kills), "sem mira (noscope)")
        m.add(p["cego"] * sum("blind" in k.icons for k in m.kills), "atirando cego")
        m.add(p["no_ar"] * sum("inair" in k.icons for k in m.kills), "no ar (airshot)")
        if getattr(m, "abertura", False):
            m.add(p["abertura"], "abate de abertura (entry)")
        if m.clutch:
            inimigos, ganhou = m.clutch
            m.add(p["clutch_ganho"] + p["clutch_por_inimigo"] * (inimigos - 1) if ganhou else p["clutch_perdido"],
                  f"clutch 1v{inimigos}" + (" ganho" if ganhou else " (perdido)"))
        # abate logo depois do banner é do round que acabou (o mesmo ajuste de sequencia.unir_sequencias)
        parte, numero, resultado = round_de(ajustar_pos_round(m.kills[-1].t, partes), partes)
        if parte is not None and numero is not None:
            if numero in (1, 13) and resultado == "vitoria":
                m.add(p["pistol_ganho"], "pistol round ganho")
            placar = _placar_antes(parte, numero)
            if placar:
                g, d, x = placar
                decisivo = _decisivo(g, d)
                if decisivo == "empate":
                    m.add(p["doze_a_doze"], f"{g}x{d}")
                elif decisivo == "match point":
                    m.add(p["match_point"], "match point")
                elif 0 < x <= 2 and (g + x >= 12 or d + x >= 12) and max(g, d) <= 12:
                    # revisão 17/10/2026: com banner perdido o placar fica menor e o match point nunca aparecia
                    m.add(p["match_point"] / 2, "match point (aproximado: há round sem banner lido)")
    else:
        m.add(p["reacao_base"], "reação")
        m.add(p["reacao_voz"] * m.voice, "voz")
        if m.emocao:
            m.add(p["reacao_emocao"], f"{m.emocao} nas falas")
    if m.voice >= limiar_voz and m.kills and m.emocao == "HYPE":   # voz alta sem emoção era callout/tática
        m.add(p["voz"], "voz alta")
    if m.talk >= 0.5 or m.hype_words:
        m.add(p["falas"], "falas de empolgação")
    if m.chat >= 0.5:
        m.add(p["chat"], "pico no chat")
    if m.emocao and m.confianca == "alta":
        m.add(p["emocao_alta"], f"{m.emocao} com o contexto do jogo")
    if m.pedido:
        m.add(PESOS["pedido"], "você pediu o clipe na live")
    if falha_com_reacao and m.kills and m.morreu_no_fim and m.reacao_na_morte:
        m.add(PESOS["falha_reacao"], f"começou bem e terminou mal, com a reação ({m.reacao_na_morte})")
    if m.score > MAXIMO:
        m.reasons.append(f"limitada a {MAXIMO}")
        m.score = float(MAXIMO)
    m.faixa = faixa(m.score)


def pontuar_todos(moments: list[Moment], partes: list[Parte], limiar_voz: float = 0.6, falha_com_reacao: bool = False) -> None:
    for m in moments:
        pontuar(m, partes, limiar_voz, falha_com_reacao)
