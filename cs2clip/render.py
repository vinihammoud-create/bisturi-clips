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
import threading
from functools import lru_cache
from pathlib import Path

from .config import Config
from .moments import Moment
from .tools import FFMPEG, FFPROBE
from .tools import substituir_arquivo
from .tools import sem_janela

W16, H16, FPS = 1920, 1080, 60
FRACAO_MIRA = 0.40   # altura da mira dentro do painel de jogo do short (0,5 = meio)
MARGEM_WEBCAM_INTEIRA = 60   # px: margem de cada lado da webcam no layout webcam_inteira (o formato que funcionou nos testes de 17/11)
VAO_WEBCAM_JOGO = 12         # px entre a webcam e o jogo
TOPO_SEGURO = 150            # px: nada importante no alto do quadro em pé (abas do app; MARCA_E_VOZ §7)
CAM_INTEIRA_MAX = 0.42       # a webcam inteira ocupa no máximo isto da altura do quadro (o jogo fica com o resto)
ESTREITAR_MIN = 0.5          # o recorte do jogo só é estreitado (para não pegar a webcam) até a metade; webcam perto do centro: não estreita
FONTES = Path(__file__).resolve().parent / "assets" / "fontes"
_TRAVA_FONTES = threading.Lock()   # os shorts renderizam em paralelo e dividem a pasta _fontes


@lru_cache(maxsize=1)
def _has_nvenc() -> bool:
    r = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                        "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True, **sem_janela())
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
                "-rc-lookahead", "32", "-bf", "3", "-g", "120", "-pix_fmt", "yuv420p", *COLOR_ARGS]
    return ["-c:v", "libx264", "-preset", "slow", "-crf", str(max(0, cfg.qualidade.cq - 1)),
            "-g", "120", "-pix_fmt", "yuv420p", *COLOR_ARGS]


COLOR_ARGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
BT709_FILTRO = ("scale=iw:ih:out_color_matrix=bt709:out_range=tv,"
                "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv")
LIMIAR_FPS_REPLAY = 25.0   # abaixo disso no slow (ex.: fonte 30 fps a 0,5x = 15 fps), interpola com minterpolate
AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def garantir_pico(arq: Path, cfg: Config | None = None, maximo: float = -1.0, margem: float = 0.5,
                  tentativas: int = 3) -> float | None:
    """S1.4b: mede o pico REAL do arquivo pronto (depois do AAC) e, se passar de `maximo` dBTP, baixa o volume do áudio
    (vídeo copiado, mesmas faixas) e mede de novo, até `tentativas` vezes. O limitador sozinho não garante: na gravação de
    23/10, 5 de 11 arquivos passavam de -1 dBTP (um em +0,2) e o comportamento dele varia de corte para corte.
    Devolve o pico final (None = sem áudio, medição impossível ou guarda desligado). Nunca estraga o arquivo: só troca se melhorar."""
    if cfg is not None and not getattr(cfg.qualidade, "garantir_pico", True):
        return None
    from . import qc
    arq = Path(arq)
    medida = qc.medir_loudness(arq)
    if medida is None:
        return None
    pico = medida[1]
    for _ in range(tentativas):
        if pico <= maximo:
            return pico
        tmp = arq.with_name(arq.stem + ".pico" + arq.suffix)
        try:
            _run([FFMPEG, "-y", "-v", "error", "-nostdin", "-i", str(arq), "-map", "0", "-map_metadata", "0", "-c:v", "copy",
                  "-af", f"volume={-(pico - maximo + margem):.2f}dB", *AUDIO_ARGS, "-movflags", "+faststart", str(tmp)])
            nova = qc.medir_loudness(tmp)
            if nova is None or nova[1] >= pico:
                break   # não melhorou: mantém o arquivo como estava
            substituir_arquivo(tmp, arq)
            pico = nova[1]
        except Exception:  # noqa: BLE001  (o guarda é uma melhoria: se falhar, o arquivo original segue valendo)
            break
        finally:
            tmp.unlink(missing_ok=True)
    return pico


_PICO = threading.local()   # `adiar=True` nesta thread: os trechos de uma sequência não passam pelo guarda um a um


def com_pico_garantido(fn):
    """Decorador dos renders de arquivo único (`out` é o 3º argumento e `cfg` o 4º): depois de renderizar, roda o guarda."""
    import functools

    @functools.wraps(fn)
    def embrulho(*args, **kwargs):
        resultado = fn(*args, **kwargs)
        out = args[2] if len(args) > 2 else kwargs.get("out")
        cfg = args[3] if len(args) > 3 else kwargs.get("cfg")
        if out is not None and not getattr(_PICO, "adiar", False):
            garantir_pico(Path(out), cfg)
        return resultado
    return embrulho


class FfmpegErro(RuntimeError):
    """O ffmpeg terminou com erro; `.stderr` guarda o que ele disse (para decidir se vale tentar de novo)."""

    def __init__(self, cmd: list[str], stderr: str):
        super().__init__("ffmpeg falhou:\n" + " ".join(cmd) + "\n" + stderr[-2000:])
        self.stderr = stderr


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, **sem_janela())
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
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
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


def _loudness_momento(src: str, m: Moment, target: float) -> dict | None:
    """Mede o loudness do momento. Quando o momento tem vários trechos (`render_por_janelas` ou short + replay),
    mede 1 vez na janela inteira e reaproveita o mesmo ganho em todos os trechos, sem pulo de volume na emenda."""
    cache = getattr(m, "_cache_loudness", None)
    janela = getattr(m, "_janela_loudness", None)
    if isinstance(cache, dict) and janela is not None:
        chave = (src, float(target))
        if chave not in cache:
            ini, fim = janela
            cache[chave] = measure_loudness(src, ini, max(0.1, fim - ini), target)
        return cache[chave]
    return measure_loudness(src, m.start, m.duration, target)


