"""Checagem de qualidade dos arquivos gerados (E4), feita com o ffprobe ANTES de o arquivo novo trocar o antigo.

Um corte que sai com tamanho errado, sem imagem, sem som ou com duração fora do esperado não pode virar
"pronto": quem renderiza chama `exigir()` no arquivo temporário e, se reprovar, o corte bom que já existia
fica como está e o problema vai para o log.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .tools import FFPROBE

TOL_DURACAO = 1.5      # s: o fade e o arredondamento do ffmpeg mexem alguns décimos
TOL_FPS = 1.5
TAMANHO_MIN = 2048     # bytes: abaixo disso nem cabeçalho de vídeo cabe direito


@dataclass
class Esperado:
    largura: int | None = None
    altura: int | None = None
    duracao: float | None = None      # None = não confere a duração
    audio: bool = False               # True: o arquivo precisa ter trilha de som
    fps: float | None = None          # None = não confere


class QcReprovou(RuntimeError):
    """O arquivo gerado não passou na checagem (mensagem em português)."""


def inspecionar(caminho: Path) -> dict:
    """O que o ffprobe vê: {"video": {...} | None, "audio": bool, "duracao": float, "tamanho": int}."""
    caminho = Path(caminho)
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,avg_frame_rate,pix_fmt:format=duration,size",
         "-of", "json", str(caminho)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise QcReprovou(f"o ffprobe não conseguiu abrir o arquivo ({(r.stderr.strip().splitlines() or ['sem detalhe'])[-1]})")
    j = json.loads(r.stdout or "{}")
    faixas = j.get("streams", [])
    video = next((s for s in faixas if s.get("codec_type") == "video"), None)
    fmt = j.get("format", {})
    try:
        dur = float(fmt.get("duration"))
    except (TypeError, ValueError):
        dur = 0.0
    try:
        tam = int(fmt.get("size"))
    except (TypeError, ValueError):
        tam = caminho.stat().st_size if caminho.exists() else 0
    return {"video": video, "audio": any(s.get("codec_type") == "audio" for s in faixas), "duracao": dur, "tamanho": tam}


def _taxa(txt) -> float:
    try:
        n, d = str(txt).split("/")
        return float(n) / float(d)
    except (ValueError, ZeroDivisionError):
        return 0.0


def verificar(caminho: Path, esp: Esperado) -> list[str]:
    """Lista de problemas (vazia = passou)."""
    caminho = Path(caminho)
    if not caminho.is_file():
        return ["o arquivo não foi criado"]
    if caminho.stat().st_size < TAMANHO_MIN:
        return [f"o arquivo está vazio ou quase ({caminho.stat().st_size} bytes)"]
    try:
        info = inspecionar(caminho)
    except QcReprovou as e:
        return [str(e)]
    problemas: list[str] = []
    v = info["video"]
    if v is None:
        return ["não tem trilha de vídeo"]
    if esp.largura and esp.altura and (v.get("width"), v.get("height")) != (esp.largura, esp.altura):
        problemas.append(f"tamanho {v.get('width')}x{v.get('height')} em vez de {esp.largura}x{esp.altura}")
    if esp.duracao is not None:
        if info["duracao"] <= 0:
            problemas.append("sem duração")
        elif abs(info["duracao"] - esp.duracao) > TOL_DURACAO:
            problemas.append(f"duração {info['duracao']:.1f}s em vez de ~{esp.duracao:.1f}s")
    if esp.audio and not info["audio"]:
        problemas.append("ficou sem som (o vídeo original tem)")
    if esp.fps:
        fps = _taxa(v.get("avg_frame_rate"))
        if abs(fps - esp.fps) > TOL_FPS:
            problemas.append(f"{fps:.0f} fps em vez de {esp.fps:.0f}")
    if v.get("pix_fmt") not in (None, "yuv420p"):
        problemas.append(f"formato de cor {v.get('pix_fmt')} (os players esperam yuv420p)")
    return problemas


def exigir(caminho: Path, esp: Esperado, nome: str = "") -> None:
    """Levanta `QcReprovou` se o arquivo não passar."""
    problemas = verificar(caminho, esp)
    if problemas:
        raise QcReprovou(f"{nome or Path(caminho).name} reprovou na checagem: " + "; ".join(problemas))


def tem_audio(video: str) -> bool:
    """O vídeo de ENTRADA tem trilha de som? (se não tem, não dá para cobrar som do corte)"""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type",
                        "-of", "csv=p=0", video], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0 and "audio" in r.stdout
