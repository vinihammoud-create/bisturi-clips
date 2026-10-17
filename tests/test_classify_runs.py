"""Testes das funções puras do parser do kill feed (cs2clip/classify.py), sem vídeo:
`_runs` (agrupa índices próximos), moldura da caixa (`_maior_vao`, `_aparar`, `_miolo`),
limpeza dos segmentos (`_sem_pingos`, `_faixa_texto`, `_juntar_palavras`), casamento de nome
cortado (`_segment_matches`), desempate de `learn_player` e `parse_box` numa caixa sintética."""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from cs2clip.classify import (  # noqa: E402
    APARA_MAX, SEG_H, Parsed, Segment, _aparar, _faixa_texto, _juntar_palavras, _maior_vao,
    _miolo, _runs, _segment_matches, _sem_pingos, learn_player, parse_box,
)

# Cores BGR com matiz medido nos recortes reais: vermelho da borda (H 0), amarelo TR (H ~23),
# azul CT (H ~104); fundo escuro da caixa (V < 60, fora de todas as máscaras); cena clara
# arenosa (H ~19, S > 90: parece "colorido", como um mapa claro atrás da caixa).
VERMELHO = (30, 30, 220)
AMARELO = (30, 190, 235)
AZUL = (230, 150, 60)
BRANCO = (255, 255, 255)
FUNDO = (20, 20, 25)
AREIA = (120, 170, 200)


# ── _runs ──────────────────────────────────────────────────────────────────────────────

def test_vazio():
    assert _runs(np.array([False, False, False]), gap=2) == []


def test_um_intervalo_continuo():
    a = np.array([False, True, True, True, False])
    assert _runs(a, gap=2) == [[1, 3]]


def test_junta_intervalos_proximos_dentro_do_gap():
    a = np.zeros(10, dtype=bool)
    a[1:3] = True   # [1,2]
    a[4:6] = True   # [4,5]: começa a 2 de distância do fim do anterior (4-2=2<=gap)
    assert _runs(a, gap=2) == [[1, 5]]


def test_nao_junta_intervalos_longe_do_gap():
    a = np.zeros(20, dtype=bool)
    a[1:3] = True
    a[15:17] = True
    assert _runs(a, gap=2) == [[1, 2], [15, 16]]


# ── moldura ────────────────────────────────────────────────────────────────────────────

def test_maior_vao_escolhe_a_maior_sequencia_abaixo_do_limite():
    frac = np.array([1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 1], float)
    assert _maior_vao(frac, minimo=3) == (2, 6)
    assert _maior_vao(frac, minimo=6) is None


def test_maior_vao_respeita_o_limite():
    frac = np.array([0.6, 0.1, 0.1, 0.1, 0.6])
    assert _maior_vao(frac, minimo=1) == (1, 3)            # 0,6 > 0,5 é borda
    assert _maior_vao(frac, minimo=1, lim=0.8) == (0, 4)   # com 0,8 (colunas) não é


def test_aparar_tira_so_as_pontas_e_no_maximo_apara_max():
    assert _aparar(np.array([0.9, 0.7, 0.1, 0.0, 0.2, 0.8])) == (2, 4)
    assert _aparar(np.array([0.9] * 5 + [0.0] * 5)) == (APARA_MAX, 9)
    assert _aparar(np.array([0.0, 0.0, 0.0])) == (0, 2)


