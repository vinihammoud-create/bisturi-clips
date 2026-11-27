"""Controles de segurança para a interface local."""
from __future__ import annotations

import os
import unicodedata

import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse

from cs2clip.caminhos import VARIAVEL as VARIAVEL_DADOS

EXTENSOES_MIDIA = {".mp4", ".jpg", ".jpeg", ".png", ".json", ".txt", ".html", ".edl"}
EXTENSOES_ENTRADA = {
    ".mp4", ".mkv", ".mov", ".flv", ".ts", ".webm", ".avi", ".m4v",
    ".mpg", ".mpeg", ".wmv", ".mts", ".m2ts", ".3gp", ".ogv", ".dem",
}


def novo_token() -> str:
    return secrets.token_urlsafe(32)


def host_permitido(host_header: str | None, porta: int) -> bool:
    if not host_header:
        return False
    host = host_header.strip().lower()
    permitidos = {f"127.0.0.1:{porta}", f"localhost:{porta}"}
    return host in permitidos


def origin_permitida(origin: str | None, porta: int) -> bool:
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
        if parsed.scheme != "http" or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            return False
        return parsed.netloc.lower() in {f"127.0.0.1:{porta}", f"localhost:{porta}"}
    except (ValueError, AttributeError):
        return False


def nome_arquivo_seguro(nome: str) -> str:
    """Produz um nome compatível com Windows sem aceitar caminhos."""
    bruto = (nome or "arquivo").replace("\x00", "")
    # Não confiar no separador da plataforma onde o servidor está rodando.
    bruto = bruto.replace("\\", "/").split("/")[-1]
    proibidos = '<>:"/\\|?*'
    seguro = "".join("_" if c in proibidos or ord(c) < 32 or unicodedata.category(c) in {"Cf", "Zl", "Zp"} else c for c in bruto)   # Cf: ex. U+202E (inverte o texto na lista)
    seguro = seguro.strip().rstrip(". ")
    if seguro in {"", ".", ".."}:
        seguro = "arquivo"
    if nome_reservado_windows(seguro):
        seguro = f"_{seguro}"
    if len(seguro) > 240:                                    # corta o nome e mantém a extensão (senão "x…x.mp4" virava "x…x" e a extensão sumia)
        ext = Path(seguro).suffix
        seguro = (seguro[:240 - len(ext)] + ext) if 0 < len(ext) <= 16 else seguro[:240]
    return seguro


# Dispositivos do Windows: o nome vale mesmo com extensão, e espaços/pontos antes do ponto da extensão são ignorados ("con .mp4" abre o CON).
_RESERVADOS_WINDOWS = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                       *(f"COM{i}" for i in [*range(0, 10), "¹", "²", "³"]), *(f"LPT{i}" for i in [*range(0, 10), "¹", "²", "³"])}   # COM0-9, LPT0-9 e ¹²³ (lista oficial da Microsoft, "Naming files")


def nome_reservado_windows(nome: str) -> bool:
    """True se o Windows trataria este nome de arquivo como um dispositivo (CON, NUL, COM1...), mesmo com espaço ou ponto antes da extensão."""
    return str(nome or "").lstrip().split(".", 1)[0].rstrip(" ").upper() in _RESERVADOS_WINDOWS


@dataclass(frozen=True)
class LimitesEnvio:
    """Tetos do que a interface aceita receber. Folgados de propósito: uma live de CS2 gravada tem dezenas de GB e uma demo até ~500 MB;
    o que o dono usa de verdade nunca chega perto. O disco livre continua sendo conferido à parte."""
    corpo_json: int = 1024 * 1024                    # 1 MiB: o maior JSON real (config, momento, lista da fila) tem poucos KB
    profundidade_json: int = 32                      # os JSONs reais têm 2-4 níveis
    video_arquivo: int = 200 * 1024 ** 3             # 200 GiB por vídeo (uma live de várias horas tem dezenas de GB)
    demo_arquivo: int = 2 * 1024 ** 3                # 2 GiB por demo (uma demo de CS2 tem até ~500 MB)


ROTA_UPLOAD = "/api/upload/arquivo"      # a única rota que recebe arquivos (PUT, um por pedido, em fluxo; B3); confira com Grep antes de criar outra


def formatar_tamanho(n: int) -> str:
    for unidade, base in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= base:
            return f"{n / base:.0f} {unidade}"
    return f"{n} bytes"


class CorpoGrandeDemais(HTTPException):
    """Pedido maior que o teto (vira resposta 413 em português no servidor)."""
    def __init__(self, mensagem: str):
        super().__init__(status_code=413, detail=mensagem)
        self.mensagem = mensagem