# Pico real (true peak) do arquivo final abaixo de -1 dBTP, o padrão de YouTube/TikTok. O loudnorm sozinho deixa
# passar os tiros (picos rápidos) e o AAC ainda cria picos entre as amostras: medido em 01/11/2026 na montagem de
# 23/10, só loudnorm deu -0,5/-0,8 dBTP depois do AAC. Com este limitador a 192 kHz (enxerga o pico entre amostras)
# em -2,5 dBFS: -14,3 LUFS e -1,7 dBTP depois do AAC 192k.
LIMITADOR_PICO = "aresample=192000,alimiter=limit=0.750:attack=1:release=60:level=false,aresample=48000"


MICRO_FADE_EMENDA = 0.015   # emenda seca no meio de uma sequência: só o anti-estalo no áudio, sem mergulho de volume


def bordas_de_fade(m, f: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """((fade de entrada, de saída) do vídeo, (idem do áudio)) deste trecho. Padrão: `f` nos dois lados. Trecho de DENTRO
    de uma sequência de round renderizado com `transicao_tipo = "corte"` (`render_por_janelas`) não tem fade nas emendas
    internas, senão cada emenda vira um mergulho para o preto (achado da prova de 65 min em 08/11/2026: 3 mergulhos numa
    montagem de 59 s). No áudio fica só um micro-fade contra estalo."""
    ini, fim = getattr(m, "_fade_ini", True), getattr(m, "_fade_fim", True)
    return ((f if ini else 0.0, f if fim else 0.0),
            (f if ini else (MICRO_FADE_EMENDA if f > 0 else 0.0), f if fim else (MICRO_FADE_EMENDA if f > 0 else 0.0)))


def fade_de_video(f_ini: float, f_fim: float, dur: float) -> str:
    out = ""
    if f_ini > 0:
        out += f",fade=t=in:st=0:d={f_ini:.2f}"
    if f_fim > 0:
        out += f",fade=t=out:st={max(0, dur - f_fim):.3f}:d={f_fim:.2f}"
    return out


def _dur_fade(x: float) -> str:
    return f"{x:.2f}" if x >= 0.05 else f"{x:.3f}"   # 0,12 continua "0.12" (comando igual ao de antes); micro-fade vira "0.015"


def audio_filter(meas: dict | None, target: float, dur: float, fade: float,
                 fade_ini: float | None = None, fade_fim: float | None = None) -> str:
    chain = []
    if meas:
        chain.append(
            f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
            f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
            f"offset={meas['target_offset']}:linear=true")
    chain.append(LIMITADOR_PICO)
    fi = fade if fade_ini is None else fade_ini
    ff = fade if fade_fim is None else fade_fim
    if fi > 0:
        chain.append(f"afade=t=in:st=0:d={_dur_fade(fi)}")
    if ff > 0:
        chain.append(f"afade=t=out:st={max(0, dur - ff):.3f}:d={_dur_fade(ff)}")
    return ",".join(chain)


@lru_cache(maxsize=16)
def _tamanho(src: str) -> tuple[int, int] | None:
    """Largura e altura da faixa de vídeo (ignora capa embutida). None se o ffprobe não souber dizer."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v", "-show_entries",
                        "stream=width,height:stream_disposition=attached_pic", "-of", "json", src],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    try:
        faixas = [s for s in json.loads(r.stdout).get("streams", [])
                  if not (s.get("disposition") or {}).get("attached_pic")]
        w, h = int(faixas[0]["width"]), int(faixas[0]["height"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return (w, h) if w > 0 and h > 0 else None


@lru_cache(maxsize=16)
def _fps_fonte(src: str) -> float:
    """Taxa de quadros da faixa de vídeo (FPS padrão se o ffprobe não souber dizer)."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v", "-show_entries",
                        "stream=avg_frame_rate,r_frame_rate:stream_disposition=attached_pic", "-of", "json", src],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    try:
        faixas = [s for s in json.loads(r.stdout).get("streams", [])
                  if not (s.get("disposition") or {}).get("attached_pic")]
        for k in ("avg_frame_rate", "r_frame_rate"):
            n, d = str(faixas[0].get(k, "")).split("/")
            fps = float(n) / float(d)
            if fps > 1.0:
                return fps
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
        pass
    return float(FPS)


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


def _render_clip_simples(src: str, m: Moment, out: Path, cfg: Config, trilha_jogo: int | None = None,
                         cartao: Path | None = None) -> None:
    """Corte horizontal 1920x1080 60fps (para montagem e uso avulso).
    `trilha_jogo`: índice da faixa de áudio só com o jogo (A1); vai como 2ª faixa do corte (a 1ª segue a mistura
    da live, que é o que toca no corte avulso) para a montagem com música tirar a voz e o Discord."""
    dur = m.duration
    f = cfg.montagem.transicao
    meas = _loudness_momento(src, m, cfg.qualidade.lufs)
    vf = f"{_escala16(_area(src))},fps={FPS}"
    if cartao is not None:
        vf += _filtro_ass(cartao)
    (v_ini, v_fim), (a_ini, a_fim) = bordas_de_fade(m, f)
    if f > 0:
        vf += fade_de_video(v_ini, v_fim, dur)
    vf += f",{BT709_FILTRO}"
    af = audio_filter(meas, cfg.qualidade.lufs, dur, f, a_ini, a_fim)
    if trilha_jogo is None:
        trilha_jogo = getattr(cfg, "_trilha_jogo", None)
    # `is not None`: a faixa 1 (índice 0) também pode ser a do jogo (gravação sem voz)
    jogo = ["-map", f"0:a:{trilha_jogo}", "-metadata:s:a:1", "title=jogo"] if trilha_jogo is not None else []
    _run_enc(lambda: [FFMPEG, "-y", "-v", "error", "-nostdin", "-ss", f"{m.start:.3f}", "-i", src, "-t", f"{dur:.3f}",
                      "-map", "0:v:0", "-map", "0:a:0?", *jogo, "-vf", vf, "-af", af,
                      *video_args(cfg), *AUDIO_ARGS, "-movflags", "+faststart", str(out)],
             cwd=cartao.parent if cartao is not None else None)