def _caixa(w: int = 240, h: int = 32, borda: int = 3) -> np.ndarray:
    """Caixa do kill feed sintética: fundo escuro, borda vermelha, 'Dollar' amarelo,
    arma branca (retângulo) e vítima 'Vads' azul."""
    img = np.full((h, w, 3), FUNDO, np.uint8)
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), VERMELHO, borda)
    cv2.putText(img, "Dollar", (12, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, AMARELO, 2)
    cv2.rectangle(img, (100, 11), (140, 21), BRANCO, -1)
    cv2.putText(img, "Vads", (160, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, AZUL, 2)
    return img


def _recorte_largo(caixa: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Como o detector entrega: cena arenosa à esquerda, borda de baixo da caixa de cima
    (faixa vermelha) no topo e a caixa deslocada para dentro. Devolve (recorte, dy, dx)."""
    h, w = caixa.shape[:2]
    dy, dx = 7, 60
    rec = np.full((h + dy + 2, w + dx, 3), AREIA, np.uint8)
    rec[:, dx:] = FUNDO
    rec[0:3, :] = VERMELHO
    rec[dy:dy + h, dx:dx + w] = caixa
    return rec, dy, dx


def test_miolo_da_caixa_exata_tira_so_a_borda():
    caixa = _caixa(borda=3)
    assert _miolo(caixa) == (3, caixa.shape[0] - 4, 3, caixa.shape[1] - 4)


def test_miolo_acha_a_caixa_dentro_do_recorte_largo():
    caixa = _caixa(borda=3)
    rec, dy, dx = _recorte_largo(caixa)
    y0, y1, x0, x1 = _miolo(rec)
    assert (y0, y1) == (dy + 3, dy + caixa.shape[0] - 4)
    assert (x0, x1) == (dx + 3, dx + caixa.shape[1] - 4)


def test_miolo_sem_borda_nenhuma_devolve_o_recorte_inteiro():
    img = np.full((30, 200, 3), FUNDO, np.uint8)
    assert _miolo(img) == (0, 29, 0, 199)


# ── limpeza dos segmentos ──────────────────────────────────────────────────────────────

def test_sem_pingos_tira_pingo_isolado_na_ponta_e_mantem_apostrofo():
    colsum = np.zeros(40, int)
    colsum[5:21] = 5          # texto
    colsum[29] = 2            # pingo: 1 coluna, 2 pixels, a 8 px do texto
    assert _sem_pingos([5, 29], colsum) == [5, 20]
    colsum[22:24] = 5         # apóstrofo: 2 colunas, 10 pixels -> fica (pingo a 5 px dele)
    assert _sem_pingos([5, 29], colsum) == [5, 23]
    colsum[29] = 0
    colsum[25] = 2            # borda de letra colada ao apóstrofo (gap 1) -> fica
    assert _sem_pingos([5, 25], colsum) == [5, 25]


def test_faixa_texto_ignora_pixels_soltos_longe_e_tolera_pingo_do_i():
    m = np.zeros((24, 10), np.uint8)
    m[0:2, 3] = 255           # resto de borda, 5 linhas vazias antes do texto
    m[7:18, :] = 255          # texto
    assert _faixa_texto(m) == [7, 17]
    m[5, 4] = 255             # pingo do i: 1 linha vazia entre ele e o texto (e 3 até o resto)
    assert _faixa_texto(m) == [5, 17]
    assert _faixa_texto(np.zeros((5, 5), np.uint8)) is None


def test_juntar_palavras_so_sem_branco_no_meio_e_vao_curto():
    sem_branco = np.zeros(200, bool)
    assert _juntar_palavras([[10, 50], [60, 120]], sem_branco) == [[10, 120]]
    com_mais = sem_branco.copy()
    com_mais[54:57] = True    # "+" entre dois nomes
    assert _juntar_palavras([[10, 50], [60, 120]], com_mais) == [[10, 50], [60, 120]]
    assert _juntar_palavras([[10, 50], [71, 120]], sem_branco) == [[10, 50], [71, 120]]  # vão 20


# ── casamento do nome ──────────────────────────────────────────────────────────────────

def _mascara(w: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.random((SEG_H, w)) > 0.5).astype(np.uint8) * 255


def test_nome_cortado_casa_com_o_fim_do_nome_do_jogador():
    player = Segment(0, 119, _mascara(180, 0))
    n = round(0.6 * 180)
    pedaco = Segment(0, 71, player.mask[:, -n:], cortado=True)     # 60% finais, cortado
    assert _segment_matches(pedaco, player)
    assert not _segment_matches(Segment(0, 71, pedaco.mask), player)           # não cortado
    assert not _segment_matches(Segment(0, 71, _mascara(n, 1), cortado=True), player)  # outro nome
    curto = Segment(0, 35, player.mask[:, -54:], cortado=True)      # 30%: pouco para afirmar
    assert not _segment_matches(curto, player)


def _parsed(left: list[Segment], right: list[Segment]) -> Parsed:
    return Parsed(left, right, 0, (150, 180), np.zeros((1, 1, 3), np.uint8))


def test_learn_player_desempata_pelo_lado_esquerdo():
    """VOD curto: a vítima 'Vads' aparece em tantas caixas quanto o jogador (2 como matador
    com o jogador assistindo, 2 como vítima do jogador). O jogador é quem está à esquerda."""
    jog = _mascara(180, 10)
    vads = _mascara(80, 11)
    caixas = [_parsed([Segment(14, 65, vads), Segment(104, 223, jog)], [Segment(330, 500, _mascara(170, 12))])
              for _ in range(2)]
    caixas += [_parsed([Segment(13, 132, jog)], [Segment(276, 328, vads)]) for _ in range(2)]
    modelo = learn_player(caixas, None)
    assert modelo.matches(caixas[2].left[0]) and not modelo.matches(caixas[0].left[0])


# ── parse_box em caixa sintética ───────────────────────────────────────────────────────

def test_parse_box_caixa_exata_e_recorte_largo_dao_os_mesmos_segmentos():
    caixa = _caixa()
    p = parse_box(caixa)
    assert p is not None and len(p.left) == 1 and len(p.right) == 1
    assert p.weapon is not None and p.weapon[1] - p.weapon[0] >= 12
    rec, dy, dx = _recorte_largo(caixa)
    q = parse_box(rec)
    assert q is not None and len(q.left) == 1 and len(q.right) == 1
    assert (q.left[0].x0, q.left[0].x1) == (p.left[0].x0, p.left[0].x1)
    assert (q.right[0].x0, q.right[0].x1) == (p.right[0].x0, p.right[0].x1)
    assert not p.left[0].cortado and not q.left[0].cortado
    modelo = learn_player([p, q], None)
    assert modelo.matches(q.left[0])


def test_parse_box_marca_nome_cortado_pela_beirada():
    caixa = _caixa()
    cortada = caixa[:, 30:]   # recorte começa no meio de "Dollar", sem a borda esquerda
    p = parse_box(cortada)
    assert p is not None and p.left[0].cortado
