"""Diagnóstico das faixas de áudio de uma gravação (B1, Plano V7 integrado). SÓ LÊ o vídeo: não altera nada.

Serve para conferir, em 1 minuto, se o OBS está gravando cada fonte numa faixa própria (mistura, jogo, microfone,
Discord) em vez da mesma mistura em todas. A sugestão de papel é uma pista, nunca uma certeza: duas faixas com fala
podem ser o microfone e o Discord, e só o dono sabe qual é qual — por isso o resultado sempre pede confirmação."""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field

import numpy as np

from .montagem import _duracao, _pcm, n_faixas_audio
from .tools import FFPROBE
from .tools import sem_janela

JANELA_S = 60.0           # total de áudio lido por faixa, em N_JANELAS trechos espalhados pela gravação inteira
N_JANELAS = 8             # (um trecho só do meio chamava de "muda" o microfone de quem jogou calado naquele pedaço)
SINAL_DB = -60.0          # trecho com volume acima disso tem sinal
SR = 16000                # 16 kHz: o VAD precisa disso
MUDA_DB = -70.0           # abaixo disso a faixa está muda
SILENCIO_DB = -55.0       # quadro de 50 ms abaixo disso conta como silêncio
IGUAL_DIFF = 1e-3         # diferença máxima entre amostras para dizer "é a mesma faixa"
FALA_ALTA = 0.25          # fala em mais de 25% do tempo: provável microfone/Discord
FALA_BAIXA = 0.08         # fala em menos de 8%: provável só jogo/música


@dataclass
class Faixa:
    ordem: int                      # 1 = primeira faixa de áudio (a que os players tocam)
    codec: str = ""
    canais: int = 0
    taxa: int = 0
    titulo: str = ""
    rms_db: float = -120.0
    pico_db: float = -120.0
    silencio: float = 1.0           # fração dos quadros de 50 ms em silêncio
    fala: float | None = None       # fração do tempo com fala (VAD); None = não medido
    corr_faixa1: float | None = None
    igual_faixa1: bool = False
    papel: str = "desconhecida"     # mistura | jogo | voz | muda | duplicada | desconhecida
    pista: str = ""
    avisos: list[str] = field(default_factory=list)
    janelas: int = 0                # quantos trechos foram lidos (0 = não amostrado)
    janelas_com_sinal: int = 0      # em quantos deles a faixa tinha sinal


def _db(x: float) -> float:
    return 20.0 * math.log10(x) if x > 1e-9 else -120.0


