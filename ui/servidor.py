"""Servidor FastAPI local da interface Bisturi Clips."""
from __future__ import annotations

import asyncio
import json
import mimetypes
from contextlib import asynccontextmanager
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect
from fastapi.staticfiles import StaticFiles

from cs2clip import __version__, caminhos, modelo_voz
from cs2clip.tools import sem_janela
from .configuracao import ConfiguracaoProjeto, ErroConfig
from .coordenador import Coordenador
from .dados import DadosProjeto
from .envio import ErroEnvio, receber as receber_arquivo
from .fila import GerenciadorFila, nucleo_vivo
from .vods import GerenciadorVod
from .gravacao import GerenciadorGravacao
from .receitas import GerenciadorReceitas
from .rotas_novas import registrar_rotas_novas
from .seguranca import (
    EXTENSOES_ENTRADA,
    CorpoGrandeDemais,
    LimiteCorpo,
    LimitesEnvio,
    ROTA_UPLOAD,
    caminho_biblioteca_seguro,
    caminho_midia_seguro,
    ambiente_dos_filhos,
    host_permitido,
    nome_arquivo_seguro,
    novo_token,
    origin_permitida,
    python_de_console,
    site_cruzado,
    validar_caminho_sem_unc,
)


# Páginas geradas pelo próprio programa (autocontidas: script e estilo embutidos). Só elas recebem uma política
# mais frouxa, e sem nenhuma rede (connect-src 'none'): assim não conseguem ler o token da interface nem chamar a API.
PAGINAS_PROPRIAS = {"revisao.html", "relatorio.html"}
CSP_INTERFACE = ("default-src 'self'; script-src 'self'; style-src-elem 'self'; style-src-attr 'unsafe-inline'; "
                 "media-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
                 "frame-ancestors 'none'")
CSP_PAGINA_PROPRIA = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; "
                      "media-src 'self'; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'")

MAX_ARQUIVOS_FILA = 200      # arquivos de uma vez em POST /api/fila (uma pasta cheia de vídeos cabe com folga)

MENSAGEM_ERRO = "Não consegui concluir essa ação. Confira os dados e tente novamente."


def erro(mensagem: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"erro": mensagem}, status_code=status)


def _carregar_core_config() -> tuple[Callable[[Path], Any] | None, Callable[[], Any] | None]:
    try:
        from cs2clip.config import Config, load_config
        return load_config, Config
    except (ImportError, AttributeError):
        return None, None


def _executar_checagem(raiz: Path, pasta_dados: Path | None = None) -> dict[str, Any]:
    """Roda `python -m cs2clip --checar` (confere ffmpeg, GPU, disco...) e devolve o texto pronto para mostrar.
    `raiz` = a pasta do programa (de onde o filho roda); `pasta_dados` vai ao filho em `BISTURI_DADOS` (P2; sem ela, a própria `raiz`)."""
    ambiente = ambiente_dos_filhos(pasta_dados if pasta_dados is not None else raiz)
    ambiente["PYTHONIOENCODING"] = "utf-8"
    try:
        r = subprocess.run([str(python_de_console()), "-m", "cs2clip", "--checar"], cwd=str(raiz), env=ambiente, shell=False,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, **sem_janela())
    except (OSError, subprocess.SubprocessError):
        return {"codigo": 1, "texto": "Não consegui rodar a verificação. Confira se o programa foi instalado (instalar.bat)."}
    return {"codigo": r.returncode, "texto": (r.stdout or r.stderr or "").strip()}


def _abrir_explorer(caminho: Path) -> None:
    if hasattr(os, "startfile"):
        os.startfile(str(caminho))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["explorer.exe", str(caminho)], shell=False)  # noqa: S603  # janela-propria: o Explorer é a janela


