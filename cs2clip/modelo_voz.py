"""O modelo de voz (Whisper) no PC: está aqui? quanto já veio? baixa com progresso, cancelamento e retomada (P4.2 do plano de publicação).

Só cuida dos modelos listados em `componentes.json` (seção `modelos_de_voz`), cada um numa `revision` (commit) FIXA do Hugging Face e com o
tamanho de cada arquivo. Nome que não está lá (tiny, base, medium...) é "não gerenciado": o chamador faz como sempre fez (o faster-whisper
baixa pelo nome).

Onde mora: no cache do Hugging Face, no mesmo desenho que a biblioteca usa (`models--<dono>--<nome>/snapshots/<revision>/<arquivo>`), para o
modelo que a pessoa já tem continuar valendo. No modo instalado o cache fica em `<dados>\\modelos` (o `caminhos.py` define o HF_HOME).

Por que não `snapshot_download`/`hf_hub_download`: no huggingface_hub 2.2.0 eles baixam num arquivo temporário de nome único e o APAGAM em
qualquer falha (não há retomada entre uma tentativa e outra) e só contam o progresso a cada 10 MB (a 0,5 MB/s a barra ficaria parada 20 s).
Aqui cada arquivo vem pela sessão HTTP do próprio huggingface_hub (`get_session`, mesmo proxy e mesmo modo offline), com `Range`, para
`blobs/<hash>.incomplete`; cancelado ou com a rede caída, o `.incomplete` fica e a próxima chamada continua do byte em que parou. Inteiro,
o arquivo tem o hash conferido e só então vai para o snapshot (link simbólico para o blob ou, no Windows sem esse direito, o próprio arquivo,
exatamente como o huggingface_hub faz).
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import caminhos
from .tools import substituir_arquivo

ARQUIVO_COMPONENTES = caminhos.RAIZ_PROGRAMA / "componentes.json"

INTERVALO_AVISO_S = 0.5          # `ao_progredir` no máximo 2 vezes por segundo
JANELA_VELOCIDADE_S = 5.0        # a velocidade é a média dos últimos 5 s (não salta a cada pedaço)
ESPERA_CANCELAR_S = 0.1          # de quanto em quanto tempo o `baixar` olha o pedido de cancelar
PEDACO_BYTES = 256 * 1024        # o que se lê da rede de cada vez (o trabalho de fundo confere o "parar" entre um e outro)
TENTATIVAS = 4                   # falhas seguidas da rede (sem nenhum byte novo) antes de desistir
ESPERA_ENTRE_TENTATIVAS_S = 2.0  # espera n × isto antes da tentativa n+1
MARGEM_DISCO_BYTES = 100 * 1024 * 1024
PRAZO_REDE_S = 15.0              # sem receber nada por isto, a conexão conta como caída (e tenta de novo)

_NOME_ARQUIVO = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_REPO = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")

AoProgredir = Callable[[int, int, float, float | None], None]


class ErroDownloadModelo(RuntimeError):
    """O download do modelo de voz não deu certo; a mensagem é para a pessoa ler (o que houve e o que fazer)."""


class DownloadCancelado(ErroDownloadModelo):
    """A pessoa cancelou. O que já veio fica guardado: a próxima chamada de `baixar` continua de onde parou."""


class ModeloNaoGerenciado(ValueError):
    """O nome não está no `componentes.json`: baixe e carregue como sempre (pelo nome, com o faster-whisper)."""


@dataclass(frozen=True)
class EstadoModelo:
    nome: str
    presente: bool | None            # None = não gerenciado (o módulo não sabe; o chamador faz como sempre)
    tamanho_bytes: int
    baixado_bytes: int               # o que já está no PC, incluindo os `.incomplete`
    pasta: Path | None               # a pasta do snapshot da revisão fixada (existindo ou não)
    revisao: str | None
    outras_revisoes: tuple[str, ...] = ()   # outros snapshots do mesmo repositório no cache (só os nomes das pastas)

    @property
    def gerenciado(self) -> bool:
        return self.presente is not None


@dataclass(frozen=True)
class _Arquivo:
    nome: str
    tamanho: int
    sha256: str | None               # arquivo grande (LFS)
    git_blob: str | None             # arquivo pequeno: o SHA-1 do Git

    @property
    def blob(self) -> str:
        """O nome do arquivo em `blobs/` (o mesmo que o huggingface_hub usa: o sha256 do LFS ou o SHA-1 do Git)."""
        return self.sha256 or self.git_blob or ""


@dataclass(frozen=True)
class _Modelo:
    nome: str
    repo: str
    revisao: str
    arquivos: tuple[_Arquivo, ...]

    @property
    def tamanho(self) -> int:
        return sum(a.tamanho for a in self.arquivos)


# ── catálogo ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def modelos_gerenciados() -> list[str]:
    return list(_ler_catalogo())


def _ler_catalogo() -> dict:
    dados = json.loads(Path(ARQUIVO_COMPONENTES).read_text(encoding="utf-8"))
    secao = dados.get("modelos_de_voz") if isinstance(dados, dict) else None
    if not isinstance(secao, dict):
        raise ValueError(f"{ARQUIVO_COMPONENTES}: falta a seção modelos_de_voz")
    return secao


def _modelo(nome: str) -> _Modelo | None:
    """O modelo do catálogo, já conferido (nomes de arquivo sem pasta, revisão de 40 caracteres). None = não gerenciado."""
    e = _ler_catalogo().get(nome)
    if e is None:
        return None
    repo, revisao, arquivos = e.get("repo"), e.get("revision"), e.get("arquivos")
    if not (isinstance(repo, str) and _REPO.fullmatch(repo) and ".." not in repo):
        raise ValueError(f"componentes.json, modelo {nome}: repo inválido")
    if not (isinstance(revisao, str) and _HEX40.fullmatch(revisao)):
        raise ValueError(f"componentes.json, modelo {nome}: revision tem de ser o commit completo (40 caracteres)")
    if not (isinstance(arquivos, dict) and arquivos):
        raise ValueError(f"componentes.json, modelo {nome}: sem arquivos")
    lista = []
    for arq, info in arquivos.items():
        if not (isinstance(arq, str) and _NOME_ARQUIVO.fullmatch(arq) and arq not in (".", "..")):
            raise ValueError(f"componentes.json, modelo {nome}: nome de arquivo inválido {arq!r}")
        tamanho, sha256, git_blob = info.get("tamanho_bytes"), info.get("sha256"), info.get("git_blob")
        if not (isinstance(tamanho, int) and tamanho > 0):
            raise ValueError(f"componentes.json, modelo {nome}, {arq}: tamanho_bytes inválido")
        if not ((isinstance(sha256, str) and _HEX64.fullmatch(sha256)) or (isinstance(git_blob, str) and _HEX40.fullmatch(git_blob))):
            raise ValueError(f"componentes.json, modelo {nome}, {arq}: falta o sha256 ou o git_blob")
        lista.append(_Arquivo(arq, tamanho, sha256 if isinstance(sha256, str) else None, git_blob if isinstance(git_blob, str) else None))
    return _Modelo(nome, repo, revisao, tuple(lista))


# ── onde fica no cache ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _cache_padrao() -> Path:
    """O cache do Hugging Face que o faster-whisper usa (HF_HUB_CACHE; no modo instalado, `<dados>\\modelos\\hub`)."""
    import huggingface_hub.constants as k
    return Path(k.HF_HUB_CACHE)


def _cache(cache_dir: Path | str | None) -> Path:
    return Path(cache_dir) if cache_dir is not None else _cache_padrao()


def _pasta_repo(cache: Path, m: _Modelo) -> Path:
    return cache / ("models--" + m.repo.replace("/", "--"))


def _snapshot(cache: Path, m: _Modelo) -> Path:
    return _pasta_repo(cache, m) / "snapshots" / m.revisao


def _incompleto(cache: Path, m: _Modelo, a: _Arquivo) -> Path:
    return _pasta_repo(cache, m) / "blobs" / f"{a.blob}.incomplete"


def _tamanho(caminho: Path) -> int | None:
    """Tamanho do arquivo (seguindo link simbólico), ou None se não existe. Só `stat`: o conteúdo não é lido."""
    try:
        return caminho.stat().st_size if caminho.is_file() else None
    except OSError:
        return None


def _completo(caminho: Path, a: _Arquivo) -> bool:
    return _tamanho(caminho) == a.tamanho


def _ja_veio(cache: Path, m: _Modelo, a: _Arquivo) -> int:
    if _completo(_snapshot(cache, m) / a.nome, a) or _completo(_pasta_repo(cache, m) / "blobs" / a.blob, a):
        return a.tamanho
    return min(_tamanho(_incompleto(cache, m, a)) or 0, a.tamanho)


def _outras_revisoes(cache: Path, m: _Modelo) -> tuple[str, ...]:
    try:
        return tuple(sorted(p.name for p in (_pasta_repo(cache, m) / "snapshots").iterdir() if p.is_dir() and p.name != m.revisao))
    except OSError:
        return ()


# ── o que o resto do programa chama ─────────────────────────────────────────────────────────────────────────────────────────────────
def estado(nome: str, cache_dir: Path | None = None) -> EstadoModelo:
    """Presente = todos os arquivos da revisão fixada estão no snapshot com o tamanho certo (só `stat`, sem ler o conteúdo)."""
    m = _modelo(nome)
    if m is None:
        return EstadoModelo(nome, None, 0, 0, None, None)
    cache = _cache(cache_dir)
    snap = _snapshot(cache, m)
    presente = all(_completo(snap / a.nome, a) for a in m.arquivos)
    baixado = sum(_ja_veio(cache, m, a) for a in m.arquivos)
    return EstadoModelo(nome, presente, m.tamanho, baixado, snap, m.revisao, _outras_revisoes(cache, m))


def caminho_do_modelo(nome: str, cache_dir: Path | None = None) -> Path | None:
    """A pasta do snapshot da revisão fixada, SE está completa; senão None (não gerenciado, faltando, ou em outra revisão).
    Nunca levanta erro: quem carrega o modelo cai no comportamento de sempre."""
    try:
        e = estado(nome, cache_dir)
    except (OSError, ValueError):
        return None
    return e.pasta if e.presente else None


def em_andamento(nome: str | None = None) -> bool:
    """Há um download de fundo vivo (deste modelo, ou de qualquer um)? Depois de cancelar, o trabalho de fundo ainda pode levar alguns
    segundos para soltar a conexão: quem fecha o programa ou recomeça olha isto."""
    with _TRAVA_TRABALHOS:
        return any(t.is_alive() and (nome is None or t.nome == nome) for t in _TRABALHOS)


def baixar(nome: str, ao_progredir: AoProgredir | None = None, cancelar: threading.Event | None = None,
           cache_dir: Path | None = None) -> Path:
    """Baixa o que falta do modelo e devolve a pasta do snapshot. `ao_progredir(baixado_bytes, total_bytes, bytes_por_segundo,
    segundos_restantes_ou_None)` é chamado no máximo 2 vezes por segundo, nesta mesma thread. `cancelar.set()` faz levantar
    `DownloadCancelado` em até 1 s, sem apagar o que já veio. Problemas viram `ErroDownloadModelo` com mensagem para a pessoa."""
    m = _modelo(nome)
    if m is None:
        raise ModeloNaoGerenciado(f"o modelo {nome} não está no componentes.json")
    cache = _cache(cache_dir)
    pasta = _snapshot(cache, m)
    ja = sum(_ja_veio(cache, m, a) for a in m.arquivos)
    if all(_completo(pasta / a.nome, a) for a in m.arquivos):
        _chamar(ao_progredir, m.tamanho, m.tamanho, 0.0, 0.0)
        return pasta
    if _offline():
        raise ErroDownloadModelo(f"O modelo de voz {nome} ainda não está neste PC e o modo sem internet do Hugging Face (HF_HUB_OFFLINE) está "
                                 "ligado. Desligue-o para baixar o modelo.")
    _conferir_espaco(cache, m.tamanho - ja)
    cancelar = cancelar or threading.Event()
    trabalho = _Trabalho(m, cache, ja)
    with _TRAVA_TRABALHOS:
        _TRABALHOS[:] = [t for t in _TRABALHOS if t.is_alive()] + [trabalho]
    trabalho.start()
    medidor = MedidorVelocidade()
    agora = time.monotonic()
    medidor.registrar(agora, 0)
    medidor.hora_de_avisar(agora)
    _chamar(ao_progredir, ja, m.tamanho, 0.0, None)    # o primeiro aviso já sai: o que havia no disco (a retomada aparece na hora)
    try:
        while not trabalho.feito.is_set():
            if cancelar.wait(ESPERA_CANCELAR_S):
                raise DownloadCancelado("Download do modelo de voz cancelado. O que já veio fica guardado: na próxima vez ele continua de onde parou.")
            agora = time.monotonic()
            medidor.registrar(agora, trabalho.sessao_bytes())
            if medidor.hora_de_avisar(agora):
                baixado, vel = trabalho.baixado_bytes(), medidor.velocidade()
                _chamar(ao_progredir, baixado, m.tamanho, vel, tempo_restante(m.tamanho, baixado, vel))
    except BaseException:
        trabalho.parar.set()           # cancelado, ou o `ao_progredir` quebrou: o trabalho de fundo para no próximo pedaço, sem apagar nada
        raise
    if trabalho.erro is not None:
        raise trabalho.erro
    if not all(_completo(pasta / a.nome, a) for a in m.arquivos):
        raise ErroDownloadModelo(f"O download do modelo de voz {nome} terminou, mas faltam arquivos em {pasta}. Tente de novo.")
    medidor.registrar(time.monotonic(), trabalho.sessao_bytes())
    _chamar(ao_progredir, m.tamanho, m.tamanho, medidor.velocidade(), 0.0)
    return pasta


def tempo_restante(total: int, baixado: int, bytes_por_segundo: float) -> float | None:
    """Segundos até o fim na velocidade de agora; None se a velocidade é zero (não dá para prever)."""
    falta = max(0, total - baixado)
    if falta == 0:
        return 0.0
    return falta / bytes_por_segundo if bytes_por_segundo > 0 else None


class MedidorVelocidade:
    """A velocidade suavizada (bytes da janela dos últimos `janela_s` segundos ÷ o tempo) e o ritmo dos avisos (1 a cada `intervalo_s`).
    Os tempos vêm de fora (`time.monotonic()` no `baixar`, números fixos nos testes)."""

    def __init__(self, janela_s: float = JANELA_VELOCIDADE_S, intervalo_s: float = INTERVALO_AVISO_S):
        self.janela_s, self.intervalo_s = janela_s, intervalo_s
        self._amostras: deque[tuple[float, int]] = deque()
        self._ultimo_aviso: float | None = None

    def registrar(self, agora: float, bytes_da_sessao: int) -> None:
        self._amostras.append((agora, bytes_da_sessao))
        while len(self._amostras) > 2 and self._amostras[1][0] <= agora - self.janela_s:
            self._amostras.popleft()   # fica uma amostra no começo da janela (ou logo antes dela) como base

    def velocidade(self) -> float:
        if len(self._amostras) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self._amostras[0], self._amostras[-1]
        return max(0.0, (b1 - b0) / (t1 - t0)) if t1 > t0 else 0.0

    def hora_de_avisar(self, agora: float) -> bool:
        if self._ultimo_aviso is not None and agora - self._ultimo_aviso < self.intervalo_s - 1e-9:
            return False
        self._ultimo_aviso = agora
        return True


# ── o trabalho de fundo ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
_TRABALHOS: list[_Trabalho] = []
_TRAVA_TRABALHOS = threading.Lock()
_TRAVAS_POR_REPO: dict[str, threading.Lock] = {}


def _trava_do_repo(repo: str) -> threading.Lock:
    with _TRAVA_TRABALHOS:
        return _TRAVAS_POR_REPO.setdefault(repo, threading.Lock())


class _Parado(Exception):
    """Pedido de parar visto entre um pedaço e outro."""


class _FluxoCurto(Exception):
    """A conexão acabou antes do fim do arquivo (tenta de novo do ponto em que parou)."""


class _Trabalho(threading.Thread):
    """Baixa os arquivos que faltam, um por vez. Um só por repositório ao mesmo tempo: o de uma chamada cancelada termina de soltar a
    conexão antes de o seguinte mexer no mesmo `.incomplete`."""

    def __init__(self, m: _Modelo, cache: Path, ja: int):
        super().__init__(daemon=True, name=f"modelo-voz-{m.nome}")
        self.m, self.nome, self.cache = m, m.nome, cache
        self.parar, self.feito = threading.Event(), threading.Event()
        self.erro: ErroDownloadModelo | None = None
        self._trava = threading.Lock()
        self._base, self._sessao = ja, 0

    def sessao_bytes(self) -> int:
        with self._trava:
            return self._sessao

    def baixado_bytes(self) -> int:
        with self._trava:
            return min(self.m.tamanho, self._base + self._sessao)

    def _somar(self, n: int) -> None:
        with self._trava:
            self._sessao += n

    def run(self) -> None:
        trava = _trava_do_repo(self.m.repo)
        try:
            while not trava.acquire(timeout=0.2):
                if self.parar.is_set():
                    return
            try:
                with self._trava:              # o que estava no disco quando ESTE trabalho começou (o anterior pode ter escrito mais)
                    self._base, self._sessao = sum(_ja_veio(self.cache, self.m, a) for a in self.m.arquivos), 0
                for a in self.m.arquivos:
                    self._um_arquivo(a)
            except _Parado:
                pass                           # cancelado: o `.incomplete` fica para a próxima vez
            except ErroDownloadModelo as e:
                self.erro = e
            except Exception as e:  # noqa: BLE001  (rede, disco: vira mensagem para a pessoa)
                self.erro = _traduzir(e)
            finally:
                trava.release()
        finally:
            self.feito.set()

    def _um_arquivo(self, a: _Arquivo) -> None:
        snap, blobs = _snapshot(self.cache, self.m), _pasta_repo(self.cache, self.m) / "blobs"
        destino, blob, inc = snap / a.nome, blobs / a.blob, _incompleto(self.cache, self.m, a)
        if _completo(destino, a):
            return
        if not _completo(blob, a):
            blobs.mkdir(parents=True, exist_ok=True)
            self._receber(a, inc)
            self._conferir_hash(a, inc)
            substituir_arquivo(inc, blob)
        _publicar(blob, destino)

    def _receber(self, a: _Arquivo, inc: Path) -> None:
        """Escreve o arquivo em `inc` até ele ter o tamanho certo, continuando do que já tem (Range)."""
        from huggingface_hub import hf_hub_url
        url = hf_hub_url(self.m.repo, a.nome, revision=self.m.revisao)
        falhas = 0
        while True:
            inicio = _tamanho(inc) or 0
            if inicio > a.tamanho:             # maior do que devia: lixo de outra coisa, recomeça
                inc.unlink()
                inicio = 0
            if inicio == a.tamanho:
                return
            if self.parar.is_set():
                raise _Parado
            antes = inicio
            try:
                inicio = self._uma_conexao(url, a, inc, inicio)
            except _Parado:
                raise
            except Exception as e:  # noqa: BLE001
                if not _passageiro(e):
                    raise
                inicio = _tamanho(inc) or 0
                falhas = 0 if inicio > antes else falhas + 1
                if falhas >= TENTATIVAS:
                    raise _traduzir(e) from e
                if self.parar.wait(ESPERA_ENTRE_TENTATIVAS_S * max(1, falhas)):
                    raise _Parado from None

    def _uma_conexao(self, url: str, a: _Arquivo, inc: Path, inicio: int) -> int:
        with _fluxo(url, inicio) as (status, pedacos), open(inc, "ab") as f:
            if inicio and status == 200:       # o servidor ignorou o Range e mandou o arquivo inteiro: recomeça do zero
                f.truncate(0)
                with self._trava:
                    self._base -= inicio
                inicio = 0
            for pedaco in pedacos:
                if self.parar.is_set():
                    raise _Parado
                if inicio + len(pedaco) > a.tamanho:
                    raise ErroDownloadModelo(f"O arquivo {a.nome} do modelo de voz veio maior do que devia. Tente de novo mais tarde.")
                f.write(pedaco)
                inicio += len(pedaco)
                self._somar(len(pedaco))
        if inicio < a.tamanho:
            raise _FluxoCurto(f"{a.nome}: a conexão acabou em {inicio} de {a.tamanho} bytes")
        return inicio

    def _conferir_hash(self, a: _Arquivo, inc: Path) -> None:
        h = hashlib.sha256() if a.sha256 else hashlib.sha1(b"blob %d\x00" % a.tamanho)
        with open(inc, "rb") as f:
            while bloco := f.read(8 * 1024 * 1024):
                if self.parar.is_set():
                    raise _Parado
                h.update(bloco)
        if h.hexdigest() != a.blob:
            inc.unlink(missing_ok=True)        # inteiro e errado: guardar não serve para retomar
            raise ErroDownloadModelo(f"O arquivo {a.nome} do modelo de voz veio diferente do esperado (download corrompido). "
                                     "Apaguei a parte baixada; tente de novo.")


def _publicar(blob: Path, destino: Path) -> None:
    """Põe o blob no snapshot como o huggingface_hub: link simbólico relativo; sem esse direito (Windows comum), o próprio arquivo vai para lá."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.is_symlink() or destino.exists():
        destino.unlink()                       # sobra com tamanho errado (`_um_arquivo` só chega aqui se o destino não está completo)
    try:
        os.symlink(os.path.relpath(blob, destino.parent), destino)
    except (OSError, NotImplementedError, ValueError):
        substituir_arquivo(blob, destino)


