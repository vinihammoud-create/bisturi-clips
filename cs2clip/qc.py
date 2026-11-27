"""Checagem de qualidade dos arquivos gerados (E4), feita com o ffprobe ANTES de o arquivo novo trocar o antigo.

Um corte que sai com tamanho errado, sem imagem, sem som ou com duração fora do esperado não pode virar
"pronto": quem renderiza chama `exigir()` no arquivo temporário e, se reprovar, o corte bom que já existia
fica como está e o problema vai para o log.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .tools import FFPROBE
from .tools import sem_janela

TOL_DURACAO = 1.5      # s: o fade e o arredondamento do ffmpeg mexem alguns décimos
TOL_FPS = 1.5
TAMANHO_MIN = 2048     # bytes: abaixo disso nem cabeçalho de vídeo cabe direito
SEGUNDOS_CONTEUDO = 8.0   # quanto do começo a checagem de conteúdo (tela preta, silêncio) decodifica


@dataclass
class Esperado:
    largura: int | None = None
    altura: int | None = None
    duracao: float | None = None      # None = não confere a duração
    audio: bool = False               # True: o arquivo precisa ter trilha de som
    fps: float | None = None          # None = não confere
    cor: str | None = None            # ex.: "bt709" (None = aceita sem tag, mas reprova espaço diferente de bt709)
    conteudo: bool = False            # True: reprova vídeo inteiramente preto nos primeiros segundos (G-008)
    silencio: bool = False            # True (com `conteudo`): reprova áudio inteiramente mudo nos primeiros segundos


class QcReprovou(RuntimeError):
    """O arquivo gerado não passou na checagem (mensagem em português)."""


def inspecionar(caminho: Path) -> dict:
    """O que o ffprobe vê: {"video": {...} | None, "audio": bool, "duracao": float, "tamanho": int}."""
    caminho = Path(caminho)
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,avg_frame_rate,pix_fmt,color_space,color_primaries,color_transfer,color_range:format=duration,size",
         "-of", "json", str(caminho)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
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
    cs = v.get("color_space")
    if cs not in (None, "unknown", "bt709"):
        problemas.append(f"espaço de cor {cs} em vez de bt709")
    if esp.cor:
        tags_cor = (v.get("color_space"), v.get("color_primaries"), v.get("color_transfer"))
        if tags_cor != (esp.cor, esp.cor, esp.cor):
            problemas.append(f"tags de cor {'/'.join(str(x) for x in tags_cor)} em vez de {esp.cor}")
    if esp.conteudo:   # decodifica só os primeiros segundos: custa menos de 1 s por arquivo
        for problema in verificar_conteudo(caminho, SEGUNDOS_CONTEUDO, silencio=esp.silencio and info["audio"]):
            if problema.startswith(NAO_CONFERIU):
                # a conferência não rodou (ffmpeg dela falhou ou travou): isso não prova defeito e NÃO rejeita o corte no render;
                # o relatório de qualidade (`qualidade.gate_conteudo`) confere de novo e pede revisão
                print(f"    [aviso] {Path(caminho).name}: {problema}", flush=True)
            else:
                problemas.append(problema)
    return problemas


def exigir(caminho: Path, esp: Esperado, nome: str = "") -> None:
    """Levanta `QcReprovou` se o arquivo não passar."""
    problemas = verificar(caminho, esp)
    if problemas:
        raise QcReprovou(f"{nome or Path(caminho).name} reprovou na checagem: " + "; ".join(problemas))


def tem_audio(video: str) -> bool:
    """O vídeo de ENTRADA tem trilha de som? (se não tem, não dá para cobrar som do corte)"""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type",
                        "-of", "csv=p=0", video], capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    return r.returncode == 0 and "audio" in r.stdout


def medir_loudness(caminho: Path) -> tuple[float, float] | None:
    """(volume integrado em LUFS, pico real em dBTP) do arquivo PRONTO, pelo filtro ebur128 do ffmpeg (EBU R128),
    já depois da compressão do áudio. None se o arquivo não tem som ou o ffmpeg não soube medir."""
    from .tools import FFMPEG
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(caminho), "-vn", "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    resumo = (r.stderr or "")[(r.stderr or "").rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", resumo)
    p = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", resumo)
    if r.returncode != 0 or not i or not p:
        return None
    return float(i.group(1)), max(-150.0, float(p.group(1)))   # som mudo: o ffmpeg diz "-inf" (17/11: antes virava "não consegui medir")


def medir_vmaf(referencia: Path, distorcido: Path, duracao: float | None = None) -> float | None:
    """G-008: escore VMAF (0-100) entre duas versões do MESMO trecho (mesma resolução e mesmo instante inicial),
    por exemplo o mesmo corte em dois níveis de compressão. Compara os primeiros `duracao` segundos de cada arquivo:
    não alinha um corte ao vídeo original inteiro. Devolve None se o ffmpeg não tem `libvmaf` ou falhar."""
    from .tools import FFMPEG
    ref = Path(referencia).resolve()
    dist = Path(distorcido).resolve()
    if not ref.is_file() or not dist.is_file():
        return None
    dur_args = ["-t", f"{duracao:.2f}"] if duracao and duracao > 0 else []
    cmd = [
        FFMPEG, "-v", "info", "-nostdin",
        *dur_args, "-i", str(dist),
        *dur_args, "-i", str(ref),
        "-filter_complex", "[0:v][1:v]libvmaf",
        "-f", "null", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    for line in (r.stderr or "").splitlines():
        if "VMAF score:" in line:
            try:
                return float(line.split("VMAF score:")[-1].strip())
            except ValueError:
                pass
    return None


NAO_CONFERIU = "não consegui conferir"   # começo do texto quando a própria conferência falhou (ver `verificar` e `qualidade.gate_conteudo`)
TIMEOUT_CONFERENCIA_S = 120.0     # a conferência decodifica só uns 30 s: se passar disso, algo travou


def _rodar_conferencia(cmd: list[str], rotulo: str) -> tuple[str, str | None]:
    """Roda um ffmpeg curto de conferência. Devolve (stderr, problema): `problema` vem preenchido se o ffmpeg falhou (código
    de saída diferente de zero), estourou o prazo ou nem abriu. Antes, qualquer falha virava "nenhum problema" (G-019)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=TIMEOUT_CONFERENCIA_S, **sem_janela())
    except subprocess.TimeoutExpired:
        return "", f"{NAO_CONFERIU} {rotulo}: o ffmpeg passou de {TIMEOUT_CONFERENCIA_S:.0f} s"
    except OSError as e:
        return "", f"{NAO_CONFERIU} {rotulo}: o ffmpeg não abriu ({e})"
    if r.returncode != 0:
        ultima = next((ln.strip() for ln in reversed((r.stderr or "").splitlines()) if ln.strip()), "")
        return r.stderr or "", f"{NAO_CONFERIU} {rotulo}: o ffmpeg falhou (código {r.returncode}" + (f": {ultima[:120]})" if ultima else ")")
    return r.stderr or "", None


