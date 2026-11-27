"""Rotas do Plano E (caixa única, trabalhos, partidas novas, músicas), separadas do `servidor.py` para ele não crescer sem fim.

`registrar_rotas_novas` recebe do `create_app` só o que as rotas usam; o comportamento é o mesmo de quando elas moravam lá.
"""
from __future__ import annotations

import asyncio
import hmac
import json
from pathlib import Path
from typing import Any
from collections.abc import Callable

from cs2clip import musicas_catalogo as mc
from cs2clip import steam_local
from cs2clip.montagem import trocar_musica_do_video
from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .seguranca import nome_reservado_windows, validar_caminho_sem_unc


def _seletor_musica() -> list[dict[str, Any]]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError("O seletor de arquivos do Windows não está disponível.") from exc
    janela = tk.Tk()
    janela.withdraw()
    try:
        try:
            janela.attributes("-topmost", True)
        except Exception:
            pass
        selecionado = filedialog.askopenfilename(parent=janela, title="Escolha a música (mp3, wav, ogg, flac, m4a)",
                                                 filetypes=[("Músicas", "*.mp3 *.wav *.ogg *.flac *.m4a *.opus"), ("Todos os arquivos", "*.*")])
    finally:
        janela.destroy()
    if not selecionado:
        return []
    caminho = Path(selecionado)
    if caminho.suffix.lower() not in mc.EXTENSOES or not caminho.is_file():
        return []
    return [{"caminho": str(caminho.resolve()), "nome": caminho.name, "tamanho": caminho.stat().st_size}]


def _pasta_musicas_da_pessoa(pasta_fabrica: Path, raiz: Path, dados: Path | None) -> Path:
    """Onde a tela grava as músicas da pessoa. Modo instalado (`dados` fora do programa): `<dados>\\musicas`. Portátil (`dados` ausente
    ou igual a `raiz`): a regra de sempre do catálogo (`pasta_da_pessoa`), que dá a mesma `musicas/` da fábrica."""
    if dados is None or Path(dados).resolve() == Path(raiz).resolve():
        return mc.pasta_da_pessoa(pasta_fabrica)
    return Path(dados) / "musicas"