class DownloadDoModelo:
    """P4.2b: o download do modelo de voz pedido pela tela, numa thread de fundo; é o 5º trabalho do `Coordenador`. Um de cada vez.
    O que a tela mostra fica aqui (um dict com trava); o "Cancelar" é o `threading.Event` que vai ao `modelo_voz.baixar`."""

    def __init__(self):
        self._trava = threading.Lock()
        self._estado: dict[str, Any] = {"nome": None, "baixando": False, "baixado": 0, "total": 0, "velocidade": 0.0, "restante": None,
                                         "erro": "", "cancelado": False, "pronto": False}
        self._cancelar: threading.Event | None = None
        self._fio: threading.Thread | None = None

    def _ativo(self) -> bool:
        return self._fio is not None and self._fio.is_alive()

    def ativo(self) -> bool:
        """A thread do download desta tela está viva (só ela recusa um segundo pedido)."""
        with self._trava:
            return self._ativo()

    def baixando(self) -> bool:
        """Para o Coordenador: a thread desta tela ou o trabalho de fundo do `modelo_voz` (que pode levar uns segundos para soltar a conexão)."""
        return self.ativo() or modelo_voz.em_andamento()

    def estado(self) -> dict[str, Any]:
        with self._trava:
            return dict(self._estado)

    def iniciar(self, nome: str) -> dict[str, Any]:
        """Começa a baixar `nome` no fundo. ModeloNaoGerenciado (não está no componentes.json), RuntimeError (já há um download)."""
        if nome not in modelo_voz.modelos_gerenciados():
            raise modelo_voz.ModeloNaoGerenciado(nome)
        no_pc = modelo_voz.estado(nome)
        with self._trava:
            if self._ativo():
                raise RuntimeError("Já estou baixando o modelo de voz. Acompanhe o andamento na tela Início.")
            cancelar = threading.Event()
            self._cancelar = cancelar
            self._estado = {"nome": nome, "baixando": True, "baixado": no_pc.baixado_bytes, "total": no_pc.tamanho_bytes, "velocidade": 0.0,
                            "restante": None, "erro": "", "cancelado": False, "pronto": False}
            self._fio = threading.Thread(target=self._executar, args=(nome, cancelar), daemon=True, name="ui-modelo-voz")
            self._fio.start()                            # dentro da trava: um segundo pedido já vê a thread viva
            return dict(self._estado)

    def _progresso(self, baixado: int, total: int, velocidade: float, restante: float | None) -> None:
        with self._trava:
            self._estado.update(baixado=baixado, total=total, velocidade=velocidade, restante=restante)

    def _executar(self, nome: str, cancelar: threading.Event) -> None:
        resultado: dict[str, Any] = {}
        try:
            modelo_voz.baixar(nome, ao_progredir=self._progresso, cancelar=cancelar)
            resultado = {"pronto": True, "restante": 0.0}
        except modelo_voz.DownloadCancelado:
            resultado = {"cancelado": True}
        except modelo_voz.ErroDownloadModelo as exc:
            resultado = {"erro": str(exc)}                  # a mensagem do modelo_voz já é para a pessoa ler
        except Exception as exc:  # noqa: BLE001  (o estado nunca pode ficar preso em "baixando")
            resultado = {"erro": f"Não consegui baixar o modelo de voz ({type(exc).__name__}). Tente de novo."}
        finally:
            with self._trava:
                self._estado.update(resultado, baixando=False, velocidade=0.0)

    def cancelar(self) -> dict[str, Any]:
        with self._trava:
            if not self._ativo() or self._cancelar is None:
                raise RuntimeError("Não há download do modelo de voz em andamento.")
            self._cancelar.set()
            return {"sucesso": True}

    def parar(self) -> None:
        """Fim do programa: pede para parar (o que já veio fica guardado: o próximo download continua de onde parou)."""
        with self._trava:
            if self._cancelar is not None:
                self._cancelar.set()

    def esperar_fim(self, prazo_s: float) -> None:
        """Espera a thread desta tela e o trabalho de fundo do `modelo_voz` soltarem a conexão, até o prazo (depois disso o processo sai e
        a thread de fundo, que é daemon, cai junto; o `.incomplete` fica e a próxima vez continua dele)."""
        fim = time.monotonic() + max(0.0, prazo_s)
        with self._trava:
            fio = self._fio
        if fio is not None:
            fio.join(max(0.0, fim - time.monotonic()))
        while modelo_voz.em_andamento() and time.monotonic() < fim:
            time.sleep(0.05)


# O Python no Windows lê os tipos do registro (HKCR\.js): se outro programa deixou ".js" como text/plain, o nosniff
# faz o navegador recusar o app.js e a interface fica em branco. Fixar os tipos da interface antes de servir.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("font/ttf", ".ttf")


def _resposta_arquivo(arquivo: Path, request: Request) -> Response:
    """Serve arquivos locais com suporte a HTTP Range para player de vídeo."""
    tamanho = arquivo.stat().st_size
    tipo = mimetypes.guess_type(arquivo.name)[0] or "application/octet-stream"
    if arquivo.suffix.lower() in {".html", ".htm"} and arquivo.name not in PAGINAS_PROPRIAS:
        tipo = "text/plain"   # qualquer outra página .html em saida/ é só texto: nunca executa
    cabecalhos = {"Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff"}
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(arquivo, media_type=tipo, headers=cabecalhos)
    if not range_header.startswith("bytes=") or "," in range_header:
        return JSONResponse({"erro": "O trecho solicitado do vídeo não é válido."}, status_code=416, headers={**cabecalhos, "Content-Range": f"bytes */{tamanho}"})
    faixa = range_header[6:].strip()
    try:
        inicio_txt, fim_txt = faixa.split("-", 1)
        if inicio_txt == "":
            sufixo = int(fim_txt)
            if sufixo <= 0:
                raise ValueError
            inicio = max(0, tamanho - sufixo)
            fim = tamanho - 1
        else:
            inicio = int(inicio_txt)
            fim = int(fim_txt) if fim_txt else tamanho - 1
            if inicio < 0 or fim < inicio or inicio >= tamanho:
                raise ValueError
            fim = min(fim, tamanho - 1)
    except (ValueError, TypeError):
        return JSONResponse({"erro": "O trecho solicitado do vídeo não é válido."}, status_code=416, headers={**cabecalhos, "Content-Range": f"bytes */{tamanho}"})
    comprimento = max(0, fim - inicio + 1)

    def blocos():
        with arquivo.open("rb") as fluxo:
            fluxo.seek(inicio)
            restante = comprimento
            while restante:
                bloco = fluxo.read(min(1024 * 1024, restante))
                if not bloco:
                    break
                restante -= len(bloco)
                yield bloco

    return StreamingResponse(
        blocos(), status_code=206, media_type=tipo,
        headers={**cabecalhos, "Content-Range": f"bytes {inicio}-{fim}/{tamanho}", "Content-Length": str(comprimento)},
    )


def _seletor_nativo() -> list[dict[str, Any]]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError("O seletor de arquivos do Windows não está disponível. Use arrastar e soltar.") from exc
    janela = tk.Tk()
    janela.withdraw()
    try:
        try:
            janela.attributes("-topmost", True)
        except Exception:
            pass
        selecionados = filedialog.askopenfilenames(
            parent=janela,
            title="Escolha vídeos ou demos do CS2",
            filetypes=[("Vídeos e demos", "*.mp4 *.mkv *.mov *.flv *.ts *.webm *.avi *.m4v *.mpg *.mpeg *.wmv *.mts *.m2ts *.3gp *.ogv *.dem"), ("Todos os arquivos", "*.*")],
        )
    finally:
        janela.destroy()
    resultado = []
    for nome in selecionados:
        caminho = Path(nome)
        if caminho.suffix.lower() not in EXTENSOES_ENTRADA or not caminho.is_file():
            continue
        resultado.append({"caminho": str(caminho.resolve()), "nome": caminho.name, "tamanho": caminho.stat().st_size, "tipo": "demo" if caminho.suffix.lower() == ".dem" else "video"})
    return resultado


