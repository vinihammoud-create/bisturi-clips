"""Testes de cs2clip/dedup.py: juntar as trilhas que são a MESMA linha do kill feed.

Duas partes: funções puras com leituras falsas (sem imagem) e o gabarito real
(tests/fixtures/duplicatas/: 76 recortes de 31 grupos suspeitos de 4 VODs e 74 pares rotulados
por 2 olhadores independentes, E1 de 17/10/2026)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")

from cs2clip.classify import Parsed, Segment, parse_box  # noqa: E402
from cs2clip.dedup import agrupar_duplicatas, resolver_duplicatas  # noqa: E402
from cs2clip.tools import imread  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "duplicatas"


# ── funções puras ─────────────────────────────────────────────────────────────────────

def _mascara(semente: int, w: int = 40) -> np.ndarray:
    return (np.random.default_rng(semente).integers(0, 2, (16, w)) * 255).astype(np.uint8)


def _parsed(vitima: int, arma_w: int = 50) -> Parsed:
    seg_v = Segment(0, 39, _mascara(vitima))
    seg_e = Segment(0, 39, _mascara(999))
    return Parsed([seg_e], [seg_v], 0, (100, 100 + arma_w), np.zeros((10, 10, 3), np.uint8))


def test_mesma_vitima_e_arma_proximas_viram_um_grupo():
    itens = [(10.0, "kill", _parsed(1)), (10.4, "kill", _parsed(1))]
    assert agrupar_duplicatas(itens) == [[0, 1]]


def test_classe_diferente_nao_junta():
    # abate e assistência com a mesma vítima são linhas diferentes (eram os 5 falsos da regra antiga)
    itens = [(10.0, "kill", _parsed(1)), (10.4, "assist", _parsed(1))]
    assert agrupar_duplicatas(itens) == []


def test_longe_demais_nao_junta():
    itens = [(10.0, "kill", _parsed(1)), (19.0, "kill", _parsed(1))]
    assert agrupar_duplicatas(itens) == []
    assert agrupar_duplicatas(itens, janela=10.0) == [[0, 1]]


def test_arma_ou_vitima_diferente_nao_junta():
    assert agrupar_duplicatas([(10.0, "kill", _parsed(1, 50)), (10.4, "kill", _parsed(1, 80))]) == []
    assert agrupar_duplicatas([(10.0, "kill", _parsed(1)), (10.4, "kill", _parsed(2))]) == []


def test_sem_leitura_ou_classe_desconhecida_nunca_junta():
    assert agrupar_duplicatas([(10.0, "kill", None), (10.4, "kill", None)]) == []
    assert agrupar_duplicatas([(10.0, "unknown", _parsed(1)), (10.4, "unknown", _parsed(1))]) == []


def test_cadeia_de_tres_trilhas_vira_um_grupo():
    itens = [(10.0, "kill", _parsed(1)), (14.0, "kill", _parsed(1)), (18.0, "kill", _parsed(1))]
    assert agrupar_duplicatas(itens) == [[0, 1, 2]]


def test_resolver_fica_com_mais_acertos_e_o_instante_mais_cedo():
    itens = [(10.4, "kill", _parsed(1)), (10.0, "kill", _parsed(1)), (50.0, "kill", _parsed(2))]
    instante, descartados = resolver_duplicatas(itens, hits=[40, 3, 12])
    assert instante == {0: 10.0}      # a trilha firme (40 acertos) fica, valendo o instante mais cedo
    assert descartados == {1}         # a trilha de 3 acertos some; a do abate isolado nem entra


def test_resolver_empate_de_acertos_fica_com_a_mais_cedo():
    itens = [(10.0, "kill", _parsed(1)), (10.4, "kill", _parsed(1))]
    instante, descartados = resolver_duplicatas(itens, hits=[5, 5])
    assert instante == {0: 10.0} and descartados == {1}


# ── robustez a 1080p/1440p (E3b) ───────────────────────────────────────────────────────

def _texto(w: int = 100) -> np.ndarray:
    """Máscara tipo 'texto': faixas de largura variada, para a correlação não ser trivial."""
    m = np.zeros((16, w), np.uint8)
    rng = np.random.default_rng(7)
    x = 2
    while x < w - 6:
        larg = int(rng.integers(2, 6))
        m[2:14, x:x + larg] = 255
        x += larg + int(rng.integers(2, 5))
    return m


def test_vitima_partida_em_dois_segmentos_equivale_a_inteira():
    from cs2clip.dedup import _vitima
    inteira = _texto(100)
    a = Parsed([Segment(0, 39, _mascara(999))], [Segment(0, 99, inteira)], 0, (100, 150), np.zeros((10, 10, 3), np.uint8))
    # a mesma linha em quadro borrado: tag e nome viram dois segmentos, com um vão de 20 px no meio
    partes = [Segment(0, 39, inteira[:, :40]), Segment(60, 99, inteira[:, 60:])]
    b = Parsed([Segment(0, 39, _mascara(999))], partes, 0, (100, 150), np.zeros((10, 10, 3), np.uint8))
    assert _vitima(b).w == _vitima(a).w == 100 and _vitima(b).mask.shape == _vitima(a).mask.shape
    assert agrupar_duplicatas([(10.0, "kill", a), (13.0, "kill", b)]) == [[0, 1]]


def test_mesma_linha_borrada_e_deslocada_ainda_junta_mas_nome_diferente_nao():
    import cv2
    base = _texto(100)
    borrada = np.roll(cv2.GaussianBlur(base, (0, 0), 1.5), 2, axis=1)    # borrão + 2 px de deslocamento
    outra = _texto(100)[:, ::-1].copy()                                   # outro texto, mesma largura
    mk = lambda m: Parsed([Segment(0, 39, _mascara(999))], [Segment(0, 99, m)], 0, (100, 150),
                          np.zeros((10, 10, 3), np.uint8))
    assert agrupar_duplicatas([(10.0, "kill", mk(base)), (12.0, "kill", mk(borrada))]) == [[0, 1]]
    assert agrupar_duplicatas([(10.0, "kill", mk(base)), (12.0, "kill", mk(outra))]) == []


# ── gabarito real ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def gabarito():
    return json.loads((FIXTURES / "gabarito.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lidas(gabarito):
    """{(vod, t): (t, classe, Parsed|None)} lendo os recortes com o parse_box de hoje."""
    out = {}
    for tr in gabarito["trilhas"]:
        crop = imread(FIXTURES / tr["arquivo"])
        assert crop is not None, tr["arquivo"]
        out[(tr["vod"], round(tr["t"], 2))] = (tr["t"], tr["classe"], parse_box(crop))
    return out


def _grupos_por_vod(lidas):
    """{vod: {t: id do grupo}} agrupando todas as trilhas de cada VOD juntas, como o pipeline."""
    por_vod: dict[str, list[tuple[float, str, Parsed | None]]] = {}
    for (vod, _), item in lidas.items():
        por_vod.setdefault(vod, []).append(item)
    res = {}
    for vod, itens in por_vod.items():
        marca = {}
        for n, g in enumerate(agrupar_duplicatas(itens)):
            for i in g:
                marca[round(itens[i][0], 2)] = n
        res[vod] = marca
    return res


def test_gabarito_todas_as_trilhas_foram_lidas(lidas):
    sem_leitura = [k for k, (_, _, p) in lidas.items() if p is None]
    assert not sem_leitura, f"parse_box não leu: {sem_leitura}"


def test_gabarito_cada_par_rotulado_bate(gabarito, lidas):
    marca = _grupos_por_vod(lidas)
    erros = []
    for p in gabarito["pares"]:
        m = marca.get(p["vod"], {})
        g1, g2 = m.get(round(p["t1"], 2)), m.get(round(p["t2"], 2))
        junto = g1 is not None and g1 == g2
        if junto != p["mesma"]:
            erros.append((p["vod"], p["t1"], p["t2"], "rotulado mesma" if p["mesma"] else "rotulado diferente"))
    assert not erros, f"{len(erros)} pares errados: {erros}"


def test_gabarito_nao_junta_grupos_de_fora_do_rotulo(gabarito, lidas):
    """Duas trilhas que ninguém rotulou como par (de grupos diferentes) não podem cair juntas."""
    rotulados = {(p["vod"], round(p["t1"], 2), round(p["t2"], 2)) for p in gabarito["pares"]}
    marca = _grupos_por_vod(lidas)
    extras = []
    for vod, m in marca.items():
        ts = sorted(m)
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                if m[a] == m[b] and (vod, a, b) not in rotulados:
                    extras.append((vod, a, b))
    assert not extras, f"junção sem rótulo (grupos diferentes): {extras}"