@dataclasses.dataclass
class _ExtrasCorte:
    """O que as opções avançadas pedem para ESTE trecho do corte (ver `_extras_do_corte`)."""
    pecas: list
    voz: tuple[int, int] | None = None
    cartao: Path | None = None
    wav_jogo: Path | None = None

    @property
    def complexo(self) -> bool:
        from .efeitos import tem_efeito_de_tempo
        return tem_efeito_de_tempo(self.pecas) or self.voz is not None or self.wav_jogo is not None

    def limpar(self) -> None:
        for arq in (self.cartao, self.wav_jogo):
            if arq is not None:
                arq.unlink(missing_ok=True)


def _extras_do_corte(src: str, m: Moment, out: Path, cfg: Config, trilha_jogo: int | None) -> _ExtrasCorte | None:
    """None = nenhuma opção avançada se aplica a este trecho: o corte sai pelo caminho de sempre. Os arquivos
    auxiliares (cartão .ass, faixa do jogo .wav) ficam ao lado do `out` e `limpar()` os apaga."""
    from .efeitos import extrair_jogo_de_faixa_unica, pecas_do_corte
    pecas = pecas_do_corte(m, cfg)
    voz = _voz_ativa(cfg, src, trilha_jogo)
    cartao = wav = None
    if getattr(cfg.cortes, "cartao_tatico", False) and getattr(m, "trecho", 0) == 0:   # só no 1º trecho
        from .cartao import gerar_ass_cartao
        cartao = gerar_ass_cartao(out.with_name(out.stem + ".cartao.ass"), m)
    # Sem faixa "só jogo" de verdade (1 faixa, ou o OBS gravando a MESMA mistura em todas, como na gravação de
    # 23/10/2026): a separação cria a 2ª faixa "jogo" a partir da mistura (faixa 1).
    if trilha_jogo is None and getattr(cfg.audio, "separar_jogo_faixa_unica", False) and _n_faixas(src) >= 1:
        try:   # A4: a montagem com música precisa da 2ª faixa "jogo"; aqui ela vem da separação da voz
            wav, metodo = extrair_jogo_de_faixa_unica(src, out.with_name(out.stem + ".jogo.wav"), m.start, m.duration)
            chave = "jogo_unico_falhou" if "falhou" in metodo else "jogo_unico_metodo"
            _avisar_uma_vez(chave, f"faixa do jogo separada da voz por {metodo}")
        except Exception as e:  # noqa: BLE001  (efeito opcional: se falhar, o corte sai sem a faixa)
            _avisar_uma_vez("jogo_unico", f"não consegui separar o jogo da faixa única ({str(e)[:100]}); seguindo sem ela")
    extras = _ExtrasCorte(pecas, voz, cartao, wav)
    if not extras.complexo and cartao is None:
        return None
    return extras


@com_pico_garantido
def render_clip(src: str, m: Moment, out: Path, cfg: Config, trilha_jogo: int | None = None) -> None:
    """Corte horizontal 1920x1080 60fps (para montagem e uso avulso). Sem opção avançada ligada, é o corte de sempre.
    Com `cortes.speed_ramp`, `cortes.freeze_headshot`, `cortes.cartao_tatico`, `audio.tratar_voz` ou
    `audio.separar_jogo_faixa_unica`, entram os efeitos (tudo desligado por padrão)."""
    if trilha_jogo is None:
        trilha_jogo = getattr(cfg, "_trilha_jogo", None)
    extras = _extras_do_corte(src, m, out, cfg, trilha_jogo)
    if extras is None:
        _render_clip_simples(src, m, out, cfg, trilha_jogo)
        return
    try:
        if extras.complexo:
            _render_clip_complexo(src, m, out, cfg, trilha_jogo, extras)
        else:
            _render_clip_simples(src, m, out, cfg, trilha_jogo, extras.cartao)
    finally:
        extras.limpar()