def profundidade_json(corpo: bytes) -> int:
    """Aninhamento máximo de [] e {} no texto JSON, sem interpretar o JSON. Uma só passada com estado (dentro de aspas / escape): tempo linear
    e sem recursão, mesmo com aspas sem fechar ou barras repetidas. Colchetes dentro de texto não contam."""
    atual = maximo = 0
    dentro_de_texto = escapado = False
    for c in corpo:
        if dentro_de_texto:
            if escapado:
                escapado = False
            elif c == 0x5C:          # barra invertida
                escapado = True
            elif c == 0x22:          # "
                dentro_de_texto = False
        elif c == 0x22:
            dentro_de_texto = True
        elif c in (0x5B, 0x7B):      # [ {
            atual += 1
            if atual > maximo:
                maximo = atual
        elif c in (0x5D, 0x7D) and atual:   # ] }
            atual -= 1
    return maximo


def site_cruzado(sec_fetch_site: str | None) -> bool:
    """True se o navegador diz que o pedido veio de OUTRO site (Sec-Fetch-Site: cross-site). Sem o cabeçalho (linha de comando, testes), same-origin e none valem."""
    return (sec_fetch_site or "").strip().lower() == "cross-site"


class LimiteCorpo:
    """Middleware ASGI: recusa pedido que altera estado com corpo maior que o teto. JSON (qualquer corpo que não seja envio de arquivos) é lido inteiro, limitado
    a `corpo_json`, e confere a profundidade; o envio de arquivos (PUT em ROTA_UPLOAD) só é contado, sem guardar em memória. Funciona com ou sem Content-Length."""
    def __init__(self, app, limites: Callable[[], LimitesEnvio]):
        self.app = app
        self.limites = limites

    @staticmethod
    async def _recusar(scope, receive, send, status: int, mensagem: str) -> None:
        await JSONResponse({"erro": mensagem}, status_code=status)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await self.app(scope, receive, send)
        lim = self.limites()
        cab = {k: v for k, v in scope.get("headers", [])}
        # Só o envio de arquivos (PUT em ROTA_UPLOAD) recebe o teto grande (e só é contado); qualquer outra rota vale o teto de JSON, seja qual for o Content-Type.
        fluxo = scope.get("path") == ROTA_UPLOAD and scope["method"] == "PUT"
        teto = max(lim.video_arquivo, lim.demo_arquivo) if fluxo else lim.corpo_json
        mensagem = (f"O envio é grande demais (o limite é {formatar_tamanho(teto)} por arquivo)." if fluxo
                    else "O pedido é grande demais para esta interface.")
        declarado = cab.get(b"content-length", b"").strip()
        if declarado.isdigit() and int(declarado) > teto:
            return await self._recusar(scope, receive, send, 413, mensagem)
        if fluxo:
            contado = 0

            async def receive_contando():
                nonlocal contado
                msg = await receive()
                if msg["type"] == "http.request":
                    contado += len(msg.get("body", b""))
                    if contado > teto:
                        raise CorpoGrandeDemais(mensagem)
                return msg
            return await self.app(scope, receive_contando, send)
        partes: list[bytes] = []
        total = 0
        while True:
            msg = await receive()
            if msg["type"] != "http.request":
                return                                       # o cliente desconectou
            parte = msg.get("body", b"")
            total += len(parte)
            if total > teto:
                return await self._recusar(scope, receive, send, 413, mensagem)
            partes.append(parte)
            if not msg.get("more_body"):
                break
        corpo = b"".join(partes)
        # A contagem de profundidade lê bytes: só vale para UTF-8. Em UTF-16/32 uma letra pode conter o byte da aspa e esconder os colchetes
        # (o json do Python aceita essas codificações). O navegador e o app sempre mandam UTF-8.
        try:
            corpo.decode("utf-8")
            utf8 = b"\x00" not in corpo
        except UnicodeDecodeError:
            utf8 = False
        if not utf8:
            return await self._recusar(scope, receive, send, 400, "Os dados enviados não estão em texto UTF-8.")
        if profundidade_json(corpo) > lim.profundidade_json:
            return await self._recusar(scope, receive, send, 400, "Os dados enviados têm estruturas aninhadas demais.")
        enviado = False

        async def receive_do_corpo():
            nonlocal enviado
            if not enviado:
                enviado = True
                return {"type": "http.request", "body": corpo, "more_body": False}
            return await receive()
        await self.app(scope, receive_do_corpo, send)


def _parte_suspeita(parte: str) -> bool:
    """Parte de caminho que o Windows trata diferente do que está escrito: ':' (fluxo alternativo do NTFS), ponto ou espaço no fim (ignorados: `.cache.` abre a
    pasta `.cache`) e a própria pasta interna `.cache` (análise), em qualquer maiúscula/minúscula."""
    return ":" in parte or parte != parte.rstrip(". ") or parte.lower() == ".cache"


def id_de_video_suspeito(video_id: str) -> bool:
    """O id de um kit (nome da pasta em `saida/`) que o Windows abriria como OUTRA pasta: começa com ponto (oculta, `.cache`) ou termina com
    ponto/espaço (`kit.` abre `kit`). Uma regra só para o `/media` e para as rotas de `/api/videos/{id}` (G-009)."""
    return video_id.startswith(".") or _parte_suspeita(video_id)


