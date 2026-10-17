"""Localiza ffmpeg/ffprobe (pasta do projeto, pasta pai, PATH ou a pasta de atalhos do winget)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# o instalador põe o FFmpeg pelo winget; o PATH novo só vale em janelas abertas depois, então olha aqui também
WINGET_LINKS = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"


def _find(name: str) -> str:
    for cand in (ROOT / "bin" / f"{name}.exe", ROOT.parent / f"{name}.exe"):
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    if not found and (WINGET_LINKS / f"{name}.exe").exists():
        found = str(WINGET_LINKS / f"{name}.exe")
    if not found:
        raise SystemExit(f"{name} não encontrado. Coloque {name}.exe em {ROOT / 'bin'} ou no PATH.")
    return found


# FFMPEG/FFPROBE são procurados só quando alguém os usa (não no import): sem o ffmpeg, o `--checar` ainda roda e
# mostra a lista inteira (Python, GPU, disco, módulos). Quem importa FFMPEG sem ele instalado recebe o SystemExit.
def __getattr__(nome: str) -> str:
    if nome in ("FFMPEG", "FFPROBE"):
        achado = _find(nome.lower())
        globals()[nome] = achado   # guarda: a próxima leitura não procura de novo
        return achado
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")


# O OpenCV no Windows não abre caminhos com acento (ex.: "edição"): lê/grava via memória.
def imread(path, flags: int = 1):
    import cv2
    import numpy as np
    data = np.fromfile(str(path), np.uint8)
    return cv2.imdecode(data, flags) if data.size else None


def imwrite(path, img, params: list[int] | None = None) -> None:
    import cv2
    ext = Path(path).suffix or ".png"
    ok, buf = cv2.imencode(ext, img, params or [])
    if ok:
        Path(path).write_bytes(buf.tobytes())