def _cadeia_audio(entrada: str, pecas: list, pref: str) -> tuple[list[str], str]:
    """Filtros que cortam e esticam o áudio de `entrada` ("[0:a:0]") peça por peça (a mesma linha do tempo do vídeo)
    e devolvem o rótulo da saída juntada. Congelamento = silêncio."""
    from .efeitos import DUR_FREEZE
    n = len(pecas)
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    fil = [f"{entrada}asplit={n}" + "".join(f"[{pref}s{i}]" for i in range(n))]
    for i, p in enumerate(pecas):
        if p.congela:
            fil.append(f"[{pref}s{i}]anullsink")
            fil.append(f"aevalsrc=0|0:d={DUR_FREEZE:.3f}:s=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[{pref}a{i}]")
        else:
            tempo = f",atempo={p.vel:.4f}" if p.vel != 1.0 else ""
            fil.append(f"[{pref}s{i}]atrim=start={p.ini_rel:.3f}:duration={p.dur_fonte:.3f},asetpts=PTS-STARTPTS{tempo},{fmt}"
                       f"[{pref}a{i}]")
    fil.append("".join(f"[{pref}a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[{pref}c]")
    return fil, f"[{pref}c]"


def _render_clip_complexo(src: str, m: Moment, out: Path, cfg: Config, trilha_jogo: int | None,
                          ex: _ExtrasCorte) -> None:
    """O corte com efeito de tempo (trechos acelerados, congelamento no headshot), voz tratada e/ou faixa do jogo
    vinda do filtro. Mesmo fade, mesmo volume e mesma faixa "jogo" do corte normal, mas em um filter_complex."""
    from .efeitos import DUR_FREEZE
    from .sfx import garantir_sfx
    pecas, n = ex.pecas, len(ex.pecas)
    dur_final = sum(p.dur_saida for p in pecas)
    f = cfg.montagem.transicao
    lufs = cfg.qualidade.lufs
    tem_audio = _n_faixas(src) >= 1

    fil = [f"[0:v]{_escala16(_area(src))},setsar=1,fps={FPS},split={n}" + "".join(f"[s{i}]" for i in range(n))]
    zw, zh = int(round(W16 * 1.08 / 2) * 2), int(round(H16 * 1.08 / 2) * 2)   # punch-in de 8% no congelamento
    for i, p in enumerate(pecas):
        if p.congela:
            fil.append(f"[s{i}]trim=start={p.ini_rel:.3f}:duration={1.0 / FPS:.4f},setpts=PTS-STARTPTS,scale={zw}:{zh},"
                       f"crop={W16}:{H16},setsar=1,tpad=stop_mode=clone:stop_duration={DUR_FREEZE:.3f},"
                       f"trim=duration={DUR_FREEZE:.3f},fps={FPS},settb=AVTB[v{i}]")
        else:
            pts = "PTS-STARTPTS" if p.vel == 1.0 else f"(PTS-STARTPTS)/{p.vel:.4f}"
            fil.append(f"[s{i}]trim=start={p.ini_rel:.3f}:duration={p.dur_fonte:.3f},setpts={pts},fps={FPS},"
                       f"settb=AVTB[v{i}]")
    fil.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vc]")
    pos = _filtro_ass(ex.cartao) if ex.cartao is not None else ""
    (v_ini, v_fim), (a_ini, a_fim) = bordas_de_fade(m, f)
    if f > 0:
        pos += fade_de_video(v_ini, v_fim, dur_final)
    fil.append(f"[vc]null{pos},{BT709_FILTRO}[v]")

    entradas = ["-ss", f"{m.start:.3f}", "-t", f"{m.duration:.3f}", "-i", src]   # -t ANTES do -i: a saída tem outra duração
    mapas = ["-map", "[v]"]
    idx = 1
    if tem_audio:
        meas = _loudness_momento(src, m, lufs)
        if ex.voz and cfg.audio.tratar_voz:
            from .audio_mix import filtro_mix_voz_e_jogo_com_ducking
            fil.append(filtro_mix_voz_e_jogo_com_ducking(ex.voz[0], ex.voz[1]))
            base, af_mix = "[a_mix]", _af_voz_tratada(cfg, dur_final, f, a_ini, a_fim)
        elif ex.voz:   # só `faixa_comms`: microfone + jogo sem o Discord, mesmo volume padrão (medido) dos outros cortes
            from .audio_mix import filtro_mix_voz_e_jogo_simples
            fil.append(filtro_mix_voz_e_jogo_simples(ex.voz[0], ex.voz[1]))
            base, af_mix = "[a_mix]", audio_filter(meas, lufs, dur_final, f, a_ini, a_fim)
        else:
            base, af_mix = "[0:a:0]", audio_filter(meas, lufs, dur_final, f, a_ini, a_fim)
        filtros, saida = _cadeia_audio(base, pecas, "m")
        fil += filtros
        congelas = [i for i, p in enumerate(pecas) if p.congela]
        if congelas:   # V8: o impacto sonoro cai no instante do congelamento
            entradas += ["-i", str(garantir_sfx()["impacto"])]
            ms = int(round(sum(p.dur_saida for p in pecas[:congelas[0]]) * 1000))
            fil.append(f"[{idx}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=-12dB,"
                       f"adelay={ms}|{ms}[sfx]")
            fil.append(f"{saida}[sfx]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[mmx]")
            saida = "[mmx]"
            idx += 1
        fil.append(f"{saida}{af_mix}[a]")
        mapas += ["-map", "[a]"]
        if ex.wav_jogo is not None or trilha_jogo is not None:
            if ex.wav_jogo is not None:
                entradas += ["-i", str(ex.wav_jogo)]
                entrada_jogo, idx = f"[{idx}:a]", idx + 1
            else:
                entrada_jogo = f"[0:a:{trilha_jogo}]"
            filtros_j, saida_j = _cadeia_audio(entrada_jogo, pecas, "j")
            fil += filtros_j
            fil.append(f"{saida_j}{audio_filter(meas, lufs, dur_final, f, a_ini, a_fim)}[j]")
            mapas += ["-map", "[j]", "-metadata:s:a:1", "title=jogo"]
    _run_enc(lambda: [FFMPEG, "-y", "-v", "error", "-nostdin", *entradas, "-filter_complex", ";".join(fil), *mapas,
                      *video_args(cfg), *AUDIO_ARGS, "-movflags", "+faststart", str(out)],
             cwd=ex.cartao.parent if ex.cartao is not None else None)


def _caminho_filtro(p: Path) -> str:
    """Caminho absoluto no formato que os filtros do ffmpeg aceitam (barra normal e ':' escapado)."""
    return str(p).replace("\\", "/").replace(":", "\\:")


def _filtro_ass(ass_file: Path) -> str:
    """Trecho de filtro `,ass=...` que queima o arquivo de legenda. Quem usa roda o ffmpeg na pasta do .ass
    (`cwd=ass_file.parent`): o filtro não lida bem com caminhos com acento ou ':' no Windows."""
    if "'" in str(FONTES):   # apóstrofo fecha as aspas do filtro: usa uma cópia das fontes ao lado do .ass
        copia = ass_file.parent / "_fontes"
        with _TRAVA_FONTES:
            copia.mkdir(exist_ok=True)
            for fonte in FONTES.glob("*.ttf"):
                alvo = copia / fonte.name
                if not alvo.exists():
                    tmp = alvo.with_name(alvo.name + ".tmp")   # cópia pela metade nunca parece pronta
                    shutil.copy2(fonte, tmp)
                    substituir_arquivo(tmp, alvo)
        return f",ass=filename={ass_file.name}:fontsdir=_fontes"
    return f",ass=filename={ass_file.name}:fontsdir='{_caminho_filtro(FONTES)}'"


# ── tratamento de voz + ducking (A2/A3), só com faixas separadas do OBS ──────────────────────────

_AVISADOS_AUDIO: set[str] = set()


def _avisar_uma_vez(chave: str, texto: str) -> None:
    if chave not in _AVISADOS_AUDIO:
        _AVISADOS_AUDIO.add(chave)
        print(f"    [aviso] {texto}", flush=True)


