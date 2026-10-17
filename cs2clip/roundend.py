"""Detecta o banner nativo de fim de round do CS2 (DERROTA/VITÓRIA; também em inglês e espanhol, ver `_WORDS`).

Achado (lote P4 + verificação manual em 11/10/2026): o HUD/overlay do streamer
muda de sessão para sessão (round-score custom, barra de economia, etc.), mas o
banner de fim de round é do **próprio jogo**, sempre igual: texto grande
("» DERROTA «" / "» VITÓRIA «", em maiúsculas, com guilhemets) sobre uma faixa
colorida (vermelha na derrota) que tinge o centro da tela. Isso dá um sinal de
"a jogador perdeu/ganhou o round" sem depender da overlay do streamer nem de
demo — é a base para separar RAIVA (round perdido) de ALEGRIA (round ganho)
sem demo, proposto no ADR relacionado a R-A7/R-A7b.

Duas etapas, como no detector de kill feed (`detect.py`): um pré-filtro barato
(tingimento vermelho/dourado na faixa central OU letras/borda vermelhas ou douradas na caixa do banner,
que é como ele aparece em algumas lives) e, só quando ele dispara, OCR
(`rapidocr`, já é dependência do projeto) para confirmar a palavra.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import numpy as np

# Região do banner, em fração do quadro (medida em 1920x1080 e 960x540; ~mesma
# posição em qualquer resolução pois o CS2 sempre centraliza). As funções daqui recebem o quadro JÁ recortado
# na área do jogo (`layout.area_cached`: sem as barras pretas), então todas as frações são da área do jogo.
BANNER_REL = (0.30, 0.14, 0.40, 0.14)  # x, y, largura, altura

# Texto do banner por idioma do jogo (chaves "WinPanel_RoundWon"/"WinPanel_RoundLost" dos arquivos de idioma do
# próprio CS2, resource/csgo_<idioma>.txt, conferidos em 17/10/2026). Já normalizado: sem acento nem espaço.
_WORDS = {
    "DERROTA": "derrota",       # pt-BR "DERROTA"
    "VITORIA": "vitoria",       # pt-BR "VITÓRIA"
    "ROUNDLOST": "derrota",     # inglês "ROUND LOST"
    "ROUNDWON": "vitoria",      # inglês "ROUND WON"
    "RONDAPERDIDA": "derrota",  # espanhol (Espanha e América Latina) e pt-PT "RONDA PERDIDA"
    "RONDAGANADA": "vitoria",   # espanhol "RONDA GANADA"
    "RONDAVENCIDA": "vitoria",  # pt-PT "RONDA VENCIDA"
}


def _crop_rel(frame: np.ndarray, rel: tuple[float, float, float, float]) -> np.ndarray:
    h, w = frame.shape[:2]
    x, y, rw, rh = rel
    x0, y0 = int(x * w), int(y * h)
    x1, y1 = int((x + rw) * w), int((y + rh) * h)
    return frame[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]


def _tint_score(frame_bgr: np.ndarray) -> float:
    """0-1: o quanto a faixa central está tingida (vermelho ou dourado), sinal
    barato de que um banner de fim de round pode estar na tela."""
    band = _crop_rel(frame_bgr, (0.0, 0.10, 1.0, 0.30)).astype(np.float32)
    if band.size == 0:
        return 0.0
    b, g, r = band[..., 0], band[..., 1], band[..., 2]
    red_tint = np.clip((r - np.maximum(g, b)) / 255.0, 0, 1).mean()
    gold_tint = np.clip((np.minimum(r, g) - b) / 255.0, 0, 1).mean()
    return float(max(red_tint, gold_tint))


COR_MIN = 0.004   # fração mínima de pixels vermelho-forte ou dourado na caixa do banner (medida em 17/10/2026)


def _cor_banner(frame_bgr: np.ndarray) -> float:
    """0-1: fração de pixels vermelho forte ou dourado DENTRO da caixa do banner (letras e borda).
    Medido em 17/10/2026: em algumas lives o banner vem como caixa escura com letras vermelhas, sem tingir a
    tela; o `_tint_score` ficava em ~0,024 (abaixo do limiar) e o round se perdia. Esta medida dava 0,033."""
    import cv2
    crop = _crop_rel(frame_bgr, BANNER_REL)
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    vermelho = ((h <= 6) | (h >= 174)) & (s >= 150) & (v >= 140)
    dourado = (h >= 16) & (h <= 32) & (s >= 120) & (v >= 160)
    return float(max(vermelho.mean(), dourado.mean()))


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Z]", "", s.upper())


@dataclass
class RoundEnd:
    resultado: str  # "derrota" | "vitoria"
    confianca: float  # score do OCR (0-1)
    texto_bruto: str


def detect_round_end(frame_bgr: np.ndarray, ocr, tint_threshold: float = 0.08) -> RoundEnd | None:
    """`ocr` é uma instância de `rapidocr_onnxruntime.RapidOCR()` (o chamador
    cria 1 vez e reaproveita — carregar o modelo é caro)."""
    # pré-filtro: tela tingida (banner clássico) OU letras/borda coloridas na caixa (banner escuro)
    if _tint_score(frame_bgr) < tint_threshold and _cor_banner(frame_bgr) < COR_MIN:
        return None
    crop = _crop_rel(frame_bgr, BANNER_REL)
    if crop.size == 0:
        return None
    result, _ = ocr(crop, use_cls=False)  # texto sempre na horizontal: pula o classificador de ângulo
    if not result:
        return None
    for _, text, score in result:
        norm = _normalize(text)
        for word, resultado in _WORDS.items():
            if word in norm:
                return RoundEnd(resultado, float(score), text)
    # banner de 2 palavras ("ROUND WON") que o OCR leu em 2 pedaços
    junto = "".join(_normalize(text) for _, text, _ in result)
    for word, resultado in _WORDS.items():
        if word in junto:
            return RoundEnd(resultado, float(min(score for _, _, score in result)),
                            " ".join(text for _, text, _ in result))
    return None


# ── a própria morte ─────────────────────────────────────────────────────────────────────────────
# Quando o jogador morre, o CS2 mostra no centro da tela um painel "<quem> matou você com: <arma>" com o texto
# "matou você com" em vermelho (achado em 17/10/2026, live 2902813681). É o único jeito de saber a morte só pelo
# vídeo; serve para terminar a jogada do round na morte, para a trilha de FAILS e para o contexto da raiva.
PAINEL_MORTE_REL = (0.28, 0.48, 0.44, 0.13)   # x, y, largura, altura (fração da área do jogo, medida em 16:9)
VERMELHO_PAINEL_MIN = 0.002   # o texto vermelho é pequeno: mediana 0,007 no painel; mais alto perde mortes
LARGURAS_PAINEL = (732,)
# Pedaços do texto do painel por idioma (chaves "Panorama_DeathPanel_Killer1_*" dos arquivos de idioma do CS2,
# conferidos em 17/10/2026), já normalizados.
CHAVES_MORTE = (
    "VOCECOM", "TOUVOCE",           # pt-BR "matou você com"
    "KILLEDYOU",                    # inglês "killed you with"
    "MATOUTECOM",                   # pt-PT "matou-te com"
    "TEHAMATADO", "TEHANMATADO",    # espanhol "te ha matado con" / "te han matado con"
    "TEMATOCON",                    # espanhol da América Latina "te mató con"
)


def _vermelho_painel(frame_bgr: np.ndarray) -> float:
    import cv2
    crop = _crop_rel(frame_bgr, PAINEL_MORTE_REL)
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return float((((h <= 8) | (h >= 172)) & (s >= 130) & (v >= 120)).mean())


def _linha_vermelha(frame_bgr: np.ndarray) -> bool:
    """O vermelho do painel forma UMA linha de texto ("matou você com"): concentrado numa faixa de 8 linhas da
    imagem e com largura de texto (medido só entre o avatar e a arma: um avatar vermelho espalhava a medida e
    perdia 2 de 13 mortes reais). Medido em 17/10/2026: no painel, concentração ~1,0 e ~104 px de largura (num
    quadro 1280); passa só 0,34% dos quadros sem morte, contra 6% do filtro só de cor (o OCR é o custo)."""
    import cv2
    crop = _crop_rel(frame_bgr, PAINEL_MORTE_REL)
    if crop.size == 0:
        return False
    escala = 563 / crop.shape[1]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = ((h <= 8) | (h >= 172)) & (s >= 130) & (v >= 120)
    # só a área do texto: à direita do avatar de quem matou (que pode ser vermelho) e à esquerda da arma
    largura_crop = m.shape[1]
    m[:, : int(0.19 * largura_crop)] = False
    m[:, int(0.70 * largura_crop):] = False
    total = int(m.sum())
    if total * escala * escala < 30:
        return False
    k = max(3, round(8 / escala))
    janela = np.convolve(m.sum(1), np.ones(k, int), "valid")
    j = int(janela.argmax())
    colunas = np.where(m[j:j + k].any(0))[0]
    largura = (colunas.max() - colunas.min()) * escala if len(colunas) else 0
    return janela.max() / total >= 0.6 and janela.max() * escala * escala >= 60 and largura >= 60


@dataclass
class MorteJogador:
    confianca: float
    texto_bruto: str


def detect_own_death(frame_bgr: np.ndarray, ocr) -> MorteJogador | None:
    """O painel "... matou você com ..." está na tela? (pré-filtro de cor + OCR do painel)"""
    if _vermelho_painel(frame_bgr) < VERMELHO_PAINEL_MIN or not _linha_vermelha(frame_bgr):
        return None
    import cv2
    crop = _crop_rel(frame_bgr, PAINEL_MORTE_REL)
    # O OCR é instável com o tamanho deste texto pequeno (medido: 900 px de largura falha, 901 acerta). Ampliado
    # para 732 px ele leu 8 de 8 mortes reais (563 px: 7 de 8); uma largura só custa 1/3 de tentar três.
    for largura in LARGURAS_PAINEL:
        escala = largura / crop.shape[1]
        img = crop if abs(escala - 1) < 0.02 else cv2.resize(crop, None, fx=escala, fy=escala,
                                                              interpolation=cv2.INTER_CUBIC)
        result, _ = ocr(img, use_cls=False)
        for _, text, score in result or []:
            norm = _normalize(text)
            if any(chave in norm for chave in CHAVES_MORTE):
                return MorteJogador(float(score), text)
    return None
