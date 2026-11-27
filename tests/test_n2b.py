"""N2b (15/11/2026): fecha a FALHA leve do QA no N2 (regra do pulo dentro da jogada).

1. Quatro lacunas de teste (mutações que sobreviviam): `minimos` do `_unir` (M17), vão arredondado a 1 casa (M4), começo negativo na demo
   (M14) e a versão compacta passando da janela original (M25).
2. Fala cortada no meio de uma palavra no fim de um trecho DO MEIO (`sequencia._fechar_falas`): só ESTENDE, no máximo 1,5 s, só no vídeo
   (a demo não tem palavras), e o vão com o trecho seguinte é refeito.

Os testes usam `Config()`, nunca o config.toml do dono.
"""
import pytest

from cs2clip.config import Config
from cs2clip.demo import Abate, PartidaDemo, RoundDemo, momentos_da_demo
from cs2clip.moments import Event, Moment, snap_to_speech
from cs2clip.partes import dividir_partes
from cs2clip.rounds import RoundTimelineEntry as R
from cs2clip.sequencia import FALA_EXTRA_MAX, _fechar_falas, compactar, regra_do_corte, trechos_da_jogada, unir_sequencias
from cs2clip.transcribe import Word


def _partes():
    # banners a cada 110 s: o round 2 vai de 80 a 190 (onde ficam os abates dos testes)
    return dividir_partes([R(80.0 + 110.0 * i, "vitoria", .9) for i in range(5)], [], 700.0)


def _jogada(ts, reacao=0.0):
    """Um corte como o `build_moments` faz: 4 s antes do 1º abate, 2,5 s (+ reação) depois do último."""
    kills = [Event(t, "kill", []) for t in ts]
    return Moment(max(0.0, ts[0] - 4.0), ts[-1] + 2.5 + reacao, "abate", kills=kills, label="KILL", score=20.0)


def _igual(obtido, esperado):
    assert len(obtido) == len(esperado), f"{obtido} != {esperado}"
    for (a, b), (c, d) in zip(obtido, esperado):
        assert a == pytest.approx(c, abs=1e-6) and b == pytest.approx(d, abs=1e-6), f"{obtido} != {esperado}"


def _partida(ts, fim_round=400.0):
    rs = [RoundDemo(1, 0.0, fim_round, "T")]
    return PartidaDemo("de_x", {1: "Eu"}, 1, "Eu", rs, [Abate(t, 1, "v", "ak47") for t in ts], [], [], (1, 0), {1: "T"})


# ── M17: os trechos que o corte já tinha (reação, ajuste às falas) valem dentro de `_unir` ────────

def test_unir_reacao_de_uma_jogada_do_meio_nao_encolhe_e_junta_com_a_seguinte():
    """Abates 100 e 116: A = (96, 108,5) por reação de 6 s; B = (112, 118,5). A regra sozinha daria (96; 106) e (110; 118,5), com 4 s de vão
    (pula); com os mínimos a reação do meio fica (108,5) e o vão com 110 (1,5 s) junta tudo."""
    [m] = unir_sequencias([_jogada([100.0], reacao=6.0), _jogada([116.0])], _partes(), Config().cortes)
    _igual(m.janelas, [(96.0, 118.5)])
    assert m.segmentos == [] and (m.start, m.end) == (96.0, 118.5)


def test_unir_reacao_do_meio_maior_que_6_s_continua_inteira_quando_ha_pulo():
    [m] = unir_sequencias([_jogada([100.0], reacao=6.0), _jogada([130.0])], _partes(), Config().cortes)
    _igual(m.janelas, [(96.0, 108.5), (124.0, 132.5)])         # sem os mínimos o 1º trecho acabaria em 106,0


