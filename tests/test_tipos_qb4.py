"""QB4 (13/11/2026): bugs achados pelos tipos ("pode estar vazio") que existem de verdade em algum caminho.

Cada teste falhou no código de antes do conserto (a saída está no commit) e tem CONTROLE.
"""
import pytest

from cs2clip import gravador_demo as gd
from cs2clip import sincronizar
from test_gravador_d4 import ambiente, Relogio, JogoFalso, _cfg, _dependencias, _partida, _roteiro  # noqa: F401


def test_gravador_sem_nenhuma_tentativa_de_camera_vira_erro_claro_e_nao_typeerror(ambiente):  # noqa: F811 (fixture importada)
    """`Roteiro(tentativas_camera=0)` deixava `melhor` vazio e o gravador morria com TypeError ("None" não tem índice)."""
    tmp, demo = ambiente
    rel, jogo = Relogio(), JogoFalso(Relogio())
    dep = _dependencias(jogo, rel)
    roteiro = _roteiro()
    roteiro.tentativas_camera = 0
    with pytest.raises(gd.ErroGravacao, match="câmera") as e:
        gd.gravar(demo, _partida({1: 3}), _cfg(), tmp / "saida", dep=dep, roteiro=roteiro, imprimir=lambda *a: None)
    assert e.value.etapa == "camera"
    assert jogo.fechado                       # o jogo é fechado do mesmo jeito (o `finally` do gravador roda)


def test_estimar_sem_candidatos_devolve_none_e_nao_typeerror(monkeypatch):
    """Com `MIN_CASADOS` em 0 e listas vazias não há deslocamento para testar: devolve None (documentado), não estoura."""
    monkeypatch.setattr(sincronizar, "MIN_CASADOS", 0)
    assert sincronizar.estimar([], []) is None


def test_estimar_controle_com_abates_de_verdade_continua_achando_o_deslocamento():
    demo = [10.0, 20.0, 31.0, 44.0]
    video = [t + 5.0 for t in demo]
    s = sincronizar.estimar(demo, video)
    assert s is not None and abs(s.deslocamento - 5.0) < 0.2
