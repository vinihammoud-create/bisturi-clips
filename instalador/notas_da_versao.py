"""Notas da versão (P6): tira do `CHANGELOG.md` a seção da tag e grava num arquivo (o texto da Release rascunho no GitHub).

Uso (a partir da raiz do projeto):
  python instalador/notas_da_versao.py --tag v1.1.0 [--changelog CHANGELOG.md] [--saida dist/notas.md]

O CHANGELOG segue o formato Keep a Changelog: um título `## [1.1.0] - 2026-11-28` (ou `## 1.1.0`) por versão. A seção vai do título até o próximo
título de nível 2 (ou o fim do arquivo); linhas de link do rodapé (`[1.1.0]: https://...`) ficam de fora. Se a seção não existir ou estiver vazia,
sai com erro claro e nada é gravado (o workflow para antes de montar o instalador). Só biblioteca padrão; nenhum comando externo é chamado.

Título com "A PUBLICAR" no lugar da data (versão ainda não fechada) é RECUSADO: a publicação oficial exige a data. Só `--rascunho` (teste de montagem,
nunca a Release de verdade) aceita.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
TAG = re.compile(r"^v?(\d+\.\d+\.\d+)$")
A_PUBLICAR = re.compile(r"a\s+publicar", re.IGNORECASE)     # o marcador de "versão sem data" no título do CHANGELOG
ROTULO_LINK = re.compile(r"^\[[^\]]+\]:\s+\S+\s*$")      # `[1.1.0]: https://...` (rodapé do Keep a Changelog)
CERCA = re.compile(r"^\s*(```|~~~)")


class ErroNotas(Exception):
    """Tag malformada, CHANGELOG sem a seção (ou com a seção repetida ou vazia): a mensagem já vem pronta, em português."""


def versao_da_tag(tag: str) -> str:
    """`v1.1.0` ou `1.1.0` -> `1.1.0`. Qualquer outra coisa (sufixo, linha extra, caminho) é recusada."""
    m = TAG.fullmatch(tag) if isinstance(tag, str) else None
    if m is None:
        raise ErroNotas(f"tag inválida: {tag!r} (esperado vX.Y.Z, por exemplo v1.1.0)")
    return m.group(1)


def _versao_do_titulo(linha: str) -> str | None:
    """A versão de um título `## [1.1.0] - data` ou `## 1.1.0 (data)`; None se a linha não é título de nível 2."""
    if not linha.startswith("## "):
        return None
    primeira = linha[3:].strip().split(maxsplit=1)
    if not primeira:
        return ""
    return primeira[0].strip("[]").removeprefix("v")


def _titulos(linhas: list[str]) -> list[tuple[int, str]]:
    """(índice da linha, versão) de cada título de nível 2 fora de bloco de código."""
    achados: list[tuple[int, str]] = []
    dentro_de_codigo = False
    for n, linha in enumerate(linhas):
        if CERCA.match(linha):
            dentro_de_codigo = not dentro_de_codigo
        elif not dentro_de_codigo and (v := _versao_do_titulo(linha)) is not None:
            achados.append((n, v))
    return achados


def extrair_notas(changelog: str, tag: str, rascunho: bool = False) -> str:
    """O corpo da seção da versão (sem o título e sem o rodapé de links), com `\\n` no fim. Levanta ErroNotas.
    Título com "A PUBLICAR" (sem data) só passa com `rascunho=True`."""
    versao = versao_da_tag(tag)
    linhas = changelog.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    titulos = _titulos(linhas)
    daqui = [n for n, v in titulos if v == versao]
    if not daqui:
        existentes = ", ".join(v for _, v in titulos if TAG.fullmatch(v)) or "nenhuma"
        raise ErroNotas(f"o CHANGELOG não tem a seção da versão {versao} (título `## [{versao}] - data`); versões que ele tem: {existentes}")
    if len(daqui) > 1:
        raise ErroNotas(f"o CHANGELOG tem mais de uma seção da versão {versao}: deixe só uma")
    inicio = daqui[0]
    if not rascunho and A_PUBLICAR.search(linhas[inicio]):
        raise ErroNotas(f"o título da versão {versao} no CHANGELOG ainda diz \"A PUBLICAR\": ponha a data (`## [{versao}] - AAAA-MM-DD`) antes de publicar "
                        "(só `--rascunho`, para teste de montagem, aceita)")
    fim = next((n for n, _ in titulos if n > inicio), len(linhas))
    corpo = linhas[inicio + 1:fim]
    while corpo and (not corpo[-1].strip() or ROTULO_LINK.match(corpo[-1])):
        corpo.pop()
    texto = "\n".join(corpo).strip("\n")
    if not texto.strip():
        raise ErroNotas(f"a seção da versão {versao} no CHANGELOG está vazia")
    return texto + "\n"


def _dentro_do_projeto(caminho: str, raiz: Path, nome: str) -> Path:
    """Resolve `caminho` (relativo à raiz) e só aceita se ficar dentro do projeto."""
    if not caminho.strip() or "\x00" in caminho:
        raise ErroNotas(f"{nome} inválido")
    base = Path(raiz).resolve()
    p = Path(caminho)
    p = (p if p.is_absolute() else base / p).resolve()
    if p == base or not p.is_relative_to(base):
        raise ErroNotas(f"{nome} recusado: {p} precisa ficar dentro do projeto ({base})")
    return p


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="notas_da_versao.py", description="Extrai do CHANGELOG.md a seção de uma versão (notas da Release).")
    ap.add_argument("--tag", required=True, help="a tag da versão (v1.1.0 ou 1.1.0)")
    ap.add_argument("--changelog", default="CHANGELOG.md", help="o CHANGELOG (padrão CHANGELOG.md; dentro do projeto)")
    ap.add_argument("--saida", default="dist/notas.md", help="onde gravar as notas (padrão dist/notas.md; dentro do projeto)")
    ap.add_argument("--rascunho", action="store_true", help="aceita título com \"A PUBLICAR\" (só para teste de montagem; a Release oficial exige a data)")
    return ap


def main(argv: list[str] | None = None, raiz: Path = RAIZ) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(fluxo, "reconfigure", None)
        if reconfigurar is not None:
            reconfigurar(encoding="utf-8", errors="replace")
    a = _parser().parse_args(argv)
    try:
        origem = _dentro_do_projeto(a.changelog, raiz, "o CHANGELOG")
        destino = _dentro_do_projeto(a.saida, raiz, "o arquivo de saída")
        if not origem.is_file():
            raise ErroNotas(f"não achei o CHANGELOG em {origem}")
        notas = extrair_notas(origem.read_text(encoding="utf-8-sig"), a.tag, rascunho=a.rascunho)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(notas.encode("utf-8"))      # UTF-8 sem BOM e com LF, igual em qualquer máquina
    except ErroNotas as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    print(f"notas da versão {versao_da_tag(a.tag)}: {len(notas.splitlines())} linhas em {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
