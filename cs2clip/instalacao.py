"""O que o instalador `.exe` roda depois de copiar o programa e instalar os pacotes (P5, item 2 do plano de publicação, 27/11/2026).

  python -m cs2clip.instalacao pos --dados <pasta> [--bin <pasta>] [--nvidia|--sem-nvidia] [--com-gravacao-demo] [--com-modelo] [--sem-musicas]
  python -m cs2clip.instalacao checar

Passos do `pos` (cada um imprime `[n/N] mensagem` no stdout, que o instalador mostra no andamento; avisos saem como `AVISO: ...` e o
detalhe dos erros vai também para o stderr):
  1. a pasta de dados, o marcador `.bisturi-dados` (sem ele o desinstalador não apaga nada da pasta), o `config.toml` (cópia do
     `config.exemplo.toml`) e o `pasta_dados.txt` (UTF-8 com BOM, como o `caminhos.py` lê);
  2. o TwitchDownloaderCLI (`terceiros`: URL, tamanho e SHA-256 fixos no código);
  3. as músicas livres do `<programa>\\instalador\\componentes.json` (seção `musicas`): só https, só os sites da lista fixa, tamanho
     limitado e SHA-256 conferido ANTES de gravar;
  4. com `--com-gravacao-demo`, o HLAE e o boiler-writter (`terceiros`, com hash);
  5. a checagem do ambiente (a mesma do `checar`);
  6. com `--com-modelo`, o modelo de voz, SEMPRE por último (sem NVIDIA: o `small`; com NVIDIA: o do config).
Código de saída: 0 = pronto (ou só o modelo de voz ficou para depois, com `AVISO:`); 1 = um passo essencial falhou (a linha diz qual);
2 = argumento inválido. Essenciais: 1, 4 (quando pedido) e 5. O chat da Twitch e as músicas são opcionais: a falha vira `AVISO:` e
a instalação continua (abrir o instalador de novo tenta outra vez).
Idempotente e retomável: nada que já existe é sobrescrito (config, `pasta_dados.txt`, músicas) e o que já está instalado na versão
fixada não é baixado de novo. Nenhum comando é montado com texto (só funções Python): espaço, acento ou apóstrofo no caminho não mudam nada.
Toda rede passa por uma função `baixar` trocável (os testes nunca usam a internet).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .tools import substituir_arquivo

ARQUIVO_COMPONENTES = Path("instalador") / "componentes.json"   # dentro da pasta do programa (a `app\` no instalado)
ESQUEMA = 1
# Os únicos sites de onde uma música pode vir (o endereço do catálogo e todo redirecionamento): o GitHub e os servidores de arquivo dele.
DOMINIOS_PERMITIDOS = frozenset({"raw.githubusercontent.com", "media.githubusercontent.com", "objects.githubusercontent.com", "github.com"})
LIMITE_FAIXA_BYTES = 64 * 1024 * 1024      # uma faixa do catálogo nunca passa disto (as de hoje têm ~6 MB)
PRAZO_REDE_S = 60
AVISO_PROGRESSO_S = 5.0                    # o progresso do modelo de voz: uma linha a cada 5 s (e sempre a primeira e a do fim)
ESPERA_SOLTAR_S = 3.0
MODELO_PADRAO, MODELO_SEM_NVIDIA = "large-v3-turbo", "small"
DE_NOVO = "Abra o instalador do Bisturi Clips de novo: ele continua de onde parou."
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")     # como no montar.py: maiúsculas valem e viram minúsculas ao ler
# O desinstalador (BisturiClips.iss, PastaDadosSegura e ApagarDados) só apaga algo da pasta de dados se ela tem este marcador, e só estes
# nomes. O teste cruza as duas listas.
MARCADOR_DADOS = ".bisturi-dados"
ITENS_DO_PROGRAMA = ("modelos", "config.toml", "config.toml.bak", "config.toml.bak-antes-dos-padroes", "entrada", "saida", "demos",
                     "gravacao_demo", "trabalhos", "dados", "musicas", "logs", "cache")


class ErroEssencial(Exception):
    """Um passo sem o qual o programa não funciona falhou; a mensagem diz o que houve e o que fazer."""


class ErroDownload(Exception):
    """Uma faixa não pôde ser baixada com segurança (site fora da lista, tamanho errado, rede)."""


@dataclass(frozen=True)
class Faixa:
    arquivo: str
    titulo: str
    url: str
    sha256: str
    tamanho: int


@dataclass(frozen=True)
class Fora:
    arquivo: str
    motivo: str
    aviso: bool          # True = problema (rede, hash, catálogo); False = ficou de fora de propósito (a lista `fora` do catálogo)


@dataclass
class ResultadoMusicas:
    baixadas: list[str] = field(default_factory=list)
    ja_tinha: list[str] = field(default_factory=list)
    fora: list[Fora] = field(default_factory=list)

    def resumo(self) -> str:
        fim = ": o porquê vem abaixo." if self.fora else "."
        return f"Músicas livres: baixei {len(self.baixadas)}, já tinha {len(self.ja_tinha)}, ficaram de fora {len(self.fora)}{fim}"


@dataclass(frozen=True)
class Opcoes:
    dados: Path
    bin_dir: Path
    nvidia: bool
    gravacao_demo: bool = False
    modelo: bool = False
    musicas: bool = True


class Andamento:
    """As linhas que o instalador lê: `[n/N] mensagem` (o passo atual) e `AVISO: ...`; o detalhe dos erros vai também para o stderr."""

    def __init__(self, total: int):
        self.total, self.n = total, 0

    def proximo(self, mensagem: str) -> None:
        self.n += 1
        self.linha(mensagem)

    def linha(self, mensagem: str) -> None:
        print(f"[{self.n}/{self.total}] {mensagem}", flush=True)

    @staticmethod
    def aviso(mensagem: str) -> None:
        print(f"AVISO: {mensagem}", flush=True)

    @staticmethod
    def detalhe(mensagem: str) -> None:
        print(mensagem, file=sys.stderr, flush=True)


# ── gravar sem nunca sobrescrever ───────────────────────────────────────────────────────────────────────────────────────────────────
def criar_sem_sobrescrever(destino: Path, dados: bytes) -> bool:
    """Grava `dados` em `destino` SÓ se ele ainda não existe, de uma vez (nunca fica pela metade): escreve num temporário ao lado,
    manda ao disco e cria o nome final por link (falha se o nome já existe). Disco sem link (FAT32/exFAT): troca normal, só se ainda não
    existe. Devolve False se já existia (nada foi tocado)."""
    destino = Path(destino)
    tmp = destino.with_name(destino.name + ".baixando")
    try:
        with open(tmp, "wb") as f:
            f.write(dados)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, destino)
            return True
        except FileExistsError:
            return False
        except OSError:
            if destino.exists() or destino.is_symlink():
                return False
            substituir_arquivo(tmp, destino)
            return True
    finally:
        tmp.unlink(missing_ok=True)


def _sha256(arq: Path) -> str:
    h = hashlib.sha256()
    with open(arq, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _mesma_pasta(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _dentro(filho: Path, pai: Path) -> bool:
    try:
        Path(filho).resolve().relative_to(Path(pai).resolve())
        return True
    except ValueError:
        return False


# ── passo 1: pasta de dados ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def marcar_pasta_dados(dados: Path) -> list[str]:
    """Grava o marcador `<dados>\\.bisturi-dados` (JSON com a versão; nunca por cima). Pasta que já tinha algo com um dos nomes que o
    programa usa (`ITENS_DO_PROGRAMA`: `saida`, `entrada`... da pessoa) fica SEM marcador: o desinstalador não saberia o que é dela e o
    que é do programa, então nunca apaga nada. Devolve esses nomes (vazio = marcada agora ou já estava). Vem ANTES do `config.toml`: uma
    queda entre os dois não faz a retomada achar que o config é da pessoa."""
    from . import __version__
    marcador = dados / MARCADOR_DADOS
    if marcador.exists() or marcador.is_symlink():
        return []
    ja_tinha = [n for n in ITENS_DO_PROGRAMA if (dados / n).exists() or (dados / n).is_symlink()]
    if not ja_tinha:
        conteudo = json.dumps({"esquema": ESQUEMA, "programa": "Bisturi Clips", "versao": __version__}) + "\n"
        criar_sem_sobrescrever(marcador, conteudo.encode("utf-8"))
    return ja_tinha


def preparar_dados(dados: Path, raiz: Path, avisar: Callable[[str], None] | None = None) -> str:
    """Cria a pasta de dados, o marcador (`marcar_pasta_dados`) e o `config.toml` (do exemplo; nunca por cima de um existente) e grava o
    `pasta_dados.txt` do programa (se já existe, tem de apontar para a mesma pasta). Devolve a frase do resultado. Falha = `ErroEssencial`."""
    avisar = Andamento.aviso if avisar is None else avisar
    from . import caminhos
    if (raiz / ".git").exists():
        raise ErroEssencial(f"A pasta {raiz} é a cópia de desenvolvimento (tem .git): este passo é só do programa instalado pelo .exe. "
                            "Nada foi mudado.")
    arquivo_pasta = raiz / caminhos.ARQUIVO_PASTA_DADOS
    ja_apontava = _conferir_pasta_dados_txt(arquivo_pasta, dados)
    exemplo, config = raiz / "config.exemplo.toml", dados / "config.toml"
    try:
        dados.mkdir(parents=True, exist_ok=True)
        ja_tinha = marcar_pasta_dados(dados)
        if config.exists():
            criou = False
        elif not exemplo.is_file():
            raise ErroEssencial(f"Falta o {exemplo.name} na pasta do programa ({raiz}): a instalação está incompleta. {DE_NOVO}")
        else:
            criou = criar_sem_sobrescrever(config, exemplo.read_bytes())
        if not ja_apontava:
            criar_sem_sobrescrever(arquivo_pasta, str(dados).encode("utf-8-sig"))
    except OSError as e:
        raise ErroEssencial(f"Não consegui preparar a pasta de dados {dados}: {e}. Escolha outra pasta ou libere o acesso a ela. {DE_NOVO}") from e
    if ja_tinha:
        avisar(f"a pasta de dados {dados} já tinha {', '.join(ja_tinha)} antes do Bisturi Clips: não pus o marcador {MARCADOR_DADOS}, "
               "então o desinstalador não vai apagar nada dela (se quiser, apague à mão o que for do programa).")
    return f"Pasta de dados pronta: {dados} (config.toml {'criado do exemplo' if criou else 'já existia, não mexi'})."


def _conferir_pasta_dados_txt(arquivo: Path, dados: Path) -> bool:
    """True = o `pasta_dados.txt` já existe e aponta para `dados`. Outra pasta (ou ilegível) = `ErroEssencial`: trocar a pasta de dados
    de quem já usa o programa esconderia os vídeos, os votos e o config dele."""
    from . import caminhos
    if not (arquivo.exists() or arquivo.is_symlink()):
        return False
    try:
        atual = caminhos._validar(arquivo.read_text(encoding="utf-8-sig"), f"no arquivo {arquivo}")
    except (caminhos.ErroPastaDados, OSError, UnicodeDecodeError) as e:
        raise ErroEssencial(f"O arquivo {arquivo} (a pasta de dados do programa) está ilegível ou inválido: {e}. Não mexi nele. "
                            "Desinstale e instale de novo para escolher a pasta.") from e
    if _mesma_pasta(atual, dados):
        return True
    raise ErroEssencial(f"Este programa já usa outra pasta de dados ({atual}, no arquivo {arquivo}). Não troquei, para nada seu sumir de "
                        f"vista. Escolha {atual} no instalador, ou desinstale e instale de novo para usar {dados}.")


# ── passos 2 e 4: programas de terceiros ────────────────────────────────────────────────────────────────────────────────────────────
def _terceiros() -> Any:
    """O módulo `terceiros` (o FFmpeg só é procurado na hora de instalar o HLAE; o boiler e o TwitchDownloaderCLI não dependem dele)."""
    try:
        from . import terceiros
    except SystemExit as e:
        raise RuntimeError(f"não achei o FFmpeg do programa ({e})") from None
    return terceiros


def _ja_instalado(t: Any, chave: str, bin_dir: Path) -> bool:
    """O programa está em `bin_dir` E o registro (`bin\\TERCEIROS.json`) diz que é a versão fixada no código (versão nova = baixa de novo)."""
    if not t.instalado(chave, bin_dir):
        return False
    try:
        registro = json.loads(Path(t.REGISTRO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    alvo = t.TERCEIROS[chave]
    entrada = registro.get(alvo.pasta) if isinstance(registro, dict) else None
    return isinstance(entrada, dict) and entrada.get("versao") == alvo.versao and entrada.get("sha256") == alvo.sha256


def instalar_chat_twitch(bin_dir: Path, baixar: Callable[..., Any] | None = None) -> str:
    t = _terceiros()
    if _ja_instalado(t, "twitchdl", bin_dir):
        return f"Chat da Twitch: TwitchDownloaderCLI {t.TWITCHDL.versao} já estava instalado."
    t.instalar_twitchdl(bin_dir=bin_dir, baixar=baixar)
    return f"Chat da Twitch: TwitchDownloaderCLI {t.TWITCHDL.versao} instalado (SHA-256 conferido)."


def instalar_gravacao_demo(bin_dir: Path, baixar: Callable[..., Any] | None = None) -> list[str]:
    """HLAE e boiler-writter (gravar pela demo). O `ffmpeg.exe` que o HLAE recebe é o do próprio programa (`bin\\ffmpeg.exe`), se existir."""
    t = _terceiros()
    frases = []
    if _ja_instalado(t, "hlae", bin_dir):
        frases.append(f"HLAE {t.HLAE.versao} já estava instalado.")
    else:
        ffmpeg = bin_dir / "ffmpeg.exe"
        t.instalar_hlae(bin_dir=bin_dir, ffmpeg=ffmpeg if ffmpeg.is_file() else None, baixar=baixar)
        frases.append(f"HLAE {t.HLAE.versao} instalado (SHA-256 conferido).")
    if _ja_instalado(t, "boiler", bin_dir):
        frases.append(f"boiler-writter {t.BOILER.versao} já estava instalado.")
    else:
        t.instalar_boiler(bin_dir=bin_dir, baixar=baixar)
        frases.append(f"boiler-writter {t.BOILER.versao} instalado (SHA-256 e assinatura da Valve conferidos).")
    return frases


# ── passo 3: músicas livres ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def url_permitida(url: object) -> bool:
    """Só https, só um site da lista fixa, sem usuário/senha nem porta estranha no endereço."""
    if not isinstance(url, str) or any(c.isspace() for c in url):
        return False
    try:
        p = urllib.parse.urlsplit(url)
        porta = p.port
    except ValueError:
        return False
    return (p.scheme == "https" and p.hostname in DOMINIOS_PERMITIDOS and p.username is None and p.password is None
            and porta in (None, 443))


def ler_musicas(arquivo: Path) -> tuple[list[Faixa], list[Fora]]:
    """As faixas do catálogo que podem ser baixadas e as que ficam de fora (a lista `fora` e as que vieram sem origem, hash, tamanho ou
    com site fora da lista). Arquivo ilegível ou de outro formato = `ValueError`/`OSError`."""
    from .musicas_catalogo import EXTENSOES, nome_de_faixa_valido
    dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
    if not isinstance(dados, dict) or dados.get("esquema") != ESQUEMA:
        raise ValueError(f"esquema desconhecido (o programa lê o {ESQUEMA})")
    secao = dados.get("musicas")
    if not isinstance(secao, dict):
        raise ValueError("falta a seção musicas")
    fora: list[Fora] = []
    for item in secao.get("fora") or []:
        if isinstance(item, dict) and isinstance(item.get("arquivo"), str):
            fora.append(Fora(item["arquivo"], str(item.get("motivo") or "sem motivo registrado"), False))
    faixas: list[Faixa] = []
    vistos: set[str] = set()
    for item in secao.get("faixas") or []:
        info = item if isinstance(item, dict) else {}
        nome, url, sha, tamanho = info.get("arquivo"), info.get("url"), info.get("sha256"), info.get("tamanho")
        rotulo = nome if isinstance(nome, str) and nome else "(sem nome)"
        if not (nome_de_faixa_valido(nome) and isinstance(nome, str) and Path(nome).suffix.lower() in EXTENSOES):
            motivo = "o nome do arquivo no catálogo não é válido"
        elif nome.lower() in vistos:
            motivo = "aparece duas vezes no catálogo"
        elif not (isinstance(url, str) and url.strip()):
            motivo = "sem origem (endereço de download) no catálogo"
        elif not (isinstance(sha, str) and _HEX64.fullmatch(sha)):
            motivo = "sem SHA-256 no catálogo"
        elif not (type(tamanho) is int and 0 < tamanho <= LIMITE_FAIXA_BYTES):
            motivo = "tamanho inválido no catálogo"
        elif not url_permitida(url):
            motivo = "o endereço não é https de um site permitido (" + ", ".join(sorted(DOMINIOS_PERMITIDOS)) + "); não baixei"
        else:
            vistos.add(nome.lower())
            faixas.append(Faixa(nome, str(info.get("titulo") or Path(nome).stem), url, sha.lower(), tamanho))
            continue
        fora.append(Fora(rotulo, motivo, True))
    return faixas, fora


class _RedirecionaSoParaPermitido(urllib.request.HTTPRedirectHandler):
    """Segue redirecionamento só para https de um site da lista (o raw do GitHub manda os arquivos grandes para outro servidor dele)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001  (assinatura da biblioteca)
        if not url_permitida(newurl):
            raise ErroDownload(f"o site mandou para um endereço fora da lista permitida ({urllib.parse.urlsplit(newurl).hostname})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _abrir_padrao(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(_RedirecionaSoParaPermitido()).open(req, timeout=timeout)


def baixar_faixa(faixa: Faixa, abrir: Callable[..., Any] | None = None) -> bytes:
    """Os bytes da faixa (na memória: nada vai para o disco antes de o SHA-256 ser conferido). Recusa endereço fora da lista (o pedido e o
    endereço final, depois dos redirecionamentos) e arquivo maior que o tamanho do catálogo (para de ler na hora)."""
    abrir = _abrir_padrao if abrir is None else abrir
    if not url_permitida(faixa.url):
        raise ErroDownload("o endereço não é https de um site permitido")
    pedido = urllib.request.Request(faixa.url, headers={"User-Agent": "BisturiClips"})
    with abrir(pedido, timeout=PRAZO_REDE_S) as r:
        final = r.geturl() if hasattr(r, "geturl") else faixa.url
        if not url_permitida(final):
            raise ErroDownload("o download terminou num endereço fora da lista permitida")
        dados = bytearray()
        while bloco := r.read(1 << 20):
            dados += bloco
            if len(dados) > faixa.tamanho:
                raise ErroDownload("o arquivo é maior do que o catálogo diz")
    if len(dados) != faixa.tamanho:
        raise ErroDownload(f"veio com {len(dados)} bytes e o catálogo diz {faixa.tamanho}")
    return bytes(dados)


def instalar_musicas(componentes: Path, pasta: Path, baixar: Callable[[Faixa], bytes] | None = None) -> ResultadoMusicas:
    """Baixa as faixas que faltam em `pasta` (a de fábrica, `<programa>\\musicas`), conferindo o SHA-256 ANTES de gravar. Nunca sobrescreve:
    arquivo que já existe com o hash certo = "já tinha"; com outro conteúdo = fica como está e a faixa conta como "de fora". Hash errado,
    tamanho errado ou rede caída = nada é gravado e a faixa conta como "de fora". Rodar de novo só baixa o que ainda falta."""
    baixar = baixar_faixa if baixar is None else baixar
    faixas, fora = ler_musicas(componentes)
    res = ResultadoMusicas(fora=list(fora))
    base = Path(pasta).resolve()
    base.mkdir(parents=True, exist_ok=True)
    for f in faixas:
        destino = base / f.arquivo
        if destino.parent != base:                   # o nome já foi conferido; isto é só a segunda trava
            res.fora.append(Fora(f.arquivo, "o nome sairia da pasta de músicas", True))
            continue
        if destino.exists() or destino.is_symlink():
            if destino.is_file() and not destino.is_symlink() and _sha256(destino) == f.sha256:
                res.ja_tinha.append(f.arquivo)
            else:
                res.fora.append(Fora(f.arquivo, "já existe um arquivo com esse nome e outro conteúdo; não mexi nele", True))
            continue
        try:
            conteudo = baixar(f)
        except Exception as e:  # noqa: BLE001  (rede, site fora da lista, tamanho: a faixa fica de fora, as outras seguem)
            res.fora.append(Fora(f.arquivo, f"não consegui baixar ({e}); nada foi gravado", True))
            continue
        if len(conteudo) != f.tamanho or hashlib.sha256(conteudo).hexdigest() != f.sha256:
            res.fora.append(Fora(f.arquivo, "o arquivo baixado NÃO bate com o SHA-256 do catálogo; nada foi gravado", True))
            continue
        try:
            criou = criar_sem_sobrescrever(destino, conteudo)
        except OSError as e:
            res.fora.append(Fora(f.arquivo, f"não consegui gravar ({e})", True))
            continue
        if criou:
            res.baixadas.append(f.arquivo)
        else:
            res.fora.append(Fora(f.arquivo, "apareceu um arquivo com esse nome durante o download; não mexi nele", True))
    return res


# ── passo 5: checagem ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _item_pasta_dados(dados: Path) -> Any:
    from .checagem import Item
    resolver = "Abra o instalador de novo e escolha uma pasta de um disco deste PC onde você pode gravar."
    if not Path(dados).is_dir():
        return Item("Pasta de dados", False, f"{dados} não existe", True, resolver)
    try:
        with tempfile.TemporaryFile(dir=dados):
            pass
    except OSError as e:
        return Item("Pasta de dados", False, f"não consigo gravar em {dados} ({type(e).__name__})", True, resolver)
    return Item("Pasta de dados", True, str(dados), True, resolver)


def checar_ambiente(raiz: Path, dados: Path) -> list[Any]:
    """A checagem de sempre (`cs2clip.checagem`) com a pasta de dados na frente."""
    from . import checagem
    return [_item_pasta_dados(dados), *checagem.checar(raiz, Path(dados) / "saida")]


# ── passo 6: modelo de voz ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def cache_do_modelo(dados: Path, ambiente: Mapping[str, str] | None = None) -> Path:
    """Onde o programa instalado vai procurar o modelo: o cache do Hugging Face que a pessoa já tem (HF_HUB_CACHE, HF_HOME) ou, como o
    `caminhos.py` define no modo instalado, `<dados>\\modelos\\hub`. Calculado aqui porque nesta 1ª execução o `pasta_dados.txt` acabou de
    nascer e o `caminhos.py` (lido antes dele) ainda pensa que é o modo portátil."""
    ambiente = os.environ if ambiente is None else ambiente
    if ambiente.get("HF_HUB_CACHE"):
        return Path(ambiente["HF_HUB_CACHE"])
    if ambiente.get("HF_HOME"):
        return Path(ambiente["HF_HOME"]) / "hub"
    return Path(dados) / "modelos" / "hub"


def _modelo_do_config(config: Path) -> str:
    from .config import load_config
    try:
        return load_config(config).transcricao.modelo or MODELO_PADRAO
    except Exception:  # noqa: BLE001  (config ilegível: o padrão; o programa avisa do config quando abrir)
        return MODELO_PADRAO


def _megas(n: float) -> str:
    return f"{n / 1e6:.1f}".replace(".", ",")


def _mostrador(a: Andamento, nome: str, relogio: Callable[[], float] = time.monotonic) -> Callable[[int, int, float, float | None], None]:
    visto: dict[str, float | None] = {"ultimo": None}

    def mostrar(baixado: int, total: int, vel: float, restante: float | None) -> None:
        agora = relogio()
        ultimo = visto["ultimo"]
        if ultimo is None or baixado >= total or agora - ultimo >= AVISO_PROGRESSO_S:
            visto["ultimo"] = agora
            falta = "" if restante is None else f" · faltam {int(restante) // 60:02d}:{int(restante) % 60:02d}"
            a.linha(f"Modelo de voz {nome}: {round(baixado / 1e6)} de {round(total / 1e6)} MB · {_megas(vel)} MB/s{falta}")
    return mostrar


def _esperar_soltar(modelo_voz: Any) -> None:
    """Depois de cancelar, o download de fundo leva um instante para fechar o `.incomplete` (que fica guardado para a próxima vez)."""
    fim = time.monotonic() + ESPERA_SOLTAR_S
    try:
        while modelo_voz.em_andamento() and time.monotonic() < fim:
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass


def _passo_modelo(a: Andamento, op: Opcoes, baixar: Callable[..., Any] | None) -> None:
    """Nunca levanta: falhou ou foi cancelado = `AVISO:` (a instalação continua válida; o cartão do modelo na tela Início retoma)."""
    nome = MODELO_SEM_NVIDIA if not op.nvidia else _modelo_do_config(op.dados / "config.toml")
    tamanho = "~0,5 GB" if nome == MODELO_SEM_NVIDIA else ("~1,6 GB" if nome == MODELO_PADRAO else "")
    porque = " (sem placa NVIDIA: o modelo leve)" if not op.nvidia else ""
    a.proximo(f"Modelo de voz {nome}{porque}: baixando{' ' + tamanho if tamanho else ''}. Pode demorar; o que já veio fica guardado.")
    depois = "A instalação está pronta: o programa baixa o modelo pelo cartão da tela Início (continua de onde parou)."
    mv: Any = None
    try:
        from . import modelo_voz as mv
        funcao: Callable[..., Any] = mv.baixar if baixar is None else baixar
        funcao(nome, ao_progredir=_mostrador(a, nome), cache_dir=cache_do_modelo(op.dados))
    except KeyboardInterrupt:
        if mv is not None:
            _esperar_soltar(mv)
        a.aviso(f"modelo de voz não baixado (cancelado). {depois}")
        return
    except Exception as e:  # noqa: BLE001
        a.detalhe(f"modelo de voz {nome}: {type(e).__name__}: {e}")
        a.aviso(f"modelo de voz não baixado ({e}). {depois}")
        return
    a.linha(f"Modelo de voz {nome}: pronto.")


# ── o comando `pos` ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def pos(op: Opcoes, raiz: Path, *, baixar_musica: Callable[[Faixa], bytes] | None = None, baixar_terceiro: Callable[..., Any] | None = None,
        baixar_modelo: Callable[..., Any] | None = None, checar: Callable[[Path, Path], list[Any]] | None = None) -> int:
    """Os passos depois dos pacotes (ver o topo do arquivo). 0 = pronto; 1 = um passo essencial falhou."""
    a = Andamento(4 + int(op.gravacao_demo) + int(op.modelo))
    checar = checar_ambiente if checar is None else checar
    try:
        a.proximo("Preparando a pasta de dados e o config.toml...")
        a.linha(preparar_dados(op.dados, raiz))

        a.proximo("Chat da Twitch (TwitchDownloaderCLI, opcional)...")
        try:
            a.linha(instalar_chat_twitch(op.bin_dir, baixar_terceiro))
        except Exception as e:  # noqa: BLE001  (opcional: sem ele só o chat da Twitch não conta)
            a.detalhe(f"TwitchDownloaderCLI: {type(e).__name__}: {e}")
            a.aviso(f"o TwitchDownloaderCLI (chat da Twitch, opcional) não foi instalado: {e}. {DE_NOVO}")

        a.proximo("Músicas livres...")
        _passo_musicas(a, raiz, op.musicas, baixar_musica)

        if op.gravacao_demo:
            a.proximo("Gravar pela demo (HLAE e boiler-writter)...")
            try:
                for frase in instalar_gravacao_demo(op.bin_dir, baixar_terceiro):
                    a.linha(frase)
            except Exception as e:  # noqa: BLE001  (pedido pela pessoa: sem ele a instalação não fica como ela escolheu)
                a.detalhe(f"gravar pela demo: {type(e).__name__}: {e}")
                raise ErroEssencial(f"não consegui instalar o HLAE e o boiler-writter (gravar pela demo): {e}. {DE_NOVO}") from e

        a.proximo("Conferindo o programa instalado...")
        _passo_checagem(a, raiz, op.dados, checar)
    except ErroEssencial as e:
        a.linha(f"ERRO: {e}")
        a.detalhe(f"passo {a.n} de {a.total}: {e}")
        return 1
    except KeyboardInterrupt:
        a.linha(f"ERRO: instalação interrompida. {DE_NOVO}")
        return 1
    if op.modelo:
        _passo_modelo(a, op, baixar_modelo)
    return 0