# ── rede ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@contextmanager
def _fluxo_hf(url: str, inicio: int) -> Iterator[tuple[int, Iterator[bytes]]]:
    """Um GET (com `Range` se `inicio` > 0) pela sessão do huggingface_hub: (status, pedaços). Sem o token da pessoa: o modelo é público."""
    from huggingface_hub import get_session
    from huggingface_hub.utils import build_hf_headers, hf_raise_for_status

    import cs2clip
    cabecalhos = build_hf_headers(token=False, library_name="BisturiClips", library_version=getattr(cs2clip, "__version__", "dev"))
    cabecalhos["Accept-Encoding"] = "identity"     # sem gzip: o tamanho e o Range valem byte a byte
    if inicio:
        cabecalhos["Range"] = f"bytes={inicio}-"
    with get_session().stream("GET", url, headers=cabecalhos, timeout=PRAZO_REDE_S, follow_redirects=True) as resposta:
        hf_raise_for_status(resposta)
        yield resposta.status_code, resposta.iter_bytes(PEDACO_BYTES)


_fluxo: Callable[[str, int], AbstractContextManager[tuple[int, Iterator[bytes]]]] = _fluxo_hf   # os testes trocam por um servidor falso


def _offline() -> bool:
    import huggingface_hub.constants as k
    return bool(k.HF_HUB_OFFLINE) or os.environ.get("HF_HUB_OFFLINE", "").strip().upper() in {"1", "ON", "YES", "TRUE"}


