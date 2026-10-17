"""Teste REAL do leitor do kill feed (cs2clip/classify.py: parse_box -> learn_player -> classify)
sobre recortes de VODs do usuário.

Fixtures: tests/fixtures/caixas/<vod>_<t>.png (recortes das caixas vermelhas do kill feed,
gravados pelo detector) + labels.csv (arquivo,rotulo,vod,obs), rotulado a olho por dois
rotuladores independentes e arbitrado (E1-F3). Rótulos:
  - kill:   o streamer ("Dollar The DOG") é o PRIMEIRO nome da esquerda (pode ter "+ assistente");
  - assist: o streamer aparece DEPOIS de um "+";
  - outro:  o streamer não é matador nem assistente (vítima, linha de terceiros, desconexão)
            ou o recorte está cortado/ilegível demais para afirmar.
Acerto = (kill/assist e classify devolve o mesmo) OU (outro e classify NÃO devolve kill nem assist).

O modelo do jogador (learn_player) é aprendido com TODOS os recortes parseados das fixtures
(teste oficial). O relatório do teste de acurácia também mostra o modelo aprendido só com as
fixtures do mesmo VOD, como o pipeline faz hoje.

KNOWN_FAILING guarda os casos que ainda erram (xfail não estrito). Depois de E1-C (17/10/2026)
a lista está vazia: 66/66 acertos nos dois modos de aprendizado. Um caso que voltar a errar
falha de verdade; o teste de acurácia global exige ACURACIA_MINIMA.
"""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("cv2")

from cs2clip.classify import Parsed, PlayerModel, classify, learn_player, parse_box  # noqa: E402
from cs2clip.tools import imread  # lê caminho com acento (cv2.imread não lê no Windows)  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "caixas"
LABELS = FIXTURES / "labels.csv"
ROTULOS_VALIDOS = ("kill", "assist", "outro")
ACURACIA_MINIMA = 0.90

# Casos que o classificador atual erra: {arquivo: motivo}. Antes de E1-C eram 40 (26 noparse:
# a borda vermelha vazava no miolo; 14 unknown: nome do jogador não batia, nome quebrado em
# "Dollar" + "The DOG" ou recorte começando no meio do nome). Hoje nenhum.
KNOWN_FAILING: dict[str, str] = {}


@dataclass
class Rotulo:
    arquivo: str
    rotulo: str
    vod: str
    obs: str


@dataclass
class Leitura:
    """O que o classificador atual diz de um recorte."""
    rotulo: Rotulo
    parsed: Parsed | None
    classe: str      # modelo aprendido com todas as fixtures (oficial)
    classe_vod: str  # modelo aprendido só com as fixtures do mesmo VOD


def _ler_rotulos() -> list[Rotulo]:
    with LABELS.open(encoding="utf-8", newline="") as f:
        return [Rotulo(**linha) for linha in csv.DictReader(f)]


ROTULOS = _ler_rotulos()


def _correto(rotulo: str, classe: str) -> bool:
    if rotulo == "outro":
        return classe not in ("kill", "assist")
    return classe == rotulo


def _classe(p: Parsed | None, modelo: PlayerModel | None) -> str:
    if p is None:
        return "noparse"
    if modelo is None:
        return "unknown"
    return classify(p, modelo)


def _aprender(parsed: list[Parsed | None]) -> PlayerModel | None:
    return learn_player([p for p in parsed if p is not None], None)


@pytest.fixture(scope="module")
def leituras() -> dict[str, Leitura]:
    parsed: dict[str, Parsed | None] = {}
    for r in ROTULOS:
        img = imread(FIXTURES / r.arquivo)
        assert img is not None, f"fixture ausente: {r.arquivo}"
        parsed[r.arquivo] = parse_box(img)
    geral = _aprender(list(parsed.values()))
    por_vod: dict[str, list[Parsed | None]] = {}
    for r in ROTULOS:
        por_vod.setdefault(r.vod, []).append(parsed[r.arquivo])
    modelos = {vod: _aprender(ps) for vod, ps in por_vod.items()}
    return {r.arquivo: Leitura(r, parsed[r.arquivo],
                               _classe(parsed[r.arquivo], geral),
                               _classe(parsed[r.arquivo], modelos[r.vod]))
            for r in ROTULOS}