@lru_cache(maxsize=16)
def _n_faixas(src: str) -> int:
    from .montagem import n_faixas_audio
    return n_faixas_audio(src)


@lru_cache(maxsize=16)
def _faixa_voz_valida(src: str, k: int) -> tuple[int | None, str]:
    from .faixas import faixa_de_voz
    return faixa_de_voz(src, k)


def _voz_ativa(cfg: Config, src: str, trilha_jogo: int | None) -> tuple[int, int] | None:
    """(índice da faixa da voz, índice da faixa do jogo) quando o áudio do corte deve sair de microfone + jogo: com
    `audio.tratar_voz` (voz limpa e jogo que abaixa) OU com `audio.faixa_comms` marcada (D-G1: o Discord nunca entra no
    corte; a mistura é simples). Precisa das duas faixas, válidas no vídeo; senão None (e um aviso uma vez só) e o áudio
    sai como sempre, da mistura da live."""
    a = cfg.audio
    if not a.tratar_voz and not a.faixa_comms:
        return None
    if a.faixa_voz <= 0 or trilha_jogo is None:
        if a.tratar_voz:
            _avisar_uma_vez("voz_config", "audio.tratar_voz precisa de audio.faixa_voz e de uma audio.faixa_jogo válida "
                            "(a faixa do jogo é conferida no vídeo); seguindo sem tratar a voz")
        else:
            _avisar_uma_vez("comms_config", "audio.faixa_comms está marcada, mas falta audio.faixa_voz ou uma audio.faixa_jogo "
                            "válida: o áudio dos cortes segue da mistura da live, que pode ter o Discord")
        return None
    if _n_faixas(src) < a.faixa_voz:
        _avisar_uma_vez("voz_faixas", f"o vídeo não tem a faixa de voz {a.faixa_voz}; seguindo com o áudio da mistura")
        return None
    indice, motivo = _faixa_voz_valida(src, a.faixa_voz)   # a mesma checagem da análise: muda ou igual à mistura não serve
    if indice is None:
        _avisar_uma_vez(f"voz_invalida:{src}", f"{motivo}; seguindo com o áudio da mistura da live")
        return None
    return indice, trilha_jogo


def _af_voz_tratada(cfg: Config, dur: float, fade: float, fade_ini: float | None = None,
                    fade_fim: float | None = None) -> str:
    """Filtro de áudio depois do tratamento de voz: o volume padrão em uma passada só (a medição do corte é da faixa
    1, que não é a mistura tratada) + o fade."""
    return f"loudnorm=I={cfg.qualidade.lufs}:TP=-1.5:LRA=11," + audio_filter(None, cfg.qualidade.lufs, dur, fade, fade_ini, fade_fim)


BANNER_ANTES = 1.0    # s: o banner é lido no quadro-chave (até ~2 s depois de aparecer): some o recorte um pouco antes
BANNER_DEPOIS = 4.5   # s: o banner de vitória/derrota fica na tela por uns 4 s


def expressao_janelas_killfeed(abates_rel: list[float], banner_rel: float | None) -> str:
    """Expressão `enable` do recorte do kill feed sobre o short: aparece de 0,25 s antes a 3,2 s depois de cada abate, MENOS
    enquanto o banner de fim do round está na tela (D-G4, dono, 08/11: o recorte cobria o texto da vitória/derrota)."""
    janelas = "+".join(f"between(t\\,{max(0.0, t - 0.25):.3f}\\,{t + 3.2:.3f})" for t in abates_rel)
    if banner_rel is None:
        return janelas
    return f"({janelas})*not(between(t\\,{max(0.0, banner_rel - BANNER_ANTES):.3f}\\,{banner_rel + BANNER_DEPOIS:.3f}))"


def geometria_do_short(s, cam_box: tuple[int, int, int, int] | None, tem_cam_video: bool) -> dict:
    """Onde fica cada painel do short em pé. `classico` (o de sempre): painel da câmera na largura toda (`camera_altura`), embaixo da faixa `topo`.
    `webcam_inteira` (23/11/2026): a webcam INTEIRA, com margem de `MARGEM_WEBCAM_INTEIRA` px dos lados, a partir de `max(topo, TOPO_SEGURO)`, na proporção
    do recorte (ou do vídeo da webcam); o jogo vai do fim dela (+ `VAO_WEBCAM_JOGO`) até o pé do quadro; o resto é fundo desfocado do próprio jogo."""
    W, H = s.largura, s.altura
    painel = bool(cam_box or tem_cam_video)
    if not painel:
        return {"inteira": False, "painel": False, "topo": 0, "cam_x": 0, "cam_w": W, "cam_h": 0, "jogo_y": 0, "jogo_h": H}
    if getattr(s, "layout", "classico") != "webcam_inteira":
        topo = max(0, s.topo)
        return {"inteira": False, "painel": True, "topo": topo, "cam_x": 0, "cam_w": W, "cam_h": s.camera_altura,
                "jogo_y": topo + s.camera_altura, "jogo_h": H - topo - s.camera_altura}
    cam_w = W - 2 * MARGEM_WEBCAM_INTEIRA
    if cam_box is not None and not tem_cam_video:
        proporcao = cam_box[3] / max(1, cam_box[2])
    else:                                              # vídeo só da webcam (live + demo): mantém a proporção em que ele foi montado
        proporcao = s.camera_altura / max(1, W)
    cam_h = int(cam_w * proporcao) // 2 * 2
    cam_x = MARGEM_WEBCAM_INTEIRA
    teto = int(CAM_INTEIRA_MAX * H) // 2 * 2
    if cam_h > teto:                                   # webcam alta (retrato): limita a altura e centraliza, sem deformar (revisão de 23/11)
        cam_h = teto
        cam_w = int(cam_h / proporcao) // 2 * 2
        cam_x = (W - cam_w) // 4 * 2
    topo = max(TOPO_SEGURO, s.topo)
    jogo_y = topo + cam_h + VAO_WEBCAM_JOGO
    return {"inteira": True, "painel": True, "topo": topo, "cam_x": cam_x, "cam_w": cam_w, "cam_h": cam_h,
            "jogo_y": jogo_y, "jogo_h": H - jogo_y}