def registrar_rotas_novas(app: FastAPI, *, receitas: Any, raiz: Path, erro: Callable[..., JSONResponse], trava_seletor: Any, escolhidos: set[str],
                          music_chooser: Callable[[], list[dict[str, Any]]] | None = None, pasta_video: Callable[[str], Path] | None = None,
                          conta_aberta: Callable[[], int] | None = None, dados: Path | None = None) -> None:
    # P2 (duas bases): `raiz` = a pasta do programa; `dados` = a pasta de dados da pessoa (None = a própria `raiz`, como no portátil).
    # As rotas daqui que não são de música não tocam disco; as de música leem a fábrica em `raiz` e gravam só na pessoa, em `dados` (item 6).
    # ── E1/E2: a caixa única e os trabalhos (receitas) ──────────────────────────────────────────
    @app.post("/api/entrada/reconhecer")
    async def api_entrada_reconhecer(body: dict[str, Any] = Body(...)):
        return await asyncio.to_thread(receitas.plano, str(body.get("texto") or ""), {"sem_demo": bool(body.get("sem_demo")), "limpo": bool(body.get("limpo", True))})

    @app.get("/api/entrada/ultima-demo")
    async def api_entrada_ultima_demo():
        return {"demo": await asyncio.to_thread(receitas.ultima_demo)}

    @app.get("/api/partidas-novas")
    async def api_partidas_novas():
        return {"itens": await asyncio.to_thread(receitas.partidas_novas)}

    @app.post("/api/partidas-novas/dispensar")
    async def api_partidas_novas_dispensar(body: dict[str, Any] = Body(...)):
        try:
            receitas.dispensar(str(body.get("nome") or ""))
            return {"sucesso": True}
        except ValueError as exc:
            return erro(str(exc), 400)

    @app.post("/api/trabalhos")
    async def api_trabalhos_criar(body: dict[str, Any] = Body(...)):
        try:
            return await asyncio.to_thread(receitas.iniciar, str(body.get("texto") or ""),
                                           {"rounds": str(body.get("rounds") or ""), "todos": bool(body.get("todos")), "sem_demo": bool(body.get("sem_demo")),
                                            "limpo": bool(body.get("limpo", True))})
        except PermissionError as exc:
            return erro(str(exc), 403)
        except ValueError as exc:
            return erro(str(exc), 400)

    # ── C3: "Usar a conta aberta agora no Steam" (Configurar → Contas do Steam) ────────────────────
    @app.get("/api/steam/conta-aberta")
    async def api_steam_conta_aberta(request: Request):
        """Só LÊ a conta aberta no Steam (registro ActiveUser) e devolve o SteamID64: nunca grava nada nem mexe no Steam. Exige o token da sessão."""
        informado = request.headers.get("x-token", "")
        if not informado or not hmac.compare_digest(informado.encode("utf-8"), str(app.state.token).encode("utf-8")):
            return erro("A sessão expirou ou o pedido não é válido. Atualize a página.", 403)
        try:
            conta = int(await asyncio.to_thread(conta_aberta or steam_local.conta_logada))      # lida na hora da chamada
        except (OSError, ValueError, TypeError):
            conta = 0
        if conta <= 0:
            return {"aberta": False, "motivo": "O Steam está fechado. Abra o Steam na conta que você quer usar e clique de novo."}
        return {"aberta": True, "steamid64": str(steam_local.steamid64_da_conta(conta))}

    # ── E7: músicas com licença ──────────────────────────────────────────────────────────────────
    pasta_musicas = raiz / "musicas"                                  # a de fábrica (catalogo.json, somente leitura)
    pasta_musicas_pessoa = _pasta_musicas_da_pessoa(pasta_musicas, raiz, dados)   # catalogo_usuario.json, mp3 e historico.json da pessoa
    escolha_musica = music_chooser or _seletor_musica

    @app.get("/api/musicas")
    async def api_musicas():
        try:
            faixas = await asyncio.to_thread(mc.listar, pasta_musicas, pasta_musicas_pessoa)
        except mc.ErroCatalogo as exc:
            return erro(str(exc), 500)
        licencas = [{"chave": k, "nome": v["nome"], "url": v["url"], "credito": v["credito"], "link_obrigatorio": v["link_obrigatorio"], "aviso": v["aviso"]}
                    for k, v in mc.LICENCAS.items()]
        return {"faixas": faixas, "licencas": licencas, "resumo": {"total": len(faixas), "ativas": sum(f["ativa"] for f in faixas),
                                                                      "com_drop": sum(f["ativa"] and f["tem_drop"] for f in faixas),
                                                                      "no_sorteio": sum(f["entra_no_sorteio"] for f in faixas)}}

    @app.post("/api/musicas/escolher")
    async def api_musicas_escolher():
        if not trava_seletor.acquire(blocking=False):
            return erro("O seletor de arquivos já está aberto. Procure a janela dele na barra de tarefas.", 409)
        try:
            itens = await asyncio.to_thread(escolha_musica)
            for it in itens:
                if isinstance(it, dict) and isinstance(it.get("caminho"), str):
                    try:
                        escolhidos.add(str(validar_caminho_sem_unc(it["caminho"]).resolve()))
                    except PermissionError:
                        pass
            return {"itens": itens}
        except RuntimeError as exc:
            return erro(str(exc), 500)
        finally:
            trava_seletor.release()

    @app.post("/api/musicas/adicionar")
    async def api_musicas_adicionar(body: dict[str, Any] = Body(...)):
        caminho = str(body.get("caminho") or "")
        try:
            alvo = validar_caminho_sem_unc(caminho).resolve(strict=False)
        except PermissionError as exc:
            return erro(str(exc), 403)
        if str(alvo) not in escolhidos:
            return erro("Escolha o arquivo de música pelo botão 'Escolher arquivo de música'.", 403)
        if nome_reservado_windows(alvo.name) or nome_reservado_windows(mc.nome_seguro(alvo.name)):
            return erro("Esse nome de arquivo é reservado do Windows (como CON, NUL ou COM1). Renomeie a música e escolha de novo.", 400)
        credito = body.get("exige_credito")
        try:
            faixa = await asyncio.to_thread(
                mc.adicionar_faixa, pasta_musicas, alvo, str(body.get("licenca") or ""), str(body.get("autor") or ""), str(body.get("titulo") or ""),
                str(body.get("link") or ""), None if credito is None else bool(credito), str(body.get("estilo") or ""),
                credito_texto=str(body.get("credito_texto") or ""), pasta_pessoa=pasta_musicas_pessoa)
            return {"faixa": faixa}
        except mc.ErroCatalogo as exc:
            return erro(str(exc), 400)

    @app.post("/api/musicas/ativar")
    async def api_musicas_ativar(body: dict[str, Any] = Body(...)):
        try:
            return {"faixa": await asyncio.to_thread(mc.definir_ativa, pasta_musicas, str(body.get("arquivo") or ""), bool(body.get("ativa")),
                                                     pasta_musicas_pessoa)}
        except mc.ErroCatalogo as exc:
            return erro(str(exc), 404)

    @app.post("/api/videos/{video_id}/trocar-musica")
    async def api_videos_trocar_musica(video_id: str):
        """S1: solta a música presa a ESTE vídeo (a próxima montagem dele usa outra). Não refaz nada e não mexe nos outros vídeos."""
        if pasta_video is None:
            return erro("Esta função não está disponível.", 404)
        try:
            pasta = pasta_video(video_id)                              # valida o identificador (nada de caminho livre) e que o vídeo existe
        except PermissionError as exc:
            return erro(str(exc), 403)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        atual = ""
        try:
            bench = json.loads((pasta / "benchmark.json").read_text(encoding="utf-8"))
            atual = str(bench.get("montagem_musica") or "") if isinstance(bench, dict) else ""
        except (OSError, ValueError):
            pass
        try:
            r = await asyncio.to_thread(trocar_musica_do_video, pasta_musicas_pessoa, f"MONTAGEM_{video_id}", atual)   # o rodízio mora na pessoa
        except OSError as exc:
            return erro(str(exc), 500)
        return {**r, "limpo": video_id.endswith("_limpo")}

    @app.post("/api/videos/{video_id}/refazer-edicao")
    async def api_videos_refazer_edicao(video_id: str):
        """R1: cria um trabalho que refaz SÓ a edição de um kit de imagem limpa (sem abrir o CS2). Acompanha-se em Trabalhos."""
        if pasta_video is None:
            return erro("Esta função não está disponível.", 404)
        try:
            pasta_video(video_id)                                      # valida o identificador e que o vídeo existe
            return await asyncio.to_thread(receitas.refazer_edicao, video_id)
        except PermissionError as exc:
            return erro(str(exc), 403)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except ValueError as exc:
            return erro(str(exc), 400)
        except RuntimeError as exc:
            return erro(str(exc), 409)

    @app.get("/api/musicas/audio/{arquivo}")
    async def api_musicas_audio(arquivo: str):
        """Só toca faixa que está no catálogo (nada de caminho livre): o arquivo da pessoa, senão o de fábrica."""
        try:
            nomes = {f["arquivo"] for f in await asyncio.to_thread(mc.listar, pasta_musicas, pasta_musicas_pessoa)}
        except mc.ErroCatalogo:
            return erro("Catálogo ilegível.", 500)
        caminho = mc.caminho_da_faixa(arquivo, pasta_musicas, pasta_musicas_pessoa) if arquivo in nomes else None
        if caminho is None:
            return erro("Música não encontrada.", 404)
        return FileResponse(caminho)

    @app.get("/api/trabalhos")
    async def api_trabalhos_listar():
        return {"itens": await asyncio.to_thread(receitas.listar)}     # fora do laço: a trava das receitas pode estar ocupada com o disco

    @app.get("/api/trabalhos/{trabalho_id}")
    async def api_trabalhos_obter(trabalho_id: str):
        try:
            return await asyncio.to_thread(receitas.obter, trabalho_id)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)

    @app.post("/api/trabalhos/{trabalho_id}/cancelar")
    async def api_trabalhos_cancelar(trabalho_id: str):
        try:
            return await asyncio.to_thread(receitas.cancelar, trabalho_id)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except RuntimeError as exc:
            return erro(str(exc), 409)

    @app.post("/api/trabalhos/{trabalho_id}/confirmar")
    async def api_trabalhos_confirmar(trabalho_id: str, body: dict[str, Any] = Body(default={})):
        try:
            return await asyncio.to_thread(receitas.confirmar, trabalho_id, bool(body.get("aceitar", True)))
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except RuntimeError as exc:
            return erro(str(exc), 409)

    @app.post("/api/trabalhos/{trabalho_id}/retomar")
    async def api_trabalhos_retomar(trabalho_id: str):
        try:
            return await asyncio.to_thread(receitas.retomar, trabalho_id)
        except FileNotFoundError as exc:
            return erro(str(exc), 404)
        except RuntimeError as exc:
            return erro(str(exc), 409)
