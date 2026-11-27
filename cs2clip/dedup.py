"""Duplicatas do kill feed: a MESMA linha física detectada como mais de uma trilha.

Medido em 17/10/2026 (E1): o detector às vezes parte uma linha em 2-3 trilhas (a linha entra com
"flash", rola para baixo, ou é coberta pelo placar). Sem tratamento, cada trilha vira um abate e
`build_moments` junta as duplicatas num DOUBLE KILL falso. O classificador novo lê mais abates e
por isso revelou mais duplicatas (23 -> 56 pares suspeitos nos 4 VODs).

Critério (conferido a olho em 74 pares de 31 grupos, `tests/fixtures/duplicatas/`):
  * mesma classe (abate com abate, assistência com assistência);
  * a menos de JANELA_DUP s uma da outra;
  * mesma vítima (último nome colorido à direita) e mesma arma (largura do ícone).
Sem a exigência de mesma classe, a regra tinha 5 falsos em 56 (todos envolviam uma assistência);
com ela, não erra nenhum par do gabarito.
"""
from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

from .classify import SEG_H, Parsed, Segment

JANELA_DUP = 8.0   # s: igual a cortes.juntar_intervalo (uma duplicata aqui viraria DOUBLE KILL falso)
SIM_VITIMA = 0.6   # mesmo limiar que o classificador usa para reconhecer um nome
TOL_ARMA = 4       # px: a largura do ícone da arma oscila 1-2 px entre quadros
SUAVIZAR = 2.0     # sigma (px da máscara): a mesma linha em quadro mais borrado ainda correlaciona
DESLOCAR = 2       # px: a linha desliza 1-3 px quando entra outra embaixo
CLASSES = ("kill", "assist")
SOBREPOR_MIN_S = 0.5   # s: tempo que duas leituras precisam existir JUNTAS para contar como "ao mesmo tempo"
MESMA_LINHA_PX = 8     # px (escala 1080p): diferença de y DE ENTRADA para ser a mesma linha (entradas diferentes entram ≥ 30 px afastadas)
NASCER_JUNTAS_S = 1.0  # s: as duas leituras precisam ter APARECIDO juntas (o flash de entrada do caso real: 0,2 s)
PEDACO_MAX_S = 2.5     # s: uma das duas precisa ser um pedaço curto (o flash do caso real durou 1,3 s; uma linha inteira dura ~7 s)


def _vitima(p: Parsed) -> Segment:
    """O lado direito INTEIRO como um segmento só. Em quadros borrados (1080p/1440p) a tag do clã e o nome
    se separam em 2 segmentos, e olhar só o último dava largura 32 contra 105 da mesma linha lida inteira."""
    segs = p.right
    if len(segs) == 1:
        return segs[0]
    partes: list[np.ndarray] = []
    for i, sg in enumerate(segs):
        if i:   # o vão entre os segmentos, na escala da máscara
            esc = sg.mask.shape[1] / max(1, sg.w)
            partes.append(np.zeros((SEG_H, max(0, round((sg.x0 - segs[i - 1].x1 - 1) * esc))), np.uint8))
        partes.append(sg.mask)
    return Segment(segs[0].x0, segs[-1].x1, np.hstack(partes))


def similaridade_tolerante(a: Segment, b: Segment) -> float:
    """Correlação das máscaras depois de suavizar e tolerando pequeno deslocamento. Medido (17/10/2026,
    3 casos reais de 1080p/1440p): a comparação seca dava 0,44-0,46 para a MESMA linha; esta dá 0,79-0,85,
    e nomes de partidas diferentes ficam em p95 = 0,17."""
    if abs(a.w - b.w) > max(6, 0.06 * max(a.w, b.w)):
        return 0.0
    w = max(a.mask.shape[1], b.mask.shape[1])
    ma = cv2.GaussianBlur(cv2.resize(a.mask, (w, SEG_H)).astype(np.float32), (0, 0), SUAVIZAR)
    mb = cv2.GaussianBlur(cv2.resize(b.mask, (w, SEG_H)).astype(np.float32), (0, 0), SUAVIZAR)
    u = (ma - ma.mean()).ravel()
    melhor = 0.0
    for dx in range(-DESLOCAR, DESLOCAR + 1):
        x = np.roll(mb, dx, axis=1)
        if dx > 0:
            x[:, :dx] = 0
        elif dx < 0:
            x[:, dx:] = 0
        v = (x - x.mean()).ravel()
        d = np.linalg.norm(u) * np.linalg.norm(v)
        if d > 0:
            melhor = max(melhor, float(u @ v / d))
    return melhor


def lado_direito_parecido(a: Parsed, b: Parsed) -> bool:
    """Mesma vítima e mesma arma.

    Vítima = o lado direito inteiro (tag do clã + nome), comparado com `similaridade_tolerante` (> SIM_VITIMA).
    Arma = largura do maior ícone branco entre os nomes, tolerância TOL_ARMA px. Não usa o lado esquerdo
    porque nele o nome do jogador é igual em todas as caixas.
    """
    if a.weapon is None or b.weapon is None:
        return False
    wa = a.weapon[1] - a.weapon[0]
    wb = b.weapon[1] - b.weapon[0]
    if abs(wa - wb) > TOL_ARMA:
        return False
    return similaridade_tolerante(_vitima(a), _vitima(b)) > SIM_VITIMA