def test_unir_ajuste_as_falas_de_uma_jogada_do_meio_nao_encolhe():
    """O ajuste às falas recuou o começo da 2ª jogada para 121,0 (a regra sozinha começaria em 124,0): o começo antigo fica."""
    b = _jogada([130.0])
    b.start = 121.0
    [m] = unir_sequencias([_jogada([100.0]), b], _partes(), Config().cortes)
    _igual(m.janelas, [(96.0, 106.0), (121.0, 132.5)])


# ── M4: o vão é arredondado a 1 microssegundo, não a 1 casa ───────────────────────────────────

@pytest.mark.parametrize("vao,pula", [(13.99, False), (13.999, False), (13.9999, False), (14.0, True), (14.001, True)])
def test_funcao_pura_vao_quase_14_s_nao_pula(vao, pula):
    t = trechos_da_jogada([100.0, 100.0 + vao], 96.0, 100.0 + vao + 2.5, 6.0, 6.0, 2.0)
    assert (len(t) == 2) is pula, t


@pytest.mark.parametrize("vao,pula", [(13.99, False), (14.0, True)])
def test_unir_13_99_s_continuo_e_14_s_pula(vao, pula):
    [m] = unir_sequencias([_jogada([100.0]), _jogada([100.0 + vao])], _partes(), Config().cortes)
    assert (len(m.janelas) == 2) is pula


@pytest.mark.parametrize("vao,pula", [(13.99, False), (14.0, True)])
def test_demo_13_99_s_continuo_e_14_s_pula(vao, pula):
    [m] = momentos_da_demo(_partida([100.0, 100.0 + vao]))
    assert (len(m.janelas) == 2) is pula


# ── M14: a demo nunca começa antes de 0 ──────────────────────────────────────────────────────

def test_demo_abate_perto_do_inicio_nao_gera_tempo_negativo():
    [m] = momentos_da_demo(_partida([2.0]))
    assert m.janelas == [(0.0, 4.5)] and m.start == 0.0
    [m] = momentos_da_demo(_partida([1.0, 40.0]))
    assert m.janelas[0][0] == 0.0 and all(a >= 0.0 and b > a for a, b in m.janelas) and m.start == 0.0
    assert len(m.kills) == 2


# ── M25: o short compacto nunca sai da janela original ───────────────────────────────────────

def _jogada_de_8_s():
    m = Moment(96.0, 104.0, "abate", kills=[Event(100.0, "kill", [])], label="KILL", score=20.0)
    assert m.duration == 8.0
    return m


def test_compactar_com_shorts_antes_maior_nao_passa_do_comeco_da_janela():
    m = _jogada_de_8_s()
    curto = compactar(m, 7.5, 6.0, 1.0, 6.0)      # shorts.antes (6) > cortes.antes (4): 100 - 6 = 94 passaria do começo (96)
    assert curto is not m and curto.janelas[0][0] == 96.0 and curto.janelas == [(96.0, 101.0)]


def test_compactar_com_shorts_depois_maior_nao_passa_do_fim_da_janela():
    m = _jogada_de_8_s()
    curto = compactar(m, 7.5, 1.0, 6.0, 6.0)      # 100 + 6 = 106 passaria do fim (104)
    assert curto.janelas == [(99.0, 104.0)]


def test_compactar_nunca_sai_da_janela_externa_numa_jogada_com_pulo():
    """A trava é a janela externa da jogada (começo do 1º trecho, fim do último), como sempre foi."""
    m = Moment(96.0, 132.5, "abate", kills=[Event(100.0, "kill", []), Event(130.0, "kill", [])], label="KILL", score=20.0)
    m.segmentos = [(96.0, 104.0), (126.0, 132.5)]
    for folgas in ((6.0, 5.0), (9.0, 9.0), (2.0, 1.5)):
        curto = compactar(m, 14.0, *folgas, 6.0)
        assert len(curto.kills) == 2
        assert curto.janelas[0][0] >= 96.0 and curto.janelas[-1][1] <= 132.5 and all(b > a for a, b in curto.janelas)


# ── fala cortada no meio do trecho do meio ───────────────────────────────────────────────────

def _w(a, b):
    return Word(a, b, "palavra")