def _passo_musicas(a: Andamento, raiz: Path, pedidas: bool, baixar: Callable[[Faixa], bytes] | None) -> None:
    if not pedidas:
        a.linha("Músicas livres: não pedidas; pulei (abra o instalador de novo para baixá-las).")
        return
    componentes = raiz / ARQUIVO_COMPONENTES
    if not componentes.is_file():
        a.aviso(f"não achei a lista de músicas ({componentes}); pulei as músicas livres.")
        return
    try:
        res = instalar_musicas(componentes, raiz / "musicas", baixar)
    except (OSError, ValueError) as e:
        a.detalhe(f"músicas: {type(e).__name__}: {e}")
        a.aviso(f"a lista de músicas ({componentes}) está ilegível ({e}); pulei as músicas livres.")
        return
    a.linha(res.resumo())
    for f in res.fora:
        (a.aviso if f.aviso else a.linha)(f"a música {f.arquivo} ficou de fora: {f.motivo}")


def _passo_checagem(a: Andamento, raiz: Path, dados: Path, checar: Callable[[Path, Path], list[Any]]) -> None:
    try:
        itens = checar(raiz, dados)
    except Exception as e:  # noqa: BLE001
        raise ErroEssencial(f"a checagem do programa quebrou ({type(e).__name__}: {e}). {DE_NOVO}") from e
    graves = [i for i in itens if not i.ok and i.grave]
    for i in itens:
        if not i.ok and not i.grave:
            a.aviso(f"{i.nome}: {i.detalhe}" + (f" ({i.como_resolver})" if i.como_resolver else ""))
    if graves:
        for i in graves:
            a.detalhe(f"checagem: {i.nome}: {i.detalhe}")
        lista = "; ".join(f"{i.nome}: {i.detalhe}" for i in graves)
        raise ErroEssencial(f"a checagem achou {len(graves)} problema(s) grave(s): {lista}. {DE_NOVO}")
    a.linha("Checagem: tudo certo para trabalhar.")


