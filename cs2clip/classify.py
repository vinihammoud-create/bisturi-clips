"""Classifica cada caixa vermelha do kill feed em abate / assistência / morte.

Estrutura de uma linha do kill feed do CS2:
    [matador] (+ [ícone flash] [assistente]) [ARMA] [ícones extras] [tag] [vítima]
Nomes são texto colorido (cor do time); arma e ícones são brancos.
O nome do jogador aparece em TODAS as caixas vermelhas, então é aprendido
automaticamente como o segmento colorido mais recorrente.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .tools import imread, imwrite

SEG_H = 16  # altura normalizada da máscara do nome

# Matizes (OpenCV, 0-179) medidos nos recortes reais dos 4 VODs (histograma dos pixels com
# S > 90 e V > 110 nas linhas de texto): borda vermelha 0-7 e 172-179, com o vale em 6-10
# (em duas caixas a borda sai rosa, 164-175); nome TR amarelo com pico em ~21 (cauda a
# partir de ~9); nome CT azul com pico em ~108. Nenhuma cor de time fica em 160-179.
H_VERMELHO = (8, 160)   # matiz <= 8 ou >= 160: vermelho/rosa (borda; fora da máscara dos nomes)
# O antialias da borda com o fundo sai alaranjado (matiz 9-14, igual à cauda do amarelo TR) e
# não dá para separar por cor; separa-se por posição: fica colado na borda, e o texto nunca
# encosta nela (começa >= 6 px para dentro). Então só as APARA_MAX linhas/colunas de cada
# beirada podem ser aparadas por esse critério (fundo de caixa marrom, em mapa claro, também
# cai nessa faixa de matiz e não pode comer o texto).
H_BORDA = (14, 160)     # matiz <= 14 ou >= 160: vermelho + antialias alaranjado
APARA_MAX = 3           # px: máximo aparado de cada beirada por antialias
GAP_PALAVRA = 14        # px: espaço entre palavras de um mesmo nome ("Dollar The DOG")
X_CORTADO = 6           # px: nome que começa até aqui da beirada do recorte foi cortado
                        # (nome inteiro começa >= 7 px depois da borda; medido 7-14)
FRACAO_MIN_SUFIXO = 0.4  # um pedaço cortado só casa com o fim do nome se tiver >= 40% dele


def _runs(a: np.ndarray, gap: int) -> list[list[int]]:
    out: list[list[int]] = []
    for i in np.nonzero(a)[0]:
        if out and i - out[-1][1] <= gap:
            out[-1][1] = int(i)
        else:
            out.append([int(i), int(i)])
    return out


def _faixa_matiz(hsv: np.ndarray, faixa: tuple[int, int]) -> np.ndarray:
    H = hsv[..., 0]
    return (H <= faixa[0]) | (H >= faixa[1])


def _maior_vao(frac: np.ndarray, minimo: int, lim: float = 0.5) -> tuple[int, int] | None:
    """Maior sequência de índices com até `lim` de pixels de moldura (as pontas do vetor
    contam como borda: o recorte pode ter sido cortado sem a borda daquele lado)."""
    best = None
    for a, b in _runs(frac <= lim, gap=1):
        if b - a + 1 >= minimo and (best is None or b - a > best[1] - best[0]):
            best = (a, b)
    return best


def _miolo(crop_bgr: np.ndarray) -> tuple[int, int, int, int] | None:
    """Acha o interior da caixa dentro do recorte: (y0, y1, x0, x1), inclusivos.

    O recorte do detector não é a caixa exata: pode ter fundo de jogo à esquerda, a borda de
    baixo da caixa de cima (kill feed com várias linhas) ou a borda da própria caixa alguns
    pixels para dentro. Linhas com mais de 50% de pixels vermelhos são borda; colunas, mais
    de 80% (a borda lateral atravessa a altura toda: medido >= 0,88; uma coluna de fundo
    avermelhado sem texto chega a ~0,75). O interior é o maior vão entre elas.
    A moldura usa V > 90: o vermelho da borda tem V mediano 180-206 e o fundo avermelhado da
    caixa (semitransparente sobre cena quente), V mediano 70-87.
    """
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    moldura = _faixa_matiz(hsv, H_VERMELHO) & (hsv[..., 1] > 80) & (hsv[..., 2] > 90)
    h, w = moldura.shape
    cols = _maior_vao(moldura.mean(0), 40) or (0, w - 1)
    rows = _maior_vao(moldura[:, cols[0]:cols[1] + 1].mean(1), 8)
    if rows is None:
        return None
    cols = _maior_vao(moldura[rows[0]:rows[1] + 1].mean(0), 40, lim=0.8)
    if cols is None:
        return None
    borda = _faixa_matiz(hsv, H_BORDA) & (hsv[..., 1] > 80) & (hsv[..., 2] > 60)
    y0, y1 = _aparar(borda[rows[0]:rows[1] + 1, cols[0]:cols[1] + 1].mean(1))
    x0, x1 = _aparar(borda[rows[0] + y0:rows[0] + y1 + 1, cols[0]:cols[1] + 1].mean(0))
    return rows[0] + y0, rows[0] + y1, cols[0] + x0, cols[0] + x1


def _aparar(frac: np.ndarray) -> tuple[int, int]:
    """Índices (inclusivos) que sobram depois de tirar, de cada ponta, até APARA_MAX
    linhas/colunas com mais de 50% de pixels de borda (antialias)."""
    a, b = 0, len(frac) - 1
    while a < b and a < APARA_MAX and frac[a] > 0.5:
        a += 1
    while b > a and len(frac) - 1 - b < APARA_MAX and frac[b] > 0.5:
        b -= 1
    return a, b


def _pingo(peca: list[int], vizinha: list[int], colsum: np.ndarray) -> bool:
    """Pedaço de <= 2 colunas e <= 3 pixels, a >= 4 px do resto: antialias de ícone/borda que
    o gap de 10 px gruda no nome. Um glifo pequeno de verdade (apóstrofo, ponto) tem >= 8
    pixels; bordas de letras com 1 coluna ficam coladas ao resto (gap 0-1)."""
    larg = peca[1] - peca[0] + 1
    dist = max(vizinha[0] - peca[1], peca[0] - vizinha[1]) - 1
    return larg <= 2 and int(colsum[peca[0]:peca[1] + 1].sum()) <= 3 and dist >= 4


def _sem_pingos(r: list[int], colsum: np.ndarray) -> list[int]:
    pecas = [[r[0] + a, r[0] + b] for a, b in _runs(colsum[r[0]:r[1] + 1] >= 2, gap=1)]
    while len(pecas) > 1 and _pingo(pecas[-1], pecas[-2], colsum):
        pecas.pop()
    while len(pecas) > 1 and _pingo(pecas[0], pecas[1], colsum):
        pecas.pop(0)
    return [pecas[0][0], pecas[-1][1]] if pecas else list(r)


def _faixa_texto(mask: np.ndarray) -> list[int] | None:
    """Linhas [y0, y1] do texto: a maior sequência de linhas com pixel, tolerando até 2
    linhas vazias no meio (pingo do i). Pixels soltos longe do texto (resto de borda,
    artefato de compressão) ficariam na máscara e achatariam o nome."""
    runs = _runs(mask.any(1), gap=3)
    return max(runs, key=lambda r: r[1] - r[0]) if runs else None


def _juntar_palavras(runs: list[list[int]], white_cols: np.ndarray) -> list[list[int]]:
    """Um nome pode ter espaços maiores que o gap dos runs ("Dollar" + "The DOG"). Entre dois
    NOMES diferentes sempre há um glifo branco ("+", ícone de flash, arma); entre palavras do
    mesmo nome, só fundo. Junta runs vizinhos com vão <= GAP_PALAVRA e nada branco no meio."""
    out: list[list[int]] = []
    for r in runs:
        if out and r[0] - out[-1][1] - 1 <= GAP_PALAVRA and not white_cols[out[-1][1] + 1:r[0]].any():
            out[-1][1] = r[1]
        else:
            out.append(list(r))
    return out


@dataclass
class Segment:
    x0: int
    x1: int
    mask: np.ndarray = field(repr=False)  # máscara binária normalizada do texto
    cortado: bool = False                 # começa na beirada do recorte: o início do nome ficou de fora

    @property
    def w(self) -> int:
        return self.x1 - self.x0 + 1


@dataclass
class Parsed:
    left: list[Segment]      # matador (+ assistente)
    right: list[Segment]     # vítima
    icon_w: int              # largura da zona de ícones (arma + headshot/wallbang...)
    weapon: tuple[int, int] | None
    inner: np.ndarray = field(repr=False)


def parse_box(crop_bgr: np.ndarray) -> Parsed | None:
    m = _miolo(crop_bgr)
    if m is None:
        return None
    inner = crop_bgr[m[0]:m[1] + 1, m[2]:m[3] + 1]
    x_crop = m[2]  # coluna do recorte onde o miolo começa (para saber se um nome foi cortado)
    if inner.shape[0] < 8 or inner.shape[1] < 40:
        return None
    hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
    S, V = hsv[..., 1], hsv[..., 2]
    # nomes são azul CT ou amarelo TR: o vermelho (resto de borda) fica fora da máscara
    colored = (S > 90) & (V > 110) & ~_faixa_matiz(hsv, H_VERMELHO)
    white = (S < 70) & (V > 170)
    colsum = colored.sum(0)
    col_runs = [_sem_pingos(r, colsum) for r in _runs(colsum >= 2, gap=10) if r[1] - r[0] >= 6]
    col_runs = _juntar_palavras(col_runs, white.sum(0) >= 2)
    # linhas brancas finas (reflexo da borda) não são ícones: exige altura mínima
    tall = white.sum(0) >= 3
    wht_runs = _runs(tall, gap=4)
    if len(col_runs) < 2 or not wht_runs:
        return None
    # Arma = maior ícone branco ENTRE nomes (granada/faca/zeus são estreitos: mínimo 12 px)
    cands = [r for r in wht_runs if r[1] - r[0] >= 12
             and any(c[1] < r[0] for c in col_runs) and any(c[0] > r[1] for c in col_runs)]
    if not cands:
        return None
    weapon = max(cands, key=lambda r: (r[1] - r[0], r[0]))

    def seg(r: list[int]) -> Segment:
        m = colored[:, r[0]:r[1] + 1].astype(np.uint8) * 255
        ys = _faixa_texto(m)
        m = m[ys[0]:ys[1] + 1] if ys else m
        m = cv2.resize(m, (max(1, round(m.shape[1] * SEG_H / max(1, m.shape[0]))), SEG_H),
                       interpolation=cv2.INTER_AREA)
        return Segment(r[0], r[1], m, cortado=x_crop + r[0] <= X_CORTADO)

    left = [seg(r) for r in col_runs if r[1] < weapon[0]]
    right = [seg(r) for r in col_runs if r[0] > weapon[1]]
    if not left or not right:
        return None
    zone0 = left[-1].x1 + 1
    zone1 = right[0].x0 - 1
    icon_w = int((white[:, zone0:zone1].sum(0) >= 2).sum()) if zone1 > zone0 else 0
    return Parsed(left, right, icon_w, (weapon[0], weapon[1]), inner)


def seg_similarity(a: Segment, b: Segment) -> float:
    if abs(a.w - b.w) > max(6, 0.06 * max(a.w, b.w)):
        return 0.0
    w = max(a.mask.shape[1], b.mask.shape[1])
    ma = cv2.resize(a.mask, (w, SEG_H)).astype(np.float32).ravel()
    mb = cv2.resize(b.mask, (w, SEG_H)).astype(np.float32).ravel()
    ma -= ma.mean()
    mb -= mb.mean()
    d = np.linalg.norm(ma) * np.linalg.norm(mb)
    return float(ma @ mb / d) if d > 0 else 0.0


def _segment_matches(s: Segment, player: Segment) -> bool:
    """O segmento da vítima pode incluir a tag do clã antes do nome: testa o final também.
    Um nome cortado pela beirada do recorte ("llar The DOG") é comparado com o FIM do nome
    do jogador, se sobrou pelo menos FRACAO_MIN_SUFIXO dele."""
    if seg_similarity(s, player) > 0.6:
        return True
    if s.w > player.w + 10:
        tail = Segment(s.x1 - player.w + 1, s.x1, s.mask[:, -player.mask.shape[1]:])
        return seg_similarity(tail, player) > 0.6
    if s.cortado and s.w < player.w and s.w >= FRACAO_MIN_SUFIXO * player.w:
        n = round(s.w * player.mask.shape[1] / player.w)
        fim = Segment(0, s.w - 1, player.mask[:, -n:])
        return seg_similarity(s, fim) > 0.6
    return False


class PlayerModel:
    """Nome do jogador representado por vários exemplares (cores de time CT/TR, nitidez)."""

    def __init__(self, exemplars: list[Segment]):
        self.exemplars = exemplars[:40]

    def matches(self, s: Segment) -> bool:
        return any(_segment_matches(s, e) for e in self.exemplars)

    # Persistência: as máscaras viram PNGs numa pasta (permite reaproveitar entre vídeos)
    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        for f in folder.glob("*.png"):
            f.unlink()
        for i, e in enumerate(self.exemplars):
            imwrite(folder / f"{i:02d}_{e.w}.png", e.mask)

    @classmethod
    def load(cls, folder: Path) -> "PlayerModel | None":
        ex = []
        for f in sorted(folder.glob("*.png")):
            if not re.fullmatch(r"\d+_\d+", f.stem):
                print(f"    aviso: {f.name} ignorado (nome fora do padrão NN_largura.png)")
                continue
            w = int(f.stem.split("_")[1])
            m = imread(f, cv2.IMREAD_GRAYSCALE)
            if w < 1 or m is None or m.ndim != 2 or m.shape[1] < 1:
                print(f"    aviso: {f.name} ignorado (PNG inválido)")
                continue
            ex.append(Segment(0, w - 1, m))
        return cls(ex) if ex else None


def learn_player(parsed: list[Parsed], prior: PlayerModel | None = None) -> PlayerModel | None:
    """O nome do jogador é o segmento presente no maior número de caixas vermelhas."""
    if not parsed:
        return prior
    if prior is not None:
        # modelo salvo de outro streamer? (nome não aparece na maioria das caixas) -> descarta
        cover = sum(1 for p in parsed if any(prior.matches(s) for s in p.left + p.right)) / len(parsed)
        if cover < 0.5 and len(parsed) >= 4:
            prior = None
    cands = [s for p in parsed for s in (p.left + p.right)]
    if prior is not None:
        cands = [s for s in cands if prior.matches(s)] or cands
    # Desempate (VOD curto, mesma vítima várias vezes): nas caixas vermelhas o jogador é quem
    # está à ESQUERDA (matador/assistente); por último, o segmento mais largo (nome completo).
    best, best_key = None, (0, 0, 0)
    for c in cands:
        n = sum(1 for p in parsed if any(_segment_matches(s, c) for s in p.left + p.right))
        n_left = sum(1 for p in parsed if any(_segment_matches(s, c) for s in p.left))
        if (n, n_left, c.w) > best_key:
            best, best_key = c, (n, n_left, c.w)
    if best is None:
        return prior
    # Exemplares: segmentos de nome (lado esquerdo ou vítima) parecidos com o melhor candidato
    ex = [best] + [s for p in parsed for s in p.left + p.right
                   if s is not best and seg_similarity(s, best) > 0.6]
    if prior is not None:
        ex += prior.exemplars
    return PlayerModel(ex)


def classify(p: Parsed, player: PlayerModel) -> str:
    if player.matches(p.left[0]):
        return "kill"
    if any(player.matches(s) for s in p.left[1:]):
        return "assist"
    if any(player.matches(s) for s in p.right):
        return "death"
    return "unknown"
