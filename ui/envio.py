"""B3: recebe UM arquivo grande em fluxo, direto para a pasta `entrada/`.

O envio antigo (multipart) passava pela pasta temporária do sistema (disco C:) antes de chegar aqui, e o arquivo era gravado duas vezes.
Aqui o corpo do pedido (os bytes do arquivo) é gravado em blocos num `.part` dentro da própria `entrada/`, conferindo o espaço livre e os tetos
a cada bloco, e só no fim o nome final é reservado (sem sobrescrever outro envio). Qualquer falha ou queda de conexão apaga o `.part`.
"""
from __future__ import annotations

import asyncio
import errno
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from cs2clip.tools import substituir_arquivo

BLOCO_GRAVACAO = 1024 * 1024                  # junta os pedaços que chegam (≈64 KB) em blocos de 1 MiB antes de gravar
CONFERIR_ESPACO_A_CADA = 16 * 1024 * 1024     # confere o espaço livre a cada 16 MiB recebidos
ESPACO_MINIMO_DURANTE = 64 * 1024 * 1024      # se sobrar menos que isso no meio do envio, para (o disco pode ter sido usado por outro programa)
FOLGA_PADRAO = 1024 * 1024 * 1024             # antes de começar: tamanho do arquivo + 1 GiB livre


class ErroEnvio(Exception):
    """Falha do envio com o código HTTP e a mensagem (em português) que a interface mostra."""
    def __init__(self, status: int, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem


def sem_espaco() -> ErroEnvio:
    return ErroEnvio(507, "Não há espaço livre suficiente no disco.")


def reservar_nome_final(temporario: Path, alvo_base: Path) -> Path:
    """Dá ao `.part` o nome final SEM sobrescrever outro arquivo (nem de um envio simultâneo): `nome.ext`, depois `nome (2).ext`, `nome (3).ext`…"""
    destino = alvo_base
    contador = 2
    while True:
        try:
            try:
                os.link(temporario, destino)
            except FileExistsError:
                raise
            except OSError:
                # exFAT/FAT32 e pasta de rede não aceitam hard link: reserva o nome com O_EXCL (falha se já existir) e move o temporário para cima da reserva.
                fd_final = os.open(destino, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd_final)
                try:
                    substituir_arquivo(temporario, destino)   # o nome já é nosso (reserva O_EXCL); tolera a recusa momentânea do Windows (antivírus no arquivo recém-criado)
                except OSError:
                    destino.unlink(missing_ok=True)
                    raise
            temporario.unlink(missing_ok=True)
            return destino
        except FileExistsError:
            destino = alvo_base.with_name(f"{alvo_base.stem} ({contador}){alvo_base.suffix}")
            contador += 1


def _finalizar(arquivo, temporario: Path, alvo_base: Path) -> Path:
    arquivo.flush()
    os.fsync(arquivo.fileno())
    arquivo.close()
    return reservar_nome_final(temporario, alvo_base)


async def receber(fluxo: AsyncIterator[bytes], entrada: Path, nome: str, declarado: int, teto: int, folga: int = FOLGA_PADRAO) -> tuple[Path, int]:
    """Grava `fluxo` em `entrada/<nome>` e devolve (caminho final, bytes). `nome` já vem seguro (nome_arquivo_seguro) e com extensão conferida.
    `declarado` = Content-Length. Recusa antes de gravar se passa do `teto` ou não cabe no disco; durante, se vier mais ou menos que o declarado."""
    if declarado > teto:
        raise ErroEnvio(413, f"O arquivo {nome} é grande demais (o limite é {_tamanho(teto)}).")
    # R-010 (revisão de 26/11): o disco (espaço livre, criar o .part, gravar cada bloco de 1 MiB) é tocado numa thread, nunca no laço de eventos:
    # com o disco externo lento ou o antivírus, as outras rotas (o /api/fila a cada 1 s) seguem respondendo durante o envio
    if (await asyncio.to_thread(shutil.disk_usage, entrada)).free < declarado + folga:
        raise sem_espaco()
    fd, temporario_nome = await asyncio.to_thread(tempfile.mkstemp, prefix="upload-", suffix=".part", dir=str(entrada))
    temporario = Path(temporario_nome)
    arquivo = None
    try:
        arquivo = os.fdopen(fd, "wb")
        total = 0
        desde_conferencia = 0
        pendente = bytearray()
        async for pedaco in fluxo:
            total += len(pedaco)
            if total > declarado:
                raise ErroEnvio(400, "O envio trouxe mais dados do que o tamanho informado.")
            pendente += pedaco
            desde_conferencia += len(pedaco)
            if len(pendente) >= BLOCO_GRAVACAO:
                await asyncio.to_thread(arquivo.write, pendente)     # espera a gravação acabar antes de reusar o buffer
                pendente.clear()
            if desde_conferencia >= CONFERIR_ESPACO_A_CADA:
                desde_conferencia = 0
                if (await asyncio.to_thread(shutil.disk_usage, entrada)).free < ESPACO_MINIMO_DURANTE:
                    raise sem_espaco()
        if pendente:
            await asyncio.to_thread(arquivo.write, pendente)
        if total != declarado:
            raise ErroEnvio(400, "O envio terminou antes do tamanho informado. Tente de novo.")
        destino = await asyncio.to_thread(_finalizar, arquivo, temporario, entrada / nome)    # fsync e troca do nome fora do laço de eventos
        return destino, total
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ENOSPC:
            raise sem_espaco() from exc
        raise
    finally:
        try:
            if arquivo is None:
                os.close(fd)                         # o fdopen nem chegou a abrir: não vaza o descritor
            elif not arquivo.closed:
                arquivo.close()                      # pode levantar (ENOSPC ao descarregar o buffer): o .part é apagado de qualquer jeito
        finally:
            temporario.unlink(missing_ok=True)       # no sucesso o nome do .part já foi removido (hard link/troca); aqui só limpa sobra


def _tamanho(n: int) -> str:
    from .seguranca import formatar_tamanho
    return formatar_tamanho(n)
