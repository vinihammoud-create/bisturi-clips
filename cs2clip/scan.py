"""Varredura rápida do vídeo: ffmpeg decodifica só a ROI do kill feed em paralelo."""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .detect import ROI_REL, ROI_SIZE, KillFeedTracker, Track, _track_sim, find_boxes
from .layout import Area, filtro_area, recortar_area
from .tools import FFMPEG, FFPROBE

OVERLAP = 12.0  # s de sobreposição entre blocos paralelos


@dataclass
class VideoInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float


class VideoInvalido(Exception):
    """O arquivo não existe, não é vídeo ou está corrompido/incompleto (mensagem já em português)."""


def _taxa(txt) -> float:
    """'60000/1001' -> 59.94; '0/0' ou lixo -> 0."""
    try:
        num, den = str(txt).split("/")
        return float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _numero(txt) -> float:
    try:
        return float(txt)
    except (TypeError, ValueError):
        return 0.0


def probe(path: str) -> VideoInfo:
    """Lê o arquivo pelo CONTEÚDO (não pela extensão). Cuida de: capa embutida que parece faixa de vídeo,
    taxa variável (VFR: `r_frame_rate` pode dizer 1000 fps; vale a média real) e duração só na faixa."""
    nome = Path(path).name
    if not Path(path).is_file():
        raise VideoInvalido(f"Arquivo não encontrado: {path}")
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v", "-show_entries",
         "stream=width,height,r_frame_rate,avg_frame_rate,duration:stream_disposition=attached_pic:format=duration",
         "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        detalhe = (r.stderr.strip().splitlines() or ["sem detalhe"])[-1]
        raise VideoInvalido(f"{nome} não é um vídeo válido ou está corrompido (ffprobe: {detalhe})")
    try:
        j = json.loads(r.stdout)
        faixas = [s for s in j.get("streams", []) if not (s.get("disposition") or {}).get("attached_pic")]
        s = faixas[0]
        media, nominal = _taxa(s.get("avg_frame_rate")), _taxa(s.get("r_frame_rate"))
        fps = media if 1.0 <= media <= 240.0 else nominal if 1.0 <= nominal <= 240.0 else 0.0
        duracao = _numero((j.get("format") or {}).get("duration")) or _numero(s.get("duration"))
        info = VideoInfo(path, duracao, int(s["width"]), int(s["height"]), fps)
    except (KeyError, IndexError, ValueError, TypeError, json.JSONDecodeError):
        raise VideoInvalido(f"{nome} não tem faixa de vídeo legível ou duração (arquivo incompleto?)") from None
    if info.duration <= 0 or info.width <= 0 or info.height <= 0 or info.fps <= 0:
        raise VideoInvalido(f"{nome} tem duração, tamanho ou fps inválido (arquivo incompleto?)")
    return info


def _scan_range(path: str, start: float, length: float, fps: float, keep_from: float,
                keep_to: float, progress=None, area: Area | None = None) -> list[Track]:
    rx, ry, rw, rh = ROI_REL
    W, H = ROI_SIZE
    # ROI em fração da ÁREA DO JOGO (sem as barras pretas); sem barras o filtro é o mesmo de sempre
    vf = (f"fps={fps},{filtro_area(area)}crop=iw*{rw}:ih*{rh}:iw*{rx}:ih*{ry},"
          f"scale={W}:{H}:flags=bicubic")
    # skip_loop_filter: ~15% mais rápido; o deblocking não afeta a detecção da borda
    cmd = [FFMPEG, "-v", "error", "-nostdin", "-threads", "4", "-skip_loop_filter", "all", "-flags2", "fast",
           "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
           "-i", path, "-an", "-sn", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=W * H * 3 * 8)
    tracker = KillFeedTracker()
    frame_bytes = W * H * 3
    i = 0
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        t = start + i / fps
        img = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        tracker.update(t, find_boxes(img, keep_crop=True))
        i += 1
        if progress and i % int(fps * 10) == 0:
            progress(10.0)
    proc.stdout.close()
    if proc.wait() != 0:  # leitura cortada no meio não pode virar cache de varredura completa
        raise RuntimeError(f"ffmpeg falhou lendo o kill feed no trecho {start:.0f}-{start + length:.0f} s")
    tracks = tracker.finish()
    # Descarta entradas já visíveis no início do bloco (pertencem ao bloco anterior)
    return [tr for tr in tracks
            if keep_from <= tr.first < keep_to and (start == 0 or tr.first > start + 0.25)]


