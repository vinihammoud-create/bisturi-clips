"""Recursos avançados de edição Pro para CS2 (V2, M7, V8, A4 e V7):

* V2 (`expressao_pan_suave`): deslocamento horizontal suave no Short 9:16 em torno da mira sem movimentos bruscos.
* M7 (`gerar_trechos_speed_ramp` + `render_clip_speed_ramp`): acelera em 1,75x o deslocamento sem combate entre dois
  abates do mesmo round e volta a 1,0x antes de abrir a mira no próximo inimigo (estilo Virre CS2 / BLAST).
* V8 (`render_freeze_headshot`): congela 0,25 s no quadro exato do headshot decisivo com punch-in de 8% + SFX CC0.
* A4 (`extrair_jogo_de_faixa_unica`): separa o som do jogo da voz quando a gravação não tem faixa só do jogo (1 faixa,
  ou o OBS gravando a mesma mistura em todas): usa a IA Demucs se instalada (`instalar_separacao_ia.bat`); senão
  aplica atenuação espectral da banda vocal preservando os transientes dos tiros.
* V7 (`rife_executavel`): localiza `rife-ncnn-vulkan` em `bin/` ou no PATH para slow-motion por IA (com fallback
  automático para o `minterpolate` do FFmpeg).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .moments import Moment
from .plano import TrechoPlano
from .tools import FFMPEG, ROOT
from .tools import sem_janela


# ── V2: rastreamento horizontal suave no Short 9:16 ──────────────────────────────────────────────

def expressao_pan_suave(gx_base: float, aw: float, gw: float, kills_rel: list[float]) -> str:
    """Devolve uma expressão FFmpeg para `x` no `crop` do Short 9:16 que mantém a mira centrada em `gx_base`
    com micro-movimento orgânico amortecido nos abates, nunca saindo da área do jogo `[0, aw - gw]`."""
    gx_int = int(round(gx_base / 2) * 2)
    margem = max(0, int((aw - gw) // 2))
    if not kills_rel or margem < 16:
        return str(gx_int)
    desvio = min(18, margem // 2)
    ondas = "+".join(f"sin((t-{t:.2f})*6)*exp(-pow((t-{t:.2f})/0.35\\,2))" for t in kills_rel[:5])
    return f"clip({gx_int}+{desvio}*({ondas})\\,0\\,{max(0, int(aw - gw))})"


# ── M7: Speed-Ramp (Time-Ramping) entre abates distantes ─────────────────────────────────────────

def gerar_trechos_speed_ramp(
    m: Moment,
    vel_rapida: float = 1.75,
    folga_pos_abate: float = 0.75,
    folga_pre_abate: float = 0.90,
    gap_minimo: float = 2.6,
) -> list[TrechoPlano]:
    """Divide um momento com 2+ abates em trechos de velocidade normal (1,0x nos abates) e acelerada (`vel_rapida`
    no deslocamento vazio entre abates quando o intervalo entre eles é >= `gap_minimo`)."""
    ks = sorted(k.t for k in m.kills if m.start <= k.t <= m.end)
    if len(ks) < 2:
        return [TrechoPlano(inicio=round(m.start, 3), fim=round(m.end, 3), velocidade=1.0, motivo="jogada")]

    trechos: list[TrechoPlano] = []
    cursor = m.start
    for i in range(len(ks) - 1):
        fim_acao = min(m.end, ks[i] + folga_pos_abate)
        ini_prox = max(fim_acao, ks[i + 1] - folga_pre_abate)
        if ini_prox - fim_acao >= (gap_minimo - folga_pos_abate - folga_pre_abate):
            if fim_acao > cursor + 0.15:
                trechos.append(TrechoPlano(inicio=round(cursor, 3), fim=round(fim_acao, 3), velocidade=1.0, motivo="abate"))
            trechos.append(TrechoPlano(inicio=round(fim_acao, 3), fim=round(ini_prox, 3), velocidade=vel_rapida, motivo="jogada"))
            cursor = ini_prox
    if m.end > cursor + 0.15:
        trechos.append(TrechoPlano(inicio=round(cursor, 3), fim=round(m.end, 3), velocidade=1.0, motivo="abate"))
    return trechos or [TrechoPlano(inicio=round(m.start, 3), fim=round(m.end, 3), velocidade=1.0, motivo="jogada")]


def render_clip_speed_ramp(src: str, m: Moment, out: Path, cfg: Config, vel_rapida: float = 1.75) -> Path:
    """Renderiza um corte 16:9 aplicando speed-ramp (1,0x nos abates, `vel_rapida` nos deslocamentos entre eles)."""
    from .render import AUDIO_ARGS, BT709_FILTRO, FPS, _area, _escala16, _run_enc, video_args

    trechos = gerar_trechos_speed_ramp(m, vel_rapida=vel_rapida)
    escala = _escala16(_area(src))
    fil: list[str] = []
    n = len(trechos)
    for i, tr in enumerate(trechos):
        ini_rel = max(0.0, tr.inicio - m.start)
        dur_tr = tr.fim - tr.inicio
        v_pts = f",setpts={1.0 / tr.velocidade:.4f}*(PTS-STARTPTS)" if tr.velocidade != 1.0 else ",setpts=PTS-STARTPTS"
        fil.append(f"[0:v]trim=start={ini_rel:.3f}:duration={dur_tr:.3f}{v_pts},{escala},fps={FPS},settb=AVTB[v{i}]")
        a_tempo = f",atempo={tr.velocidade:.4f}" if tr.velocidade != 1.0 else ""
        fil.append(f"[0:a:0]atrim=start={ini_rel:.3f}:duration={dur_tr:.3f},asetpts=PTS-STARTPTS{a_tempo},"
                   f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
    fil.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][a]")
    fil.append(f"[vc]{BT709_FILTRO}[v]")

    dur_bruta = m.end - m.start
    _run_enc(lambda: [
        FFMPEG, "-y", "-v", "error", "-nostdin",
        "-ss", f"{m.start:.3f}", "-i", src, "-t", f"{dur_bruta:.3f}",
        "-filter_complex", ";".join(fil),
        "-map", "[v]", "-map", "[a]",
        *video_args(cfg), *AUDIO_ARGS, "-movflags", "+faststart", str(out),
    ])
    return out


# ── V8: Freeze-Frame + Punch-in no Headshot Decisivo ─────────────────────────────────────────────

def render_freeze_headshot(src: str, m: Moment, out: Path, cfg: Config, dur_freeze: float = 0.25,
                           zoom: float = 1.08) -> Path:
    """Congela o quadro exato do último headshot (ou último abate) por `dur_freeze` s com zoom de `zoom` (8%)
    e impacto sonoro CC0 (`impacto_hs.wav`), seguindo com o restante do corte."""
    from .render import AUDIO_ARGS, BT709_FILTRO, FPS, H16, W16, _area, _escala16, _run_enc, video_args
    from .sfx import garantir_sfx

    hss = [k.t for k in m.kills if k.hs and m.start < k.t < m.end]
    alvo_t = hss[-1] if hss else (m.kills[-1].t if m.kills else (m.start + m.duration * 0.5))
    rel = max(0.2, min(m.duration - 0.2, alvo_t - m.start))
    escala = _escala16(_area(src))
    impacto = garantir_sfx()["impacto"]
    ms_delay = max(0, int(round(rel * 1000)))

    zw = int(round(W16 * zoom / 2) * 2)
    zh = int(round(H16 * zoom / 2) * 2)
    fil = [
        f"[0:v]{escala},setsar=1,fps={FPS},split=3[v_antes][v_freeze][v_depois]",
        f"[v_antes]trim=start=0:duration={rel:.3f},setpts=PTS-STARTPTS,settb=AVTB[va]",
        f"[v_freeze]trim=start={rel:.3f}:duration={1.0 / FPS:.4f},setpts=PTS-STARTPTS,"
        f"scale={zw}:{zh},crop={W16}:{H16},setsar=1,tpad=stop_mode=clone:stop_duration={dur_freeze:.3f},"
        f"trim=duration={dur_freeze:.3f},fps={FPS},settb=AVTB[vf]",
        f"[v_depois]trim=start={rel:.3f}:duration={max(0.1, m.duration - rel):.3f},setpts=PTS-STARTPTS,settb=AVTB[vd]",
        f"[va][vf][vd]concat=n=3:v=1:a=0,{BT709_FILTRO}[v]",
        f"[0:a:0]atrim=start=0:duration={rel:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[aa]",
        f"aevalsrc=0|0:d={dur_freeze:.3f}:s=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[afz]",
        f"[0:a:0]atrim=start={rel:.3f}:duration={max(0.1, m.duration - rel):.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[ad]",
        "[aa][afz][ad]concat=n=3:v=0:a=1[a_base]",
        f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume=-12dB,adelay={ms_delay}|{ms_delay}[a_sfx]",
        "[a_base][a_sfx]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[a]",
    ]
    _run_enc(lambda: [
        FFMPEG, "-y", "-v", "error", "-nostdin",
        "-ss", f"{m.start:.3f}", "-t", f"{m.duration:.3f}", "-i", src,
        "-i", str(impacto),
        "-filter_complex", ";".join(fil),
        "-map", "[v]", "-map", "[a]",
        *video_args(cfg), *AUDIO_ARGS, "-movflags", "+faststart", str(out),
    ])
    return out


# ── M7 + V8 dentro do corte normal (render.render_clip): o corte vira uma lista de "peças" ────────

DUR_FREEZE = 0.25        # s de congelamento no headshot decisivo
MARGEM_FREEZE = 0.3      # s: o headshot precisa estar a mais que isso das pontas do trecho


@dataclass
class Peca:
    """Um pedaço do corte: um trecho normal (vel 1,0), um acelerado (vel > 1) ou um congelamento."""
    ini_rel: float            # s desde o começo do corte, no vídeo de origem
    dur_fonte: float          # s do vídeo de origem que a peça cobre (no congelamento, o instante do quadro: 0)
    vel: float = 1.0
    congela: bool = False

    @property
    def dur_saida(self) -> float:
        return DUR_FREEZE if self.congela else self.dur_fonte / self.vel


def headshot_decisivo(m: Moment) -> float | None:
    """Instante (s do vídeo) do último headshot do momento, ou None se não houve."""
    hss = [k.t for k in m.kills if k.hs]
    return max(hss) if hss else None


def pecas_do_corte(m: Moment, cfg: Config, vel_rapida: float = 1.75) -> list[Peca]:
    """As peças de UM trecho do corte (`m` é o momento inteiro ou um dos trechos de uma sequência), de acordo com
    `cortes.speed_ramp` e `cortes.freeze_headshot`. Sem nenhum dos dois, ou sem o que ajustar, é uma peça só:
    o corte normal."""
    normal = [Peca(0.0, max(0.0, m.end - m.start))]
    c = cfg.cortes
    if not (getattr(c, "speed_ramp", False) or getattr(c, "freeze_headshot", False)):
        return normal
    if getattr(c, "speed_ramp", False):
        trechos = gerar_trechos_speed_ramp(m, vel_rapida=vel_rapida)
        pecas = [Peca(max(0.0, t.inicio - m.start), t.fim - t.inicio, t.velocidade) for t in trechos
                 if t.fim - t.inicio > 0.05]
    else:
        pecas = normal
    alvo = headshot_decisivo(m) if getattr(c, "freeze_headshot", False) else None
    if alvo is not None and m.start + MARGEM_FREEZE < alvo < m.end - MARGEM_FREEZE:
        rel, saida = alvo - m.start, []
        for p in pecas:
            if p.vel == 1.0 and p.ini_rel < rel < p.ini_rel + p.dur_fonte:
                saida += [Peca(p.ini_rel, rel - p.ini_rel), Peca(rel, 0.0, congela=True),
                          Peca(rel, p.ini_rel + p.dur_fonte - rel)]
            else:
                saida.append(p)
        pecas = saida
    return pecas or normal


def duracao_do_corte(m: Moment, cfg: Config) -> float:
    """Duração final de UM trecho do corte com os efeitos ligados (o QC compara o arquivo com isto)."""
    return sum(p.dur_saida for p in pecas_do_corte(m, cfg))


def tem_efeito_de_tempo(pecas: list[Peca]) -> bool:
    return len(pecas) > 1 or any(p.congela or p.vel != 1.0 for p in pecas)


# ── A4: Separador de jogo vs. voz (gravação sem faixa só do jogo) ────────────────────────────────

# IA opcional: Demucs 4.1.0 (modelo htdemucs), só CPU, numa pasta própria (.venv-ia) criada pelo
# instalar_separacao_ia.bat; apagar a pasta desfaz tudo. Conferido pelo auditor de segurança em 01/11/2026.
# "hf://" obriga o download do modelo em safetensors (não executa código) pelo Hugging Face: sem ele, o demucs pode
# cair sem avisar no servidor antigo da Meta, que carrega os pesos por pickle.
MODELO_DEMUCS = "hf://htdemucs"
CACHE_MODELO_DEMUCS = Path.home() / ".cache" / "huggingface" / "hub" / "models--adefossez--HTDemucs"
_TRAVA_IA = threading.Lock()     # os cortes saem em paralelo; a IA usa todos os núcleos: uma separação por vez


def comando_demucs() -> list[str] | None:
    """Como chamar o demucs: pelo Python da pasta `.venv-ia` (instalação à parte) ou pelo próprio Python do
    programa, se o pacote estiver nele. Nunca pelo PATH: os atalhos .bat não ativam a .venv, e o `demucs` do PATH
    seria outro. None = não instalado (a separação usa o filtro do FFmpeg)."""
    for py in (ROOT / ".venv-ia" / "Scripts" / "python.exe", ROOT / ".venv-ia" / "bin" / "python"):
        if py.is_file():
            return [str(py), "-m", "demucs"]
    if importlib.util.find_spec("demucs") is not None:
        return [sys.executable, "-m", "demucs"]
    return None


def _env_demucs() -> dict[str, str]:
    env = dict(os.environ, HF_HUB_DISABLE_TELEMETRY="1", PYTHONIOENCODING="utf-8")
    if any(CACHE_MODELO_DEMUCS.glob("snapshots/*/*.safetensors")):
        env["HF_HUB_OFFLINE"] = "1"      # modelo já baixado: nenhum acesso à rede depois do 1º uso
    return env


def _ultima_linha(texto: str | None) -> str:
    linhas = [ln.strip() for ln in (texto or "").splitlines() if ln.strip()]
    return linhas[-1][:160] if linhas else ""


def _separar_com_ia(cmd: list[str], src: str, destino_wav: Path, recorte_args: list[str], dur: float) -> str:
    """Roda o Demucs. Devolve "" se deu certo (o .wav está em `destino_wav`) ou o motivo da falha."""
    tmp_in = destino_wav.with_suffix(".in.wav")
    out_dir = destino_wav.parent / f"_ia_{destino_wav.stem}"     # uma pasta por corte (os cortes saem em paralelo)
    limite = 3600.0 if dur <= 0 else max(300.0, dur * 8.0)       # CPU: ~1,5x a duração; o 1º uso baixa 84 MB
    try:
        subprocess.run([FFMPEG, "-y", "-v", "error", "-nostdin", *recorte_args, "-i", src,
                        "-vn", "-ar", "44100", "-ac", "2", str(tmp_in)], check=True, capture_output=True, **sem_janela())
        with _TRAVA_IA:
            r = subprocess.run([*cmd, "--two-stems=vocals", "-n", MODELO_DEMUCS, "-d", "cpu", "-o", str(out_dir),
                                str(tmp_in)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=limite, env=_env_demucs(), **sem_janela())
        achados = sorted(out_dir.rglob("no_vocals.wav"))
        if r.returncode == 0 and achados:
            shutil.move(str(achados[0]), str(destino_wav))
            return ""
        return _ultima_linha(r.stderr) or f"código {r.returncode}, sem o arquivo sem voz"
    except subprocess.TimeoutExpired:
        return f"passou de {limite:.0f} s"
    except (OSError, subprocess.CalledProcessError) as e:
        return str(e)[:160]
    finally:
        tmp_in.unlink(missing_ok=True)
        shutil.rmtree(out_dir, ignore_errors=True)


def extrair_jogo_de_faixa_unica(src: str, destino_wav: Path, ini: float = 0.0, dur: float = 0.0) -> tuple[Path, str]:
    """Extrai o áudio do jogo tirando a voz quando a gravação não tem faixa só do jogo (A4).
    Com a IA instalada (`comando_demucs`), usa o modelo `htdemucs --two-stems=vocals` (`no_vocals.wav`).
    Sem ela (ou se falhar), aplica supressão espectral da banda fundamental da fala (220-2600 Hz no canal central)
    preservando sub-graves (passos/explosões) e agudos (tiros/headshots). Devolve (wav, como foi separado)."""
    destino_wav = Path(destino_wav)
    destino_wav.parent.mkdir(parents=True, exist_ok=True)
    recorte_args = (["-ss", f"{ini:.3f}"] if ini > 0 else []) + (["-t", f"{dur:.3f}"] if dur > 0 else [])

    cmd = comando_demucs()
    falha_ia = _separar_com_ia(cmd, src, destino_wav, recorte_args, dur) if cmd else ""
    if cmd and not falha_ia:
        return destino_wav, "IA (Demucs htdemucs)"

    # Fallback leve e instantâneo via FFmpeg: atenua a região vocal central mantendo graves (<180 Hz) e agudos (>3200 Hz)
    af = (
        "aresample=48000,"
        "equalizer=f=550:t=q:w=1.2:g=-9,"
        "equalizer=f=1400:t=q:w=1.4:g=-8,"
        "equalizer=f=80:t=q:w=1.0:g=2,"
        "equalizer=f=5500:t=q:w=1.2:g=2"
    )
    subprocess.run([FFMPEG, "-y", "-v", "error", "-nostdin", *recorte_args, "-i", src,
                    "-vn", "-af", af, "-c:a", "pcm_s16le", str(destino_wav)], check=True, capture_output=True, **sem_janela())
    if falha_ia:
        return destino_wav, f"filtro espectral FFmpeg (a IA falhou: {falha_ia})"
    return destino_wav, "filtro espectral FFmpeg (IA não instalada: qualidade menor)"


# ── V7: Localizador do RIFE Vulkan portátil ──────────────────────────────────────────────────────

def rife_executavel() -> Path | None:
    """Procura `rife-ncnn-vulkan` em `bin/` ou no PATH do sistema."""
    for cand in (ROOT / "bin" / "rife-ncnn-vulkan.exe", ROOT / "bin" / "rife-ncnn-vulkan"):
        if cand.is_file():
            return cand
    achado = shutil.which("rife-ncnn-vulkan")
    return Path(achado) if achado else None
