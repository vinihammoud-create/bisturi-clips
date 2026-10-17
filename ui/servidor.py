"""Servidor FastAPI local da interface Bisturi Clips."""
from __future__ import annotations

import asyncio
import errno
import json
import mimetypes
from contextlib import asynccontextmanager
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles

from .configuracao import ConfiguracaoProjeto, ErroConfig
from .dados import DadosProjeto
from .fila import GerenciadorFila, pid_vivo
from .seguranca import (
    EXTENSOES_ENTRADA,
    caminho_biblioteca_seguro,
    caminho_midia_seguro,
    host_permitido,
    nome_arquivo_seguro,
    novo_token,
    origin_permitida,
)


# Páginas geradas pelo próprio programa (autocontidas: script e estilo embutidos). Só elas recebem uma política
# mais frouxa, e sem nenhuma rede (connect-src 'none'): assim não conseguem ler o token da interface nem chamar a API.
PAGINAS_PROPRIAS = {"revisao.html", "relatorio.html"}
CSP_INTERFACE = ("default-src 'self'; script-src 'self'; style-src-elem 'self'; style-src-attr 'unsafe-inline'; "
                 "media-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
                 "frame-ancestors 'none'")
CSP_PAGINA_PROPRIA = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; "
                      "media-src 'self'; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'")

MENSAGEM_ERRO = "Não consegui concluir essa ação. Confira os dados e tente novamente."


def erro(mensagem: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"erro": mensagem}, status_code=status)


def _carregar_core_config() -> tuple[Callable[[Path], Any] | None, Callable[[], Any] | None]:
    try:
        from cs2clip.config import Config, load_config
        return load_config, Config
    except (ImportError, AttributeError):
        return None, None