@com_pico_garantido
def render_short(src: str, m: Moment, out: Path, cfg: Config, cam_box: tuple[int, int, int, int] | None,
                 ass_file: Path | None, tremida: bool = False, velocidade: float = 1.0,
                 area_jogo: tuple[int, int, int, int] | None = None, cam_video: str | None = None) -> None:
    """Vertical 1080x1920: webcam em cima, gameplay embaixo, legendas + selo, zoom no abate.

    cam_box em pixels da escala 1920x1080. `tremida`: chacoalha o jogo ~0,15 s em cada abate (versão com efeitos).
    `velocidade` < 1: câmera lenta (0,5 = metade; usado no replay do último abate).
    `area_jogo`: onde o jogo está no vídeo (px, sem as barras pretas); a mira é o centro dela e o recorte não
    passa para a barra. None = jogo na tela toda (o recorte de sempre).
    `cam_video` (S5): vídeo SÓ da webcam, já no tamanho do painel e na MESMA linha do tempo de `src` (a webcam da live junto da imagem limpa); vira o painel de
    cima no lugar do recorte de `cam_box`.
    """
    s = cfg.shorts
    W, H = s.largura, s.altura
    dur = m.duration
    painel = bool(cam_box or cam_video)
    geo = geometria_do_short(s, cam_box, bool(cam_video))
    cam_h, topo, game_h = geo["cam_h"], geo["topo"], geo["jogo_h"]
    inteira = geo["inteira"]
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
    if inteira and cam_box is not None and not cam_video:
        # 23/11 (prévia real): com zoom 1 o recorte do jogo (998 px) pegava 27 px da webcam da live na borda; estreita o recorte (mira no centro) até ela
        cx, _, cw, _ = cam_box
        centro = ax + aw / 2
        livre = centro - (cx + cw) if cx + cw <= centro else (cx - centro if cx >= centro else 0.0)
        if livre > 0 and ESTREITAR_MIN * gw <= 2 * livre < gw:
            gw = 2 * livre
            gh = gw * game_h / W
    # A mira (centro do quadro 16:9) fica a FRACAO_MIRA da altura do painel, não no meio: com a câmera em cima,
    # o meio caía em y~1264 de 1920, sob a descrição e os botões do Shorts/TikTok/Reels (abaixo de ~1248).
    gx = ax + (aw - gw) / 2
    gy = min(max(float(ay), ay + ah / 2 - FRACAO_MIRA * gh), ay + ah - gh)
    ev = lambda v: int(round(v / 2) * 2)  # noqa: E731

    interpolar = velocidade < 1.0 and _fps_fonte(src) * velocidade < LIMIAR_FPS_REPLAY
    fps_entrada = "" if interpolar else f",fps={FPS}"
    com_kf = bool(getattr(s, "mostrar_killfeed", True) and m.kills and gw < aw * 0.90 and not inteira)   # webcam_inteira é "sem efeito" (MARCA_E_VOZ §7)
    parts = [f"[0:v]{_escala16(area)}{fps_entrada},split={(3 if com_kf else 2) + (1 if inteira else 0)}[a][b]" + ("[kf]" if com_kf else "")
             + ("[fundo]" if inteira else "")]
    x_recorte = str(ev(gx))
    if getattr(s, "pan_suave", False) and m.kills and ax == 0 and aw == W16:   # V2: só com o jogo no quadro inteiro
        from .efeitos import expressao_pan_suave
        rel = [e.t - m.start for e in m.kills if -1.0 <= e.t - m.start <= dur + 1.0]
        x_recorte = "'" + expressao_pan_suave(gx, aw, gw, rel) + "'"
    game = f"[b]crop={ev(gw)}:{ev(gh)}:{x_recorte}:{ev(gy)},scale={W}:{game_h}:flags=lanczos"
    if s.zoom_no_abate and m.kills:
        bumps = "+".join(f"exp(-pow((t-{e.t - m.start + 0.04:.3f})/0.14\\,2))" for e in m.kills)
        z = f"(1+0.065*({bumps}))"
        game += f",scale=w='trunc(iw*{z}/2)*2':h='trunc(ih*{z}/2)*2':eval=frame,crop={W}:{game_h}"
    if tremida and m.kills:
        janela = "+".join(f"between(t\\,{e.t - m.start:.3f}\\,{e.t - m.start + 0.15:.3f})" for e in m.kills)
        game += (f",crop={W - 24}:{game_h - 24}:x='12+10*sin(t*90)*({janela})':y='12+8*cos(t*77)*({janela})'"
                 f",scale={W}:{game_h}")
    if com_kf:
        parts.append(game + "[g0]")
        kx, ky = ev(ax + aw * 0.72), ev(ay + ah * 0.055)
        kw, kh = max(64, ev(aw * 0.27)), max(32, ev(ah * 0.14))
        janelas_kf = expressao_janelas_killfeed([e.t - m.start for e in m.kills],
                                                None if m.round_banner_t is None else m.round_banner_t - m.start)
        parts.append(f"[kf]crop={kw}:{kh}:{kx}:{ky},scale={ev(W * 0.44)}:-2:flags=lanczos[kfo]")
        parts.append(f"[g0][kfo]overlay=x=W-w-20:y=20:enable='{janelas_kf}'[g]")
    else:
        parts.append(game + "[g]")
    cam_w = geo["cam_w"]
    if painel:
        if cam_video:
            parts.append("[a]nullsink")                                  # a imagem do jogo não vira painel: a webcam vem do segundo arquivo
            parts.append(f"[1:v]scale={cam_w}:{cam_h}:flags=lanczos,setsar=1[c]")
        elif cam_box is not None:     # sem `cam_video`, `painel` só é verdadeiro se houver `cam_box`
            cx, cy, cw, ch = cam_box
            parts.append(f"[a]crop={cw}:{ch}:{cx}:{cy},scale={cam_w}:{cam_h}:flags=lanczos,unsharp=5:5:0.5[c]")
        if inteira:   # 23/11: fundo = o próprio jogo, pequeno, desfocado e um pouco escuro; webcam com margem; jogo embaixo
            parts.append(f"[fundo]crop={ev(aw)}:{ev(ah)}:{ev(ax)}:{ev(ay)},scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
                         f"gblur=sigma=10,eq=brightness=-0.06,scale={W}:{H}:flags=bilinear,setsar=1[bg]")
            parts.append(f"[bg][c]overlay=x={geo['cam_x']}:y={topo}[bgc]")
            parts.append(f"[bgc][g]overlay=x=0:y={geo['jogo_y']}[v0]")
        else:
            parts.append(f"[c][g]vstack=inputs=2,pad={W}:{H}:0:{topo}:color=0x0b0d10[v0]" if topo
                         else "[c][g]vstack=inputs=2[v0]")
    else:
        parts.append("[a]nullsink")
        parts.append("[g]null[v0]")
    post = "[v0]format=yuv420p"
    if ass_file is not None:
        post += _filtro_ass(ass_file)
    f = 0.1
    if velocidade != 1.0:   # câmera lenta: estica o tempo antes dos fades (que contam na duração final)
        if interpolar:
            post += (f",setpts={1 / velocidade:.4f}*PTS,"
                     f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=obmc:me_mode=bilat:me=epzs,"
                     f"tpad=stop_mode=clone:stop_duration=0.2,trim=duration={dur / velocidade:.3f}")
        else:
            post += f",setpts={1 / velocidade:.4f}*PTS,fps={FPS}"   # repete quadros: o arquivo segue em 60 fps
    dur_saida = dur / velocidade
    ini_c, fim_c = getattr(m, "_fade_ini", True), getattr(m, "_fade_fim", True)   # False = emenda interna de sequência (corte seco)
    post += ((f",fade=t=in:st=0:d={f}" if ini_c else "")
             + (f",fade=t=out:st={max(0, dur_saida - 0.2):.3f}:d=0.2" if fim_c else "") + f",{BT709_FILTRO}[v]")
    parts.append(post)

    voz = _voz_ativa(cfg, src, getattr(cfg, "_trilha_jogo", None))
    _, (a_ini, a_fim) = bordas_de_fade(m, 0.12)
    if voz and cfg.audio.tratar_voz:   # A2/A3: voz tratada + jogo que abaixa quando você fala, em vez da mistura crua da faixa 1
        af = _af_voz_tratada(cfg, dur, 0.12, a_ini, a_fim)
    else:
        af = audio_filter(_loudness_momento(src, m, cfg.qualidade.lufs), cfg.qualidade.lufs, dur, 0.12, a_ini, a_fim)
    if velocidade != 1.0:
        af = f"atempo={velocidade:.4f}," + af.replace(f"st={max(0, dur - 0.12):.3f}", f"st={max(0, dur_saida - 0.12):.3f}")
    if voz:
        from .audio_mix import filtro_mix_voz_e_jogo_com_ducking, filtro_mix_voz_e_jogo_simples
        mix = (filtro_mix_voz_e_jogo_com_ducking(voz[0], voz[1]) if cfg.audio.tratar_voz
               else filtro_mix_voz_e_jogo_simples(voz[0], voz[1]))
        parts.append(mix + f";[a_mix]{af}[a_s]")
        audio_cmd = ["-map", "[a_s]"]
    else:
        audio_cmd = ["-map", "0:a:0?", "-af", af]
    # roda na pasta do .ass: o filtro de legendas não lida bem com caminhos com acento/":" no Windows
    # -t ANTES do -i: limita o que é lido. Depois do -i limitaria a saída, que na câmera lenta é mais longa
    # (o replay saía cortado pela metade e a checagem passava por pouco, 17/10/2026).
    entrada_cam = ["-ss", f"{m.start:.3f}", "-t", f"{dur:.3f}", "-i", str(cam_video)] if cam_video else []   # mesma linha do tempo de `src`: mesmo -ss e -t
    _run_enc(lambda: [FFMPEG, "-y", "-v", "error", "-nostdin", "-ss", f"{m.start:.3f}", "-t", f"{dur:.3f}", "-i", src, *entrada_cam,
                      "-filter_complex", ";".join(parts), "-map", "[v]", *audio_cmd,
                      *video_args(cfg), *AUDIO_ARGS, "-movflags", "+faststart", str(out)],
             cwd=ass_file.parent if ass_file is not None else None)