Posicao = tuple[float, float, float]   # (fim, y, largura) da trilha na tela: o que a trilha tem além de (t, classe, parsed)


def _mesma_posicao_ao_mesmo_tempo(ti: float, pi: Posicao, tj: float, pj: Posicao) -> bool:
    """Duas leituras que NASCERAM juntas (≤ NASCER_JUNTAS_S), na MESMA linha da tela, existiram juntas (≥ SOBREPOR_MIN_S) e uma delas é
    um pedaço curto (≤ PEDACO_MAX_S) são a mesma linha: o flash de entrada lido como trilha à parte.
    Caso real (13/11/2026, VOD 1234567890, 50m45s): a 1ª leitura pegou o flash de entrada (máscara do nome toda preenchida,
    similaridade 0,30 contra a leitura limpa) e a regra do nome não juntou: 1 abate virou 2 e o DOUBLE virou TRIPLE KILL.
    Por que tantas condições (revisão de 17/11): o y guardado é o de ENTRADA da trilha. Quando a linha de cima expira, um abate
    sobe e o seguinte ENTRA no mesmo y; os dois existem juntos (mesmo VOD, 78m13s: "→ Lingard_V" e "→ Lenuevo", 2,2 s um do outro).
    Só a posição juntava esses dois abates reais; exigir nascer junto e ser pedaço curto os separa."""
    sobra = min(pi[0], pj[0]) - max(ti, tj)
    pedaco = min(pi[0] - ti, pj[0] - tj)
    return (abs(ti - tj) <= NASCER_JUNTAS_S and pedaco <= PEDACO_MAX_S and sobra >= SOBREPOR_MIN_S
            and abs(pi[1] - pj[1]) <= MESMA_LINHA_PX and abs(pi[2] - pj[2]) <= 30)


def agrupar_duplicatas(itens: Sequence[tuple[float, str, Parsed | None]],
                       janela: float = JANELA_DUP,
                       posicoes: Sequence[Posicao | None] | None = None) -> list[list[int]]:
    """`itens[i] = (t, classe, parsed)`. Devolve os grupos (listas de índices, ordenados por
    instante) com 2 ou mais trilhas da mesma linha. Trilhas sem leitura (`parsed` None) ou
    de outra classe que não kill/assist nunca entram em grupo.
    `posicoes[i]` (opcional) = (fim, y, largura) da trilha: com ela, leituras simultâneas na mesma linha da tela
    também são juntas, mesmo que o nome da vítima não bata (flash de entrada, quadro borrado)."""
    ordem = sorted((i for i, (_, c, p) in enumerate(itens) if p is not None and c in CLASSES),
                   key=lambda i: itens[i][0])
    pai = {i: i for i in ordem}

    def raiz(x: int) -> int:
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x

    for pos, i in enumerate(ordem):
        for j in ordem[pos + 1:]:
            if itens[j][0] - itens[i][0] > janela:
                break
            pi, pj = itens[i][2], itens[j][2]      # (as duas existem: `ordem` só tem trilhas com leitura)
            if itens[i][1] != itens[j][1] or pi is None or pj is None:
                continue
            if lado_direito_parecido(pi, pj):
                pai[raiz(j)] = raiz(i)
            elif (posicoes is not None and posicoes[i] is not None and posicoes[j] is not None
                  and pi.weapon is not None and pj.weapon is not None
                  and abs((pi.weapon[1] - pi.weapon[0]) - (pj.weapon[1] - pj.weapon[0])) <= TOL_ARMA
                  and _mesma_posicao_ao_mesmo_tempo(itens[i][0], posicoes[i], itens[j][0], posicoes[j])):   # type: ignore[arg-type]
                pai[raiz(j)] = raiz(i)
    grupos: dict[int, list[int]] = {}
    for i in ordem:
        grupos.setdefault(raiz(i), []).append(i)
    return [sorted(g, key=lambda i: itens[i][0]) for g in grupos.values() if len(g) > 1]


def resolver_duplicatas(itens: Sequence[tuple[float, str, Parsed | None]], hits: Sequence[int],
                        janela: float = JANELA_DUP,
                        posicoes: Sequence[Posicao | None] | None = None) -> tuple[dict[int, float], set[int]]:
    """Decide quem fica em cada grupo de duplicatas.

    Fica a trilha com mais acertos (recorte mais firme; empate: a mais cedo) e o evento passa a
    valer o instante MAIS CEDO do grupo, que é quando a linha realmente apareceu.
    Devolve (`{índice que fica: instante do evento}`, índices descartados)."""
    instante: dict[int, float] = {}
    descartados: set[int] = set()
    for g in agrupar_duplicatas(itens, janela, posicoes):
        fica = max(g, key=lambda i: (hits[i], -itens[i][0]))
        instante[fica] = min(itens[i][0] for i in g)
        descartados.update(i for i in g if i != fica)
    return instante, descartados
