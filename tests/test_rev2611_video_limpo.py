"""Revisão de 26/11/2026, lote A1: A-008 (casar o momento com o round gravado pelo tempo quando o número não casa)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cs2clip import video_limpo as vl
from cs2clip.moments import Event, Moment
from cs2clip.video_limpo import RoundGravado


def _rounds_gravados_com_numeracao_antiga():
    # pasta gravada antes da correção do R-023: o round físico 16 foi salvo como "17"
    return [RoundGravado(17, Path("round_17.mp4"), 1000.0, 1080.0, 80.0)], {17: 5.0 - 1000.0}


def _momento(n, ini=1010.0, fim=1030.0, kill=1020.0):
    return Moment(ini, fim, "abate", kills=[Event(kill, "kill")], score=20, idx=1, round_n=n)


def test_a008_numero_antigo_nao_casa_mas_o_tempo_casa_com_aviso():
    rs, desloc = _rounds_gravados_com_numeracao_antiga()
    avisos: list[str] = []
    saida = vl.mapear_momentos([_momento(16)], rs, desloc, imprimir=avisos.append)
    assert len(saida) == 1
    assert saida[0].kills[0].t == pytest.approx(1020.0 + 5.0 - 1000.0)
    assert len(avisos) == 1 and "tempo" in avisos[0].lower() and "16->17" in avisos[0]


def test_a008_numero_que_casa_nao_gera_aviso():
    rs, desloc = _rounds_gravados_com_numeracao_antiga()
    avisos: list[str] = []
    assert len(vl.mapear_momentos([_momento(17)], rs, desloc, imprimir=avisos.append)) == 1
    assert avisos == []


def test_a008_numero_igual_ao_de_outro_round_gravado_mas_tempo_de_outro_casa_pelo_tempo():
    # gravados na numeração antiga: "16" (físico 15) e "17" (físico 16). O momento do físico 16 vem com n=16 (certo, novo).
    rs = [RoundGravado(16, Path("a.mp4"), 900.0, 980.0, 80.0), RoundGravado(17, Path("b.mp4"), 1000.0, 1080.0, 80.0)]
    desloc = {16: -900.0, 17: 80.0 - 1000.0}
    saida = vl.mapear_momentos([_momento(16)], rs, desloc, imprimir=lambda *_: None)
    assert len(saida) == 1 and saida[0].kills[0].t == pytest.approx(1020.0 + 80.0 - 1000.0)


def test_a008_sem_round_gravado_no_tempo_continua_de_fora():
    rs, desloc = _rounds_gravados_com_numeracao_antiga()
    fora_do_tempo = _momento(16, ini=500.0, fim=520.0, kill=510.0)
    passa_do_fim = _momento(16, ini=1070.0, fim=1095.0, kill=1075.0)       # não cabe inteiro no round: sem a regra de aparar para o caso por tempo
    assert vl.mapear_momentos([fora_do_tempo, passa_do_fim], rs, desloc, imprimir=lambda *_: None) == []