def listar(video: str) -> list[Faixa]:
    """As faixas de áudio do arquivo, pelo ffprobe (ordem 1, 2, 3...)."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=codec_name,channels,sample_rate:stream_tags=title,name", "-of", "json", str(video)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    try:
        fluxos = json.loads(r.stdout).get("streams", [])
    except ValueError:
        return []
    faixas = []
    for i, s in enumerate(fluxos, 1):
        tags = s.get("tags") or {}
        faixas.append(Faixa(i, s.get("codec_name", ""), int(s.get("channels") or 0), int(s.get("sample_rate") or 0),
                            tags.get("title") or tags.get("name") or ""))
    return faixas


def medir(faixa: Faixa, a: np.ndarray) -> None:
    """Preenche volume e silêncio a partir das amostras (mono, -1..1)."""
    if not len(a):
        faixa.avisos.append("não consegui ler o áudio desta faixa")
        return
    faixa.rms_db = _db(float(np.sqrt(np.mean(a ** 2))))
    faixa.pico_db = _db(float(np.max(np.abs(a))))
    q = int(0.05 * SR)
    n = len(a) // q
    if n:
        rms_q = np.sqrt(np.mean(a[: n * q].reshape(n, q) ** 2, axis=1))
        faixa.silencio = float(np.mean(rms_q < 10 ** (SILENCIO_DB / 20)))


def comparar_com_faixa1(faixa: Faixa, a: np.ndarray, base: np.ndarray) -> None:
    n = min(len(a), len(base))
    if n < SR or faixa.ordem == 1:
        return
    x, b = a[:n], base[:n]
    faixa.igual_faixa1 = bool(np.max(np.abs(x - b)) < IGUAL_DIFF)
    if float(np.std(x)) > 1e-7 and float(np.std(b)) > 1e-7:
        faixa.corr_faixa1 = float(np.corrcoef(x, b)[0, 1])


def amostrar(video: str, faixa: int, dur_total: float, total_s: float = JANELA_S, n: int = N_JANELAS,
             sr: int = SR) -> tuple[np.ndarray, int, int]:
    """Lê `total_s` segundos da faixa `faixa` (índice de 0) em `n` trechos espalhados pela gravação inteira. Devolve
    (áudio emendado, trechos com sinal, trechos lidos). Gravação curta (<= total_s) é lida inteira, num trecho só."""
    if dur_total <= total_s or n <= 1:
        trechos = [(0.0, max(dur_total, 1.0))]
    else:
        dur = total_s / n
        trechos = [(max(0.0, dur_total * (i + 0.5) / n - dur / 2), dur) for i in range(n)]
    pedacos, com_sinal = [], 0
    for ini, dur in trechos:
        a = _pcm(str(video), faixa, ini, dur, sr)
        if len(a):
            pedacos.append(a)
            com_sinal += int(_db(float(np.sqrt(np.mean(a ** 2)))) > SINAL_DB)
    return (np.concatenate(pedacos) if pedacos else np.zeros(0, np.float32)), com_sinal, len(trechos)


_NOMES = (("comms", ("discord", "comms", "comunica", "chat", "call")), ("voz", ("voz", "mic", "voice", "fala")),
          ("jogo", ("jogo", "game")), ("mistura", ("mix", "live", "master", "mistura")))


def _papel_pelo_nome(titulo: str) -> str | None:
    """O papel que o NOME da faixa no OBS indica ("Voz", "Jogo", "Discord", "Mix (live)"...), ou None."""
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFKD", titulo.lower()) if not unicodedata.combining(c))
    return next((papel for papel, chaves in _NOMES if any(c in t for c in chaves)), None)


def sugerir_papeis(faixas: list[Faixa], jogo: int = 0, voz: int = 0, comms: int = 0) -> None:
    """Preenche `papel` e `pista` de cada faixa. Regras simples e honestas; o dono confirma. `jogo`, `voz` e `comms`
    (números de 1 a 6, 0 = não informado) são o que o dono configurou: valem mais que a adivinhação."""
    marcadas = {jogo: ("jogo", "marcada por você como só o jogo (audio.faixa_jogo)"),
                voz: ("voz", "marcada por você como só o microfone (audio.faixa_voz)"),
                comms: ("comms", "marcada por você como Discord/comunicação (audio.faixa_comms)")}
    marcadas.pop(0, None)
    for f in faixas:
        if f.ordem in marcadas and f.ordem != 1 and f.rms_db >= MUDA_DB and not f.igual_faixa1:
            f.papel, f.pista = marcadas[f.ordem]
            continue
        pelo_nome = _papel_pelo_nome(f.titulo) if f.titulo and f.ordem != 1 else None
        if f.igual_faixa1:
            f.papel, f.pista = "duplicada", "igual à faixa 1: o OBS gravou a mesma mistura aqui"
        elif pelo_nome and pelo_nome != "mistura":
            f.papel, f.pista = pelo_nome, f"o nome da faixa no OBS (\"{f.titulo}\") diz que é {pelo_nome}"
            if f.janelas and f.janelas_com_sinal == 0:
                f.avisos.append(f"nenhum dos {f.janelas} trechos lidos tem sinal: fonte desligada, sem captura ou ninguém usou nesta gravação")
        elif _estado(f) == "muda":
            f.papel, f.pista = "muda", "sem som nenhum em nenhum trecho lido (fonte desligada ou faixa não marcada no OBS)"
        elif f.ordem == 1:
            f.papel, f.pista = "mistura", "a faixa 1 é a mistura que os players tocam"
        elif f.fala is None:
            f.papel, f.pista = "desconhecida", "fala não medida"
        elif f.fala >= FALA_ALTA:
            f.papel, f.pista = "voz", "tem fala em boa parte do tempo: microfone ou Discord (só você sabe qual)"
        elif f.fala <= FALA_BAIXA:
            f.papel, f.pista = "jogo", "quase sem fala: parece só o som do jogo"
        else:
            f.papel, f.pista = "desconhecida", "fala em parte do tempo: pode ser jogo com voz de fundo"
    if voz and not comms:   # o microfone já foi dito: a outra faixa com fala só pode ser outra pessoa (Discord)
        for f in faixas:
            if f.papel == "voz" and f.ordem != voz:
                f.papel, f.pista = "comms", "tem fala e não é o microfone que você marcou: provável Discord (sugestão; confirme e marque audio.faixa_comms)"
    vozes = [f.ordem for f in faixas if f.papel == "voz" and f.ordem not in marcadas]
    if len(vozes) > 1:
        for f in faixas:
            if f.papel == "voz":
                f.avisos.append(f"as faixas {', '.join(map(str, vozes))} têm fala: confirme qual é o microfone e qual é o Discord")
    if not any(f.papel == "jogo" for f in faixas):
        for f in faixas[:1]:
            f.avisos.append("nenhuma faixa parece ser só o jogo: a montagem com música precisa de uma")


def analisar(video: str, janela_s: float = JANELA_S, com_fala: bool = True, jogo: int = 0, voz: int = 0,
             comms: int = 0) -> list[Faixa]:
    faixas = listar(video)
    if not faixas:
        return []
    dur = _duracao(video)
    base = None
    for f in faixas:
        a, f.janelas_com_sinal, f.janelas = amostrar(video, f.ordem - 1, dur, janela_s)
        medir(f, a)
        if f.ordem == 1:
            base = a
        elif base is not None:
            comparar_com_faixa1(f, a, base)
        if com_fala and len(a) and f.janelas_com_sinal > 0:
            try:
                from .audio import speech_segments
                segs = speech_segments((a * 32768.0).astype(np.int16), SR)
                f.fala = min(1.0, sum(b - c for c, b in segs) / max(len(a) / SR, 1e-6))
            except Exception as e:  # noqa: BLE001  (sem o detector de fala, o resto do diagnóstico continua)
                f.avisos.append(f"fala não medida ({str(e)[:60]})")
    sugerir_papeis(faixas, jogo=jogo, voz=voz, comms=comms)
    return faixas


def texto(video: str, faixas: list[Faixa]) -> str:
    """Resumo em português, uma faixa por linha, para colar ou ler no terminal."""
    if not faixas:
        return f"{video}: nenhuma faixa de áudio encontrada."
    linhas = [f"{video}: {len(faixas)} faixa(s) de áudio"]
    for f in faixas:
        fala = "?" if f.fala is None else f"{f.fala * 100:.0f}%"
        igual = " | IGUAL à faixa 1" if f.igual_faixa1 else ""
        linhas.append(f"  Faixa {f.ordem}{' (' + f.titulo + ')' if f.titulo else ''}: {f.papel.upper()} — {f.pista}")
        trechos = f" · sinal em {f.janelas_com_sinal} de {f.janelas} trechos" if f.janelas > 1 else ""
        linhas.append(f"     volume {f.rms_db:.0f} dB · pico {f.pico_db:.0f} dB · silêncio {f.silencio * 100:.0f}% · fala {fala}{trechos}{igual}")
        for aviso in f.avisos:
            linhas.append(f"     ! {aviso}")
    linhas.append("Isto é uma pista, não uma certeza: confira ouvindo cada faixa (VLC > Áudio > Faixa de áudio).")
    return "\n".join(linhas)


def _estado(f: Faixa) -> str:
    sem_sinal = f.janelas_com_sinal == 0 if f.janelas else f.rms_db < MUDA_DB   # amostrada: só é muda sem sinal em TODOS os trechos
    return "muda" if sem_sinal else "duplicada" if f.igual_faixa1 else "ok"


def perfil_de_faixas(faixas: list[Faixa], audio) -> dict:
    """O perfil de faixas de UM vídeo: quantas, o estado de cada uma (ok / muda / duplicada da faixa 1) e o que o dono
    marcou em `[audio]`. Serve para o programa notar quando a gravação seguinte vem diferente."""
    return {"formato": 1, "n": len(faixas), "estado": {str(f.ordem): _estado(f) for f in faixas},
            "marcadas": {"jogo": audio.faixa_jogo, "voz": audio.faixa_voz, "comms": audio.faixa_comms}}


_RUIM = {"muda": "está muda", "duplicada": "ficou igual à faixa 1 (o OBS gravou a mesma mistura)"}


def comparar_perfis(anterior: dict | None, atual: dict) -> list[str]:
    """Avisos (português) sobre o perfil atual: faixa marcada pelo dono que não presta, e diferenças para a gravação anterior.
    Faixa que melhorou não gera aviso."""
    avisos = []
    for nome, k in atual.get("marcadas", {}).items():
        est = atual["estado"].get(str(k)) if k else None
        if est in _RUIM and k != 1:
            avisos.append(f"audio.faixa_{nome} = {k}: a faixa {k} {_RUIM[est]} neste vídeo")
    if anterior:
        if anterior.get("n") != atual["n"]:
            avisos.append(f"este vídeo tem {atual['n']} faixa(s) de áudio e o anterior tinha {anterior.get('n')} faixa(s): o OBS mudou?")
        for ordem, est in atual["estado"].items():
            antes = anterior.get("estado", {}).get(ordem)
            if antes == "ok" and est in _RUIM:
                avisos.append(f"a faixa {ordem} estava boa no vídeo anterior e agora {_RUIM[est]}")
    return avisos


def avisos_de_configuracao(audio, n_faixas: int) -> list[str]:
    """Avisos (em português) sobre as faixas configuradas em `[audio]`, para o começo do processamento."""
    avisos = []
    for nome, k in (("faixa_jogo", audio.faixa_jogo), ("faixa_voz", audio.faixa_voz), ("faixa_comms", audio.faixa_comms)):
        if k > n_faixas:
            avisos.append(f"audio.{nome} = {k}, mas o vídeo só tem {n_faixas} faixa(s) de áudio: essa configuração não vale para este vídeo")
    if audio.faixa_comms and not (audio.faixa_voz and audio.faixa_jogo):
        avisos.append("audio.faixa_comms está marcada, mas falta audio.faixa_voz ou audio.faixa_jogo: sem as duas o programa usa a "
                      "mistura da live (faixa 1), que pode ter o Discord dentro. Marque as duas para o Discord ficar fora dos cortes")
    return avisos


def faixa_de_voz(src: str, k: int) -> tuple[int | None, str]:
    """A faixa que a análise de voz (VAD, grito, Whisper, emoção) deve ler, a partir de `audio.faixa_voz = k`.
    Devolve (índice de 0, aviso). None = ler a mistura de sempre (k <= 1, faixa que não existe, muda ou igual à
    faixa 1: nesses casos o aviso explica por quê)."""
    if k <= 1:
        return None, ""
    n = n_faixas_audio(src)
    if n < k:
        return None, f"o vídeo tem {n} faixa(s) e não tem a faixa {k} (audio.faixa_voz): a voz vem da mistura"
    dur = _duracao(src)
    mix, _, _ = amostrar(src, 0, dur)
    voz, com_sinal, _ = amostrar(src, k - 1, dur)
    if not len(voz) or com_sinal == 0:   # muda = sem sinal em NENHUM trecho da gravação inteira (calado no meio não conta)
        return None, f"a faixa {k} (audio.faixa_voz) está muda: a voz vem da mistura"
    if len(mix) == len(voz) and float(np.max(np.abs(mix - voz))) < IGUAL_DIFF:
        return None, f"a faixa {k} (audio.faixa_voz) é igual à faixa 1: a voz vem da mistura"
    return k - 1, ""