def _executar_checagem(raiz: Path) -> dict[str, Any]:
    """Roda `python -m cs2clip --checar` (confere ffmpeg, GPU, disco...) e devolve o texto pronto para mostrar."""
    ambiente = os.environ.copy()
    ambiente["PYTHONIOENCODING"] = "utf-8"
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run([sys.executable, "-m", "cs2clip", "--checar"], cwd=str(raiz), env=ambiente, shell=False,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return {"codigo": 1, "texto": "Não consegui rodar a verificação. Confira se o programa foi instalado (instalar.bat)."}
    return {"codigo": r.returncode, "texto": (r.stdout or r.stderr or "").strip()}


def _abrir_explorer(caminho: Path) -> None:
    if hasattr(os, "startfile"):
        os.startfile(str(caminho))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["explorer.exe", str(caminho)], shell=False)  # noqa: S603


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
) -> FastAPI:
    raiz = Path(root_dir).resolve() if root_dir is not None else Path(__file__).resolve().parent.parent
    saida = raiz / "saida"
    entrada = raiz / "entrada"
    for pasta_permitida in (saida, entrada):
        if pasta_permitida.is_symlink() or pasta_permitida.resolve(strict=False).parent != raiz:
            raise RuntimeError("As pastas entrada e saida precisam ser pastas normais dentro da raiz do projeto.")
    saida.mkdir(parents=True, exist_ok=True)
    entrada.mkdir(parents=True, exist_ok=True)
    token = novo_token()
    dados = DadosProjeto(raiz)
    fila = GerenciadorFila(raiz, popen_factory=popen_factory, command_builder=command_builder)
    core_load, core_cls = _carregar_core_config()
    configuracao = ConfiguracaoProjeto(raiz, load_config_fn=load_config_fn or core_load, config_cls=config_cls or core_cls)
    escolha = chooser or _seletor_nativo
    abrir_pasta = open_folder_fn or _abrir_explorer
    checagem = checar_fn or _executar_checagem

    @asynccontextmanager
    async def ciclo_vida(_app: FastAPI):
        try:
            yield
        finally:
            fila.encerrar()

    app = FastAPI(title="Bisturi Clips", docs_url=None, redoc_url=None, openapi_url=None, lifespan=ciclo_vida)

    @app.exception_handler(StarletteHTTPException)
    async def erro_http(_request: Request, exc: StarletteHTTPException):
        mensagens = {404: "Não encontrei esse endereço ou arquivo.", 405: "Esta ação não é permitida neste endereço.", 413: "O arquivo enviado é maior do que o permitido pelo servidor.", 422: "Os dados enviados não estão no formato esperado."}
        return JSONResponse({"erro": mensagens.get(exc.status_code, "Não consegui concluir essa ação.")}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def erro_validacao(_request: Request, _exc: RequestValidationError):
        return JSONResponse({"erro": "Confira os dados preenchidos e tente novamente."}, status_code=422)

    @app.exception_handler(Exception)
    async def erro_interno(_request: Request, _exc: Exception):
        return JSONResponse({"erro": "Ocorreu um erro inesperado. Tente novamente."}, status_code=500)

    app.state.raiz = raiz
    app.state.port = port
    app.state.token = token
    app.state.dados = dados
    app.state.fila = fila
    app.state.configuracao = configuracao

    @app.middleware("http")
    async def proteger_requisicao(request: Request, call_next):
        host = request.headers.get("host")
        if not host_permitido(host, app.state.port):
            return JSONResponse({"erro": "Acesso recusado: use o endereço local da interface."}, status_code=403)
        origin = request.headers.get("origin")
        if not origin_permitida(origin, app.state.port):
            return JSONResponse({"erro": "Acesso recusado: a origem desta solicitação não é permitida."}, status_code=403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            informado = request.headers.get("x-token", "")
            if not informado or not secrets_compare(informado, app.state.token):
                return JSONResponse({"erro": "A sessão expirou ou o pedido não é válido. Atualize a página."}, status_code=403)
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

    @app.get("/api/indice")
    async def api_indice():
        # Cada cartão da biblioteca ganha uma miniatura (capa), escolhida aqui: o índice do núcleo não tem.
        itens = []
        for item in dados.indice():
            video_id = item.get("id")
            itens.append({**item, "capa": dados.capa(video_id) if isinstance(video_id, str) else None})
        return {"itens": itens}

    @app.get("/api/entrada")
    async def api_entrada():
        return {"itens": dados.listar_entrada()}

    @app.get("/api/log")
    async def api_log():
        return {"linhas": dados.linhas_log(30)}

    @app.get("/api/videos/{video_id}")
    async def api_video(video_id: str):
        try:
            indice = next((item for item in dados.indice() if item.get("id") == video_id), {})
            momentos = dados.momentos(video_id)
            partes = dados.partes(video_id)
            demo = dados.demo(video_id)
            pasta = dados.pasta_video(video_id)
            montagem = f"MONTAGEM_{video_id}.mp4"
            links = {
                "revisao": (pasta / "revisao.html").is_file(),
                "capitulos": (pasta / "capitulos_youtube.txt").is_file(),
                "montagem": (pasta / montagem).is_file(),
                "montagem_arquivo": montagem if (pasta / montagem).is_file() else None,
                "fails_arquivo": f"FAILS_{video_id}.mp4" if (pasta / f"FAILS_{video_id}.mp4").is_file() else None,
            }
            return {"id": video_id, "indice": indice, "momentos": momentos, "partes": partes, "demo": demo, "links": links,
                    "publicar": dados.publicar(video_id)}
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except PermissionError as exc:
            return erro(str(exc), 403)

    def _estado_fila_seguro() -> dict[str, Any]:
        estado = fila.listar()
        progresso = estado.get("progresso") or dados.progresso()
        if estado.get("externo") and progresso.get("estado") == "rodando":
            progresso = estado["externo"].get("progresso", progresso)
        if progresso.get("estado") == "rodando" and not pid_vivo(progresso.get("pid")):
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

    @app.post("/api/escolher-arquivo")
    async def api_escolher_arquivo():
        try:
            itens = await asyncio.to_thread(escolha)
            return {"itens": itens}
        except RuntimeError as exc:
            return erro(str(exc), 503)
        except Exception:
            return erro("Não consegui abrir o seletor de arquivos. Tente arrastar o arquivo para a janela.", 500)

    @app.post("/api/upload")
    async def api_upload(files: list[UploadFile] = File(...)):
        salvos: list[dict[str, Any]] = []
        caminhos_temporarios: list[Path] = []
        try:
            for upload in files:
                nome = nome_arquivo_seguro(upload.filename or "arquivo")
                if Path(nome).suffix.lower() not in EXTENSOES_ENTRADA:
                    raise ValueError(f"O arquivo {nome} tem uma extensão não permitida.")
                tamanho_previsto = getattr(upload, "size", None)
                if isinstance(tamanho_previsto, int) and tamanho_previsto > shutil.disk_usage(entrada).free:
                    raise OSError("Não há espaço livre suficiente para receber o arquivo.")
                alvo_base = entrada / nome
                alvo = alvo_base
                contador = 2
                while alvo.exists():
                    sufixo = alvo_base.suffix
                    alvo = alvo_base.with_name(f"{alvo_base.stem} ({contador}){sufixo}")
                    contador += 1
                fd, temporario_nome = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=str(entrada))
                os.close(fd)
                temporario = Path(temporario_nome)
                caminhos_temporarios.append(temporario)
                total = 0
                try:
                    with temporario.open("wb") as destino:
                        while True:
                            bloco = await upload.read(1024 * 1024)
                            if not bloco:
                                break
                            if shutil.disk_usage(entrada).free < len(bloco):
                                raise OSError("Não há espaço livre suficiente para receber o arquivo.")
                            destino.write(bloco)
                            total += len(bloco)
                        destino.flush()
                        os.fsync(destino.fileno())
                    # Reservar o nome sem sobrescrever outro upload concorrente.
                    destino_final = alvo
                    contador = 2
                    while True:
                        try:
                            try:
                                os.link(temporario, destino_final)
                            except FileExistsError:
                                raise
                            except OSError:
                                # exFAT/FAT32 e pasta de rede não aceitam hard link: reserva o nome com O_EXCL
                                # (falha se já existir) e move o temporário para cima da reserva.
                                fd_final = os.open(destino_final, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                                os.close(fd_final)
                                try:
                                    os.replace(temporario, destino_final)
                                except OSError:
                                    destino_final.unlink(missing_ok=True)
                                    raise
                            break
                        except FileExistsError:
                            suf = alvo_base.suffix
                            destino_final = alvo_base.with_name(f"{alvo_base.stem} ({contador}){suf}")
                            contador += 1
                    temporario.unlink(missing_ok=True)
                    salvos.append({"caminho": str(destino_final.resolve()), "nome": destino_final.name, "tamanho": total, "tipo": "demo" if destino_final.suffix.lower() == ".dem" else "video"})
                finally:
                    await upload.close()
            return {"itens": salvos}
        except ValueError as exc:
            for p in caminhos_temporarios:
                p.unlink(missing_ok=True)
            for item in salvos:
                Path(item["caminho"]).unlink(missing_ok=True)
            return erro(str(exc), 415)
        except OSError as exc:
            for p in caminhos_temporarios:
                p.unlink(missing_ok=True)
            for item in salvos:
                Path(item["caminho"]).unlink(missing_ok=True)
            sem_espaco = getattr(exc, "errno", None) == errno.ENOSPC or "espaço" in str(exc).lower()
            mensagem = ("Não há espaço livre suficiente no disco." if sem_espaco
                        else "Não consegui salvar o envio. Tente de novo ou use \"Escolher no computador\".")
            return erro(mensagem, 507)

    @app.post("/api/fila")
    async def api_adicionar_fila(body: dict[str, Any] = Body(...)):
        arquivos = body.get("arquivos")
        if not isinstance(arquivos, list):
            return erro("Escolha pelo menos um arquivo.")
        opcoes = body.get("opcoes", {})
        if not isinstance(opcoes, dict):
            return erro("As opções de processamento não são válidas.")
        try:
            caminhos: list[str] = []
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
                    caminhos.append(item)
                else:
                    return erro("A lista de arquivos não é válida.")
            jobs = fila.adicionar_varios(caminhos, opcoes)
            return {"itens": jobs}
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
            resultado = dados.marcar_momento(video_id, body.get("foco"), body.get("acao", ""), lambda: not fila.esta_processando())
            return resultado
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except PermissionError as exc:
            return erro(str(exc), 403)
        except RuntimeError as exc:
            return erro(str(exc), 409)
        except ValueError as exc:
            return erro(str(exc), 400)

    @app.get("/api/config")
    async def api_ler_config():
        try:
            return configuracao.ler()
        except ErroConfig as exc:
            return erro(str(exc), 503)

    @app.post("/api/config")
    async def api_salvar_config(body: dict[str, Any] = Body(...)):
        try:
            return configuracao.salvar(body.get("alteracoes", {}))
        except ErroConfig as exc:
            return erro(str(exc), 400)

    @app.post("/api/config/padroes")
    async def api_padroes_config(body: dict[str, Any] = Body(...)):
        try:
            return configuracao.voltar_padrao(body.get("chaves", []))
        except ErroConfig as exc:
            return erro(str(exc), 400)

    # Primeira abertura (S5): a marca de "já viu" fica em dados/ (pasta não versionada).
    def _arquivo_boas_vindas() -> Path:
        pasta_dados = raiz / "dados"
        if pasta_dados.is_symlink() or pasta_dados.resolve(strict=False).parent != raiz:
            raise PermissionError("A pasta dados precisa ser uma pasta normal dentro da raiz do projeto.")
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


app = create_app()