# ── linha de comando ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _tem_nvidia() -> bool:
    return shutil.which("nvidia-smi") is not None


def _validar_dados(texto: str, raiz: Path) -> Path:
    """A pasta escolhida, com as mesmas regras do `caminhos.py` (o que vale aqui vale quando o programa abrir) e fora da pasta do programa
    (a desinstalação apaga a pasta do programa inteira). `ValueError` com a mensagem para a pessoa."""
    from . import caminhos
    dados = caminhos._validar(texto, "escolhida no instalador (--dados)")
    if dados == Path(dados.anchor):                         # também `D:\.` e `D:\x\..` (o _validar já resolveu)
        raise ValueError(f"A pasta de dados ({dados}) não pode ser a raiz do disco: escolha uma pasta dentro dele (por exemplo, "
                         f"{dados.anchor}Bisturi Clips).")
    for programa in (raiz, raiz.parent):
        if Path(programa.anchor) == programa:
            continue                                        # programa na raiz de um disco: só a própria pasta do programa conta
        if _dentro(dados, programa) or _dentro(programa, dados):
            raise ValueError(f"A pasta de dados ({dados}) não pode ficar dentro da pasta do programa ({programa}) nem conter a pasta do "
                             "programa: a desinstalação apaga a pasta do programa inteira. Escolha outra (por exemplo, Vídeos\\Bisturi Clips).")
    return dados