def _dois(words=None, duracao=0.0, k2=130.0, cortes=None):
    [m] = unir_sequencias([_jogada([100.0]), _jogada([k2])], _partes(), cortes or Config().cortes, words, duracao)
    return m


def test_caso_do_qa_o_corte_do_meio_espera_o_fim_da_palavra():
    """Abate em 100, janela (96; 102,5), palavra 105,0 a 106,6: o `snap_to_speech` real não mexe (silêncio em 102,5); a regra do pulo
    acabaria o trecho do meio em 100 + 6 = 106,0, no meio da palavra. Agora espera o fim dela (106,6). (Com 4 s depois do abate o caso
    era a palavra 103,0 a 104,6; com 6 s ela já termina antes do corte: o caso foi refeito para cruzar o 106,0.)"""
    words = [_w(105.0, 106.6)]
    a = _jogada([100.0])
    snap_to_speech(a, words, 700.0)
    assert (a.start, a.end) == (96.0, 102.5)                       # antes o corte caía em 102,5, no silêncio
    assert _dois().janelas == [(96.0, 106.0), (124.0, 132.5)]      # sem palavras: 106,0 (a regra pura)
    m = _dois(words)
    _igual(m.janelas, [(96.0, 106.6), (124.0, 132.5)])
    assert m.segmentos == m.janelas and (m.start, m.end) == (96.0, 132.5)


def test_palavra_longe_do_fim_do_trecho_nao_muda_nada():
    base = _dois().janelas
    assert _dois([_w(110.0, 111.0), _w(90.0, 91.0), _w(104.5, 105.0)]).janelas == base


def test_limite_de_1_5_s_estende_no_limite_e_nao_passa_dele():
    _igual(_dois([_w(105.0, 107.5)]).janelas, [(96.0, 107.5), (124.0, 132.5)])      # 1,5 s exatos: estende
    _igual(_dois([_w(105.0, 107.51)]).janelas, [(96.0, 106.0), (124.0, 132.5)])     # 1,51 s: fica como está
    _igual(_dois([_w(105.0, 110.0)]).janelas, [(96.0, 106.0), (124.0, 132.5)])      # palavra longa: fica como está
    assert FALA_EXTRA_MAX == 1.5


def test_sem_palavras_e_igual_a_hoje():
    base = _dois().janelas
    assert _dois(None).janelas == base and _dois([]).janelas == base


def test_palavra_que_comeca_ou_termina_exatamente_no_corte_nao_estende():
    base = _dois().janelas
    assert _dois([_w(106.0, 106.8)]).janelas == base        # começa no corte: não está no meio dela
    assert _dois([_w(105.0, 106.0)]).janelas == base        # termina no corte


def test_o_ultimo_trecho_nao_e_mexido_aqui():
    """O fim do último trecho é do `snap_to_speech` do `build_moments` (já aplicado antes): `_fechar_falas` não mexe nele."""
    words = [_w(132.0, 133.4)]       # atravessa o fim do último trecho (132,5)
    assert _dois(words).janelas == _dois().janelas == [(96.0, 106.0), (124.0, 132.5)]


def test_refaz_o_vao_com_o_proximo_e_junta_quando_fica_menor_que_o_pulo_minimo():
    """Abates em 100 e 115: trechos (96; 106) e (109; 117,5), vão de 3,0 s (pula). A palavra estende o 1º; o vão encolhe e, abaixo de
    `pulo_minimo` (2,0 s), os trechos se juntam. Vão de exatamente 2,0 s ainda pula."""
    def com(fim_palavra):
        return unir_sequencias([_jogada([100.0]), _jogada([115.0])], _partes(), Config().cortes, [_w(105.0, fim_palavra)], 0.0)[0]

    _igual(unir_sequencias([_jogada([100.0]), _jogada([115.0])], _partes(), Config().cortes)[0].janelas, [(96.0, 106.0), (109.0, 117.5)])
    _igual(com(106.6).janelas, [(96.0, 106.6), (109.0, 117.5)])          # vão de 2,4 s: continua pulando
    _igual(com(107.0).janelas, [(96.0, 107.0), (109.0, 117.5)])          # vão de exatamente 2,0 s: pula
    m = com(107.01)                                                      # vão de 1,99 s: junta
    _igual(m.janelas, [(96.0, 117.5)])
    assert m.segmentos == [] and (m.start, m.end) == (96.0, 117.5) and len(m.kills) == 2
    _igual(com(107.5).janelas, [(96.0, 117.5)])


