"""Revisão de 26/11/2026, lote A1: R-026 (apaga o material bruto `hlae/take*` de round juntado, só se a gravação inteira terminou sem erro)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_gravador_d4 import (  # noqa: F401  (a fixture `ambiente` precisa estar visível neste arquivo)
    JogoFalso,
    Relogio,
    _cfg,
    _dependencias,
    _partida,
    _roteiro,
    ambiente,
)

from cs2clip import gravador_demo as gd


def _takes(tmp: Path) -> list[str]:
    return sorted(p.name for p in (tmp / "saida" / "hlae").glob("take*"))


def _gravar(ambiente_, verificacoes, partida=None):
    tmp, demo = ambiente_
    rel = Relogio()
    partida = partida or _partida({1: 3, 2: 3})
    return gd.gravar(demo, partida, _cfg(), tmp / "saida", trechos=gd.rounds_para_gravar(partida, True, 3, 2.0, 3.0),
                     dep=_dependencias(JogoFalso(rel), rel, verificacoes=verificacoes), roteiro=_roteiro(), imprimir=lambda *a: None)


@pytest.mark.lento
def test_r026_gravacao_boa_apaga_os_takes_juntados_e_guarda_os_rounds(ambiente):  # noqa: F811
    tmp, _ = ambiente
    reg = _gravar(ambiente, [(False, "0 caixa"), (True, "3 caixa(s)"), (True, "ok")])     # o round 1 gastou 2 takes (câmera), o 2, mais 1
    assert _takes(tmp) == []                                                              # nenhum take bruto sobrou
    assert (tmp / "saida" / "rounds" / "round_01.mp4").is_file() and (tmp / "saida" / "rounds" / "round_02.mp4").is_file()
    assert not reg.get("erro") and reg["bruto_apagado"]["takes"] == 3
    assert json.loads((tmp / "saida" / "registro.json").read_text(encoding="utf-8"))["bruto_apagado"]["takes"] == 3


@pytest.mark.lento
def test_r026_erro_em_qualquer_round_nao_apaga_nada(ambiente):  # noqa: F811
    tmp, _ = ambiente
    # o round 1 grava bem; no round 2 nenhuma câmera serve: a gravação termina com erro
    with pytest.raises(gd.ErroGravacao, match="Nenhuma das câmeras"):
        _gravar(ambiente, [(True, "ok")] + [(False, "0 caixa")] * 10)
    takes = _takes(tmp)
    assert len(takes) >= 2 and "take0000" in takes                                       # inclusive o do round 1, que tinha sido juntado
    assert (tmp / "saida" / "rounds" / "round_01.mp4").is_file()
    assert "bruto_apagado" not in json.loads((tmp / "saida" / "registro.json").read_text(encoding="utf-8"))


@pytest.mark.lento
def test_r026_take_de_outra_gravacao_que_nao_foi_juntado_agora_fica(ambiente):  # noqa: F811
    tmp, _ = ambiente
    velho = tmp / "saida" / "hlae" / "take0099"
    (velho / "bisturi").mkdir(parents=True)
    (velho / "bisturi" / "video.mp4").write_bytes(b"x")
    _gravar(ambiente, [(True, "ok"), (True, "ok")])
    assert _takes(tmp) == ["take0099"]


def test_r026_so_apaga_dentro_da_pasta_hlae_e_so_com_o_round_pronto(tmp_path):
    hlae = tmp_path / "hlae"
    (hlae / "take0001").mkdir(parents=True)
    (hlae / "take0001" / "video.mp4").write_bytes(b"1234")
    (hlae / "take0002").mkdir()
    (tmp_path / "fora").mkdir()
    (tmp_path / "fora" / "dado.txt").write_text("nao apague", encoding="utf-8")
    pronto = tmp_path / "round_01.mp4"
    pronto.write_bytes(b"ok")
    res = gd.apagar_brutos_juntados(hlae, [("take0001", pronto), ("..", pronto), ("../fora", pronto), ("take0002", tmp_path / "nao_existe.mp4"),
                                           ("take0003", pronto), ("fora", pronto)])
    assert res == (1, 4)
    assert not (hlae / "take0001").exists() and (hlae / "take0002").is_dir()             # sem o round pronto, fica
    assert (tmp_path / "fora" / "dado.txt").read_text(encoding="utf-8") == "nao apague" and hlae.is_dir()