def _argumentos() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m cs2clip.instalacao", description="Passos do instalador do Bisturi Clips.")
    sub = ap.add_subparsers(dest="acao", required=True)
    p = sub.add_parser("pos", help="o que vem depois dos pacotes (pasta de dados, chat da Twitch, músicas, checagem, modelo de voz)")
    p.add_argument("--dados", required=True, help="a pasta de dados escolhida (caminho completo)")
    p.add_argument("--bin", default=None, help="a pasta bin do programa (padrão: <programa>\\bin)")
    placa = p.add_mutually_exclusive_group()
    placa.add_argument("--nvidia", action="store_true", help="há placa NVIDIA (modelo de voz do config)")
    placa.add_argument("--sem-nvidia", action="store_true", help="sem placa NVIDIA (modelo de voz small)")
    p.add_argument("--com-gravacao-demo", action="store_true", help="instala HLAE e boiler-writter (gravar pela demo)")
    p.add_argument("--com-modelo", action="store_true", help="baixa o modelo de voz (último passo; falhar não invalida a instalação)")
    p.add_argument("--sem-musicas", action="store_true", help="não baixa as músicas livres")
    sub.add_parser("checar", help="confere o programa instalado (0 = pronto; 1 = problema grave)")
    return ap


def main(argv: list[str] | None = None, *, raiz_programa: Path | None = None, baixar_musica: Callable[[Faixa], bytes] | None = None,
         baixar_terceiro: Callable[..., Any] | None = None, baixar_modelo: Callable[..., Any] | None = None,
         checar: Callable[[Path, Path], list[Any]] | None = None, tem_nvidia: Callable[[], bool] | None = None) -> int:
    """0 = pronto; 1 = falha essencial (ou checagem com problema grave); 2 = argumento inválido. Os parâmetros com nome são para os testes."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    try:
        a = _argumentos().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    try:
        from . import caminhos
    except ValueError as e:                                  # `pasta_dados.txt` ou `BISTURI_DADOS` inválidos (ErroPastaDados)
        print(f"ERRO: {e}", flush=True)
        Andamento.detalhe(str(e))
        return 1
    raiz = Path(raiz_programa).resolve() if raiz_programa is not None else caminhos.RAIZ_PROGRAMA
    if a.acao == "checar":
        return _checar(raiz, checar)
    try:
        dados = _validar_dados(a.dados, raiz)
        bin_dir = Path(a.bin) if a.bin else raiz / "bin"
        if not bin_dir.is_absolute():
            raise ValueError(f"A pasta --bin ({a.bin}) tem de ser um caminho completo.")
    except ValueError as e:
        print(f"ERRO: {e}", flush=True)
        Andamento.detalhe(str(e))
        return 2
    nvidia = True if a.nvidia else False if a.sem_nvidia else (tem_nvidia or _tem_nvidia)()
    op = Opcoes(dados, bin_dir.resolve(), nvidia, a.com_gravacao_demo, a.com_modelo, not a.sem_musicas)
    return pos(op, raiz, baixar_musica=baixar_musica, baixar_terceiro=baixar_terceiro, baixar_modelo=baixar_modelo, checar=checar)


def _checar(raiz: Path, checar: Callable[[Path, Path], list[Any]] | None) -> int:
    from . import caminhos, checagem
    try:
        dados = caminhos.resolver_pasta_dados(raiz)
    except caminhos.ErroPastaDados as e:
        print(f"ERRO: {e}", flush=True)
        return 1
    itens = (checar_ambiente if checar is None else checar)(raiz, dados)
    print(checagem.texto(itens), flush=True)
    return 1 if checagem.tem_problema_grave(itens) else 0


if __name__ == "__main__":
    raise SystemExit(main())
