r"""Sugere a nota mínima de short/montagem a partir dos SEUS votos (pesquisa P7, R4).

Uso:  .venv\Scripts\python.exe tools\calibrar.py [tools\gabarito_momentos.csv ...] [--saida saida]
Votos lidos: a coluna "voto" (👍 postaria / 👎 não) dos CSVs exportados pela página de revisão, e na interface
👍 Gostei (aprovado) e 🗑 Descartar (excluido) gravados nos momentos.json da pasta de saída. Nada é mudado no
config.toml: o comando só mostra a sugestão para você decidir."""
import argparse
import csv
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from cs2clip.calibracao import sugerir_limiar  # noqa: E402


def votos_do_csv(arquivo: Path) -> list[tuple[float, bool]]:
    votos = []
    try:
        linhas = list(csv.DictReader(arquivo.open(encoding="utf-8-sig")))
    except OSError:
        return votos
    for r in linhas:
        voto, nota = (r.get("voto") or "").strip().lower(), (r.get("programa_nota") or "").strip()
        if voto in ("sim", "nao") and nota:
            try:
                votos.append((float(nota), voto == "sim"))
            except ValueError:
                pass
    return votos


def votos_da_interface(saida: Path) -> list[tuple[float, bool]]:
    votos = []
    for arq in saida.glob("*/momentos.json"):
        try:
            momentos = json.loads(arq.read_text(encoding="utf-8")).get("momentos", [])
        except (OSError, ValueError):
            continue
        for m in momentos:
            if m.get("excluido"):
                votos.append((float(m.get("nota", 0)), False))
            elif m.get("aprovado") is True:
                votos.append((float(m.get("nota", 0)), True))
    return votos


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("csvs", nargs="*", default=[str(RAIZ / "tools" / "gabarito_momentos.csv")])
    ap.add_argument("--saida", default=str(RAIZ / "saida"))
    a = ap.parse_args(argv)
    votos = [v for c in a.csvs for v in votos_do_csv(Path(c))] + votos_da_interface(Path(a.saida))
    s = sugerir_limiar(votos)
    print(f"Votos: {s.votos} ({s.aprovados} 👍)")
    print(s.mensagem)
    if s.limiar is not None:
        print(f"Sugestão: no config.toml, nota_short = {s.limiar:.0f} (e nota_montagem um pouco abaixo).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