def test_so_estende_nunca_encolhe_e_nao_perde_abate():
    for fim_palavra in (106.1, 106.6, 107.5, 108.0, 120.0):
        sem = _dois().janelas
        com = _dois([_w(105.0, fim_palavra)])
        assert len(com.kills) == 2
        assert sum(b - a for a, b in com.janelas) >= sum(b - a for a, b in sem) - 1e-9
        assert com.janelas[0][0] == sem[0][0] and com.janelas[-1] == sem[-1]
        assert all(any(a <= k.t <= b for a, b in com.janelas) for k in com.kills)


def test_duracao_do_video_limita_a_extensao():
    _igual(_dois([_w(105.0, 106.6)], duracao=106.3).janelas, [(96.0, 106.3), (124.0, 132.5)])
    _igual(_dois([_w(105.0, 106.6)], duracao=0.0).janelas, [(96.0, 106.6), (124.0, 132.5)])      # 0 = não informada


def test_varios_trechos_do_meio_cada_um_com_a_sua_palavra():
    ms = [_jogada([100.0]), _jogada([120.0]), _jogada([145.0])]
    words = [_w(105.5, 107.0), _w(125.0, 126.2)]      # trechos do meio: (96; 106), (114; 126) e o último (139; 147,5)
    [m] = unir_sequencias(ms, _partes(), Config().cortes, words, 0.0)
    _igual(m.janelas, [(96.0, 107.0), (114.0, 126.2), (139.0, 147.5)])


def test_pelo_lado_do_helper_palavras_sobrepostas_vale_a_que_termina_depois():
    t = _fechar_falas([(96.0, 104.0), (126.0, 132.5)], [_w(103.0, 104.3), _w(103.5, 105.0)], 0.0, 2.0)
    _igual(t, [(96.0, 105.0), (126.0, 132.5)])
    assert _fechar_falas([(96.0, 104.0)], [_w(103.0, 104.6)], 0.0, 2.0) == [(96.0, 104.0)]       # um trecho só: é o último


def test_os_tres_casos_de_aceite_do_n2_nao_mudam_com_palavras_longe_ou_vazias():
    casos = {
        "ace": ([95.3, 98.8, 117.4, 143.0, 172.4], [(91.3, 104.8), (111.4, 123.4), (137.0, 149.0), (166.4, 174.9)]),
        "quadra1": ([42.7, 59.3, 60.3, 74.8], [(38.7, 48.7), (53.3, 66.3), (68.8, 77.3)]),
        "quadra2": ([212.4, 228.9, 233.2, 234.9], [(208.4, 218.4), (222.9, 237.4)]),
    }
    for nome, (ks, esperado) in casos.items():
        grupos, atual = [], [ks[0]]
        for k in ks[1:]:
            if k - atual[-1] <= 8.0:
                atual.append(k)
            else:
                grupos.append(atual)
                atual = [k]
        grupos.append(atual)
        for palavras in (None, [], [_w(1.0, 2.0), _w(600.0, 601.0)]):
            [m] = unir_sequencias([_jogada(g) for g in grupos], _partes(), Config().cortes, palavras, 700.0)
            _igual(m.janelas, esperado)


def test_regra_do_corte_continua_com_as_tres_chaves():
    assert set(regra_do_corte(Config().cortes)) == {"depois_de_cada_abate", "antes_do_proximo", "pulo_minimo"}
