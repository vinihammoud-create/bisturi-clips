"""Benchmark automático (A4, Plano V7 integrado): a cada processamento grava `saida/<vídeo>/benchmark.json` e
acrescenta uma linha em `saida/benchmarks.csv`, para comparar versões, vídeos e configurações sem planilha manual.

Honestidade das colunas:
  * `tempo_etapas_s` é a SOMA dos tempos das etapas medidas (`tempos.json`), não o relógio do processo inteiro
    (partida do Python e relatórios ficam de fora, ~5% no vídeo de 23/10);
  * `rtf` = tempo_etapas_s / duracao_s (menor = mais rápido; 0,31 quer dizer 3,2 s de vídeo por segundo).
Medir nunca pode derrubar o processamento: qualquer erro aqui é engolido."""
from __future__ import annotations

import csv
import datetime
import json
import platform
import subprocess
import sys
from pathlib import Path

from .config import Config
from .tools import ROOT, gravar_atomico, substituir_arquivo
from .tools import sem_janela

# etapa (pedaço do nome que o Step imprime) -> coluna do CSV; o que não casa soma em t_outras_s
_ETAPAS = (("Varrendo", "t_varredura_s"), ("Lendo as suas mortes", "t_mortes_s"), ("Analisando a voz", "t_voz_s"),
           ("Transcrevendo", "t_whisper_s"), ("Renderizando", "t_render_s"), ("Montagem com", "t_montagem_s"))
COLUNAS = ("run_id", "data_utc", "video", "programa_commit", "ffmpeg", "python", "sistema", "perfil", "whisper_modelo",
           "encoder", "duracao_s", "largura", "altura", "fps", "tamanho_bytes", "faixas_audio",
           "tempo_etapas_s", "rtf", *(c for _, c in _ETAPAS), "t_outras_s",
           "montagem_musica", "montagem_lufs", "montagem_pico_dbtp")


def _commit() -> str:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                           timeout=5, **sem_janela())
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def montar(info, cfg: Config, nome: str, etapas: list[dict], som_montagem: dict | None, n_faixas: int,
           tamanho_bytes: int) -> dict:
    from .refazer import versao_ffmpeg
    agora = datetime.datetime.now(datetime.UTC)
    duracao = float(getattr(info, "duration", 0.0) or 0.0)
    tempos = {coluna: 0.0 for _, coluna in _ETAPAS}
    outras = 0.0
    for e in etapas:
        seg = float(e.get("segundos", 0.0))
        coluna = next((c for pedaco, c in _ETAPAS if pedaco in str(e.get("etapa", ""))), None)
        if coluna:
            tempos[coluna] += seg
        else:
            outras += seg
    total = round(sum(tempos.values()) + outras, 1)
    som = som_montagem or {}
    return {
        "run_id": agora.strftime("%Y%m%dT%H%M%S%fZ"), "data_utc": agora.strftime("%Y-%m-%dT%H:%M:%SZ"), "video": nome,
        "programa_commit": _commit(), "ffmpeg": versao_ffmpeg(), "python": platform.python_version(),
        "sistema": platform.platform(), "perfil": cfg.montagem.perfil or "", "whisper_modelo": cfg.transcricao.modelo,
        "encoder": cfg.qualidade.encoder, "duracao_s": round(duracao, 1), "largura": int(getattr(info, "width", 0) or 0),
        "altura": int(getattr(info, "height", 0) or 0), "fps": float(getattr(info, "fps", 0.0) or 0.0),
        "tamanho_bytes": int(tamanho_bytes), "faixas_audio": int(n_faixas), "tempo_etapas_s": total,
        "rtf": round(total / duracao, 3) if duracao > 0 else None,
        **{c: round(v, 1) for c, v in tempos.items()}, "t_outras_s": round(outras, 1),
        "montagem_musica": som.get("musica", ""), "montagem_lufs": som.get("lufs"), "montagem_pico_dbtp": som.get("pico_dbtp"),
        "ritmo": som.get("ritmo") or None,
        "etapas": list(etapas),
    }


def _acrescentar_csv(arquivo: Path, linha: dict) -> None:
    if arquivo.is_file():
        with arquivo.open(encoding="utf-8", newline="") as f:
            cabecalho = next(csv.reader(f), [])
        if tuple(cabecalho) != COLUNAS:   # formato antigo: guarda ao lado em vez de misturar colunas
            carimbo = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            substituir_arquivo(arquivo, arquivo.with_name(f"benchmarks.antigo-{carimbo}.csv"))
    novo = not arquivo.is_file()
    with arquivo.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS, extrasaction="ignore")
        if novo:
            w.writeheader()
        w.writerow(linha)


def gravar(out: Path, info, cfg: Config, nome: str, etapas: list[dict], som_montagem: dict | None, n_faixas: int,
           tamanho_bytes: int) -> None:
    try:
        dados = montar(info, cfg, nome, etapas, som_montagem, n_faixas, tamanho_bytes)
        gravar_atomico(out / "benchmark.json", json.dumps(dados, ensure_ascii=False, indent=1))
        _acrescentar_csv(out.parent / "benchmarks.csv", dados)
    except Exception as e:  # noqa: BLE001  (medir nunca derruba o processamento)
        print(f"    [aviso] benchmark não gravado ({str(e)[:100]})", file=sys.stderr)
