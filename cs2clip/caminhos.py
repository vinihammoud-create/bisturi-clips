"""Onde mora o programa e onde moram os dados da pessoa (P2 do plano de publicação, 26/11/2026).

Modo portátil (rodar da pasta do projeto, ZIP + instalar.bat, o dono): programa e dados na MESMA pasta, como sempre foi.
Modo instalado (o .exe): o programa fica em `...\\Bisturi Clips\\app` e os dados numa pasta que a pessoa escolheu.

A pasta de dados é decidida uma vez, na importação, nesta ordem:
  1. a variável `BISTURI_DADOS` (a interface a passa aos processos filhos; os testes usam);
  2. o arquivo `pasta_dados.txt` na pasta do programa (o instalador o grava em UTF-8 com BOM: o atalho do Windows não define variável);
  3. a própria pasta do programa (modo portátil).
Valor inválido (vazio, relativo, de rede, arquivo, disco que não existe) = `ErroPastaDados`, com a mensagem para a pessoa.
Pasta que ainda não existe vale: nada é criado aqui, quem grava cria a subpasta de que precisa.

O único efeito da importação: no modo instalado, `HF_HOME=<dados>\\modelos` (o modelo de voz fica junto dos dados), só se a pessoa ainda
não tem um. Este módulo tem de ser importado antes do `huggingface_hub` (ele lê o HF_HOME ao ser importado). No portátil nada é definido:
o modelo continua em `%USERPROFILE%\\.cache\\huggingface`.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

VARIAVEL = "BISTURI_DADOS"
ARQUIVO_PASTA_DADOS = "pasta_dados.txt"
DICA = "Use o caminho completo de uma pasta de um disco deste PC (por exemplo D:\\Bisturi Clips) ou reinstale o Bisturi Clips."

# A pasta do programa: a raiz do repositório no portátil, `app\` no instalado. É o mesmo valor de `cs2clip.tools.ROOT` (que continua
# existindo); calculado aqui de novo para este módulo não importar nada do pacote.
RAIZ_PROGRAMA = Path(__file__).resolve().parent.parent


class ErroPastaDados(ValueError):
    """A pasta de dados escolhida não serve; a mensagem diz de onde veio o valor e o que fazer."""


def resolver_pasta_dados(raiz_programa: Path, ambiente: Mapping[str, str] | None = None) -> Path:
    """A pasta de dados (caminho absoluto, resolvido): `BISTURI_DADOS` → `<raiz_programa>\\pasta_dados.txt` → `raiz_programa`.
    Só lê: não cria pasta nem arquivo. `ambiente` padrão = `os.environ`."""
    ambiente = os.environ if ambiente is None else ambiente
    if VARIAVEL in ambiente:
        return _validar(ambiente[VARIAVEL], f"na variável {VARIAVEL}")
    arquivo = Path(raiz_programa) / ARQUIVO_PASTA_DADOS
    texto = _ler_pasta_dados_txt(arquivo)
    if texto is not None:
        return _validar(texto, f"no arquivo {arquivo}")
    return Path(raiz_programa).resolve()


def resolver_pasta_logs(raiz_programa: Path, pasta_dados: Path) -> Path:
    """Portátil: `<raiz>\\saida` (onde o ui.log sempre morou). Instalado: `<dados>\\logs`."""
    raiz, dados = Path(raiz_programa).resolve(), Path(pasta_dados).resolve()
    return raiz / "saida" if dados == raiz else dados / "logs"


def _ler_pasta_dados_txt(arquivo: Path) -> str | None:
    """O texto do `pasta_dados.txt` (sem o BOM), ou None se ele não existe."""
    try:
        return arquivo.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise ErroPastaDados(f"Não consegui ler a pasta de dados no arquivo {arquivo} ({type(e).__name__}). {DICA}") from e


def _validar(valor: str, origem: str) -> Path:
    texto = valor.strip()
    if len(texto) >= 2 and texto[0] == texto[-1] == '"':      # `set X="C:\..."` no cmd guarda as aspas; nome de pasta nunca tem aspas
        texto = texto[1:-1].strip()
    if not texto:
        raise ErroPastaDados(f"A pasta de dados {origem} está vazia. {DICA}")
    if "\x00" in texto:
        raise ErroPastaDados(f"A pasta de dados {origem} tem um caractere inválido. {DICA}")
    if "\n" in texto or "\r" in texto:
        raise ErroPastaDados(f"A pasta de dados {origem} tem mais de uma linha. Deixe só o caminho da pasta. {DICA}")
    barras = texto.replace("/", "\\")
    if barras.startswith("\\\\") or barras.startswith("\\??\\"):
        raise ErroPastaDados(f"A pasta de dados {origem} ({texto}) é um caminho de rede ou especial do Windows. {DICA}")
    caminho = Path(texto)
    if not caminho.is_absolute():
        raise ErroPastaDados(f"A pasta de dados {origem} ({texto}) não é um caminho completo (começa com a letra do disco, como C:\\). {DICA}")
    try:
        if not Path(caminho.anchor).exists():
            raise ErroPastaDados(f"O disco {caminho.anchor} da pasta de dados {origem} ({texto}) não existe neste PC. "
                                 f"Se é um disco externo, ligue-o e abra o programa de novo. {DICA}")
        if caminho.exists() and not caminho.is_dir():
            raise ErroPastaDados(f"A pasta de dados {origem} ({texto}) é um arquivo, não uma pasta. {DICA}")
        return caminho.resolve()
    except OSError as e:
        raise ErroPastaDados(f"Não consegui conferir a pasta de dados {origem} ({texto}): {e}. {DICA}") from e


PASTA_DADOS = resolver_pasta_dados(RAIZ_PROGRAMA)
PASTA_LOGS = resolver_pasta_logs(RAIZ_PROGRAMA, PASTA_DADOS)


def modo_instalado() -> bool:
    """True quando os dados moram fora da pasta do programa (o instalador escolheu a pasta, ou `BISTURI_DADOS` aponta para outra)."""
    return PASTA_DADOS != RAIZ_PROGRAMA


if modo_instalado():
    os.environ.setdefault("HF_HOME", str(PASTA_DADOS / "modelos"))   # nunca troca o HF_HOME que a pessoa já tenha