def render_por_janelas(m: Moment, out: Path, render_um, cfg: Config | None = None) -> None:
    """Momento com mais de um trecho (sequência do round, sem o tempo parado do meio): cada trecho é renderizado
    do jeito normal (com seu fade e seu volume) e os trechos são juntados em um vídeo só.
    `render_um(momento_de_um_trecho, destino)` é quem renderiza (corte horizontal ou short).
    O guarda de pico roda UMA vez, no arquivo juntado: por trecho, só o trecho com o tiro alto perdia volume e a
    emenda ganhava um degrau (achado G-G06 do Gemini)."""
    janelas = m.janelas
    if len(janelas) == 1:
        render_um(m, out)
        return
    partes: list[Path] = []
    cache_loudness = getattr(m, "_cache_loudness", None)
    if not isinstance(cache_loudness, dict):
        cache_loudness = {}

    def trecho(i: int) -> Moment:
        mm = dataclasses.replace(m, start=janelas[i][0], end=janelas[i][1], segmentos=[])
        mm.trecho = i      # as legendas usam: o gancho (nome da jogada) só no 1º trecho
        if cfg is not None and cfg.montagem.transicao_tipo == "corte":   # emendas internas secas; só as pontas têm fade
            mm._fade_ini = i == 0
            mm._fade_fim = i == len(janelas) - 1
        mm._janela_loudness = getattr(m, "_janela_loudness", None) or (janelas[0][0], janelas[-1][1])
        mm._cache_loudness = cache_loudness
        return mm

    antes = getattr(_PICO, "adiar", False)
    try:
        _PICO.adiar = True                        # cada trecho sai sem o guarda; ele roda no final, no arquivo inteiro
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
        concat(partes, out, cfg=cfg)   # com a config do dono: se os trechos saíram em encoders diferentes, o reencode usa a qualidade dela (R-027)
    finally:
        _PICO.adiar = antes
        for parte in partes:
            parte.unlink(missing_ok=True)
    garantir_pico(out, cfg)