def keyframe_candidates(info: VideoInfo, progress=None, on_frame=None,
                        area: Area | None = None) -> tuple[list[float], float]:
    """Passada 1: decodifica só os keyframes e devolve os instantes com caixa vermelha.

    Retorna (tempos_com_caixa, intervalo_médio_entre_keyframes).
    `on_frame(t, img_bgr)`, se dado, é chamado com cada quadro-chave inteiro (é como o banner de
    fim de round é lido na mesma passada, sem decodificar o vídeo de novo).
    `area`: área do jogo (layout.area_cached); a ROI é tirada dela. None = quadro inteiro.
    """
    import av  # PyAV (vem com o faster-whisper)

    rx, ry, rw, rh = ROI_REL
    W, H = ROI_SIZE
    hits: list[float] = []
    times: list[float] = []
    with av.open(info.path) as c:
        # fr.time é absoluto (inclui o start_time do arquivo, ex.: .ts com 10 s); o ffmpeg com -ss das janelas,
        # mortes e render conta do início do arquivo. Desconta para as duas escalas baterem.
        base = (c.start_time or 0) / av.time_base
        s = c.streams.video[0]
        s.codec_context.skip_frame = "NONKEY"
        s.thread_type = "AUTO"
        last_p = 0.0
        for fr in c.decode(s):
            if fr.time is None:
                continue
            t = max(0.0, fr.time - base)
            times.append(t)
            img = fr.to_ndarray(format="bgr24")
            if on_frame is not None:
                on_frame(t, img)
            img = recortar_area(img, area)
            h, w = img.shape[:2]
            roi = img[int(ry * h):int((ry + rh) * h), int(rx * w):int((rx + rw) * w)]
            roi = cv2.resize(roi, (W, H), interpolation=cv2.INTER_CUBIC if h < 1080 else cv2.INTER_AREA)
            if find_boxes(roi):
                hits.append(t)
            if progress and t - last_p >= 60:
                progress((t - last_p) * 0.5)
                last_p = t
    gaps = np.diff(times) if len(times) > 1 else np.array([99.0])
    return hits, float(np.median(gaps))


def scan_video_fast(info: VideoInfo, fps: float = 10.0, workers: int = 4, progress=None,
                    on_frame=None, area: Area | None = None) -> list[Track] | None:
    """Duas passadas: keyframes -> janelas de 10 fps só onde há caixa. None = usar varredura completa.
    `on_frame(t, img_bgr)`: ver `keyframe_candidates`. `area`: área do jogo (None = quadro inteiro)."""
    hits, kf_gap = keyframe_candidates(info, progress, on_frame, area)
    if kf_gap > 3.0:  # keyframes muito espaçados: uma caixa curta poderia passar despercebida
        return None
    # agrupa keyframes consecutivos com caixa em janelas
    wins: list[list[float]] = []
    for t in hits:
        if wins and t - wins[-1][1] <= kf_gap * 1.6:
            wins[-1][1] = t
        else:
            wins.append([t, t])
    # recua 8,5 s (vida máxima de uma linha no kill feed): mesmo que os primeiros keyframes
    # falhem, a janela cobre o instante em que a linha apareceu
    spans = [(max(0.0, a - 8.5), min(info.duration, b + 0.6)) for a, b in wins]
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1.0:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    def run(span):
        a, b = span
        return _frames_tracks(info.path, a, b - a, fps, area)

    with ThreadPoolExecutor(max(1, workers)) as ex:
        results = list(ex.map(run, merged))
    # Um rastreador por janela; une entradas idênticas em janelas vizinhas (placar cobrindo o feed)
    tracks = sorted((tr for r in results for tr in r), key=lambda tr: tr.first)
    out: list[Track] = []
    for tr in tracks:
        dup = next((o for o in out if tr.first - o.last <= 12.0 and abs(o.w - tr.w) <= 30
                    and _track_sim(o, tr) > 0.9), None)
        if dup is not None:
            dup.last = max(dup.last, tr.last)
            dup.hits += tr.hits
        else:
            out.append(tr)
    if progress:
        progress(info.duration * 0.5)
    return out


def _as_box(tr: Track):
    from .detect import Box
    return Box(0, 0, tr.w, 0, tr.profile, None, tr.mask)


def _frames_tracks(path: str, start: float, length: float, fps: float,
                   area: Area | None = None) -> list[Track]:
    rx, ry, rw, rh = ROI_REL
    W, H = ROI_SIZE
    vf = f"fps={fps},{filtro_area(area)}crop=iw*{rw}:ih*{rh}:iw*{rx}:ih*{ry},scale={W}:{H}:flags=bicubic"
    cmd = [FFMPEG, "-v", "error", "-nostdin", "-threads", "2", "-skip_loop_filter", "all", "-flags2", "fast",
           "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path, "-an", "-sn", "-vf", vf,
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=W * H * 3 * 8)
    tracker = KillFeedTracker()
    fb = W * H * 3
    i = 0
    while True:
        buf = proc.stdout.read(fb)
        if len(buf) < fb:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        tracker.update(start + i / fps, find_boxes(img, keep_crop=True))
        i += 1
    proc.stdout.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg falhou lendo o kill feed no trecho {start:.0f}-{start + length:.0f} s")
    return tracker.finish()


def scan_video(info: VideoInfo, fps: float = 10.0, workers: int = 4, progress=None,
               area: Area | None = None) -> list[Track]:
    dur = info.duration
    n = max(1, min(workers, int(dur // 120) or 1))
    edges = [dur * k / n for k in range(n + 1)]
    jobs = []
    for k in range(n):
        s, e = edges[k], edges[k + 1]
        read_from = max(0.0, s - OVERLAP)
        jobs.append((info.path, read_from, e - read_from + 0.5, fps, s, e if k < n - 1 else dur + 1))
    with ThreadPoolExecutor(n) as ex:
        results = list(ex.map(lambda a: _scan_range(*a, progress=progress, area=area), jobs))
    tracks = [tr for r in results for tr in r]
    tracks.sort(key=lambda tr: tr.first)
    return tracks
