"""Revisão de 26/11/2026, lote A1: G-010 (a janela do chat olha para trás, [t-4, t], e não para o futuro)."""
from __future__ import annotations

import json

from cs2clip import chat


def _curva(tmp_path, instantes, duracao=40.0):
    comentarios = [{"content_offset_seconds": t, "message": {"body": "nice"}, "commenter": {"name": f"u{i}"}}
                   for i, t in enumerate(instantes)]
    arq = tmp_path / "chat.json"
    arq.write_text(json.dumps({"comments": comentarios}), encoding="utf-8")
    curva, _ = chat.chat_curve(arq, duracao)
    return curva


def _segundos_ativos(curva):
    return sorted({i // int(round(1 / chat.HOP)) for i, v in enumerate(curva) if v > 0})


def test_g010_pico_de_chat_so_acende_do_segundo_do_pico_em_diante(tmp_path):
    curva = _curva(tmp_path, [10.0] * 5)
    ativos = _segundos_ativos(curva)
    assert ativos and min(ativos) >= 10          # nada antes do pico (antes: a curva já subia a partir de 6 s)
    assert ativos == [10, 11, 12, 13, 14]        # a janela [t-4, t]: o segundo t enxerga as mensagens dos 4 s anteriores


def test_g010_impulso_no_inicio_do_video_nao_estoura_a_curva(tmp_path):
    curva = _curva(tmp_path, [0.0] * 5, duracao=20.0)
    assert _segundos_ativos(curva) == [0, 1, 2, 3, 4]
