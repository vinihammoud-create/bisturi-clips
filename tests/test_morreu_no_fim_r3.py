"""R3 (dono, 17/11/2026): "fez 2 kills e morreu para uma AWP", e o kit só dizia "round ganho" (VOD 1234571000, round 14). A sua morte até 4 s depois do
último abate da jogada marca "morreu no fim do lance" e o corte vai até 1,5 s depois dela. Números do caso real: abates 3049,47 e 3068,32 (depois do
conserto do abate duplicado), morte lida em 3071,95, fim do corte em ~3071."""
import json

from cs2clip.moments import Event, Moment
from cs2clip.sequencia import marcar_morte_no_fim


def _jogada(segmentos=True):
    m = Moment(3045.47, 3070.82, "abate", kills=[Event(3049.47, "kill"), Event(3068.32, "kill")], idx=1)
    if segmentos:
        m.segmentos = [(3045.47, 3055.47), (3062.32, 3070.82)]
    return m


def test_caso_real_do_round_14_mostra_a_morte_e_marca_o_lance():
    m = _jogada()
    assert marcar_morte_no_fim([m], [3071.95], duracao=6148.35) == 1
    assert m.morreu_no_fim and abs(m.end - 3073.45) < 1e-6 and m.segmentos[-1] == (3062.32, 3073.45)
    assert m.segmentos[0] == (3045.47, 3055.47) and m.start == 3045.47                 # começo e pulo não mudam


def test_controles_morte_longe_antes_ou_sem_morte_nao_mudam_nada():
    for mortes in ([3072.33], [3040.0], [], [3200.0]):                                 # 4,01 s depois; antes da jogada; nenhuma; outro round
        m = _jogada()
        assert marcar_morte_no_fim([m], mortes) == 0 and not m.morreu_no_fim and m.end == 3070.82
    reacao = Moment(3060.0, 3070.0, "reacao", idx=2)                                   # reação sem abate nunca é marcada
    assert marcar_morte_no_fim([reacao], [3069.0]) == 0 and not reacao.morreu_no_fim


def test_corte_nunca_encolhe_e_nao_passa_do_fim_do_video():
    m = _jogada(segmentos=False)
    m.end = 3080.0                                                                     # a reação já levava o fim além da morte
    assert marcar_morte_no_fim([m], [3071.95]) == 1 and m.morreu_no_fim and m.end == 3080.0
    m = _jogada(segmentos=False)
    marcar_morte_no_fim([m], [3071.95], duracao=3072.5)
    assert m.end == 3072.5


def test_a_marca_vai_para_o_momentos_json_a_revisao_e_o_relatorio(tmp_path):
    from cs2clip.momentos_io import gravar_momentos
    from cs2clip.report import entry
    from cs2clip.revisao import itens_revisao
    m = _jogada()
    marcar_morte_no_fim([m], [3071.95])
    gravar_momentos(tmp_path, [m], "v", 6148.35, "7")
    assert json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]["morreu_no_fim"] is True
    assert entry(m, "v", "t.jpg", "c.mp4", None)["morreu"] is True
    assert itens_revisao([m], {1: {"corte": "c.mp4"}})[0]["morreu_no_fim"] is True


def test_edicao_pela_demo_mostra_a_morte_logo_depois_do_ultimo_abate():
    # a demo sabe o instante exato da morte: até 4 s depois do último abate, o corte vai até 1 s depois dela e o momento é marcado
    from cs2clip.demo import Abate, PartidaDemo, RoundDemo, momentos_da_demo
    def partida(morte):
        p = PartidaDemo("de_mirage", {1: "eu"}, 1, "eu", [RoundDemo(14, 100.0, 200.0, "CT")])
        p.abates = [Abate(120.0, 14, "a", "m4a1"), Abate(140.0, 14, "b", "m4a1")]
        p.mortes = [morte] if morte is not None else []
        p.lados = {14: "CT"}
        return p
    m = momentos_da_demo(partida(143.0))[0]
    assert m.morreu_no_fim and abs(m.end - 144.0) < 1e-6 and m.round_resultado == "vitoria"     # time ganhou, ele morreu: as duas coisas aparecem
    for morte in (None, 144.5, 141.0 - 30):                                                       # sem morte, longe demais, antes da jogada
        m = momentos_da_demo(partida(morte))[0]
        assert not m.morreu_no_fim and abs(m.end - 142.5) < 1e-6                                  # o fim de sempre: 2,5 s depois do último abate
