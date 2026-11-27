"""Monta o instalador do Bisturi Clips (P5): confere `instalador/componentes.json`, baixa `uv` e FFmpeg com hash, monta a pasta de pacote e chama o ISCC.

Só biblioteca padrão (mais o próprio `cs2clip`, para ler `terceiros.py` e gerar os efeitos sonoros). NADA aqui instala nem executa o programa montado:
só baixa (com SHA-256 conferido ANTES de gravar), copia e chama o compilador do Inno Setup.

Uso (a partir da raiz do projeto):
  python instalador/montar.py --validar                         só confere os dados (sem rede)
  python instalador/montar.py --baixar                          traz o uv e o FFmpeg para o cache (confere o hash)
  python instalador/montar.py --pacote <pasta>                  monta a pasta que o `.iss` consome (precisa do uv no cache: use --baixar antes)
  python instalador/montar.py --baixar --pacote P --compilar    tudo: baixa, monta e compila (`ISCC`, com /DAppVersion, /DPacote, /DAppIdGuid e
                                                                os /DFFmpegUrl, /DFFmpegSha256 e /DFFmpegTamanhoExtraido do componentes.json)
Tudo é idempotente: rodar de novo não estraga nada (arquivo do cache com hash certo não é baixado de novo).

Contrato com o `.iss`: `<pacote>\\app\\` (programa: cs2clip, ui, musicas de fábrica, componentes.json...) e `<pacote>\\uv.exe`. O programa instalado
é a pasta `{app}\\app`. Os `.wav` dos efeitos sonoros não estão no git: o `montar.py` os gera dentro de `app\\cs2clip\\assets\\sfx`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))          # `python instalador/montar.py` põe só `instalador/` no caminho: o cs2clip fica na raiz

ESQUEMA = 1
ARQUIVO_COMPONENTES = Path("instalador") / "componentes.json"
CACHE_PADRAO = Path(".tmp_agentes") / "cache_instalador"
SAIDA_PADRAO = Path(".tmp_agentes") / "saida_instalador"
ARQUIVO_ISS = Path("instalador") / "BisturiClips.iss"

# De onde o componentes.json pode mandar baixar (https, sem usuário, sem porta). Redirecionamentos do GitHub vão para os hosts da 2ª lista.
DOMINIOS_PERMITIDOS = frozenset({"github.com", "raw.githubusercontent.com"})
DOMINIOS_REDIRECIONAMENTO = DOMINIOS_PERMITIDOS | {"objects.githubusercontent.com", "release-assets.githubusercontent.com"}
EXTENSOES_AUDIO = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus"}
LIMITE_UV_EXE = 200 * 1024 * 1024          # bytes: o uv.exe tem ~33 MB; acima disso a extração é recusada
PREFIXO_URL_FFMPEG = "https://github.com/"   # o mesmo `#if Pos(...)` do BisturiClips.iss

SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
VERSAO = re.compile(r"^\d+\.\d+\.\d+$")
GUID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
CARACTERES_PROIBIDOS_NO_NOME = set('<>:"/\\|?*') | {chr(c) for c in range(32)}


class ErroMontagem(Exception):
    """Dado inválido, hash que não bate, pacote com coisa proibida ou compilador com problema (mensagem pronta, em português)."""


# ── os dados: componentes.json ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Baixavel:
    versao: str
    url: str
    sha256: str
    tamanho: int
    tamanho_extraido: int | None = None     # só o FFmpeg: o total descompactado do .7z (o ExternalSize do .iss)


@dataclass(frozen=True)
class Faixa:
    arquivo: str
    titulo: str
    url: str
    sha256: str
    tamanho: int


@dataclass(frozen=True)
class Componentes:
    uv: Baixavel
    ffmpeg: Baixavel
    faixas: tuple[Faixa, ...] = field(default_factory=tuple)
    fora: dict[str, str] = field(default_factory=dict)       # arquivo -> motivo


def url_permitida(url: str, dominios: frozenset[str] | set[str] = DOMINIOS_PERMITIDOS) -> str | None:
    """None se a URL serve; senão o motivo. Só `https`, host na lista, sem usuário/senha, porta padrão e sem espaço ou caractere de controle."""
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 for c in url):
        return "não é um texto de URL válido"
    try:
        p = urllib.parse.urlsplit(url)
        porta = p.port
    except ValueError:
        return "URL malformada"
    if p.scheme != "https":
        return "precisa começar com https://"
    if p.username is not None or p.password is not None:
        return "não pode ter usuário ou senha"
    if porta not in (None, 443):
        return "não pode ter porta diferente da padrão"
    if (p.hostname or "").lower() not in dominios:
        return f"o domínio {p.hostname!r} não está na lista permitida ({', '.join(sorted(dominios))})"
    return None


def nome_de_arquivo_simples(nome: Any) -> bool:
    """Só um nome (sem pasta, sem `..`, sem caractere que o Windows não aceita, sem ponto ou espaço no fim)."""
    return (isinstance(nome, str) and bool(nome) and nome not in (".", "..") and Path(nome).name == nome
            and not (set(nome) & CARACTERES_PROIBIDOS_NO_NOME) and not nome.endswith((".", " ")))


def _inteiro_positivo(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _problemas_do_item(rotulo: str, item: Any, sufixo_url: str | None) -> list[str]:
    if not isinstance(item, dict):
        return [f"{rotulo}: precisa ser um objeto"]
    p: list[str] = []
    motivo = url_permitida(item.get("url", ""))
    if motivo:
        p.append(f"{rotulo}: url {motivo}")
    elif sufixo_url and not urllib.parse.urlsplit(item["url"]).path.lower().endswith(sufixo_url):
        p.append(f"{rotulo}: a url precisa terminar em {sufixo_url}")
    if not isinstance(item.get("sha256"), str) or not SHA256.match(item["sha256"]):
        p.append(f"{rotulo}: sha256 precisa ter 64 caracteres hexadecimais")
    if not _inteiro_positivo(item.get("tamanho")):
        p.append(f"{rotulo}: tamanho precisa ser um número inteiro maior que zero")
    return p


def _problemas_do_ffmpeg(item: Any) -> list[str]:
    """O que o `.iss` exige a mais do FFmpeg: a url da release do GitHub (`#if Pos("https://github.com/", ...)`) e o tamanho descompactado."""
    if not isinstance(item, dict):
        return []
    p: list[str] = []
    if isinstance(item.get("url"), str) and not item["url"].startswith(PREFIXO_URL_FFMPEG):
        p.append(f"ffmpeg: a url precisa começar com {PREFIXO_URL_FFMPEG} (o instalador só aceita a release fixa do GitHub)")
    extraido = item.get("tamanho_extraido")
    if not _inteiro_positivo(extraido):
        p.append("ffmpeg: tamanho_extraido (o total descompactado do .7z, em bytes) precisa ser um número inteiro maior que zero")
    elif _inteiro_positivo(item.get("tamanho")) and extraido <= item["tamanho"]:
        p.append("ffmpeg: tamanho_extraido precisa ser maior que o tamanho do .7z baixado")
    return p


def problemas_dos_dados(dados: Any) -> list[str]:
    """Todos os problemas do `componentes.json` já lido (lista vazia = serve). Nunca levanta."""
    if not isinstance(dados, dict):
        return ["o arquivo precisa ser um objeto JSON"]
    p: list[str] = []
    if dados.get("esquema") != ESQUEMA or isinstance(dados.get("esquema"), bool):
        p.append(f"esquema precisa ser {ESQUEMA} (veio {dados.get('esquema')!r})")
    for chave, sufixo in (("uv", ".zip"), ("ffmpeg", ".7z")):
        item = dados.get(chave)
        p += _problemas_do_item(chave, item, sufixo)
        if isinstance(item, dict):
            versao = item.get("versao")
            if not isinstance(versao, str) or not versao.strip():
                p.append(f"{chave}: falta a versao")
            elif isinstance(item.get("url"), str) and f"/{versao}/" not in urllib.parse.urlsplit(item["url"]).path:
                p.append(f"{chave}: a url precisa ser a da versão {versao} (o caminho tem de conter /{versao}/)")
    p += _problemas_do_ffmpeg(dados.get("ffmpeg"))
    musicas = dados.get("musicas")
    if not isinstance(musicas, dict):
        return p + ["musicas: precisa ser um objeto com faixas e fora"]
    vistos: set[str] = set()
    faixas = musicas.get("faixas")
    for i, f in enumerate(faixas if isinstance(faixas, list) else [], 1):
        rotulo = f"musicas.faixas[{i}]"
        p += _problemas_do_item(rotulo, f, None)
        if not isinstance(f, dict):
            continue
        nome = f.get("arquivo")
        if not isinstance(nome, str) or not nome_de_arquivo_simples(nome) or Path(nome).suffix.lower() not in EXTENSOES_AUDIO:
            p.append(f"{rotulo}: arquivo precisa ser só o nome de um arquivo de áudio (sem pasta)")
        elif nome.lower() in vistos:
            p.append(f"{rotulo}: arquivo repetido ({nome})")
        else:
            vistos.add(nome.lower())
        if not isinstance(f.get("titulo"), str) or not f["titulo"].strip():
            p.append(f"{rotulo}: falta o titulo")
    if not isinstance(faixas, list):
        p.append("musicas.faixas: precisa ser uma lista")
    fora = musicas.get("fora")
    if not isinstance(fora, list):
        p.append("musicas.fora: precisa ser uma lista")
        fora = []
    for i, f in enumerate(fora, 1):
        rotulo = f"musicas.fora[{i}]"
        if not isinstance(f, dict) or not nome_de_arquivo_simples(f.get("arquivo")) or not isinstance(f.get("arquivo"), str):
            p.append(f"{rotulo}: arquivo precisa ser só um nome de arquivo")
            continue
        if not isinstance(f.get("motivo"), str) or not f["motivo"].strip():
            p.append(f"{rotulo}: falta o motivo")
        if f["arquivo"].lower() in vistos:
            p.append(f"{rotulo}: {f['arquivo']} está em faixas e em fora ao mesmo tempo")
        vistos.add(f["arquivo"].lower())
    return p


def _a_partir_dos_dados(dados: dict[str, Any]) -> Componentes:
    def baixavel(i: dict[str, Any]) -> Baixavel:
        return Baixavel(i["versao"], i["url"], i["sha256"].lower(), i["tamanho"], i.get("tamanho_extraido"))
    m = dados["musicas"]
    return Componentes(baixavel(dados["uv"]), baixavel(dados["ffmpeg"]),
                       tuple(Faixa(f["arquivo"], f["titulo"], f["url"], f["sha256"].lower(), f["tamanho"]) for f in m["faixas"]),
                       {f["arquivo"]: f["motivo"] for f in m["fora"]})


def validar_dados(dados: Any) -> Componentes:
    p = problemas_dos_dados(dados)
    if p:
        raise ErroMontagem("componentes.json inválido:\n  - " + "\n  - ".join(p))
    return _a_partir_dos_dados(dados)


def ler_componentes(arquivo: Path) -> Componentes:
    """Lê e valida o `componentes.json`. UTF-8 sem BOM."""
    try:
        bruto = Path(arquivo).read_bytes()
    except OSError as e:
        raise ErroMontagem(f"não consegui ler {arquivo}: {e}") from e
    if bruto.startswith(b"\xef\xbb\xbf"):
        raise ErroMontagem(f"{arquivo} tem BOM: o arquivo precisa ser UTF-8 sem BOM")
    try:
        dados = json.loads(bruto.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ErroMontagem(f"{arquivo} não é um JSON em UTF-8 válido: {e}") from e
    return validar_dados(dados)


def conferir_catalogo(comp: Componentes, raiz: Path = RAIZ) -> list[str]:
    """Toda faixa do catálogo de fábrica (`musicas/catalogo.json`) está em `faixas` ou em `fora` (com o motivo), e nada sobra: nenhuma faixa some calada."""
    try:
        cat = json.loads((Path(raiz) / "musicas" / "catalogo.json").read_text(encoding="utf-8"))
        nomes = [str(f.get("arquivo")) for f in cat.get("faixas", []) if isinstance(f, dict)]
    except (OSError, ValueError, AttributeError) as e:
        return [f"não consegui ler musicas/catalogo.json ({type(e).__name__})"]
    listadas = {f.arquivo for f in comp.faixas} | set(comp.fora)
    problemas = [f"a faixa do catálogo {n!r} não está em faixas nem em fora" for n in nomes if n not in listadas]
    problemas += [f"{n!r} está no componentes.json mas não no catálogo de fábrica" for n in sorted(listadas - set(nomes))]
    return problemas


def conferir_terceiros() -> list[str]:
    """TwitchDownloaderCLI, HLAE e boiler vêm de `cs2clip/terceiros.py` (nunca duplicados aqui): só confere que cada um tem url permitida, hash e tamanho."""
    from cs2clip import terceiros
    problemas: list[str] = []
    for chave, t in terceiros.TERCEIROS.items():
        motivo = url_permitida(t.url)
        if motivo:
            problemas.append(f"terceiros.{chave}: url {motivo}")
        if not SHA256.match(t.sha256):
            problemas.append(f"terceiros.{chave}: sha256 inválido")
        if not _inteiro_positivo(t.tamanho):
            problemas.append(f"terceiros.{chave}: tamanho inválido")
    return problemas


# ── caminhos seguros ──────────────────────────────────────────────────────────────────────────────
PASTAS_DE_CODIGO = ("cs2clip", "ui", "tests", "docs", "tools", "musicas", "bin", "instalador", ".git")


def pasta_do_projeto(caminho: Path | str, raiz: Path = RAIZ, nome: str = "pasta") -> Path:
    """Resolve `caminho` e só aceita se ficar DENTRO do projeto e fora das pastas de código (o pacote nunca é gravado em cima do código)."""
    raiz = Path(raiz).resolve()
    texto = str(caminho)
    if not texto.strip() or "\x00" in texto or '"' in texto:
        raise ErroMontagem(f"{nome} inválida")
    p = Path(texto)
    p = (raiz / p if not p.is_absolute() else p).resolve()
    if p == raiz or not p.is_relative_to(raiz):
        raise ErroMontagem(f"{nome} recusada: {p} precisa ficar dentro do projeto ({raiz}), numa subpasta")
    for codigo in PASTAS_DE_CODIGO:
        if p.is_relative_to(raiz / codigo):
            raise ErroMontagem(f"{nome} recusada: {p} está dentro de {codigo}\\ (use outra pasta, por exemplo .tmp_agentes\\...)")
    return p


def _dentro_de(pasta: Path, arquivo: Path) -> Path:
    pasta = Path(pasta).resolve()
    r = Path(arquivo).resolve()
    if not r.is_relative_to(pasta) or r == pasta:
        raise ErroMontagem(f"o caminho {arquivo} sairia de {pasta}")
    return r


# ── download com hash ─────────────────────────────────────────────────────────────────────────────
class _RedirecionamentoSeguro(urllib.request.HTTPRedirectHandler):
    """O servidor pode redirecionar (GitHub manda para o armazenamento dele), mas só para https e hosts da lista."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        motivo = url_permitida(newurl, DOMINIOS_REDIRECIONAMENTO)
        if motivo:
            raise ErroMontagem(f"redirecionamento recusado: {motivo}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _abrir_padrao(req: urllib.request.Request, timeout: float = 120):
    return urllib.request.build_opener(_RedirecionamentoSeguro).open(req, timeout=timeout)


def sha256_do_arquivo(arq: Path) -> str:
    h = hashlib.sha256()
    with open(arq, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def baixar_conferido(url: str, sha256: str, tamanho: int, destino: Path, abrir: Callable[..., Any] | None = None,
                     dominios: frozenset[str] | set[str] = DOMINIOS_PERMITIDOS) -> tuple[Path, bool]:
    """Baixa `url` para `destino` SÓ se o conteúdo tiver o tamanho e o SHA-256 pedidos. Devolve (destino, baixou_agora).
    * `destino` já existe com o hash certo: não toca, não usa a rede (baixou_agora=False);
    * o conteúdo vai para `<destino>.parcial`; hash ou tamanho errado = o parcial some e o `destino` (se existia) continua intacto;
    * só no fim o parcial vira `destino` (troca atômica)."""
    from cs2clip.tools import substituir_arquivo
    motivo = url_permitida(url, dominios)
    if motivo:
        raise ErroMontagem(f"download recusado ({url}): {motivo}")
    if not SHA256.match(sha256 or "") or not _inteiro_positivo(tamanho):
        raise ErroMontagem("download recusado: sem SHA-256 e tamanho válidos não se baixa nada")
    sha256 = sha256.lower()
    destino = Path(destino)
    if destino.is_file() and destino.stat().st_size == tamanho and sha256_do_arquivo(destino) == sha256:
        return destino, False
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    abrir = _abrir_padrao if abrir is None else abrir
    h = hashlib.sha256()
    total = 0
    try:
        with abrir(urllib.request.Request(url, headers={"User-Agent": "BisturiClips-montar"}), timeout=120) as r, open(parcial, "wb") as f:
            final = getattr(r, "geturl", lambda: url)()
            motivo = url_permitida(final, DOMINIOS_REDIRECIONAMENTO)
            if motivo:
                raise ErroMontagem(f"o download terminou em um endereço não permitido: {motivo}")
            while True:
                bloco = r.read(1 << 20)
                if not bloco:
                    break
                total += len(bloco)
                if total > tamanho:                                    # maior que o esperado: para já, sem encher o disco
                    raise ErroMontagem(f"{destino.name}: o arquivo baixado é maior que os {tamanho} bytes esperados; nada foi mantido")
                h.update(bloco)
                f.write(bloco)
        if total != tamanho:
            raise ErroMontagem(f"{destino.name}: o tamanho baixado ({total}) não é o esperado ({tamanho}); nada foi mantido")
        if h.hexdigest() != sha256:
            raise ErroMontagem(f"{destino.name}: o SHA-256 do arquivo baixado NÃO bate com o do componentes.json; nada foi mantido")
        substituir_arquivo(parcial, destino)
        return destino, True
    except OSError as e:
        raise ErroMontagem(f"{destino.name}: não consegui baixar ({e})") from e
    finally:
        parcial.unlink(missing_ok=True)


def nome_do_cache(item: Baixavel) -> str:
    nome = Path(urllib.parse.unquote(urllib.parse.urlsplit(item.url).path)).name
    if not nome_de_arquivo_simples(nome):
        raise ErroMontagem(f"não dá para tirar um nome de arquivo seguro de {item.url}")
    return nome


def baixar_componentes(comp: Componentes, cache: Path, abrir: Callable[..., Any] | None = None) -> dict[str, Path]:
    """Traz o uv e o FFmpeg para `cache`. Devolve {"uv": caminho, "ffmpeg": caminho}."""
    cache = Path(cache)
    saida: dict[str, Path] = {}
    for chave, item in (("uv", comp.uv), ("ffmpeg", comp.ffmpeg)):
        alvo = _dentro_de(cache, cache / nome_do_cache(item))
        caminho, novo = baixar_conferido(item.url, item.sha256, item.tamanho, alvo, abrir)
        print(f"  {chave} {item.versao}: {'baixado' if novo else 'já estava no cache'} ({caminho.name}, hash conferido)", flush=True)
        saida[chave] = caminho
    return saida


# ── o pacote ──────────────────────────────────────────────────────────────────────────────────────
PASTAS_DO_PROGRAMA = ("cs2clip", "ui")
ARQUIVOS_DA_RAIZ = ("componentes.json", "config.exemplo.toml", "overrides.txt", "LICENSE", "CREDITOS.md", "README.md")
LOCKS_OBRIGATORIOS = ("requirements-runtime.lock", "requirements-gpu.lock")
ARQUIVOS_DE_MUSICAS = ("catalogo.json", "licencas.txt", "COMO_ADICIONAR_MUSICAS.md")
ARQUIVOS_FORA_DO_PROGRAMA = ("ui/PEDIDOS_AO_NUCLEO.md",)        # o que `tools/exportar_publico.py` também deixa de fora dentro de cs2clip/ e ui/

# O que NUNCA entra no pacote (a lista do `tools/exportar_publico.py` mais o que é dado da pessoa). Conferido na montagem e de novo no fim.
PASTAS_PROIBIDAS = frozenset({".git", ".github", ".githooks", ".claude", ".venv", ".venv-ia", ".tmp_agentes", ".pytest_cache", ".pytest_tmp",
                              "__pycache__", "tests", "docs", "dados", "entrada", "demos", "gravacao_demo", "trabalhos", "backups",
                              "quarentena", "node_modules", "perfis_aprendidos"})
PREFIXOS_PROIBIDOS = ("saida",)                                  # saida, saida2, saida_teste...
EXTENSOES_PROIBIDAS = frozenset({".mp4", ".mkv", ".mov", ".webm", ".flv", ".ts", ".avi", ".m4v", ".wmv", ".mpg", ".mpeg", ".m2ts", ".3gp",
                                 ".dem", ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".bundle", ".pyc", ".pyo", ".bak", ".tmp",
                                 ".parcial", ".log", ".exe", ".dll", ".pem", ".key"})
NOMES_PROIBIDOS = frozenset({"claude.md", "config.toml", ".env", "pasta_dados.txt", "segredos_steam.json", "historico.json",
                             "catalogo_usuario.json", "ui.log", "terceiros.json", "pedidos_ao_nucleo.md"})
PADROES_PROIBIDOS = (re.compile(r"^config\.toml\."), re.compile(r"^config-.*\.tmp$"), re.compile(r"\.secret\."), re.compile(r"^client_secret"),
                     re.compile(r"^token.*\.json$"))
# Fora do pacote, dentro do app\: o .wav dos efeitos é gerado pelo montar.py (não é copiado) e o uv.exe vive na raiz do pacote.
EXCECOES_DO_PACOTE = frozenset({"uv.exe"})


def motivo_proibido(relativo: str) -> str | None:
    """Por que o caminho (relativo ao pacote, com / ou \\) não pode estar no pacote, ou None se pode."""
    partes = [p for p in re.split(r"[\\/]+", relativo) if p]
    for i, parte in enumerate(partes):
        baixo = parte.lower()
        ultimo = i == len(partes) - 1
        if baixo in PASTAS_PROIBIDAS:
            return f"pasta proibida ({parte})"
        if baixo.startswith(PREFIXOS_PROIBIDOS) and not ultimo:
            return f"pasta de saída ({parte})"
        if ultimo:
            if baixo in NOMES_PROIBIDOS:
                return f"arquivo proibido ({parte})"
            if any(pad.search(baixo) for pad in PADROES_PROIBIDOS):
                return f"arquivo proibido ({parte})"
            if baixo in EXCECOES_DO_PACOTE and len(partes) == 1:
                return None
            if Path(baixo).suffix in EXTENSOES_PROIBIDAS:
                return f"tipo de arquivo proibido ({Path(baixo).suffix})"
    return None


def verificar_pacote(pacote: Path) -> list[str]:
    """Varre o pacote pronto: cada arquivo proibido vira uma linha `caminho: motivo` (vazio = limpo). Link simbólico também é proibido."""
    pacote = Path(pacote)
    achados: list[str] = []
    for atual, pastas, arquivos in os.walk(pacote, followlinks=False):
        for nome in [*pastas, *arquivos]:
            caminho = Path(atual) / nome
            rel = caminho.relative_to(pacote).as_posix()
            if caminho.is_symlink():
                achados.append(f"{rel}: link simbólico")
            elif m := motivo_proibido(rel):
                achados.append(f"{rel}: {m}")
    return achados


def arquivos_rastreados(raiz: Path) -> set[str] | None:
    """Os arquivos que o git conhece (caminhos com /), ou None se não há git/repositório (aí vale só a lista de proibidos)."""
    if not (Path(raiz) / ".git").exists() or shutil.which("git") is None:
        return None
    try:
        topo = subprocess.run(["git", "-C", str(raiz), "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8",
                              timeout=60, check=False)
        if topo.returncode != 0 or Path(topo.stdout.strip()).resolve() != Path(raiz).resolve():
            return None                  # a pasta não é a raiz de um repositório (pode estar dentro de outro): não vale
        r = subprocess.run(["git", "-C", str(raiz), "ls-files", "-z"], capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return {p for p in r.stdout.decode("utf-8", errors="replace").split("\0") if p}


def _copiar_arquivo(origem: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origem, destino)


def _arquivos_da_pasta(raiz: Path, pasta: str, rastreados: set[str] | None) -> list[str]:
    """Caminhos (relativos à raiz, com /) de tudo que pode ir de `pasta`: sem proibidos, sem link simbólico, e (com git) só o que está no git."""
    achados: list[str] = []
    base = raiz / pasta
    for atual, pastas, arquivos in os.walk(base, followlinks=False):
        pastas[:] = sorted(p for p in pastas if not (Path(atual) / p).is_symlink() and motivo_proibido(p + "/x") is None)
        for nome in sorted(arquivos):
            caminho = Path(atual) / nome
            rel = caminho.relative_to(raiz).as_posix()
            if caminho.is_symlink() or rel in ARQUIVOS_FORA_DO_PROGRAMA or motivo_proibido(rel):
                continue
            if rastreados is not None and rel not in rastreados:
                continue
            achados.append(rel)
    return achados


def preparar_destino(destino: Path) -> None:
    """Cria a pasta do pacote. Se ela já existe, só a refaz se tiver cara de pacote antigo (só `app` e `uv.exe`): nunca apaga uma pasta qualquer."""
    destino = Path(destino)
    if destino.exists():
        if not destino.is_dir():
            raise ErroMontagem(f"{destino} existe e não é uma pasta")
        estranhos = [p.name for p in destino.iterdir() if p.name not in ("app", "uv.exe")]
        if estranhos:
            raise ErroMontagem(f"{destino} já existe e tem outras coisas ({', '.join(sorted(estranhos)[:5])}): escolha uma pasta nova ou vazia")
        for p in destino.iterdir():
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    destino.mkdir(parents=True, exist_ok=True)


def extrair_uv(zip_uv: Path, sha256: str, destino_exe: Path) -> Path:
    """Tira só o `uv.exe` do zip do uv (depois de conferir o hash do zip outra vez), por troca atômica. Nome de membro exato, sem pasta."""
    from cs2clip.tools import substituir_arquivo
    zip_uv = Path(zip_uv)
    if not zip_uv.is_file():
        raise ErroMontagem(f"o zip do uv não está no cache ({zip_uv.name}): rode com --baixar antes")
    if sha256_do_arquivo(zip_uv) != sha256.lower():
        raise ErroMontagem("o zip do uv no cache NÃO tem o hash do componentes.json: apague-o do cache e rode --baixar")
    with zipfile.ZipFile(zip_uv) as z:
        membro = next((i for i in z.infolist() if i.filename == "uv.exe"), None)
        if membro is None:
            raise ErroMontagem("o zip do uv não tem o uv.exe")
        if membro.file_size > LIMITE_UV_EXE:
            raise ErroMontagem("o uv.exe do zip é grande demais (possível bomba de descompressão); nada foi extraído")
        destino_exe.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino_exe.with_name(destino_exe.name + ".tmp")
        try:
            with z.open(membro) as o, open(tmp, "wb") as s:
                shutil.copyfileobj(o, s)
            substituir_arquivo(tmp, destino_exe)
        finally:
            tmp.unlink(missing_ok=True)
    return destino_exe


def gerar_sfx_padrao(pasta: Path) -> dict[str, Path]:
    """Gera os 4 `.wav` dos efeitos sonoros (determinísticos, CC0) em `pasta` com o gerador do próprio programa (`cs2clip.sfx`)."""
    from cs2clip.sfx import garantir_sfx
    return garantir_sfx(pasta)


def montar_pacote(raiz: Path, destino: Path, zip_uv: Path, sha256_uv: str, gerar_sfx: Callable[[Path], dict[str, Path]] | None = None) -> list[str]:
    """Monta `destino\\app\\` e `destino\\uv.exe`. Devolve os caminhos copiados (relativos ao pacote). Recusa se algo proibido entrar."""
    raiz = Path(raiz).resolve()
    destino = Path(destino).resolve()
    gerar_sfx = gerar_sfx_padrao if gerar_sfx is None else gerar_sfx
    faltando = [n for n in (*ARQUIVOS_DA_RAIZ, *LOCKS_OBRIGATORIOS, str(ARQUIVO_COMPONENTES).replace("\\", "/"),
                            *(f"musicas/{n}" for n in ARQUIVOS_DE_MUSICAS)) if not (raiz / n).is_file()]
    faltando += [p for p in PASTAS_DO_PROGRAMA if not (raiz / p).is_dir()]
    if faltando:
        raise ErroMontagem("faltam no projeto: " + ", ".join(faltando))
    rastreados = arquivos_rastreados(raiz)
    preparar_destino(destino)
    app = destino / "app"
    lista: list[str] = []
    origens: list[str] = []
    for pasta in PASTAS_DO_PROGRAMA:
        origens += _arquivos_da_pasta(raiz, pasta, rastreados)
    origens += list(ARQUIVOS_DA_RAIZ) + sorted(p.name for p in raiz.glob("requirements-*.lock")) + [str(ARQUIVO_COMPONENTES).replace("\\", "/")]
    origens += [f"musicas/{n}" for n in ARQUIVOS_DE_MUSICAS]
    origens += sorted(p.relative_to(raiz).as_posix() for p in (raiz / "bin").glob("*.txt")) if (raiz / "bin").is_dir() else []
    for rel in origens:
        origem = _dentro_de(raiz, raiz / rel)
        if origem.is_symlink() or not origem.is_file():
            raise ErroMontagem(f"{rel} não é um arquivo comum")
        if motivo := motivo_proibido(rel):
            raise ErroMontagem(f"{rel} não pode entrar no pacote: {motivo}")
        _copiar_arquivo(origem, _dentro_de(app, app / rel))
        lista.append(f"app/{rel}")
    extrair_uv(zip_uv, sha256_uv, destino / "uv.exe")
    lista.append("uv.exe")
    wavs = gerar_sfx(app / "cs2clip" / "assets" / "sfx")
    if len(wavs) != 4 or not all(Path(w).is_file() for w in wavs.values()):
        raise ErroMontagem("os 4 efeitos sonoros (.wav) não foram gerados")
    # Os .wav geram-se aqui de propósito: a verificação final os deixa passar só dentro de assets\sfx.
    achados = [a for a in verificar_pacote(destino) if not _wav_do_sfx(a)]
    if achados:
        raise ErroMontagem("o pacote tem coisa proibida (nada foi entregue ao instalador):\n  - " + "\n  - ".join(achados))
    lista += [f"app/cs2clip/assets/sfx/{Path(w).name}" for w in sorted(wavs.values(), key=str)]
    return lista


def _wav_do_sfx(achado: str) -> bool:
    caminho, _, motivo = achado.partition(": ")
    return caminho.startswith("app/cs2clip/assets/sfx/") and caminho.lower().endswith(".wav") and motivo.startswith("tipo de arquivo proibido")


# ── o compilador (Inno Setup) ─────────────────────────────────────────────────────────────────────
def achar_iscc(explicito: str | None = None, ambiente: dict[str, str] | None = None, which: Callable[[str], str | None] = shutil.which) -> Path:
    """O `ISCC.exe`: o caminho dado, a variável ISCC, o PATH ou as pastas padrão do Inno (6 e 7, por usuário ou todos)."""
    ambiente = dict(os.environ) if ambiente is None else ambiente
    candidatos: list[str] = []
    for c in (explicito, ambiente.get("ISCC")):
        if c:
            candidatos.append(c)
    if (w := which("ISCC")):
        candidatos.append(w)
    for base in (ambiente.get("LOCALAPPDATA", ""), ambiente.get("ProgramFiles(x86)", ""), ambiente.get("ProgramFiles", "")):
        if base:
            for v in ("Inno Setup 7", "Inno Setup 6"):
                candidatos += [str(Path(base) / "Programs" / v / "ISCC.exe"), str(Path(base) / v / "ISCC.exe")]
    for c in candidatos:
        p = Path(c)
        if p.name.lower() == "iscc.exe" and p.is_file():
            return p
    raise ErroMontagem("não achei o ISCC.exe (compilador do Inno Setup): instale o Inno Setup, ponha-o no PATH ou use --iscc <caminho>")


def argumentos_do_iscc(iscc: Path, iss: Path, pacote: Path, versao: str, saida: Path, ffmpeg: Baixavel, guid: str | None) -> list[str]:
    """A lista de argumentos (nunca texto de shell). `guid` sem chaves: aqui vira `{{GUID}` (a barra dupla do Inno escapa a chave de abertura).
    Todo `#ifndef X` + `#error` do `.iss` tem o seu `/DX=` aqui (o teste cruza as duas listas)."""
    if not VERSAO.match(versao):
        raise ErroMontagem(f"versão inválida para o instalador: {versao!r} (esperado x.y.z)")
    if (url_permitida(ffmpeg.url) or not ffmpeg.url.startswith(PREFIXO_URL_FFMPEG) or not SHA256.match(ffmpeg.sha256)
            or not _inteiro_positivo(ffmpeg.tamanho_extraido)):
        raise ErroMontagem("dados do FFmpeg inválidos para o instalador (url da release do GitHub, sha256 e tamanho_extraido do componentes.json)")
    for nome, caminho in (("pacote", pacote), ("saída", saida), (".iss", iss)):
        t = str(caminho)
        if '"' in t or "\x00" in t or t.endswith(("\\", "/")):
            raise ErroMontagem(f"caminho de {nome} com caractere não aceito: {t!r}")
    args = [str(iscc), f"/DAppVersion={versao}", f"/DPacote={pacote}", f"/DFFmpegUrl={ffmpeg.url}", f"/DFFmpegSha256={ffmpeg.sha256.lower()}",
            f"/DFFmpegTamanhoExtraido={ffmpeg.tamanho_extraido}"]
    if guid is not None:
        if not GUID.match(guid):
            raise ErroMontagem(f"AppId inválido: {guid!r} (esperado 8-4-4-4-12 hexadecimais, sem chaves)")
        args.append(f"/DAppIdGuid={{{{{guid.upper()}}}")
    return args + [f"/O{saida}", str(iss)]


def compilar(raiz: Path, pacote: Path, versao: str, saida: Path, ffmpeg: Baixavel, guid: str | None = None, iscc: str | None = None,
             executar: Callable[..., Any] = subprocess.run) -> Path:
    """Roda o ISCC sobre `instalador/BisturiClips.iss` (o FFmpeg vem do componentes.json). Devolve a pasta de saída. O instalador gerado NUNCA
    é executado."""
    iss = Path(raiz) / ARQUIVO_ISS
    if not iss.is_file():
        raise ErroMontagem(f"não achei {ARQUIVO_ISS} (o script do instalador)")
    if not (Path(pacote) / "app").is_dir() or not (Path(pacote) / "uv.exe").is_file():
        raise ErroMontagem(f"{pacote} não parece um pacote montado (faltam app\\ ou uv.exe): rode --pacote antes")
    args = argumentos_do_iscc(achar_iscc(iscc), iss, Path(pacote), versao, Path(saida), ffmpeg, guid)
    Path(saida).mkdir(parents=True, exist_ok=True)
    r = executar(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800, check=False)
    if r.returncode != 0:
        fim = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-15:])
        raise ErroMontagem(f"o ISCC falhou (código {r.returncode}):\n{fim}")
    return Path(saida)


# ── linha de comando ──────────────────────────────────────────────────────────────────────────────
def validar_tudo(raiz: Path = RAIZ) -> Componentes:
    """`--validar`: componentes.json, catálogo de músicas e terceiros. Sem rede. Levanta ErroMontagem com todos os problemas."""
    comp = ler_componentes(Path(raiz) / ARQUIVO_COMPONENTES)
    problemas = conferir_catalogo(comp, raiz) + conferir_terceiros()
    if problemas:
        raise ErroMontagem("a conferência dos dados achou problemas:\n  - " + "\n  - ".join(problemas))
    return comp


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="montar.py", description="Monta o instalador do Bisturi Clips (confere, baixa com hash, monta o pacote, compila).")
    ap.add_argument("--validar", action="store_true", help="só confere os dados (componentes.json, catálogo, terceiros); sem rede")
    ap.add_argument("--baixar", action="store_true", help="traz uv e FFmpeg para o cache, conferindo o SHA-256")
    ap.add_argument("--pacote", metavar="PASTA", help="monta a pasta de pacote (dentro do projeto, fora das pastas de código)")
    ap.add_argument("--compilar", action="store_true", help="chama o ISCC sobre instalador/BisturiClips.iss (precisa de --pacote)")
    ap.add_argument("--cache", metavar="PASTA", default=str(CACHE_PADRAO), help=f"pasta de cache dos downloads (padrão {CACHE_PADRAO})")
    ap.add_argument("--saida", metavar="PASTA", default=str(SAIDA_PADRAO), help=f"onde o ISCC grava o Setup (padrão {SAIDA_PADRAO})")
    ap.add_argument("--guid", metavar="GUID", help="AppId do instalador (8-4-4-4-12, sem chaves); sem ele vale o do .iss. Use outro nos testes.")
    ap.add_argument("--iscc", metavar="CAMINHO", help="caminho do ISCC.exe (padrão: variável ISCC, PATH, pastas do Inno)")
    return ap


def main(argv: list[str] | None = None, raiz: Path = RAIZ, abrir: Callable[..., Any] | None = None, executar: Callable[..., Any] = subprocess.run) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(fluxo, "reconfigure", None)
        if reconfigurar is not None:
            reconfigurar(encoding="utf-8", errors="replace")
    ap = _parser()
    a = ap.parse_args(argv)
    if not (a.validar or a.baixar or a.pacote or a.compilar):
        ap.error("diga o que fazer: --validar, --baixar, --pacote PASTA e/ou --compilar")
    if a.compilar and not a.pacote:
        ap.error("--compilar precisa de --pacote PASTA")
    try:
        print("[conferindo os dados]", flush=True)
        comp = validar_tudo(raiz)
        print(f"  ok: uv {comp.uv.versao}, FFmpeg {comp.ffmpeg.versao}, {len(comp.faixas)} músicas, {len(comp.fora)} de fora", flush=True)
        cache = pasta_do_projeto(a.cache, raiz, "o cache") if (a.baixar or a.pacote) else None
        if a.baixar and cache is not None:
            print("[baixando para o cache]", flush=True)
            baixar_componentes(comp, cache, abrir)
        if a.pacote and cache is not None:
            destino = pasta_do_projeto(a.pacote, raiz, "o pacote")
            print(f"[montando o pacote em {destino}]", flush=True)
            zip_uv = cache / nome_do_cache(comp.uv)
            lista = montar_pacote(raiz, destino, zip_uv, comp.uv.sha256)
            print(f"  ok: {len(lista)} arquivos", flush=True)
            if a.compilar:
                from cs2clip import __version__
                print("[compilando com o ISCC]", flush=True)
                saida = pasta_do_projeto(a.saida, raiz, "a saída")
                print(f"  pronto: {compilar(raiz, destino, __version__, saida, comp.ffmpeg, a.guid, a.iscc, executar)}", flush=True)
    except ErroMontagem as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