def _acuracia(ls: list[Leitura], vod: bool = False) -> tuple[int, int]:
    ok = sum(_correto(l.rotulo.rotulo, l.classe_vod if vod else l.classe) for l in ls)
    return ok, len(ls)


def _pct(ok: int, n: int) -> str:
    return f"{ok}/{n} = {ok / n:.2f}" if n else "0/0"


def relatorio(leituras: dict[str, Leitura]) -> str:
    """Acurácia global, por VOD e por rótulo, nos dois modos de aprendizado."""
    ls = list(leituras.values())
    linhas = [f"modelo geral (oficial): {_pct(*_acuracia(ls))}   |   modelo por VOD: {_pct(*_acuracia(ls, vod=True))}"]
    for vod in sorted({l.rotulo.vod for l in ls}):
        sub = [l for l in ls if l.rotulo.vod == vod]
        linhas.append(f"  VOD {vod}: geral {_pct(*_acuracia(sub))} | por VOD {_pct(*_acuracia(sub, vod=True))}")
    for rot in ROTULOS_VALIDOS:
        sub = [l for l in ls if l.rotulo.rotulo == rot]
        linhas.append(f"  rótulo {rot:6}: geral {_pct(*_acuracia(sub))} | por VOD {_pct(*_acuracia(sub, vod=True))}")
    linhas.append(f"  classes (geral): {dict(Counter(l.classe for l in ls))}")
    linhas.append(f"  classes (por VOD): {dict(Counter(l.classe_vod for l in ls))}")
    erros = [f"{a} rótulo={l.rotulo.rotulo} classify={l.classe}" for a, l in leituras.items()
             if not _correto(l.rotulo.rotulo, l.classe)]
    linhas.append(f"  erros (geral): {len(erros)}")
    linhas += [f"    {e}" for e in erros]
    return "\n".join(linhas)


# ── sanidade das fixtures ──────────────────────────────────────────────────────────────

def test_labels_batem_com_os_pngs():
    pngs = {f.name for f in FIXTURES.glob("*.png")}
    rotulados = {r.arquivo for r in ROTULOS}
    assert pngs == rotulados, f"sem rótulo: {sorted(pngs - rotulados)}; sem PNG: {sorted(rotulados - pngs)}"
    assert len(ROTULOS) == len(rotulados), "arquivo repetido em labels.csv"
    assert all(r.rotulo in ROTULOS_VALIDOS for r in ROTULOS)
    assert all(r.arquivo.startswith(r.vod + "_") for r in ROTULOS)


def test_known_failing_so_tem_fixtures_existentes():
    sobrando = set(KNOWN_FAILING) - {r.arquivo for r in ROTULOS}
    assert not sobrando, f"KNOWN_FAILING cita recortes que não existem mais: {sorted(sobrando)}"


# ── um teste por recorte ───────────────────────────────────────────────────────────────

def _caso(r: Rotulo):
    marcas = []
    if r.arquivo in KNOWN_FAILING:
        marcas.append(pytest.mark.xfail(strict=False, reason=KNOWN_FAILING[r.arquivo]))
    return pytest.param(r.arquivo, marks=marcas, id=r.arquivo)


@pytest.mark.parametrize("arquivo", [_caso(r) for r in ROTULOS])
def test_recorte(leituras, arquivo):
    l = leituras[arquivo]
    assert _correto(l.rotulo.rotulo, l.classe), \
        f"{arquivo}: rótulo={l.rotulo.rotulo}, classify={l.classe} ({l.rotulo.obs})"


# ── globais ────────────────────────────────────────────────────────────────────────────

def test_acuracia_global(leituras):
    rel = relatorio(leituras)
    ok, n = _acuracia(list(leituras.values()))
    assert ok / n >= ACURACIA_MINIMA, f"acurácia {ok / n:.2f} < {ACURACIA_MINIMA}\n{rel}"


def test_nenhum_falso_kill(leituras):
    """Um rótulo outro/assist nunca pode virar kill (viraria corte falso)."""
    falsos = [f"{a} rótulo={l.rotulo.rotulo}" for a, l in leituras.items()
              if l.rotulo.rotulo in ("outro", "assist") and l.classe == "kill"]
    assert falsos == [], f"falsos kills: {falsos}"
