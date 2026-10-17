"""Registro de problemas de uma execução: 1 linha clara no terminal, o detalhe completo em `log.txt`.

Regra do projeto: o programa nunca mostra "Pronto" se algo falhou. Quem conta os problemas é o
`Registro`; o `__main__` só diz "Pronto" quando `registro.n == 0`."""
from __future__ import annotations

import datetime
import traceback
from pathlib import Path


def resumo_erro(texto: str, limite: int = 200) -> str:
    """Última linha com conteúdo do erro (no ffmpeg, a que explica o que deu errado), cortada."""
    linhas = [ln.strip() for ln in str(texto).splitlines() if ln.strip()]
    ultima = linhas[-1] if linhas else "erro sem mensagem"
    return ultima if len(ultima) <= limite else ultima[:limite - 1] + "…"


class Registro:
    def __init__(self, arquivo: Path):
        self.arquivo = arquivo
        self.n = 0
        self._cabecalho = False

    def _escrever(self, linhas: str) -> None:
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        with open(self.arquivo, "a", encoding="utf-8") as f:
            if not self._cabecalho:
                f.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
                self._cabecalho = True
            f.write(linhas.rstrip() + "\n")

    def erro(self, contexto: str, detalhe: str | BaseException) -> str:
        """Conta o problema, grava o detalhe completo no log e devolve a linha curta para o terminal."""
        if isinstance(detalhe, BaseException):
            completo = "".join(traceback.format_exception(type(detalhe), detalhe, detalhe.__traceback__))
        else:
            completo = str(detalhe)
        self.n += 1
        self._escrever(f"[erro] {contexto}\n{completo}")
        return f"[erro] {contexto}: {resumo_erro(str(detalhe))}"
