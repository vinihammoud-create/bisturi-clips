"""Controles de segurança para a interface local."""
from __future__ import annotations

import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

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
    seguro = "".join("_" if c in proibidos or ord(c) < 32 else c for c in bruto)
    seguro = seguro.strip().rstrip(". ")
    if seguro in {"", ".", ".."}:
        seguro = "arquivo"
    # Nomes de dispositivo reservados do Windows.
    stem = seguro.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        seguro = f"_{seguro}"
    return seguro[:240]


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
    if "/" in video_id or "\\" in video_id or ":" in video_id:
        raise PermissionError("Identificador de vídeo inválido.")
    if not isinstance(relativo, str) or not relativo or "\x00" in relativo:
        raise PermissionError("Caminho inválido.")
    path_bruto = Path(relativo)
    if path_bruto.is_absolute() or path_bruto.drive or relativo.startswith(("/", "\\")):
        raise PermissionError("Caminho absoluto não permitido.")
    partes = relativo.replace("\\", "/").split("/")
    if any(parte in {"", ".", ".."} for parte in partes) or any(parte.lower() == ".cache" for parte in partes):
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
    if any(parte in {"", ".", ".."} for parte in partes) or any(parte.lower() == ".cache" for parte in partes):
        raise PermissionError("Caminho não permitido.")
    if Path(partes[-1]).suffix.lower() not in EXTENSOES_MIDIA:
        raise PermissionError("Tipo de arquivo não permitido.")
    alvo = caminho_dentro(base_saida, base_saida.joinpath(*partes))
    if not alvo.is_file():
        raise FileNotFoundError("Arquivo não encontrado.")
    return alvo


def python_de_console() -> Path:
    """O Python para rodar o núcleo (`cs2clip`) e a checagem. A interface abre com o pythonw.exe (sem janela
    preta), mas o processo filho precisa do python.exe: o pythonw não tem saída para o programa ler o andamento."""
    atual = Path(sys.executable)
    if atual.name.lower() == "pythonw.exe":
        irmao = atual.with_name("python.exe")
        if irmao.is_file():
            return irmao
    return atual