def _espaco_livre(pasta: Path) -> int:
    for p in (pasta, *pasta.parents):          # a pasta do cache pode ainda não existir
        try:
            return shutil.disk_usage(p).free
        except OSError:
            continue
    return 0


def _legivel(n: int) -> str:
    return f"{n / 1e9:.1f} GB".replace(".", ",") if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


def _conferir_espaco(cache: Path, falta: int) -> None:
    precisa, livre = falta + MARGEM_DISCO_BYTES, _espaco_livre(cache)
    if livre < precisa:
        raise ErroDownloadModelo(f"Falta espaço no disco para o modelo de voz: ele precisa de {_legivel(precisa)} livres em {cache.anchor or cache} "
                                 f"e há {_legivel(livre)}. Libere pelo menos {_legivel(precisa - livre)} e tente de novo.")


def _nomes_da_classe(e: BaseException) -> set[str]:
    return {c.__name__ for c in type(e).__mro__}


def _status_http(e: BaseException) -> int | None:
    status = getattr(getattr(e, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _disco_cheio(e: BaseException) -> bool:
    return isinstance(e, OSError) and (e.errno == errno.ENOSPC or getattr(e, "winerror", None) in (39, 112))


def _passageiro(e: BaseException) -> bool:
    """Falha que costuma passar sozinha (vale tentar de novo do ponto em que parou)."""
    if isinstance(e, _FluxoCurto):
        return True
    if "OfflineModeIsEnabled" in _nomes_da_classe(e) or _disco_cheio(e):
        return False
    status = _status_http(e)
    if status is not None:
        return status >= 500 or status in (408, 429)
    return bool(_nomes_da_classe(e) & {"TransportError", "TimeoutException", "NetworkError", "ConnectError", "RemoteProtocolError"}) \
        or isinstance(e, (ConnectionError, TimeoutError))


def _traduzir(e: BaseException) -> ErroDownloadModelo:
    """O erro técnico vira uma frase para a pessoa (sem internet, site fora do ar, disco cheio...)."""
    if isinstance(e, ErroDownloadModelo):
        return e
    guardado = "O que já veio fica guardado: na próxima vez o download continua de onde parou."
    nomes = _nomes_da_classe(e)
    if "OfflineModeIsEnabled" in nomes:
        return ErroDownloadModelo("O modo sem internet do Hugging Face (HF_HUB_OFFLINE) está ligado. Desligue-o para baixar o modelo de voz.")
    if _disco_cheio(e):
        return ErroDownloadModelo(f"O disco encheu durante o download do modelo de voz. Libere espaço e tente de novo. {guardado}")
    status = _status_http(e)
    if status is not None:
        if status >= 500:
            return ErroDownloadModelo(f"O site do Hugging Face (de onde vem o modelo de voz) está fora do ar ou com problema (erro {status}). "
                                      f"Tente de novo mais tarde. {guardado}")
        if status == 429:
            return ErroDownloadModelo(f"O site do Hugging Face pediu para esperar (muitos pedidos). Tente de novo daqui a alguns minutos. {guardado}")
        return ErroDownloadModelo(f"O site do Hugging Face não entregou o modelo de voz (erro {status}); ele pode ter mudado de lugar. "
                                  "Atualize o Bisturi Clips ou peça ajuda.")
    if nomes & {"TimeoutException", "ReadTimeout", "ConnectTimeout"} or isinstance(e, TimeoutError):
        return ErroDownloadModelo(f"A conexão com o site do Hugging Face ficou parada. Confira a internet e tente de novo. {guardado}")
    if nomes & {"TransportError", "NetworkError", "ConnectError", "RemoteProtocolError"} or isinstance(e, (ConnectionError, _FluxoCurto)):
        return ErroDownloadModelo(f"Sem conexão com a internet (ou o site do Hugging Face não respondeu). Confira a internet e tente de novo. {guardado}")
    if isinstance(e, OSError):
        return ErroDownloadModelo(f"Não consegui gravar o modelo de voz no disco: {e}")
    return ErroDownloadModelo(f"Não consegui baixar o modelo de voz: {e}")


def _chamar(ao_progredir: AoProgredir | None, baixado: int, total: int, vel: float, restante: float | None) -> None:
    if ao_progredir is not None:
        ao_progredir(baixado, total, vel, restante)
