"""Andamento da execução em `saida/progresso.json`, para uma interface (ou qualquer outro programa) mostrar a
barra de progresso sem ler o texto do terminal. Só escreve; ninguém precisa ler de volta.

Formato 1 (sempre este objeto, reescrito inteiro de forma atômica a cada mudança):
    {"formato": 1, "estado": "rodando" | "pronto" | "com_problemas" | "erro",
     "modo": "completo" | "previa", "pid": 1234, "iniciado_em": "2026-10-17T14:02:11", "atualizado_em": "2026-10-17T14:02:40",
     "video": "cs2 twitch.mp4", "video_n": 1, "video_total": 2,
     "etapa": "Renderizando 29 cortes ...", "detalhe": "12 de 37", "etapas_feitas": 6, "etapas_estimadas": 10,
     "mensagem": ""}
`etapas_estimadas` é um palpite (a lista muda com chat, webcam e render): a interface deve tratar a barra de um
vídeo como aproximada e só considerar terminado quando `estado` deixar de ser "rodando".
Nunca levanta erro: se não der para gravar (disco cheio, arquivo aberto no leitor), segue sem avisar.
"""
from __future__ import annotations

import datetime
import json
import os
import threading
from pathlib import Path

FORMATO = 1
ETAPAS_ESTIMADAS = 10

_lock = threading.Lock()
_arq: Path | None = None
_estado: dict = {}


def _agora() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _gravar() -> None:
    if _arq is None:
        return
    _estado["atualizado_em"] = _agora()
    tmp = _arq.with_name(_arq.name + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(_estado, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, _arq)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def iniciar(out_root: Path, video_total: int, modo: str = "completo") -> None:
    """Começa uma execução (zera o que havia). `modo`: "completo" ou "previa" (CS2/OBS abertos: sem GPU)."""
    global _arq
    with _lock:
        _arq = Path(out_root) / "progresso.json"
        _estado.clear()
        _estado.update({"formato": FORMATO, "estado": "rodando", "pid": os.getpid(), "iniciado_em": _agora(),
                        "modo": modo, "video": "", "video_n": 0, "video_total": video_total, "etapa": "Começando",
                        "detalhe": "", "etapas_feitas": 0, "etapas_estimadas": ETAPAS_ESTIMADAS, "mensagem": ""})
        _gravar()


def video(nome: str, n: int) -> None:
    with _lock:
        _estado.update({"video": nome, "video_n": n, "etapa": "Começando", "detalhe": "", "etapas_feitas": 0})
        _gravar()


def etapa(nome: str) -> None:
    with _lock:
        _estado.update({"etapa": nome, "detalhe": ""})
        _gravar()


def etapa_feita() -> None:
    with _lock:
        _estado["etapas_feitas"] = _estado.get("etapas_feitas", 0) + 1
        _gravar()


def detalhe(texto: str) -> None:
    with _lock:
        _estado["detalhe"] = texto
        _gravar()


def fim(estado: str, mensagem: str = "") -> None:
    """`estado`: "pronto" | "com_problemas" | "erro"."""
    with _lock:
        _estado.update({"estado": estado, "mensagem": mensagem, "detalhe": ""})
        if estado == "pronto":
            _estado["etapas_feitas"] = _estado.get("etapas_estimadas", ETAPAS_ESTIMADAS)
        _gravar()
