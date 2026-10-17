r"""Mede o acerto do programa contra o gabarito do usuário.

Uso:  .venv\Scripts\python.exe tools\medir_gabarito.py [tools\gabarito_momentos.csv] [--saida saida]
O CSV vem do botão "Exportar CSV" da página revisao.html (ou preenchido à mão, ver docs/engineering/GABARITO.md)."""
import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from cs2clip import medicao  # noqa: E402


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", default=str(RAIZ / "tools" / "gabarito_momentos.csv"))
    ap.add_argument("--saida", default=str(RAIZ / "saida"))
    a = ap.parse_args(argv)
    arq = Path(a.csv)
    if not arq.exists():
        print(f"Não achei o gabarito em {arq}.\nExporte o CSV pela página revisao.html e salve nesse caminho.")
        return 1
    linhas, avisos = medicao.ler_gabarito(arq)
    if not linhas:
        print("O gabarito não tem nenhuma linha válida.\n" + "\n".join(avisos))
        return 1
    momentos, av2 = medicao.carregar_momentos(Path(a.saida), {ln.vod for ln in linhas})
    rel = medicao.medir(linhas, momentos)
    rel.avisos = avisos + av2
    print(medicao.texto(rel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