def create_app(
    root_dir: Path | str | None = None,
    port: int = 8765,
    popen_factory: Callable[..., Any] | None = None,
    command_builder: Callable[..., list[str]] | None = None,
    load_config_fn: Callable[[Path], Any] | None = None,
    config_cls: Callable[[], Any] | None = None,
    chooser: Callable[[], list[dict[str, Any]]] | None = None,
    open_folder_fn: Callable[[Path], Any] | None = None,
    checar_fn: Callable[[Path], dict[str, Any]] | None = None,
    vod_popen_factory: Callable[..., Any] | None = None,
    vod_cli: Path | None = None,
    gravacao_popen_factory: Callable[..., Any] | None = None,
    gravacao_replays: Callable[[], Path | None] | None = None,
    receita_popen_factory: Callable[..., Any] | None = None,
    receita_conta_logada: Callable[[], int] | None = None,
    receita_downloads: Path | None = None,
    music_chooser: Callable[[], list[dict[str, Any]]] | None = None,
    limites: LimitesEnvio | None = None,
    steam_conta_aberta: Callable[[], int] | None = None,
    dados_dir: Path | str | None = None,
) -> FastAPI:
    # P2 (duas bases, 26/11/2026). `raiz` = o PROGRAMA: só os arquivos da interface (`static`) e a pasta de onde os filhos rodam.
    # `raiz_dados` = os DADOS da pessoa: saida, entrada, dados, config.toml, trabalhos, demos, gravações e o que conta como "arquivo do projeto".
    # `dados_dir` se vier; senão `root_dir` (os testes passam uma pasta só: programa e dados juntos, como no portátil); senão `PASTA_DADOS`
    # (no portátil, a própria pasta do programa: tudo igual a antes).
    raiz = Path(root_dir).resolve() if root_dir is not None else Path(__file__).resolve().parent.parent
    if dados_dir is not None:
        raiz_dados = Path(dados_dir).resolve()
    elif root_dir is not None:
        raiz_dados = raiz
    else:
        raiz_dados = Path(caminhos.PASTA_DADOS).resolve()
    saida = raiz_dados / "saida"
    entrada = raiz_dados / "entrada"
    for pasta_permitida in (saida, entrada):
        if pasta_permitida.is_symlink() or pasta_permitida.resolve(strict=False).parent != raiz_dados:
            raise RuntimeError("As pastas entrada e saida precisam ser pastas normais dentro da pasta de dados do programa.")
    saida.mkdir(parents=True, exist_ok=True)
    entrada.mkdir(parents=True, exist_ok=True)
    token = novo_token()
    dados = DadosProjeto(raiz_dados)
    fila = GerenciadorFila(raiz_dados, popen_factory=popen_factory, command_builder=command_builder, programa=raiz)
    vods = GerenciadorVod(entrada, popen_factory=vod_popen_factory, cli=vod_cli)   # C4: baixar VOD da Twitch pelo link
    trava_cs2 = threading.Lock()                     # G-003: UMA trava do CS2/Steam para a receita e para a tela "Gravar pela demo"
    gravacao = GerenciadorGravacao(raiz_dados, popen_factory=gravacao_popen_factory, pasta_replays=gravacao_replays, trava_cs2=trava_cs2,
                                   programa=raiz)   # D6: gravar pela demo
    def _pastas_de_demo() -> list[Path]:
        achadas = [raiz_dados / "demos"]
        replays = gravacao.carregar_pasta_replays()
        return achadas + ([Path(replays)] if replays else [])
    escolhidos: set[str] = set()                     # G-004: caminhos externos autorizados pelo seletor nativo (vira app.state.arquivos_escolhidos)
    # A pessoa escolheu para os dados a pasta ACIMA do programa (ex.: dados em `Bisturi Clips\` e o programa em `Bisturi Clips\app\`):
    # o que está dentro do programa continua não sendo "da pessoa". No portátil (as duas iguais) isto é falso e nada muda.
    programa_dentro_dos_dados = raiz != raiz_dados and raiz.is_relative_to(raiz_dados)

    def _dentro_dos_dados(alvo: Path) -> bool:
        """P2: "dentro do projeto" = dentro da pasta de DADOS (no portátil, a do projeto inteira, como antes); nunca um arquivo do programa."""
        if not alvo.is_relative_to(raiz_dados):
            return False
        return not (programa_dentro_dos_dados and alvo.is_relative_to(raiz))

    def _autorizado(caminho: str) -> bool:
        """Caminho que a interface pode abrir: dentro da pasta de dados, dentro da pasta de demos do CS2, ou escolhido pelo seletor nativo."""
        try:
            alvo = Path(caminho).resolve(strict=False)
        except OSError:
            return False
        if str(alvo) in escolhidos or _dentro_dos_dados(alvo):
            return True
        for base in _pastas_de_demo():
            try:
                alvo.relative_to(Path(base).resolve(strict=False))
                return True
            except (ValueError, OSError):
                continue
        return False
    receitas = GerenciadorReceitas(raiz_dados, popen_factory=receita_popen_factory, conta_logada=receita_conta_logada, fila=fila, vods=vods,
                                   pasta_downloads=receita_downloads, procurar_em=_pastas_de_demo, autorizado=_autorizado,
                                   trava_cs2=trava_cs2, programa=raiz)   # E2: a receita (baixar → gravar → editar)
    modelo = DownloadDoModelo()                      # P4.2b: o download do modelo de voz pela tela Início
    coordenador = Coordenador(fila, receitas, gravacao, vods, modelo)   # M-008: "o que está rodando?" e o desligar, num lugar só (ui/coordenador.py)
    core_load, core_cls = _carregar_core_config()
    configuracao = ConfiguracaoProjeto(raiz_dados, load_config_fn=load_config_fn or core_load, config_cls=config_cls or core_cls)
    escolha = chooser or _seletor_nativo
    trava_seletor = threading.Lock()   # um seletor nativo por vez: cada um cria um tk.Tk() em thread própria
    abrir_pasta = open_folder_fn or _abrir_explorer
    checagem = checar_fn or (lambda pasta_programa: _executar_checagem(pasta_programa, raiz_dados))

    @asynccontextmanager
    async def ciclo_vida(_app: FastAPI):
        try:
            yield
        finally:
            coordenador.encerrar()     # A-001: os gerenciadores param (receitas, fila, gravação, VOD, modelo de voz), árvores de processo incluídas

    app = FastAPI(title="Bisturi Clips", docs_url=None, redoc_url=None, openapi_url=None, lifespan=ciclo_vida)

    @app.exception_handler(StarletteHTTPException)
    async def erro_http(_request: Request, exc: StarletteHTTPException):
        mensagens = {404: "Não encontrei esse endereço ou arquivo.", 405: "Esta ação não é permitida neste endereço.", 413: "O arquivo enviado é maior do que o permitido pelo servidor.", 422: "Os dados enviados não estão no formato esperado."}
        mensagem = exc.mensagem if isinstance(exc, CorpoGrandeDemais) else mensagens.get(exc.status_code, "Não consegui concluir essa ação.")
        return JSONResponse({"erro": mensagem}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def erro_validacao(_request: Request, _exc: RequestValidationError):
        return JSONResponse({"erro": "Confira os dados preenchidos e tente novamente."}, status_code=422)

    @app.exception_handler(Exception)
    async def erro_interno(_request: Request, _exc: Exception):
        return JSONResponse({"erro": "Ocorreu um erro inesperado. Tente novamente."}, status_code=500)

    app.state.raiz = raiz                            # o programa
    app.state.raiz_dados = raiz_dados                # os dados da pessoa (no portátil, a mesma pasta)
    app.state.port = port
    app.state.token = token
    app.state.dados = dados
    app.state.fila = fila
    app.state.receitas = receitas
    app.state.gravacao = gravacao
    app.state.vods = vods
    app.state.modelo = modelo
    app.state.coordenador = coordenador
    app.state.configuracao = configuracao
    app.state.ultima_atividade = time.monotonic()    # última vez que a página falou com o servidor (ela pergunta a cada 1 s)
    app.state.janela_fechada_em = None               # a página avisou que foi fechada (volta a None se ela voltar)
    app.state.ultima_midia = 0.0                     # último vídeo/relatório servido (janelas de relatório abertas à parte)
    app.state.pedir_saida = False                    # botão "Encerrar o programa"
    app.state.arquivos_escolhidos = escolhidos       # G-004: caminhos externos autorizados pelo seletor nativo ou upload
    app.state.limites = limites or LimitesEnvio()    # QB2: tetos de corpo JSON, profundidade e envio de arquivos

    # Adicionado ANTES do middleware abaixo, ou seja, roda depois dele: host, origem e token são conferidos antes de ler o corpo.
    app.add_middleware(LimiteCorpo, limites=lambda: app.state.limites)

    @app.middleware("http")
    async def proteger_requisicao(request: Request, call_next):
        host = request.headers.get("host")
        if not host_permitido(host, app.state.port):
            return JSONResponse({"erro": "Acesso recusado: use o endereço local da interface."}, status_code=403)
        origin = request.headers.get("origin")
        if not origin_permitida(origin, app.state.port):
            return JSONResponse({"erro": "Acesso recusado: a origem desta solicitação não é permitida."}, status_code=403)
        if site_cruzado(request.headers.get("sec-fetch-site")):   # o navegador diz que veio de outro site (sem o cabeçalho, vale)
            return JSONResponse({"erro": "Acesso recusado: este pedido veio de outro site."}, status_code=403)
        # R-015: o token vale para TODO pedido a /api/ (GET incluído: config, kits e caminhos são dados pessoais). Única exceção: o áudio das músicas,
        # que a tag <audio> carrega sem poder mandar cabeçalho (só toca faixa do catálogo; /media e /static não começam com /api/).
        caminho_pedido = request.url.path
        audio_por_tag = request.method in {"GET", "HEAD"} and caminho_pedido.startswith("/api/musicas/audio/") and len(caminho_pedido) > len("/api/musicas/audio/")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} or (caminho_pedido.startswith("/api/") and not audio_por_tag):
            informado = request.headers.get("x-token", "")
            if not informado or not secrets_compare(informado, app.state.token):
                return JSONResponse({"erro": "A sessão expirou ou o pedido não é válido. Atualize a página."}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/janela-fechada":
            app.state.ultima_atividade = time.monotonic()
            app.state.janela_fechada_em = None       # a página voltou (ex.: atualizou com F5)
        if request.url.path.startswith(("/media/", "/media-geral/")):
            app.state.ultima_midia = time.monotonic()    # um relatório aberto à parte ainda está tocando vídeo
        resposta = await call_next(request)
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        caminho = request.scope.get("path", "")
        propria = caminho.startswith(("/media/", "/media-geral/")) and caminho.rsplit("/", 1)[-1] in PAGINAS_PROPRIAS
        resposta.headers["Content-Security-Policy"] = CSP_PAGINA_PROPRIA if propria else CSP_INTERFACE
        resposta.headers["Referrer-Policy"] = "no-referrer"
        resposta.headers["Cache-Control"] = "no-store" if request.url.path == "/" else "no-cache"
        return resposta

    static = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static), check_dir=True), name="static")

    def html_inicial() -> HTMLResponse:
        arquivo = static / "index.html"
        try:
            html = arquivo.read_text(encoding="utf-8")
        except OSError:
            return HTMLResponse("<h1>Bisturi Clips</h1><p>Os arquivos da interface não foram encontrados.</p>", status_code=500)
        html = html.replace("__UI_TOKEN__", token)
        return HTMLResponse(html)

    @app.get("/", response_class=HTMLResponse)
    async def inicio():
        return html_inicial()

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/api/versao")
    async def api_versao():
        return {"versao": __version__}

    # ── P4.2b: o modelo de voz (Whisper) que o config.toml pede ─────────────────────────────────────────────────────────────────────
    erro_componentes = "Não consegui ler a lista de componentes do programa (componentes.json). Reinstale o Bisturi Clips ou peça ajuda."

    def _modelo_configurado() -> str:
        campo = next((c for c in configuracao.ler()["campos"] if c["secao"] == "transcricao" and c["chave"] == "modelo"), None)
        valor = campo.get("valor") if campo else None
        if not isinstance(valor, str) or not valor.strip():
            raise ErroConfig("O config.toml não diz qual modelo de voz usar ([transcricao] modelo).")
        return valor.strip()

    def _estado_do_modelo() -> dict[str, Any]:
        nome = _modelo_configurado()
        try:
            e = modelo_voz.estado(nome)
        except (OSError, ValueError):
            # Sem um componentes.json legível o programa faz como antes (o transcribe também cai no nome): a tela não mostra o cartão.
            e = modelo_voz.EstadoModelo(nome, None, 0, 0, None, None)
            aviso = erro_componentes
        else:
            aviso = ""
        return {"nome": nome, "gerenciado": e.gerenciado, "presente": e.presente, "ja_tem": bool(e.presente or e.outras_revisoes),
                "outras_revisoes": bool(e.outras_revisoes), "tamanho_bytes": e.tamanho_bytes, "baixado_bytes": e.baixado_bytes,
                "pasta": str(e.pasta) if e.pasta is not None else None, "aviso": aviso, "download": modelo.estado()}

    @app.get("/api/modelo")
    async def api_modelo():
        try:
            return await asyncio.to_thread(_estado_do_modelo)
        except ErroConfig as exc:
            return erro(str(exc), 503)

    @app.post("/api/modelo/baixar")
    async def api_modelo_baixar():
        if await asyncio.to_thread(coordenador.pesado_rodando):
            return erro("Há uma análise, um trabalho ou uma gravação pela demo rodando agora. Baixe o modelo de voz quando terminar.", 409)
        try:
            nome = await asyncio.to_thread(_modelo_configurado)
        except ErroConfig as exc:
            return erro(str(exc), 503)
        try:
            return await asyncio.to_thread(modelo.iniciar, nome)
        except modelo_voz.ModeloNaoGerenciado:
            return erro(f"O modelo de voz {nome} (escolhido em Configurar) não é um dos que o Bisturi baixa por aqui. Ele é baixado sozinho na "
                        "primeira vez que você processar um vídeo, com a internet ligada.", 400)
        except RuntimeError as exc:
            return erro(str(exc), 409)
        except (OSError, ValueError):
            return erro(erro_componentes, 503)

    @app.post("/api/modelo/cancelar")
    async def api_modelo_cancelar():
        try:
            return modelo.cancelar()
        except RuntimeError as exc:
            return erro(str(exc), 409)

    def _indice_com_capas() -> dict[str, Any]:
        # Cada cartão da biblioteca ganha uma miniatura (capa), escolhida aqui: o índice do núcleo não tem.
        itens = []
        for item in dados.indice():
            video_id = item.get("id")
            q = dados.qualidade(video_id) if isinstance(video_id, str) else None
            itens.append({**item, "capa": dados.capa(video_id) if isinstance(video_id, str) else None,
                          "qualidade": q["status"] if q else None})
        return {"itens": itens}

    @app.get("/api/indice")
    async def api_indice():
        # R-010: com muitos kits sem miniatura leva segundos de disco; numa thread, as outras rotas seguem respondendo
        return await asyncio.to_thread(_indice_com_capas)

    @app.get("/api/entrada")
    async def api_entrada():
        return {"itens": dados.listar_entrada()}

    @app.get("/api/log")
    async def api_log(desde: float | None = None):
        # `desde` = quando o job começou: o log.txt acumula execuções, e só a deste job interessa.
        return {"linhas": dados.linhas_log(30, desde)}

    @app.get("/api/videos/{video_id}")
    async def api_video(video_id: str):
        return await asyncio.to_thread(_pagina_do_video, video_id)      # R-010: leitura de vários JSON fora do laço de eventos

    def _pagina_do_video(video_id: str):
        try:
            indice = next((item for item in dados.indice() if item.get("id") == video_id), {})
            momentos = dados.momentos(video_id)
            partes = dados.partes(video_id)
            demo = dados.demo(video_id)
            pasta = dados.pasta_video(video_id)
            montagem = f"MONTAGEM_{video_id}.mp4"
            links = {
                "revisao": (pasta / "revisao.html").is_file(),
                "relatorio": (pasta / "relatorio.html").is_file(),   # relatório só deste vídeo (o núcleo grava um por vídeo)
                "capitulos": (pasta / "capitulos_youtube.txt").is_file(),
                "montagem": (pasta / montagem).is_file(),
                "montagem_arquivo": montagem if (pasta / montagem).is_file() else None,
                "fails_arquivo": f"FAILS_{video_id}.mp4" if (pasta / f"FAILS_{video_id}.mp4").is_file() else None,
            }
            return {"id": video_id, "indice": indice, "momentos": momentos, "partes": partes, "demo": demo, "links": links,
                    "publicar": dados.publicar(video_id), "qualidade": dados.qualidade(video_id),
                    "nada_para_postar": dados.nada_para_postar(video_id)}
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except PermissionError as exc:
            return erro(str(exc), 403)

    @app.post("/api/videos/{video_id}/apagar")
    async def api_video_apagar(video_id: str, body: dict[str, Any] = Body(default={})):
        """Botão "Apagar kit" da Biblioteca. A tela pede confirmação e manda o próprio id em `confirmar`
        (um clique perdido ou um pedido sem a confirmação não apaga nada)."""
        if body.get("confirmar") != video_id:
            return erro("Confirme antes de apagar.", 400)
        if await asyncio.to_thread(coordenador.mexe_nos_kits):   # G-007: análise, edição de qualquer receita ou gravação com edição
            return erro("Há uma análise ou uma edição gravando os kits agora. Espere terminar para apagar um kit.", 409)
        try:
            return await asyncio.to_thread(dados.apagar_kit, video_id)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except PermissionError as exc:
            return erro(str(exc), 403)
        except RuntimeError as exc:
            return erro(str(exc), 409)

    def _estado_fila_seguro() -> dict[str, Any]:
        estado = fila.listar()
        progresso = estado.get("progresso") or dados.progresso()
        if estado.get("externo") and progresso.get("estado") == "rodando":
            progresso = estado["externo"].get("progresso", progresso)
        if progresso.get("estado") == "rodando" and not nucleo_vivo(progresso.get("pid"), progresso.get("iniciado_em")):   # R-003: PID reaproveitado não conta
            progresso = dict(progresso)
            progresso["estado"] = "erro"
            progresso["mensagem"] = "O processo não está mais ativo. A execução foi interrompida."
            estado["progresso"] = progresso
        return {**estado, "progresso": progresso}

    @app.get("/api/progresso")
    async def api_progresso():
        estado = _estado_fila_seguro()
        return {"progresso": estado["progresso"], "fila": estado["itens"], "ativo": estado["ativo"], "externo": bool(estado.get("externo"))}

    @app.get("/api/fila")
    async def api_fila():
        return _estado_fila_seguro()

    # ── D6: gravar pela demo ─────────────────────────────────────────────────────────────────────
    @app.get("/api/gravacao")
    async def api_gravacao():
        from cs2clip.baixar_demo import lista_permitida
        return {"demos": await asyncio.to_thread(gravacao.demos), "lista_ultimas": await asyncio.to_thread(lista_permitida), **gravacao.estado()}

    registrar_rotas_novas(app, receitas=receitas, raiz=raiz, dados=raiz_dados, erro=erro, trava_seletor=trava_seletor, escolhidos=escolhidos,
                          music_chooser=music_chooser, pasta_video=dados.pasta_video, conta_aberta=steam_conta_aberta)   # E1/E2/E7: caixa única, trabalhos, partidas novas e músicas (ui/rotas_novas.py)

    @app.get("/api/gravacao/estado")
    async def api_gravacao_estado():
        return gravacao.estado()

    @app.get("/api/gravacao/plano")
    async def api_gravacao_plano(demo: str = "", jogador: str = ""):
        try:
            return await asyncio.to_thread(gravacao.plano, demo, jogador)
        except ValueError as exc:
            return erro(str(exc), 400)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)

    @app.get("/api/gravacao/jogadores")
    async def api_gravacao_jogadores(demo: str = ""):
        try:
            return {"jogadores": await asyncio.to_thread(gravacao.jogadores, demo)}
        except ValueError as exc:
            return erro(str(exc), 400)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)

    @app.get("/api/gravacao/checklist")
    async def api_gravacao_checklist(demo: str = ""):
        try:
            return {"itens": await asyncio.to_thread(gravacao.checklist, demo or None)}
        except ValueError as exc:
            return erro(str(exc), 400)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)

    @app.get("/api/gravacao/resultados")
    async def api_gravacao_resultados(demo: str = "", jogador: str = ""):
        try:
            gravacao.caminho_da_demo(demo)
            return gravacao.resultados(demo, jogador)
        except (ValueError, FileNotFoundError):
            return erro("Escolha uma das demos da lista.", 400)

    @app.post("/api/gravacao/baixar")
    async def api_gravacao_baixar(body: dict[str, Any] = Body(...)):
        try:
            return gravacao.iniciar_download(codigo=body.get("codigo") or None, ultima=int(body.get("ultima") or 0), so_listar=bool(body.get("listar")))
        except (ValueError, TypeError) as exc:
            return erro(str(exc), 400)
        except RuntimeError as exc:
            return erro(str(exc), 409)

    @app.post("/api/gravacao/gravar")
    async def api_gravacao_gravar(body: dict[str, Any] = Body(...)):
        try:
            return gravacao.iniciar_gravacao(str(body.get("demo") or ""), str(body.get("rounds") or ""), bool(body.get("todos")), bool(body.get("editar")),
                                             str(body.get("jogador") or ""))
        except ValueError as exc:
            return erro(str(exc), 400)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except RuntimeError as exc:
            return erro(str(exc), 409)

    @app.post("/api/gravacao/cancelar")
    async def api_gravacao_cancelar():
        try:
            return gravacao.cancelar()
        except RuntimeError as exc:
            return erro(str(exc), 409)

    @app.get("/api/baixar-vod")
    async def api_vods():
        return vods.listar()

    @app.post("/api/baixar-vod/cancelar")
    async def api_cancelar_vod():
        try:
            return vods.cancelar()
        except RuntimeError as exc:
            return erro(str(exc), 409)

    @app.post("/api/baixar-vod")
    async def api_baixar_vod(body: dict[str, Any] = Body(...)):
        link = body.get("link")
        if not isinstance(link, str):
            return erro("Cole o link do VOD da Twitch.")
        try:
            return vods.iniciar(link)
        except ValueError as exc:
            return erro(str(exc), 400)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except RuntimeError as exc:
            return erro(str(exc), 409)

    @app.post("/api/escolher-arquivo")
    async def api_escolher_arquivo():
        if not trava_seletor.acquire(blocking=False):
            return erro("O seletor de arquivos já está aberto. Procure a janela dele na barra de tarefas.", 409)
        try:
            itens = await asyncio.to_thread(escolha)
            for it in itens:
                if isinstance(it, dict) and isinstance(it.get("caminho"), str):
                    try:
                        p = validar_caminho_sem_unc(it["caminho"]).resolve()
                        app.state.arquivos_escolhidos.add(str(p))
                    except PermissionError:
                        pass
            return {"itens": itens}
        except RuntimeError as exc:
            return erro(str(exc), 503)
        except Exception:
            return erro("Não consegui abrir o seletor de arquivos. Tente arrastar o arquivo para a janela.", 500)
        finally:
            trava_seletor.release()

    @app.put(ROTA_UPLOAD)
    async def api_upload_arquivo(request: Request, nome: str):
        """B3: UM arquivo por pedido, o corpo são os bytes. Vai em fluxo direto para entrada/ (sem pasta temporária do sistema, sem cópia dupla)."""
        lim: LimitesEnvio = app.state.limites
        if not (nome or "").strip():
            return erro("Faltou o nome do arquivo.", 400)
        nome = nome_arquivo_seguro(nome)
        extensao = Path(nome).suffix.lower()
        if extensao not in EXTENSOES_ENTRADA:
            return erro(f"O arquivo {nome} tem uma extensão não permitida.", 415)
        declarado = request.headers.get("content-length", "").strip()
        if not declarado:
            return erro("O envio precisa informar o tamanho do arquivo (Content-Length).", 411)
        if not declarado.isdigit():
            return erro("O tamanho informado do arquivo não é válido.", 400)
        teto = lim.demo_arquivo if extensao == ".dem" else lim.video_arquivo
        try:
            destino, total = await receber_arquivo(request.stream(), entrada, nome, int(declarado), teto)
        except ErroEnvio as exc:
            return erro(exc.mensagem, exc.status)
        except ClientDisconnect:
            return erro("A conexão foi interrompida durante o envio.", 499)
        except OSError:
            return erro("Não consegui salvar o envio. Tente de novo ou use \"Escolher no computador\".", 507)
        resolvido_up = str(destino.resolve())
        app.state.arquivos_escolhidos.add(resolvido_up)
        return {"itens": [{"caminho": resolvido_up, "nome": destino.name, "tamanho": total, "tipo": "demo" if extensao == ".dem" else "video"}]}

    @app.post("/api/fila")
    async def api_adicionar_fila(body: dict[str, Any] = Body(...)):
        arquivos = body.get("arquivos")
        if not isinstance(arquivos, list):
            return erro("Escolha pelo menos um arquivo.")
        if len(arquivos) > MAX_ARQUIVOS_FILA:
            return erro(f"São arquivos demais de uma vez (o limite é {MAX_ARQUIVOS_FILA}). Adicione em partes.")
        opcoes = body.get("opcoes", {})
        if not isinstance(opcoes, dict):
            return erro("As opções de processamento não são válidas.")
        try:
            caminhos: list[str | Path] = []
            for item in arquivos:
                if isinstance(item, str) and item.startswith("entrada:"):
                    nome = nome_arquivo_seguro(item[len("entrada:"):])
                    if nome != item[len("entrada:"):] or Path(nome).suffix.lower() not in EXTENSOES_ENTRADA:
                        return erro("O nome do arquivo na pasta entrada não é válido.")
                    origem = (entrada / nome).resolve()
                    try:
                        origem.relative_to(entrada.resolve())
                    except ValueError:
                        return erro("O arquivo não está dentro da pasta entrada.", 403)
                    if not origem.is_file():
                        return erro("Não encontrei esse arquivo na pasta entrada.", 404)
                    caminhos.append(str(origem))
                elif isinstance(item, str):
                    cand = validar_caminho_sem_unc(item)
                    resolvido = cand.resolve(strict=False)
                    dentro_dados = _dentro_dos_dados(resolvido)        # P2: da pasta de dados, nunca do programa
                    if not dentro_dados and str(resolvido) not in app.state.arquivos_escolhidos:
                        # R-002: `motivo` deixa a tela reconhecer este caso (depois de reiniciar a lista do seletor está vazia) sem depender do texto.
                        return JSONResponse({"erro": "Escolha o arquivo pelo botão 'Escolher no computador' ou coloque-o na pasta entrada.",
                                             "motivo": "precisa_seletor"}, status_code=403)
                    caminhos.append(str(resolvido))
                else:
                    return erro("A lista de arquivos não é válida.")
            jobs = fila.adicionar_varios(caminhos, opcoes)
            return {"itens": jobs}
        except PermissionError as exc:
            return erro(str(exc), 403)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except (ValueError, RuntimeError) as exc:
            return erro(str(exc), 409 if isinstance(exc, RuntimeError) else 400)

    @app.post("/api/fila/cancelar-tudo")
    async def api_cancelar_tudo():
        return fila.cancelar_tudo()

    @app.post("/api/fila/{job_id}/cancelar")
    async def api_cancelar_job(job_id: str):
        try:
            return fila.cancelar(job_id)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except ValueError as exc:
            return erro(str(exc), 409)

    @app.post("/api/fila/{job_id}/jogador")
    async def api_informar_jogador(job_id: str, body: dict[str, Any] = Body(...)):
        try:
            return fila.informar_jogador(job_id, body.get("jogador", ""))
        except ValueError as exc:
            return erro(str(exc), 400)

    @app.post("/api/videos/{video_id}/momento")
    async def api_momento(video_id: str, body: dict[str, Any] = Body(...)):
        try:
            # R-011: só bloqueia o que GRAVA nos kits (análise, edição de uma receita, gravação com edição); download de VOD e "esperando você" não.
            # Numa thread: a pergunta pega a trava das receitas, que pode estar ocupada com o disco.
            resultado = await asyncio.to_thread(
                dados.marcar_momento, video_id, body.get("foco"), body.get("acao", ""), lambda: not coordenador.mexe_nos_kits(),
                motivo=body.get("motivo"), avaliacao=body.get("avaliacao"), ajuste=body.get("ajuste"))
            return resultado
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except PermissionError as exc:
            return erro(str(exc), 403)
        except RuntimeError as exc:
            return erro(str(exc), 409)
        except ValueError as exc:
            return erro(str(exc), 400)

    # R-010: ler e gravar o config.toml (com fsync e cópia de segurança) fora do laço de eventos
    @app.get("/api/config")
    async def api_ler_config():
        try:
            return await asyncio.to_thread(configuracao.ler)
        except ErroConfig as exc:
            return erro(str(exc), 503)

    @app.post("/api/config")
    async def api_salvar_config(body: dict[str, Any] = Body(...)):
        try:
            return await asyncio.to_thread(configuracao.salvar, body.get("alteracoes", {}))
        except ErroConfig as exc:
            return erro(str(exc), 400)

    @app.post("/api/config/padroes")
    async def api_padroes_config(body: dict[str, Any] = Body(...)):
        try:
            return await asyncio.to_thread(configuracao.voltar_padrao, body.get("chaves", []))
        except ErroConfig as exc:
            return erro(str(exc), 400)

    # Primeira abertura (S5): a marca de "já viu" fica em dados/ (pasta não versionada) da pasta de dados da pessoa.
    def _arquivo_boas_vindas() -> Path:
        pasta_dados = raiz_dados / "dados"
        if pasta_dados.is_symlink() or pasta_dados.resolve(strict=False).parent != raiz_dados:
            raise PermissionError("A pasta dados precisa ser uma pasta normal dentro da pasta de dados do programa.")
        return pasta_dados / "boas_vindas.json"

    @app.get("/api/boas-vindas")
    async def api_ver_boas_vindas():
        try:
            ja_viu = _arquivo_boas_vindas().is_file()
        except PermissionError:
            ja_viu = True   # sem lugar seguro para guardar a marca: não insistir a cada abertura
        if ja_viu:
            return {"mostrar": False}
        marca = ""
        try:
            campo = next((c for c in configuracao.ler()["campos"] if c["secao"] == "shorts" and c["chave"] == "marca"), None)
            if campo and isinstance(campo.get("valor"), str):
                marca = campo["valor"]
        except ErroConfig:
            pass
        return {"mostrar": True, "marca": marca}

    @app.post("/api/boas-vindas")
    async def api_concluir_boas_vindas(body: dict[str, Any] = Body(default={})):
        # Com "marca" no corpo, grava o @ do canal em shorts.marca (mesma validação da tela Configurar).
        if "marca" in body:
            marca = body.get("marca")
            if not isinstance(marca, str):
                return erro("Digite o @ do canal como texto.")
            try:
                configuracao.salvar({"shorts.marca": marca.strip()})
            except ErroConfig as exc:
                return erro(str(exc), 400)
        try:
            arquivo = _arquivo_boas_vindas()
            arquivo.parent.mkdir(parents=True, exist_ok=True)
            arquivo.write_text(json.dumps({"visto_em": time.strftime("%Y-%m-%d %H:%M")}) + "\n", encoding="utf-8")
        except (OSError, PermissionError):
            return erro("Não consegui guardar que você já viu as boas-vindas.", 500)
        return {"sucesso": True}

    @app.post("/api/janela-fechada")
    async def api_janela_fechada():
        # A página manda isto ao ser fechada. Se ela não voltar logo (F5 volta), o programa encerra sozinho,
        # a não ser que haja processamento (ui/__main__.py, vigia_encerramento).
        app.state.janela_fechada_em = time.monotonic()
        return {"sucesso": True}

    @app.post("/api/encerrar")
    async def api_encerrar():
        """Botão "Encerrar o programa": cancela o que estiver rodando (A-001: todos os gerenciadores) e pede ao servidor para sair.
        As receitas vivas ficam "interrompida" (dá para Retomar); o fim do servidor espera os processos e derruba o que não sair."""
        await asyncio.to_thread(coordenador.cancelar_tudo)
        app.state.pedir_saida = True
        return {"sucesso": True, "mensagem": "Programa encerrado. Pode fechar esta janela."}

    @app.post("/api/checar")
    async def api_checar():
        return await asyncio.to_thread(checagem, raiz)

    @app.post("/api/abrir-pasta")
    async def api_abrir_pasta(body: dict[str, Any] = Body(default={})):
        video_id = body.get("id", "")
        try:
            if video_id == "":
                destino = saida.resolve()
            elif isinstance(video_id, str):
                destino = dados.pasta_video(video_id)
            else:
                return erro("Pasta inválida.")
            if destino != saida.resolve() and saida.resolve() not in destino.parents:
                return erro("Pasta não permitida.", 403)
            await asyncio.to_thread(abrir_pasta, destino)
            return {"sucesso": True}
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except PermissionError as exc:
            return erro(str(exc), 403)
        except Exception:
            return erro("Não consegui abrir a pasta no Explorer.", 500)

    @app.get("/media/{video_id}/{relativo:path}")
    async def api_midia(video_id: str, relativo: str, request: Request):
        try:
            arquivo = caminho_midia_seguro(saida, video_id, relativo)
            return _resposta_arquivo(arquivo, request)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except PermissionError as exc:
            return erro(str(exc), 403)

    @app.get("/media-geral/{relativo:path}")
    async def api_midia_geral(relativo: str, request: Request):
        try:
            arquivo = caminho_biblioteca_seguro(saida, relativo)
            return _resposta_arquivo(arquivo, request)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except PermissionError as exc:
            return erro(str(exc), 403)

    return app


def secrets_compare(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# R-008 (revisão de 26/11/2026): não há mais `app = create_app()` aqui. Importar este módulo (testes, ferramentas, o verificador de tipos) criava
# saida/ e entrada/ na raiz e ligava a thread da fila. O app do programa é criado só em `ui/__main__.main()`, já com a porta escolhida.
