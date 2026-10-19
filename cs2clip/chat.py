"""Chat da Twitch como sinal de empolgação (picos de mensagens / emotes) -> curva 0–1.

Usa o TwitchDownloaderCLI (github.com/lay295/TwitchDownloader, MIT) para baixar o
chat do VOD pelo ID — detectado no nome do arquivo (ex.: 2893023758-....mp4).
Regra de pico (boas práticas de detectores de highlight por chat):
  janela de 5 s vs. mediana dos 3 min anteriores; precisa de >= 2x a base e
  >= 4 mensagens; cada pessoa conta no máx. 2x a cada 10 s; emotes/risadas pesam 2x;
  bots e comandos (!) são ignorados.
O chat reage alguns segundos DEPOIS do lance: a defasagem é calibrada por vídeo
cruzando os picos do chat com os abates detectados (fallback: config).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import numpy as np

from .audio import HOP
from .tools import ROOT

CLI = ROOT / "bin" / "TwitchDownloaderCLI.exe"
CHAT_TIMEOUT_S = 900   # chat de VOD de várias horas demora; mais que isso é API travada

BOTS = {"nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "wizebot", "soundalerts",
        "sery_bot", "botisimo", "kofistreambot", "creatisbot", "pokemoncommunitygame", "own3d"}
HYPE_TOKENS = {
    "kekw", "lul", "lol", "omegalul", "pog", "pogchamp", "poggers", "pogu", "clip", "clipa", "ace",
    "monkas", "ez", "insano", "absurdo", "caralho", "carai", "porra", "nossa", "mds", "w", "gg", "wtf",
    "monstro", "brabo", "nice", "ns", "lindo",
}
_LAUGH = re.compile(r"(k{3,}|(ha){2,}|(rs){2,})", re.I)


def vod_id_from_name(path: str) -> str | None:
    """ID do VOD da Twitch no começo do nome (hoje com 10 dígitos, ex.: 2902813681-...). 9 a 11 dígitos:
    nome de data como '20261017_201533' (celular, gravadores) tem só 8 e não vira VOD falso."""
    m = re.match(r"^(?:v)?(\d{9,11})(?:[-_ ]|$)", Path(path).stem)
    return m.group(1) if m else None


def _json_valido(f: Path) -> bool:
    try:
        json.loads(f.read_text(encoding="utf-8"))
        return True
    except (OSError, ValueError):
        return False


def download_chat(vod_id: str, out_json: Path) -> bool:
    if out_json.exists() and out_json.stat().st_size > 100:
        return True
    if not CLI.exists():
        print("    [chat] TwitchDownloaderCLI não encontrado em bin/ — sinal de chat ignorado.")
        return False
    # baixa num temporário e só troca pelo nome final se veio inteiro: um chat.json pela metade seria
    # reaproveitado como "pronto" em toda análise seguinte
    tmp = out_json.with_name(f"{out_json.stem}.tmp{out_json.suffix}")
    for _ in range(2):  # a API às vezes devolve resposta vazia: tenta de novo
        try:
            r = subprocess.run([str(CLI), "chatdownload", "--id", vod_id, "-o", str(tmp),
                                "--collision", "Overwrite"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=CHAT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            tmp.unlink(missing_ok=True)
            print(f"    [chat] chat indisponível: o download do VOD {vod_id} passou de {CHAT_TIMEOUT_S} s.")
            return False   # sem segunda tentativa: a API travou
        if r.returncode == 0 and tmp.exists() and _json_valido(tmp):
            os.replace(tmp, out_json)
            return True
    tmp.unlink(missing_ok=True)
    print(f"    [chat] não foi possível baixar o chat do VOD {vod_id} (VOD apagado/privado?).")
    return False


def chat_curve(chat_json: Path, duration: float) -> tuple[np.ndarray, int]:
    """Curva 0–1 (resolução HOP) com a intensidade dos picos do chat, SEM correção de atraso."""
    data = json.loads(chat_json.read_text(encoding="utf-8"))
    nsec = int(duration) + 1
    cnt = np.zeros(nsec, np.float32)
    seen: dict[tuple[str, int], int] = {}
    n = 0
    for c in data.get("comments", []):
        t = float(c.get("content_offset_seconds", -1))
        body = ((c.get("message") or {}).get("body") or "").strip()
        user = ((c.get("commenter") or {}).get("name") or "").lower()
        if not (0 <= t < nsec) or not body or user in BOTS or body.startswith("!"):
            continue
        key = (user, int(t // 10))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 2:
            continue
        toks = set(re.findall(r"\w+", body.lower()))
        hype = bool(toks & HYPE_TOKENS or _LAUGH.search(body))
        cnt[int(t)] += 2.0 if hype else 1.0
        n += 1
    win = np.convolve(cnt, np.ones(5, np.float32), mode="full")[4:4 + nsec]  # [t-4, t]
    base = np.ones(nsec, np.float32)
    for t in range(nsec):
        prev = win[max(0, t - 180):max(0, t - 5)]
        if prev.size:
            base[t] = max(1.0, float(np.median(prev)))
    c = np.clip((win / base - 2.0) / 3.0, 0.0, 1.0) * (win >= 4)
    return np.repeat(c, int(round(1 / HOP))).astype(np.float32), n


def estimate_lag(chat: np.ndarray, kill_times: list[float], default: float) -> float:
    """Defasagem (s) que melhor alinha picos do chat com os abates; fallback = default."""
    if len(kill_times) < 5 or chat.max() <= 0:
        return default
    lags = np.arange(2.0, 15.5, 0.5)
    scores = []
    for L in lags:
        s = 0.0
        for k in kill_times:
            a, b = int((k + L - 1) / HOP), int((k + L + 3) / HOP)
            if b > a and a < len(chat):
                s += float(chat[a:min(b, len(chat))].max())
        scores.append(s)
    scores = np.array(scores)
    if scores.max() < 1.0 or scores.max() < 1.5 * np.median(scores):
        return default
    return float(lags[int(np.argmax(scores))])


def shift(curve: np.ndarray, lag: float) -> np.ndarray:
    """Atribui a reação do chat em t+lag ao instante t."""
    k = int(lag / HOP)
    out = np.zeros_like(curve)
    if k < len(curve):
        out[:len(curve) - k] = curve[k:]
    return out
