"""B1: achar sozinho a demo (.dem) da partida na pasta do CS2 e casar com a gravação do OBS.

O dono só clica em "baixar" no CS2 (Assistir → Suas partidas). O Bisturi:
  1. acha a pasta `...\\game\\csgo\\replays` pela instalação do Steam (só LÊ os caminhos de `libraryfolders.vdf`);
  2. lista as demos `match730_*.dem` com a data da partida (arquivo `.dem.info` do lado, ou a data do arquivo);
  3. escolhe as que caem na hora da gravação (o nome do arquivo do OBS traz o início);
  4. COPIA a escolhida para dentro do projeto (`demos\\`), conferindo o hash. A original nunca é movida nem apagada;
  5. descobre quem é o dono na demo comparando os abates de cada jogador com os do kill feed (sem pedir o nome).

Nada aqui abre o CS2, o Steam ou o OBS, nem lê segredos (config.vdf/loginusers.vdf ficam intocados).
A confirmação final de que a demo é DESTA gravação é a sincronização de `cs2clip/sincronizar.py`: sem sincronia confiável, a demo é descartada.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .caminhos import PASTA_DADOS
from .tools import gravar_atomico, substituir_arquivo

PASTA_DEMOS_PROJETO = PASTA_DADOS / "demos"
PADRAO_DEMO = "match730_*.dem"
STEAM_PADRAO = (Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam"))
RELATIVO_REPLAYS = Path("steamapps") / "common" / "Counter-Strike Global Offensive" / "game" / "csgo" / "replays"
FOLGA_ANTES_S = 90 * 60     # a data da demo pode ser o início ou o fim da partida: aceita até 90 min antes do início da gravação...
FOLGA_DEPOIS_S = 30 * 60    # ...e até 30 min depois do fim dela
MIN_ABATES_PARA_DONO = 3    # jogador com menos abates que isto na demo não é testado como "o dono"


# ── pasta do CS2 ─────────────────────────────────────────────────────────────────────────────────

def bibliotecas_steam(steam: Path | None = None) -> list[Path]:
    """Pastas de biblioteca do Steam, lidas de `libraryfolders.vdf` (só os caminhos "path"; nada mais do arquivo é usado)."""
    raizes = [steam] if steam else [p for p in STEAM_PADRAO if p.is_dir()]
    saida: list[Path] = []
    for raiz in raizes:
        saida.append(raiz)
        vdf = raiz / "steamapps" / "libraryfolders.vdf"
        try:
            texto = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for achado in re.findall(r'"path"\s+"([^"]+)"', texto):
            caminho = Path(achado.replace("\\\\", "\\"))
            if caminho not in saida:
                saida.append(caminho)
    return saida


def pasta_replays(steam: Path | None = None) -> Path | None:
    """`...\\game\\csgo\\replays` da primeira biblioteca que tem o CS2, ou None."""
    for biblioteca in bibliotecas_steam(steam):
        pasta = biblioteca / RELATIVO_REPLAYS
        if pasta.is_dir():
            return pasta
    return None


# ── datas ───────────────────────────────────────────────────────────────────────────────────────

def _varint(b: bytes, i: int) -> tuple[int, int] | None:
    v = s = 0
    while i < len(b):
        x = b[i]
        i += 1
        v |= (x & 0x7F) << s
        s += 7
        if not x & 0x80:
            return v, i
        if s > 63:
            return None
    return None


def ler_matchtime(info: Path) -> datetime.datetime | None:
    """Data da partida lida do `.dem.info` (protobuf `CDataGCCStrike15_v2_MatchInfo`: campo 2 = `matchtime`, segundos desde 1970).
    Percorre só os campos de nível alto; qualquer coisa fora do formato devolve None (nunca levanta erro)."""
    try:
        b = Path(info).read_bytes()
    except OSError:
        return None
    if b[:1] == b"{":                                  # `.dem.info` gravado pelo Bisturi (D5): JSON com `matchtime`
        try:
            valor = json.loads(b.decode("utf-8")).get("matchtime")
        except (ValueError, UnicodeDecodeError, AttributeError):
            return None
        return datetime.datetime.fromtimestamp(valor) if isinstance(valor, int) and 1_500_000_000 < valor < 2_500_000_000 else None
    i = 0
    while i < len(b):
        lido = _varint(b, i)
        if lido is None:
            return None
        tag, i = lido
        campo, tipo = tag >> 3, tag & 7
        if tipo == 0:
            lido = _varint(b, i)
            if lido is None:
                return None
            valor, i = lido
            if campo == 2 and 1_500_000_000 < valor < 2_500_000_000:
                return datetime.datetime.fromtimestamp(valor)
        elif tipo == 1:
            i += 8
        elif tipo == 5:
            i += 4
        elif tipo == 2:
            lido = _varint(b, i)
            if lido is None:
                return None
            tam, i = lido
            i += tam
        else:
            return None
    return None


@dataclass(frozen=True)
class DemoCS2:
    caminho: Path
    quando: datetime.datetime
    aproximada: bool          # True = sem `.dem.info`: a data é a do arquivo (quando foi baixado), não a da partida


def listar_demos(pasta: Path | None = None, padrao: str = PADRAO_DEMO) -> list[DemoCS2]:
    """`padrao`: o das partidas do CS2 (`match730_*.dem`); a tela de gravação usa `*.dem` na pasta `demos\\` do Bisturi
    (demos de campeonato baixadas da HLTV, com nomes como `timea-vs-timeb-m1-ancient.dem`)."""
    pasta = pasta or pasta_replays()
    if pasta is None or not Path(pasta).is_dir():
        return []
    saida: list[DemoCS2] = []
    for dem in sorted(Path(pasta).glob(padrao)):
        quando = ler_matchtime(dem.with_name(dem.name + ".info"))
        aproximada = quando is None
        if quando is None:
            try:
                quando = datetime.datetime.fromtimestamp(dem.stat().st_mtime)
            except OSError:
                continue
        saida.append(DemoCS2(dem, quando, aproximada))
    return sorted(saida, key=lambda d: d.quando)


_NOME_OBS = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})-(\d{2})-(\d{2})")


def inicio_da_gravacao(video: Path, duracao_s: float = 0.0) -> datetime.datetime | None:
    """Início da gravação: o nome padrão do OBS (`2026-11-04 21-39-10.mp4`) ou, sem ele, a data do arquivo menos a duração."""
    achado = _NOME_OBS.search(Path(video).stem)
    if achado:
        try:
            ano, mes, dia, hora, minuto, segundo = (int(x) for x in achado.groups())   # o padrão tem 6 grupos
            return datetime.datetime(ano, mes, dia, hora, minuto, segundo)
        except ValueError:
            pass
    try:
        fim = datetime.datetime.fromtimestamp(Path(video).stat().st_mtime)
    except OSError:
        return None
    return fim - datetime.timedelta(seconds=max(0.0, duracao_s))


# O nome do VOD no começo: `<id>-twitch` (baixado pelo Bisturi) e `<id>-<id do canal>-<uuid>` (baixado pelo painel da Twitch), com ` (1)` de cópia repetida.
# Só esses dois: `<id>-<palavra qualquer>` pegaria arquivos do dono que só começam com um número de 9 a 11 dígitos (`2026111311-gameplay.mp4`).
_VOD_TWITCH = re.compile(r"^([0-9]{9,11})-(?:twitch|[0-9]{6,12}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?: \([0-9]+\))?$", re.IGNORECASE)
LIMITE_DEMOS_DE_VOD = 6      # sem a hora da live: quantas demos recentes testar (cada teste lê a demo: 1 a 20 s)
LIMITE_DEMOS_COM_HORA = 8    # com a hora da live: no máximo quantas partidas de uma mesma live
FOLGA_ANTES_VOD_S = 75 * 60  # a hora da demo é o INÍCIO da partida: uma que começou até 75 min antes da live ainda aparece no começo dela
PASTA_CACHE_VOD = PASTA_DADOS / "dados" / "vod_info"      # `dados\` nunca vai para o git
_SEM_RESPOSTA: set[tuple[str, str]] = set()      # (pasta do cache, VOD) que já falharam neste processo: não insiste a cada chamada


def vod_id_do_arquivo(video: Path) -> str | None:
    """Número do VOD da Twitch no começo do nome do arquivo (`1234567890-9876543210-0a1b2c3d-….mp4`, `1234567890-twitch.mp4`); None se não for nome de VOD."""
    achado = _VOD_TWITCH.match(Path(video).stem)
    return achado.group(1) if achado else None


def e_vod_da_twitch(video: Path) -> bool:
    return vod_id_do_arquivo(video) is not None


def inicio_do_vod(video: Path, pasta_cache: Path | None = None, consultar: bool = True) -> datetime.datetime | None:
    """M3: a hora REAL (local) em que a live começou, perguntada à Twitch uma vez por VOD e guardada em `dados\\vod_info\\<id>.json`. A data do arquivo é a do
    download e engana. `consultar=False` (chat da Twitch desligado: o mesmo interruptor de "falar com a Twitch") só usa o que já estiver guardado.
    Sem internet ou sem resposta: None (e não tenta de novo neste processo)."""
    vod = vod_id_do_arquivo(video)
    if vod is None:
        return None
    pasta = Path(pasta_cache) if pasta_cache else PASTA_CACHE_VOD
    arq = pasta / f"{vod}.json"
    info = None
    try:
        lido = json.loads(arq.read_text(encoding="utf-8"))
        if isinstance(lido, dict) and isinstance(lido.get("inicio_utc"), str) and _hora_local_do_cache(lido["inicio_utc"]) is not None:
            info = lido
    except (OSError, ValueError):
        info = None
    if info is None:
        chave = (str(pasta), vod)
        if not consultar or chave in _SEM_RESPOSTA:
            return None
        from . import twitch_vod
        info = twitch_vod.info_do_vod(vod)
        if info is None:
            _SEM_RESPOSTA.add(chave)
            return None
        try:
            pasta.mkdir(parents=True, exist_ok=True)
            gravar_atomico(arq, json.dumps(info))
        except OSError:
            pass                                    # sem cache a consulta só se repete na próxima análise
    return _hora_local_do_cache(info["inicio_utc"])


def _hora_local_do_cache(texto: str) -> datetime.datetime | None:
    """`2026-11-14T00:35:15+00:00` -> hora local (naive), como a hora das demos. Sem fuso (leria 3 h errado), data absurda ou texto ruim: None."""
    try:
        quando = datetime.datetime.fromisoformat(texto)
        if quando.tzinfo is None or not 2010 <= quando.year <= 2100:
            return None
        return quando.astimezone().replace(tzinfo=None)
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def candidatas(video: Path, duracao_s: float, demos: list[DemoCS2], inicio_vod: datetime.datetime | None = None) -> list[DemoCS2]:
    """Demos cuja data cai na gravação (com folga), da mais próxima do meio dela para a mais distante.
    VOD baixado da Twitch: a data do arquivo é a do download, não a da live. Com `inicio_vod` (a hora real da live, ver `inicio_do_vod`), são candidatas só as
    partidas que começaram dentro da live (ou até 75 min antes), em ordem de hora: uma live com 2 partidas devolve as 2. Sem ela, as mais NOVAS.
    Quem confirma é a conferência por conteúdo (`escolher_dono`): demo que não casa com o kill feed é descartada."""
    if e_vod_da_twitch(video):
        if inicio_vod is None:
            return sorted(demos, key=lambda d: d.quando, reverse=True)[:LIMITE_DEMOS_DE_VOD]
        fim_vod = inicio_vod + datetime.timedelta(seconds=duracao_s)
        dentro = [d for d in demos if inicio_vod - datetime.timedelta(seconds=FOLGA_ANTES_VOD_S) <= d.quando <= fim_vod]
        return sorted(dentro, key=lambda d: d.quando)[:LIMITE_DEMOS_COM_HORA]
    inicio = inicio_da_gravacao(video, duracao_s)
    if inicio is None:
        return []
    fim = inicio + datetime.timedelta(seconds=duracao_s)
    ini_ok = inicio - datetime.timedelta(seconds=FOLGA_ANTES_S)
    fim_ok = fim + datetime.timedelta(seconds=FOLGA_DEPOIS_S)
    meio = inicio + (fim - inicio) / 2
    dentro = [d for d in demos if ini_ok <= d.quando <= fim_ok]
    return sorted(dentro, key=lambda d: abs((d.quando - meio).total_seconds()))


# ── cópia segura para dentro do projeto ──────────────────────────────────────────────────────────

def _sha256(arq: Path) -> str:
    h = hashlib.sha256()
    with open(arq, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def copiar_para_projeto(demo: Path, destino: Path | None = None) -> Path:
    """Copia a demo (e o `.dem.info`) para `destino` e confere o hash. A original fica onde está. Se a cópia já existe e é igual, reaproveita."""
    destino = Path(destino) if destino else PASTA_DEMOS_PROJETO
    destino.mkdir(parents=True, exist_ok=True)
    demo = Path(demo)
    copia = destino / demo.name
    if not (copia.is_file() and copia.stat().st_size == demo.stat().st_size and _sha256(copia) == _sha256(demo)):
        parcial = copia.with_name(copia.name + ".parcial")
        shutil.copy2(demo, parcial)
        if _sha256(parcial) != _sha256(demo):
            parcial.unlink(missing_ok=True)
            raise OSError(f"A cópia de {demo.name} não bateu com o original (hash diferente); nada foi mantido.")
        substituir_arquivo(parcial, copia)
    info = demo.with_name(demo.name + ".info")
    if info.is_file() and info.resolve() != (destino / info.name).resolve():   # G-016: já está na pasta de destino = é o mesmo arquivo
        shutil.copy2(info, destino / info.name)
    return copia


def demos_da_gravacao(video: Path, duracao_s: float, pasta: Path | None = None, consultar_twitch: bool = True,
                      pasta_cache: Path | None = None) -> list[Path]:
    """Demos do CS2 que podem ser desta gravação, JÁ COPIADAS para `demos\\` do projeto. Lista vazia = não achou nada.
    `consultar_twitch`: para VOD da Twitch, pergunta (uma vez) a hora real da live; desligue com o chat da Twitch desligado."""
    demos = listar_demos(pasta)
    if e_vod_da_twitch(video):
        inicio = inicio_do_vod(video, pasta_cache, consultar_twitch)
        achadas = candidatas(video, duracao_s, demos, inicio_vod=inicio)
        return [d.caminho for d in achadas]          # são só candidatas a testar (várias x ~260 MB): leitura direta, sem copiar para o projeto
    achadas = candidatas(video, duracao_s, demos)
    return [copiar_para_projeto(d.caminho) for d in achadas]


# ── quem é o dono na demo ────────────────────────────────────────────────────────────────────────

@dataclass
class Dono:
    nome: str
    steamid: int
    casados: int
    total_demo: int
    residuo: float
    confianca: str


def escolher_dono(demo: Path, tempos_video: list[float], quem: str | int | None = None, motivos: list[str] | None = None) -> Dono | None:
    """Quem, entre os jogadores da demo, é o dono da gravação: o que tem mais abates casando com o kill feed do vídeo.
    Com `quem` dado, só ele é testado. Sem sincronia confiável (≥ média) para ninguém, devolve None (a demo não é desta gravação).
    `motivos` (opcional): se a demo nem abriu, o texto do erro entra aqui, para o chamador não dizer "não é desta gravação" de uma demo ilegível."""
    from .demo import DemoInvalida, ler_demo, listar_jogadores
    from .sincronizar import estimar
    if quem:
        nomes = [(None, quem)]
    else:
        try:
            jogadores = listar_jogadores(demo)
        except DemoInvalida as e:
            if motivos is not None:
                motivos.append(str(e))
            return None
        nomes = [(n, s) for n, s, k in jogadores if k >= MIN_ABATES_PARA_DONO]
    melhor: Dono | None = None
    for nome, ident in nomes:
        try:
            partida = ler_demo(demo, ident)
        except DemoInvalida as e:
            if motivos is not None:
                motivos.append(str(e))
            continue
        if partida.jogador is None or not partida.abates:
            continue
        sinc = estimar([a.t for a in partida.abates], list(tempos_video))
        if sinc is None or not sinc.confiavel:
            continue
        cand = Dono(partida.nome or str(nome), int(partida.jogador), sinc.casados, sinc.total_demo, sinc.residuo, sinc.confianca)
        if melhor is None or (cand.casados, -cand.residuo) > (melhor.casados, -melhor.residuo):
            melhor = cand
    return melhor