def verificar_conteudo(caminho: Path, max_segundos: float = 30.0, silencio: bool = False) -> list[str]:
    """G-008: checagem de conteúdo do arquivo pronto, nos primeiros `max_segundos`: vídeo INTEIRAMENTE preto e,
    com `silencio=True`, áudio INTEIRAMENTE mudo. O QC final chama isto quando `Esperado.conteudo` está ligado
    (`qualidade.checar_conteudo`). Devolve a lista de problemas. Se o próprio ffmpeg da conferência falhar ou travar, isso
    entra na lista como "não consegui conferir ...": a conferência que não rodou não vale como "vídeo perfeito"."""
    from .tools import FFMPEG
    caminho = Path(caminho).resolve()
    if not caminho.is_file():
        return ["o arquivo não foi criado"]
    try:
        info = inspecionar(caminho)
        dur = float(info.get("duracao", 0.0))
    except Exception:
        dur = 0.0
    t_teste = min(dur, max_segundos) if dur > 0 else max_segundos
    if t_teste <= 0.5:
        return []

    problemas: list[str] = []
    # Detecção de tela preta contínua
    cmd_black = [
        FFMPEG, "-v", "info", "-nostdin", "-t", f"{t_teste:.2f}", "-i", str(caminho), "-an",
        "-vf", "blackdetect=d=1.5:pic_th=0.98", "-f", "null", "-",
    ]
    err_b, falha_b = _rodar_conferencia(cmd_black, "se o vídeo é preto")
    if falha_b:
        problemas.append(falha_b)
    for line in ("" if falha_b else err_b).splitlines():
        if "black_start:" in line and "black_duration:" in line:
            try:
                dur_preto = float(line.split("black_duration:")[1].split()[0])
                if dur_preto >= t_teste - 0.5:
                    problemas.append("vídeo inteiramente preto")
                    break
            except (ValueError, IndexError):
                pass

    if silencio:   # áudio que existe mas é só silêncio: a faixa errada foi para o arquivo
        cmd_mudo = [
            FFMPEG, "-v", "info", "-nostdin", "-t", f"{t_teste:.2f}", "-i", str(caminho), "-vn",
            "-af", f"silencedetect=noise=-60dB:d={max(0.5, t_teste - 0.5):.2f}", "-f", "null", "-",
        ]
        err_s, falha_s = _rodar_conferencia(cmd_mudo, "se o áudio é mudo")
        if falha_s:
            problemas.append(falha_s)
        elif "silence_start" in err_s:
            problemas.append("áudio inteiramente mudo")

    return problemas
