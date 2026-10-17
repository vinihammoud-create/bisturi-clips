"""Linha de base do kill feed: varre VODs reais e mede o detector/classificador como estão hoje.

Para cada vídeo: probe -> scan_video_fast (cronometrado) -> salva o best_crop de TODAS as
trilhas em PASTA/<id>/<t>.png -> parse_box -> learn_player (só com as caixas desse vídeo)
-> classify -> números + pares suspeitos de duplicata. Imprime e grava JSON.

Uso (dentro da pasta do projeto):
    .venv\\Scripts\\python.exe tools\\baseline_killfeed.py --saida PASTA VIDEO.mp4 [VIDEO2.mp4 ...]
    [--json ARQUIVO.json]   (padrão: PASTA/baseline.json)

Testes das funções puras (sem vídeo):
    .venv\\Scripts\\python.exe -m pytest -q tools\\baseline_killfeed.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # permite rodar o script direto, sem instalar o pacote
    sys.path.insert(0, str(ROOT))

from cs2clip.chat import vod_id_from_name  # noqa: E402
from cs2clip.classify import Parsed, classify, learn_player, parse_box, seg_similarity  # noqa: E402
from cs2clip.icons import icons_of  # noqa: E402
from cs2clip.scan import probe, scan_video, scan_video_fast  # noqa: E402
from cs2clip.tools import imwrite  # noqa: E402

# critério de duplicata: o mesmo do pipeline (cs2clip/dedup.py)
from cs2clip.dedup import JANELA_DUP, SIM_VITIMA, TOL_ARMA, lado_direito_parecido  # noqa: E402,F401


@dataclass
class Caixa:
    t: float
    fim: float
    hits: int
    w: int
    arquivo: str
    parsed: Parsed | None
    classe: str = "noparse"
    icones: list[str] | None = None


def pares_suspeitos(kills: list[Caixa]) -> list[dict]:
    """Pares de kills a <= JANELA_DUP s um do outro com lado direito parecido (ordem temporal)."""
    ks = sorted(kills, key=lambda c: c.t)
    out = []
    for i, a in enumerate(ks):
        for b in ks[i + 1:]:
            if b.t - a.t > JANELA_DUP:
                break
            if a.parsed and b.parsed and lado_direito_parecido(a.parsed, b.parsed):
                out.append({"t1": a.t, "t2": b.t, "dt": round(b.t - a.t, 2)})
    return out


def _nome_recorte(t: float, usados: set[str] | None = None) -> str:
    """<t com 8 dígitos e 2 casas>.png; duas trilhas no mesmo instante (kill + assist no mesmo
    quadro) ganham sufixo _2, _3... para não sobrescrever o recorte uma da outra."""
    base = f"{t:08.2f}"
    nome, k = f"{base}.png", 1
    while usados is not None and nome in usados:
        k += 1
        nome = f"{base}_{k}.png"
    if usados is not None:
        usados.add(nome)
    return nome


def varrer(info, workers: int, progress=None):
    """scan_video_fast; se ele recusar (keyframes espaçados), cai na varredura completa."""
    tracks = scan_video_fast(info, workers=workers, progress=progress)
    modo = "rapido"
    if tracks is None:
        modo = "completo"
        tracks = scan_video(info, workers=workers, progress=progress)
    return tracks, modo


def medir_video(path: Path, saida: Path, workers: int = 6) -> dict:
    vid = vod_id_from_name(str(path)) or path.stem[:40]
    pasta = saida / vid
    pasta.mkdir(parents=True, exist_ok=True)
    info = probe(str(path))
    print(f"\n> {path.name}  ({info.duration:.0f}s, {info.width}x{info.height} @ {info.fps:.0f}fps)", flush=True)

    t0 = time.time()
    tracks, modo = varrer(info, workers)
    t_scan = time.time() - t0
    print(f"  varredura ({modo}, workers={workers}): {t_scan:.1f}s, {len(tracks)} caixas", flush=True)

    caixas: list[Caixa] = []
    usados: set[str] = set()
    for tr in tracks:
        f = pasta / _nome_recorte(tr.first, usados)
        if tr.best_crop is not None:
            imwrite(f, tr.best_crop)
        p = parse_box(tr.best_crop) if tr.best_crop is not None else None
        caixas.append(Caixa(round(tr.first, 2), round(tr.last, 2), tr.hits, tr.w, f.name, p))

    # aprende o jogador só com as caixas DESTE vídeo (sem modelo salvo de outro VOD)
    model = learn_player([c.parsed for c in caixas if c.parsed], None)
    for c in caixas:
        if c.parsed and model:
            c.classe = classify(c.parsed, model)
            c.icones = icons_of(c.parsed)
        elif c.parsed:
            c.classe = "unknown"

    cont = {k: sum(c.classe == k for c in caixas) for k in ("kill", "assist", "death", "unknown", "noparse")}
    kills = [c for c in caixas if c.classe == "kill"]
    dups = pares_suspeitos(kills)
    res = {
        "id": vid,
        "arquivo": path.name,
        "duracao_s": round(info.duration, 2),
        "resolucao": f"{info.width}x{info.height}",
        "fps": round(info.fps, 2),
        "modo_varredura": modo,
        "workers": workers,
        "tempo_varredura_s": round(t_scan, 1),
        "n_caixas": len(caixas),
        "contagem": cont,
        "n_hs": sum("headshot" in (c.icones or []) for c in kills),
        "kills_t": [c.t for c in kills],
        "assists_t": [c.t for c in caixas if c.classe == "assist"],
        "pares_suspeitos": dups,
        "caixas": [{"t": c.t, "fim": c.fim, "hits": c.hits, "w": c.w, "classe": c.classe, "arquivo": c.arquivo}
                   for c in caixas],
    }
    print(f"  {cont}  HS={res['n_hs']}  pares suspeitos={len(dups)}", flush=True)
    print(f"  kills:   {res['kills_t']}", flush=True)
    print(f"  assists: {res['assists_t']}", flush=True)
    for d in dups:
        print(f"  dup? {d['t1']:.2f} ~ {d['t2']:.2f} (dt={d['dt']}s)", flush=True)
    (pasta / "baseline.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("videos", nargs="+", type=Path)
    ap.add_argument("--saida", required=True, type=Path, help="pasta dos recortes (PASTA/<id>/<t>.png)")
    ap.add_argument("--json", type=Path, default=None, help="JSON agregado (padrão: PASTA/baseline.json)")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    a.saida.mkdir(parents=True, exist_ok=True)
    out = [medir_video(v, a.saida, a.workers) for v in a.videos]
    dest = a.json or (a.saida / "baseline.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON: {dest}")
    return 0


# ── testes (pytest tools/baseline_killfeed.py) ─────────────────────────────────────────

def _parsed_fake(vitima: "np.ndarray", arma_w: int) -> Parsed:  # noqa: F821
    import numpy as np
    from cs2clip.classify import Segment
    m = (vitima > 0).astype(np.uint8) * 255
    seg = Segment(100, 100 + m.shape[1] - 1, m)
    return Parsed([seg], [seg], 0, (40, 40 + arma_w), np.zeros((1, 1, 3), np.uint8))


def _mascaras():
    import numpy as np
    rng = np.random.default_rng(0)
    a = rng.random((16, 60)) > 0.5
    b = rng.random((16, 60)) > 0.5
    return a, b


def test_lado_direito_parecido_mesma_vitima_mesma_arma():
    a, _ = _mascaras()
    assert lado_direito_parecido(_parsed_fake(a, 30), _parsed_fake(a, 31))


def test_lado_direito_diferente_por_arma_ou_vitima():
    a, b = _mascaras()
    assert not lado_direito_parecido(_parsed_fake(a, 30), _parsed_fake(a, 40))  # arma diferente
    assert not lado_direito_parecido(_parsed_fake(a, 30), _parsed_fake(b, 30))  # vítima diferente


def test_pares_suspeitos_respeita_janela_e_noparse():
    a, _ = _mascaras()
    p = _parsed_fake(a, 30)
    ks = [Caixa(10.0, 11.0, 5, 300, "a.png", p, "kill"),
          Caixa(12.5, 13.0, 5, 300, "b.png", p, "kill"),     # dup de 10.0 (dt 2,5)
          Caixa(30.0, 31.0, 5, 300, "c.png", p, "kill"),     # longe (> 8 s)
          Caixa(31.0, 32.0, 5, 300, "d.png", None, "noparse")]  # sem parse: não compara
    assert pares_suspeitos(ks) == [{"t1": 10.0, "t2": 12.5, "dt": 2.5}]


def test_nome_recorte_8_digitos_2_casas():
    assert _nome_recorte(194.83) == "00194.83.png"
    assert _nome_recorte(3.5) == "00003.50.png"


def test_nome_recorte_nao_sobrescreve_mesmo_instante():
    usados: set[str] = set()
    assert _nome_recorte(851.33, usados) == "00851.33.png"
    assert _nome_recorte(851.33, usados) == "00851.33_2.png"
    assert _nome_recorte(851.33, usados) == "00851.33_3.png"
    assert _nome_recorte(852.0, usados) == "00852.00.png"


if __name__ == "__main__":
    raise SystemExit(main())