def _faixa(arquivo: Path, sel: str, campos: str) -> str:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", sel, "-show_entries", f"stream={campos}",
                        "-of", "csv=p=0", str(arquivo)], capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    return r.stdout.strip()


def _duracao(arquivo: Path) -> float:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(arquivo)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


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
    n_a = len([ln for ln in _faixa(arquivo, "a", "index").splitlines() if ln.strip()])
    return f"{v} | áudio {a or 'nenhum'} x{n_a}{' | x264' if _feito_no_x264(arquivo) else ''}"


def _lista_concat(p: Path) -> str:
    """Linha da lista do concat. Apóstrofo no caminho (C:\\Users\\D'Avila) fecharia as aspas: vira '\\'' (o escape
    que o ffmpeg documenta). Caminho absoluto: o ffmpeg resolve os relativos a partir da pasta da lista."""
    return "file '" + p.resolve().as_posix().replace("'", "'\\''") + "'\n"


def _juntar_reencodando(files: list[Path], out: Path, cfg: Config) -> None:
    """Junta trechos de formatos diferentes (outro encoder, perfil ou tamanho): mais lento, mas sempre funciona.
    Erro aqui se repetiria para sempre, porque os cortes do cache são os mesmos a cada execução."""
    w, h = (int(x) for x in _faixa(files[0], "v:0", "width,height").split(",")[:2])
    tem_a = [bool(_faixa(f, "a:0", "codec_name")) for f in files]
    com_audio = any(tem_a)   # trecho mudo ganha silêncio: o som dos outros não pode ser descartado
    fil = []
    for i, f in enumerate(files):
        fil.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                   f"setsar=1,fps={FPS},format=yuv420p,{BT709_FILTRO}[v{i}]")
        if com_audio:
            if tem_a[i]:
                fil.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
            else:
                fil.append(f"aevalsrc=0|0:d={_duracao(f):.3f}:s=48000[a{i}]")
    entradas = "".join(f"[v{i}][a{i}]" if com_audio else f"[v{i}]" for i in range(len(files)))
    fil.append(f"{entradas}concat=n={len(files)}:v=1:a={1 if com_audio else 0}[v]" + ("[a]" if com_audio else ""))
    _run_enc(lambda: [FFMPEG, "-y", "-v", "error", "-nostdin", *[x for f in files for x in ("-i", str(f))],
                      "-filter_complex", ";".join(fil), "-map", "[v]", *(["-map", "[a]"] if com_audio else []),
                      *video_args(cfg), *(AUDIO_ARGS if com_audio else []), "-movflags", "+faststart", str(out)])


_MUSICA_AUTO = object()   # `musica` não informada: o `concat` escolhe a faixa sozinho


def concat(files: list[Path], out: Path, cfg: Config | None = None,
           tiros_rel: list[float | None] | None = None, musica: Path | None | object = _MUSICA_AUTO,
           abates_rel: list[list[float]] | None = None) -> str | None:
    """Junta os trechos. `musica`: a faixa já decidida pelo chamador (None = sem música); sem ela, escolhe pela
    chave do arquivo. Quem também usa a faixa fora daqui (o `.fcpxml`) deve decidir antes e passar.
    Na montagem devolve o resumo ("com música X (~120 BPM)" ou o motivo de ter saído sem música)."""
    # "-c copy" só é seguro com o mesmo formato em todos: se a GPU falhou no meio, um trecho pode ter saído em
    # x264 e outro em NVENC (os dois 'h264', mas com parâmetros diferentes) e o arquivo juntado sairia com blocos
    # e ainda passaria na checagem. Nesse caso junta reencodando (`cfg` dá o encoder; sem ele, o padrão).
    if not files:   # o ffmpeg daria só "Invalid data found", sem dizer o motivo
        raise ValueError("nada para juntar: nenhum trecho foi renderizado")
    if cfg is not None and out.name.startswith(("MONTAGEM_", "FAILS_", "MELHORES_DE_TODOS")):
        from .montagem import musica_da_montagem, render_montagem
        if out.name.startswith("FAILS_"):
            faixa = None                                   # compilação de fails: sem música
        elif musica is _MUSICA_AUTO:
            faixa = musica_da_montagem(cfg, out.name.split(".tmp")[0])
        else:
            faixa = musica
        return render_montagem(files, out, cfg, faixa, tiros_rel=tiros_rel, abates_rel=abates_rel)
    codecs = {_codec(f) for f in files}
    if len(codecs) > 1:
        print(f"    (trechos em formatos diferentes: juntando com reencode, mais lento — {' / '.join(sorted(codecs))})",
              flush=True)
        if cfg is None:     # quem chama sem a config do dono leva a qualidade padrão: avisa em vez de calar (R-027)
            print("    [aviso] juntando sem a configuração do dono: o reencode usa a qualidade padrão", flush=True)
        _juntar_reencodando(files, out, cfg or Config())
        return None
    lst = out.with_suffix(".txt")
    lst.write_text("".join(_lista_concat(p) for p in files), encoding="utf-8")
    try:
        _run([FFMPEG, "-y", "-v", "error", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(lst),
              "-map", "0", "-c", "copy", "-movflags", "+faststart", str(out)])   # -map 0: leva a faixa do jogo (A1)
    finally:
        lst.unlink(missing_ok=True)   # a lista não pode sobrar nem quando o ffmpeg falha
    return None


def thumbnail(src: str, t: float, out: Path, width: int = 480) -> None:
    _run([FFMPEG, "-y", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
          "-vf", f"scale={width}:-2", "-q:v", "3", str(out)])