def caminho_dentro(base: Path, caminho: Path) -> Path:
    base_resolvida = base.resolve()
    resolvido = caminho.resolve(strict=False)
    try:
        resolvido.relative_to(base_resolvida)
    except ValueError as exc:
        raise PermissionError("Caminho fora da pasta permitida.") from exc
    return resolvido


def caminho_midia_seguro(base_saida: Path, video_id: str, relativo: str) -> Path:
    if not isinstance(video_id, str) or not video_id or video_id in {".", ".."} or "\x00" in video_id:
        raise PermissionError("Identificador de vídeo inválido.")
    if "/" in video_id or "\\" in video_id or ":" in video_id or id_de_video_suspeito(video_id):
        raise PermissionError("Identificador de vídeo inválido.")        # G-009: nem pasta oculta (`.cache`) nem ponto/espaço no fim (o Windows ignora)
    if not isinstance(relativo, str) or not relativo or "\x00" in relativo:
        raise PermissionError("Caminho inválido.")
    path_bruto = Path(relativo)
    if path_bruto.is_absolute() or path_bruto.drive or relativo.startswith(("/", "\\")):
        raise PermissionError("Caminho absoluto não permitido.")
    partes = relativo.replace("\\", "/").split("/")
    if any(parte in {"", ".", ".."} for parte in partes) or any(_parte_suspeita(parte) for parte in partes):
        raise PermissionError("Caminho não permitido.")
    if Path(partes[-1]).suffix.lower() not in EXTENSOES_MIDIA:
        raise PermissionError("Tipo de arquivo não permitido.")
    pasta_video = caminho_dentro(base_saida, base_saida / video_id)
    if not pasta_video.is_dir():
        raise FileNotFoundError("Vídeo não encontrado.")
    alvo = caminho_dentro(pasta_video, pasta_video.joinpath(*partes))
    if not alvo.is_file():
        raise FileNotFoundError("Arquivo não encontrado.")
    return alvo


def caminho_biblioteca_seguro(base_saida: Path, relativo: str) -> Path:
    if not isinstance(relativo, str) or not relativo or "\x00" in relativo:
        raise PermissionError("Caminho inválido.")
    bruto = Path(relativo)
    if bruto.is_absolute() or bruto.drive or relativo.startswith(("/", "\\")):
        raise PermissionError("Caminho absoluto não permitido.")
    partes = relativo.replace("\\", "/").split("/")
    if any(parte in {"", ".", ".."} for parte in partes) or any(_parte_suspeita(parte) for parte in partes):
        raise PermissionError("Caminho não permitido.")
    if Path(partes[-1]).suffix.lower() not in EXTENSOES_MIDIA:
        raise PermissionError("Tipo de arquivo não permitido.")
    alvo = caminho_dentro(base_saida, base_saida.joinpath(*partes))
    if not alvo.is_file():
        raise FileNotFoundError("Arquivo não encontrado.")
    return alvo


def validar_caminho_sem_unc(arquivo: str | Path) -> Path:
    """Recusa caminhos de rede UNC (\\\\servidor\\pasta) e caracteres nulos antes de tocar no disco/rede (G-004)."""
    texto = str(arquivo or "").strip()
    if not texto or "\x00" in texto:
        raise PermissionError("Caminho de arquivo inválido.")
    if texto.replace("/", "\\").startswith(("\\\\", "\\??\\")):      # `\\srv`, `\\?\UNC`, `\\.\pipe` e `\??\UNC` (G-009): rede ou objeto do sistema
        raise PermissionError("Caminhos de rede (UNC) ou de dispositivo não são permitidos.")
    caminho = Path(texto)
    if caminho.drive.startswith(("\\\\", "//")):
        raise PermissionError("Caminhos de rede (UNC) não são permitidos.")
    return caminho


def ambiente_dos_filhos(pasta_dados: Path) -> dict[str, str]:
    """O ambiente dos processos filhos (núcleo, gravador, baixador, checagem): o de hoje e mais `BISTURI_DADOS` = a pasta de dados DESTA interface
    (P2, 26/11/2026). Assim o filho lê o mesmo config.toml e grava nas mesmas pastas que a interface mostra; o valor herdado, se houver, é trocado."""
    ambiente = os.environ.copy()
    ambiente[VARIAVEL_DADOS] = str(Path(pasta_dados).resolve())
    return ambiente


def python_de_console() -> Path:
    """O Python para rodar o núcleo (`cs2clip`) e a checagem. A interface abre com o pythonw.exe (sem janela
    preta), mas o processo filho precisa do python.exe: o pythonw não tem saída para o programa ler o andamento."""
    atual = Path(sys.executable)
    if atual.name.lower() == "pythonw.exe":
        irmao = atual.with_name("python.exe")
        if irmao.is_file():
            return irmao
    return atual
