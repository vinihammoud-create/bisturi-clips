"""Renderização com ffmpeg: cortes 16:9, shorts 9:16 e montagem.

* Corte com precisão de frame (-ss na entrada + reencode).
* NVENC (GPU) em qualidade alta quando disponível; senão libx264.
* Loudness em 2 passadas (EBU R128 / loudnorm) para volume padrão de plataforma.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from .config import Config
from .moments import Moment
from .tools import FFMPEG, FFPROBE

W16, H16, FPS = 1920, 1080, 60
FRACAO_MIRA = 0.40   # altura da mira dentro do painel de jogo do short (0,5 = meio)
FONTES = Path(__file__).resolve().parent / "assets" / "fontes"


@lru_cache(maxsize=1)
def _has_nvenc() -> bool:
    r = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                        "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
    return r.returncode == 0


ENCODERS = ("auto", "h264_nvenc", "hevc_nvenc", "libx264")
_AVISADOS: set[str] = set()


def encoder_efetivo(cfg: Config) -> str:
    """O encoder que sai de verdade agora ('auto' resolvido; x264 depois que a GPU falhou). Serve para a impressão
    do cache: um corte feito em outro encoder não deve ser juntado com "-c copy" aos de agora."""
    enc = cfg.qualidade.encoder
    if enc not in ENCODERS:
        if enc not in _AVISADOS:  # avisa 1 vez só, não a cada corte
            _AVISADOS.add(enc)
            print(f"    [aviso] config.toml: encoder '{enc}' desconhecido; usando libx264. Valores aceitos: "
                  f"{', '.join(ENCODERS)}")
        enc = "libx264"
    if enc == "auto":
        enc = "h264_nvenc" if _has_nvenc() else "libx264"
    if enc.endswith("_nvenc") and _NVENC_QUEBROU:
        enc = "libx264"
    return enc


def video_args(cfg: Config) -> list[str]:
    enc = encoder_efetivo(cfg)
    if enc.endswith("_nvenc"):
        return ["-c:v", enc, "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", str(cfg.qualidade.cq),
                "-b:v", "0", "-maxrate", "16M", "-bufsize", "32M", "-spatial-aq", "1", "-temporal-aq", "1",
                "-rc-lookahead", "32", "-bf", "3", "-g", "120", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "slow", "-crf", str(max(0, cfg.qualidade.cq - 1)),
            "-g", "120", "-pix_fmt", "yuv420p"]


AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


class FfmpegErro(RuntimeError):
    """O ffmpeg terminou com erro; `.stderr` guarda o que ele disse (para decidir se vale tentar de novo)."""

    def __init__(self, cmd: list[str], stderr: str):
        super().__init__("ffmpeg falhou:\n" + " ".join(cmd) + "\n" + stderr[-2000:])
        self.stderr = stderr


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        raise FfmpegErro(cmd, r.stderr)


# O NVENC (GPU) pode falhar no meio do lote: GPU ocupada por outro programa, memória de vídeo cheia, driver.
# Quando isso acontece, o corte é refeito em x264 (CPU) e o resto do lote já segue em x264: lento, mas termina.
_NVENC_QUEBROU = False
_ERRO_NVENC = re.compile(r"nvenc|cuda|encode ?session|no capable devices|out of memory", re.I)


def nvenc_quebrou() -> bool:
    return _NVENC_QUEBROU


def _marcar_nvenc_quebrado(motivo: str) -> None:
    global _NVENC_QUEBROU
    if not _NVENC_QUEBROU:
        _NVENC_QUEBROU = True
        ultima = [ln for ln in motivo.splitlines() if ln.strip()][-1:] or ["sem detalhe"]
        print(f"    [aviso] a GPU (NVENC) falhou ({ultima[0].strip()[:120]}); o resto sai em x264 (CPU), mais lento.",
              flush=True)


def _run_enc(fabrica, cwd: Path | None = None) -> None:
    """Roda o comando de render. `fabrica()` devolve o comando (relê o encoder a cada chamada, então depois de
    um erro de NVENC a segunda tentativa já usa x264)."""
    cmd = fabrica()
    try:
        _run(cmd, cwd)
    except FfmpegErro as e:
        if not any("_nvenc" in a for a in cmd) or not _ERRO_NVENC.search(e.stderr or ""):
            raise
        _marcar_nvenc_quebrado(e.stderr)
        _run(fabrica(), cwd)


def measure_loudness(src: str, start: float, dur: float, target: float) -> dict | None:
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}", "-vn",
           "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return None
    d = json.loads(m.group(0))
    try:
        if float(d["input_i"]) < -60:  # praticamente silêncio
            return None
    except ValueError:
        return None
    return d


def audio_filter(meas: dict | None, target: float, dur: float, fade: float) -> str:
    chain = []
    if meas:
        chain.append(
            f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
            f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
            f"offset={meas['target_offset']}:linear=true")
    chain.append("aresample=48000")
    if fade > 0:
        chain.append(f"afade=t=in:st=0:d={fade:.2f},afade=t=out:st={max(0, dur - fade):.3f}:d={fade:.2f}")
    return ",".join(chain)


@lru_cache(maxsize=16)
def _tamanho(src: str) -> tuple[int, int] | None:
    """Largura e altura da faixa de vídeo (ignora capa embutida). None se o ffprobe não souber dizer."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v", "-show_entries",
                        "stream=width,height:stream_disposition=attached_pic", "-of", "json", src],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        faixas = [s for s in json.loads(r.stdout).get("streams", [])
                  if not (s.get("disposition") or {}).get("attached_pic")]
        w, h = int(faixas[0]["width"]), int(faixas[0]["height"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return (w, h) if w > 0 and h > 0 else None


def _area(src: str) -> tuple[int, int, int, int] | None:
    """Onde a imagem fica no quadro 1920x1080 (largura, altura, x, y) quando o vídeo NÃO é 16:9 (4:3, ultrawide):
    a proporção é mantida e sobra faixa preta. None = 16:9 (ou desconhecido): ocupa o quadro todo, como sempre."""
    t = _tamanho(src)
    if t is None or abs(t[0] / t[1] - W16 / H16) <= 0.02 * W16 / H16:
        return None
    k = min(W16 / t[0], H16 / t[1])
    ev = lambda v: int(round(v / 2) * 2)  # noqa: E731
    aw, ah = min(W16, ev(t[0] * k)), min(H16, ev(t[1] * k))
    return aw, ah, (W16 - aw) // 2 // 2 * 2, (H16 - ah) // 2 // 2 * 2


def _escala16(area: tuple[int, int, int, int] | None) -> str:
    """Filtro que leva a entrada para 1920x1080: esticar só no 16:9; senão escala + faixa preta (sem deformar)."""
    if area is None:
        return f"scale={W16}:{H16}:flags=lanczos"
    aw, ah, ax, ay = area
    return f"scale={aw}:{ah}:flags=lanczos,setsar=1,pad={W16}:{H16}:{ax}:{ay}:color=black"


def _cam_na_area(cam_box: tuple[int, int, int, int], area: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """A webcam é achada no quadro esticado para 1920x1080; leva o recorte para a área sem deformação: o centro vai
    para o mesmo ponto da imagem e o tamanho encolhe por igual (mantém a proporção do painel da câmera)."""
    aw, ah, ax, ay = area
    sx, sy = aw / W16, ah / H16
    x, y, w, h = cam_box
    k = min(sx, sy)
    w, h = min(w * k, aw), min(h * k, ah)
    cx, cy = ax + (x + cam_box[2] / 2) * sx, ay + (y + cam_box[3] / 2) * sy
    x = min(max(cx - w / 2, ax), ax + aw - w)
    y = min(max(cy - h / 2, ay), ay + ah - h)
    par = lambda v: int(v // 2 * 2)  # noqa: E731  (para baixo: o recorte nunca passa da borda)
    return par(x), par(y), par(w), par(h)


def _jogo_no_quadro(src: str, area_jogo: tuple[int, int, int, int] | None,
                    area: tuple[int, int, int, int] | None) -> tuple[float, float, float, float] | None:
    """Área do jogo (px do vídeo, sem as barras pretas; ver layout.area_cached) levada ao quadro 1920x1080 do render
    (x, y, largura, altura). None = sem barras: o recorte usa a imagem toda, como sempre."""
    t = _tamanho(src)
    if area_jogo is None or t is None:
        return None
    aw, ah, ax, ay = area or (W16, H16, 0, 0)
    sx, sy = aw / t[0], ah / t[1]
    x, y, w, h = area_jogo
    return ax + x * sx, ay + y * sy, w * sx, h * sy


def render_clip(src: str, m: Moment, out: Path, cfg: Config) -> None:
    """Corte horizontal 1920x1080 60fps (para montagem e uso avulso)."""
    dur = m.duration
    f = cfg.montagem.transicao
    meas = measure_loudness(src, m.start, dur, cfg.qualidade.lufs)
    vf = f"{_escala16(_area(src))},fps={FPS}"
    if f > 0:
        vf += f",fade=t=in:st=0:d={f:.2f},fade=t=out:st={max(0, dur - f):.3f}:d={f:.2f}"
    af = audio_filter(meas, cfg.qualidade.lufs, dur, f)
    _run_enc(lambda: [FFMPEG, "-y", "-v", "error", "-nostdin", "-ss", f"{m.start:.3f}", "-i", src, "-t", f"{dur:.3f}",
                      "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, "-af", af,
                      *video_args(cfg), *AUDIO_ARGS, "-movflags", "+faststart", str(out)])


def _caminho_filtro(p: Path) -> str:
    """Caminho absoluto no formato que os filtros do ffmpeg aceitam (barra normal e ':' escapado)."""
    return str(p).replace("\\", "/").replace(":", "\\:")


def render_short(src: str, m: Moment, out: Path, cfg: Config, cam_box: tuple[int, int, int, int] | None,
                 ass_file: Path | None, tremida: bool = False, velocidade: float = 1.0,
                 area_jogo: tuple[int, int, int, int] | None = None) -> None:
    """Vertical 1080x1920: webcam em cima, gameplay embaixo, legendas + selo, zoom no abate.

    cam_box em pixels da escala 1920x1080. `tremida`: chacoalha o jogo ~0,15 s em cada abate (versão com efeitos).
    `velocidade` < 1: câmera lenta (0,5 = metade; usado no replay do último abate).
    `area_jogo`: onde o jogo está no vídeo (px, sem as barras pretas); a mira é o centro dela e o recorte não
    passa para a barra. None = jogo na tela toda (o recorte de sempre).
    """
    s = cfg.shorts
    W, H = s.largura, s.altura
    dur = m.duration
    cam_h = s.camera_altura if cam_box else 0
    topo = max(0, s.topo) if cam_box else 0
    game_h = H - cam_h - topo
    # vídeo que não é 16:9: a imagem fica numa área do quadro (com faixa preta); o recorte e a câmera seguem ela
    area = _area(src)
    aw, ah, ax, ay = area or (W16, H16, 0, 0)
    if area and cam_box:
        cam_box = _cam_na_area(cam_box, area)
    jogo = _jogo_no_quadro(src, area_jogo, area)
    if jogo:   # barras pretas no próprio vídeo (4:3 numa live 16:9): o recorte fica dentro do jogo
        ax, ay, aw, ah = jogo
    # recorte do gameplay centrado na mira, com a proporção do painel
    gh = ah / max(1.0, s.zoom_gameplay)
    gw = gh * W / game_h
    if gw > aw:
        gw = aw
        gh = gw * game_h / W
    # A mira (centro do quadro 16:9) fica a FRACAO_MIRA da altura do painel, não no meio: com a câmera em cima,
    # o meio caía em y~1264 de 1920, sob a descrição e os botões do Shorts/TikTok/Reels (abaixo de ~1248).
    gx = ax + (aw - gw) / 2
    gy = min(max(float(ay), ay + ah / 2 - FRACAO_MIRA * gh), ay + ah - gh)
    ev = lambda v: int(round(v / 2) * 2)  # noqa: E731

    parts = [f"[0:v]{_escala16(area)},fps={FPS},split=2[a][b]"]
    game = f"[b]crop={ev(gw)}:{ev(gh)}:{ev(gx)}:{ev(gy)},scale={W}:{game_h}:flags=lanczos"
    if s.zoom_no_abate and m.kills:
        bumps = "+".join(f"exp(-pow((t-{e.t - m.start + 0.04:.3f})/0.14\\,2))" for e in m.kills)
        z = f"(1+0.065*({bumps}))"
        game += f",scale=w='trunc(iw*{z}/2)*2':h='trunc(ih*{z}/2)*2':eval=frame,crop={W}:{game_h}"
    if tremida and m.kills:
        janela = "+".join(f"between(t\\,{e.t - m.start:.3f}\\,{e.t - m.start + 0.15:.3f})" for e in m.kills)
        game += (f",crop={W - 24}:{game_h - 24}:x='12+10*sin(t*90)*({janela})':y='12+8*cos(t*77)*({janela})'"
                 f",scale={W}:{game_h}")
    parts.append(game + "[g]")
    if cam_box:
        cx, cy, cw, ch = cam_box
        parts.append(f"[a]crop={cw}:{ch}:{cx}:{cy},scale={W}:{cam_h}:flags=lanczos,unsharp=5:5:0.5[c]")
        parts.append(f"[c][g]vstack=inputs=2,pad={W}:{H}:0:{topo}:color=0x0b0d10[v0]" if topo
                     else "[c][g]vstack=inputs=2[v0]")
    else:
        parts.append("[a]nullsink")
        parts.append("[g]null[v0]")
    post = "[v0]format=yuv420p"
    if ass_file is not None:
        if "'" in str(FONTES):   # apóstrofo fecha as aspas do filtro: usa uma cópia das fontes ao lado do .ass
            copia = ass_file.parent / "_fontes"
            copia.mkdir(exist_ok=True)
            for fonte in FONTES.glob("*.ttf"):
                if not (copia / fonte.name).exists():
                    shutil.copy2(fonte, copia / fonte.name)
            post += f",ass=filename={ass_file.name}:fontsdir=_fontes"
        else:
            post += f",ass=filename={ass_file.name}:fontsdir='{_caminho_filtro(FONTES)}'"
    f = 0.1
    if velocidade != 1.0:   # câmera lenta: estica o tempo antes dos fades (que contam na duração final)
        post += f",setpts={1 / velocidade:.4f}*PTS,fps={FPS}"   # repete quadros: o arquivo segue em 60 fps
    dur_saida = dur / velocidade
    post += f",fade=t=in:st=0:d={f},fade=t=out:st={max(0, dur_saida - 0.2):.3f}:d=0.2[v]"
    parts.append(post)

    meas = measure_loudness(src, m.start, dur, cfg.qualidade.lufs)
    af = audio_filter(meas, cfg.qualidade.lufs, dur, 0.12)
    if velocidade != 1.0:
        af = f"atempo={velocidade:.4f}," + af.replace(f"st={max(0, dur - 0.12):.3f}", f"st={max(0, dur_saida - 0.12):.3f}")
    # roda na pasta do .ass: o filtro de legendas não lida bem com caminhos com acento/":" no Windows
    # -t ANTES do -i: limita o que é lido. Depois do -i limitaria a saída, que na câmera lenta é mais longa
    # (o replay saía cortado pela metade e a checagem passava por pouco, 17/10/2026).
    _run_enc(lambda: [FFMPEG, "-y", "-v", "error", "-nostdin", "-ss", f"{m.start:.3f}", "-t", f"{dur:.3f}", "-i", src,
                      "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "0:a:0?", "-af", af,
                      *video_args(cfg), *AUDIO_ARGS, "-movflags", "+faststart", str(out)],
             cwd=ass_file.parent if ass_file is not None else None)


def render_por_janelas(m: Moment, out: Path, render_um) -> None:
    """Momento com mais de um trecho (sequência do round, sem o tempo parado do meio): cada trecho é renderizado
    do jeito normal (com seu fade e seu volume) e os trechos são juntados em um vídeo só.
    `render_um(momento_de_um_trecho, destino)` é quem renderiza (corte horizontal ou short)."""
    janelas = m.janelas
    if len(janelas) == 1:
        render_um(m, out)
        return
    partes: list[Path] = []

    def trecho(i: int) -> Moment:
        mm = dataclasses.replace(m, start=janelas[i][0], end=janelas[i][1], segmentos=[])
        mm.trecho = i      # as legendas usam: o gancho (nome da jogada) só no 1º trecho
        return mm

    try:
        na_gpu: list[int] = []                    # trechos que começaram com o NVENC ainda funcionando
        for i in range(len(janelas)):
            parte = out.with_name(f"{out.stem}.seg{i}{out.suffix}")
            partes.append(parte)                      # entra antes de renderizar: se falhar, a limpeza acha
            if not _NVENC_QUEBROU:
                na_gpu.append(i)
            render_um(trecho(i), parte)
        if _NVENC_QUEBROU and na_gpu:
            # a GPU caiu no meio: refaz em x264 os trechos que podem ter saído em NVENC, para todos baterem
            for i in na_gpu:
                render_um(trecho(i), partes[i])
        concat(partes, out)
    finally:
        for parte in partes:
            parte.unlink(missing_ok=True)


def _faixa(arquivo: Path, sel: str, campos: str) -> str:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", sel, "-show_entries", f"stream={campos}",
                        "-of", "csv=p=0", str(arquivo)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip()


def _feito_no_x264(arquivo: Path) -> bool:
    """O x264 grava a própria marca ("x264 - core") no 1º quadro; o NVENC não. Os dois dão 'h264', mas cada um tem
    seus parâmetros (SPS/PPS): juntados com "-c copy", o trecho do outro encoder pode sair com blocos."""
    try:
        with open(arquivo, "rb") as fh:
            return b"x264 - core" in fh.read(4 << 20)
    except OSError:
        return False


def _codec(arquivo: Path) -> str:
    """Tudo o que precisa ser igual nos trechos para juntar sem reencodar."""
    v = _faixa(arquivo, "v:0", "codec_name,profile,pix_fmt,width,height,r_frame_rate")
    a = _faixa(arquivo, "a:0", "codec_name,sample_rate,channels")
    return f"{v} | áudio {a or 'nenhum'}{' | x264' if _feito_no_x264(arquivo) else ''}"


def _lista_concat(p: Path) -> str:
    """Linha da lista do concat. Apóstrofo no caminho (C:\\Users\\D'Avila) fecharia as aspas: vira '\\'' (o escape
    que o ffmpeg documenta). Caminho absoluto: o ffmpeg resolve os relativos a partir da pasta da lista."""
    return "file '" + p.resolve().as_posix().replace("'", "'\\''") + "'\n"


def _juntar_reencodando(files: list[Path], out: Path, cfg: Config) -> None:
    """Junta trechos de formatos diferentes (outro encoder, perfil ou tamanho): mais lento, mas sempre funciona.
    Erro aqui se repetiria para sempre, porque os cortes do cache são os mesmos a cada execução."""
    w, h = (int(x) for x in _faixa(files[0], "v:0", "width,height").split(",")[:2])
    com_audio = all(_faixa(f, "a:0", "codec_name") for f in files)
    fil = []
    for i in range(len(files)):
        fil.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                   f"setsar=1,fps={FPS},format=yuv420p[v{i}]")
        if com_audio:
            fil.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    entradas = "".join(f"[v{i}][a{i}]" if com_audio else f"[v{i}]" for i in range(len(files)))
    fil.append(f"{entradas}concat=n={len(files)}:v=1:a={1 if com_audio else 0}[v]" + ("[a]" if com_audio else ""))
    _run_enc(lambda: [FFMPEG, "-y", "-v", "error", "-nostdin", *[x for f in files for x in ("-i", str(f))],
                      "-filter_complex", ";".join(fil), "-map", "[v]", *(["-map", "[a]"] if com_audio else []),
                      *video_args(cfg), *(AUDIO_ARGS if com_audio else []), "-movflags", "+faststart", str(out)])


def concat(files: list[Path], out: Path, cfg: Config | None = None) -> None:
    # "-c copy" só é seguro com o mesmo formato em todos: se a GPU falhou no meio, um trecho pode ter saído em
    # x264 e outro em NVENC (os dois 'h264', mas com parâmetros diferentes) e o arquivo juntado sairia com blocos
    # e ainda passaria na checagem. Nesse caso junta reencodando (`cfg` dá o encoder; sem ele, o padrão).
    if not files:   # o ffmpeg daria só "Invalid data found", sem dizer o motivo
        raise ValueError("nada para juntar: nenhum trecho foi renderizado")
    codecs = {_codec(f) for f in files}
    if len(codecs) > 1:
        print(f"    (trechos em formatos diferentes: juntando com reencode, mais lento — {' / '.join(sorted(codecs))})",
              flush=True)
        _juntar_reencodando(files, out, cfg or Config())
        return
    lst = out.with_suffix(".txt")
    lst.write_text("".join(_lista_concat(p) for p in files), encoding="utf-8")
    try:
        _run([FFMPEG, "-y", "-v", "error", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(lst),
              "-c", "copy", "-movflags", "+faststart", str(out)])
    finally:
        lst.unlink(missing_ok=True)   # a lista não pode sobrar nem quando o ffmpeg falha


def thumbnail(src: str, t: float, out: Path, width: int = 480) -> None:
    _run([FFMPEG, "-y", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
          "-vf", f"scale={width}:-2", "-q:v", "3", str(out)])
