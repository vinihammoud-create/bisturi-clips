"""N2 (15/11/2026): a regra do pulo dentro da jogada e a regra de ouro do dono.

Regra do pulo: numa jogada com mais de um abate o vídeo fica `depois_de_cada_abate` (6 s) depois de cada abate, volta `antes_do_proximo`
(6 s) antes do seguinte e só pula se sobrar `pulo_minimo` (2 s) ou mais para cortar: dois abates a menos de 14 s ficam contínuos. (O dono pediu
6 s e 5 s, no mesmo dia trocou por 4 e 4 e, depois de ver o ACE da Mirage de 20/10, voltou para 6 e 6: N2c.) Uma função só (`sequencia.trechos_da_jogada`) serve a edição pela demo, a edição pelo vídeo
(`_unir`, `remover_repetidos`) e, por elas, o short e a montagem.

Regra de ouro do dono: "nunca dividir kills do mesmo round: se matei 4 no round 6, o vídeo tem os 4 abates, nem menos". Nenhum limite
(`sequencia_max`, short compacto) tira abate; com a demo (parte exata) todos os abates do round vão num vídeo só.

Os números dos casos reais vêm do kit da Mirage de 20/10 (docs/HISTORICO.md §3.6 e docs/editing/REGRAS_DE_CORTE.md). Os testes usam `Config()`, nunca o config.toml do dono.
"""
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from cs2clip import editar_demo as ed
from cs2clip.config import Config, Cortes, load_config
from cs2clip.demo import Abate, PartidaDemo, RoundDemo, momentos_da_demo
from cs2clip.moments import Event, Moment
from cs2clip.partes import dividir_partes
from cs2clip.refazer import impressao
from cs2clip.rounds import RoundTimelineEntry as R
from cs2clip.sequencia import _fechar_falas, compactar, regra_do_corte, remover_repetidos, trechos_da_jogada, unir_sequencias

ACE = [95.3, 98.8, 117.4, 143.0, 172.4]
QUADRA_1 = [42.7, 59.3, 60.3, 74.8]
QUADRA_2 = [212.4, 228.9, 233.2, 234.9]

ESPERADO = {
    # ACE: 91,3 = 95,3 - 4; (91,3-101,3) e (92,8-104,8) se juntam; 111,4 = 117,4 - 6 (vão de 6,6 s desde 104,8: pula); 137,0 = 143,0 - 6;
    # 166,4 = 172,4 - 6 e o fim 174,9 = 172,4 + 2,5
    "ace": (ACE, [(91.3, 104.8), (111.4, 123.4), (137.0, 149.0), (166.4, 174.9)]),
    # QUADRA 1: 53,3 = 59,3 - 6 (vão de 4,6 s desde 48,7); (53,3-65,3) e (54,3-66,3) se juntam; 68,8 = 74,8 - 6 (vão de 2,5 s >= 2: pula)
    "quadra1": (QUADRA_1, [(38.7, 48.7), (53.3, 66.3), (68.8, 77.3)]),
    # QUADRA 2: o 2º trecho começa em 222,9 = 228,9 - 6 (vão de 4,5 s desde 218,4). Os abates de 233,2 e 234,9 caem dentro dele (228,9 + 6 = 234,9)
    # e o fim NÃO passa de 237,4 = 234,9 + 2,5: o `depois_de_cada_abate` (233,2 + 6 = 239,2) é limitado pelo fim da jogada (`min(fim, ...)`), que
    # é o de sempre (último abate + `cortes.depois`); o fim e o começo não mudam
    "quadra2": (QUADRA_2, [(208.4, 218.4), (222.9, 237.4)]),
}


def _igual(obtido, esperado):
    assert len(obtido) == len(esperado), f"{obtido} != {esperado}"
    for (a, b), (c, d) in zip(obtido, esperado):
        assert a == pytest.approx(c, abs=1e-6) and b == pytest.approx(d, abs=1e-6), f"{obtido} != {esperado}"


def _regra(**kw):
    c = Config().cortes
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _pela_funcao(ks, cortes=None):
    c = cortes or Config().cortes
    return trechos_da_jogada(ks, ks[0] - c.antes, ks[-1] + c.depois, **regra_do_corte(c))


def _kills_nas_janelas(m):
    return all(any(a <= k.t <= b for a, b in m.janelas) for k in m.kills)


# ── os padrões ──────────────────────────────────────────────────────────────────────────────────

def test_padroes_da_regra_sao_6_6_2():
    c = Config().cortes
    assert (c.depois_de_cada_abate, c.antes_do_proximo, c.pulo_minimo) == (6.0, 6.0, 2.0)
    assert regra_do_corte(c) == {"depois_de_cada_abate": 6.0, "antes_do_proximo": 6.0, "pulo_minimo": 2.0}


def test_config_antigo_com_pular_parado_e_antes_depois_fixados_continua_lendo_e_nao_anula_a_regra(tmp_path):
    arq = tmp_path / "config.toml"
    arq.write_text("[cortes]\nantes = 4.0\ndepois = 2.5\npular_parado = 3.5\n", encoding="utf-8")
    cfg = load_config(arq)
    assert cfg.cortes.pular_parado == 3.5                       # continua aceita
    assert (cfg.cortes.depois_de_cada_abate, cfg.cortes.antes_do_proximo, cfg.cortes.pulo_minimo) == (6.0, 6.0, 2.0)
    _igual(_pela_funcao(ACE, cfg.cortes), ESPERADO["ace"][1])   # e o pulo não depende dela
    arq.write_text("[cortes]\ndepois_de_cada_abate = 8\nantes_do_proximo = 3\npulo_minimo = 1\n", encoding="utf-8")
    c = load_config(arq).cortes
    assert (c.depois_de_cada_abate, c.antes_do_proximo, c.pulo_minimo) == (8.0, 3.0, 1.0)


def test_config_recusa_numero_negativo_da_regra(tmp_path):
    arq = tmp_path / "config.toml"
    arq.write_text("[cortes]\npulo_minimo = -1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="pulo_minimo"):
        load_config(arq)


def test_exemplo_nao_fixa_as_chaves_novas():
    exemplo = Path(__file__).resolve().parents[1] / "config.exemplo.toml"
    c = load_config(exemplo).cortes
    assert (c.depois_de_cada_abate, c.antes_do_proximo, c.pulo_minimo) == (6.0, 6.0, 2.0)
    texto = exemplo.read_text(encoding="utf-8")                  # os números comentados no exemplo são os padrões do código
    for linha in ("# depois_de_cada_abate = 6.0", "# antes_do_proximo = 6.0", "# pulo_minimo = 2.0"):
        assert linha in texto
    assert "volta 6 s antes" in texto and "14 s ou mais" in texto


# ── a função pura: os 3 casos reais e as bordas ─────────────────────────────────────────────────

@pytest.mark.parametrize("caso", sorted(ESPERADO))
def test_funcao_pura_casos_reais_da_tabela(caso):
    ks, esperado = ESPERADO[caso]
    _igual(_pela_funcao(ks), esperado)


@pytest.mark.parametrize("vao,pula", [(2.0, False), (9.0, False), (9.9, False), (10.0, False), (13.0, False), (13.99, False),
                                      (14.0, True), (14.1, True), (30.0, True)])
def test_funcao_pura_o_pulo_so_existe_a_partir_de_14_s(vao, pula):
    """6 + 6 + 2: a 13,99 s sobram 1,99 s (contínuo); a 14,0 s sobram exatamente 2 s (pula)."""
    a, b = 100.0, 100.0 + vao
    t = trechos_da_jogada([a, b], a - 4.0, b + 2.5, 6.0, 6.0, 2.0)
    assert (len(t) == 2) is pula
    if pula:
        _igual(t, [(96.0, 106.0), (b - 6.0, b + 2.5)])
    else:
        _igual(t, [(96.0, b + 2.5)])


def test_funcao_pura_vao_de_14_s_cai_no_limite_sem_erro_de_ponto_flutuante():
    t = trechos_da_jogada([95.3, 109.3], 91.3, 111.8, 6.0, 6.0, 2.0)       # 109.3 - 6 - (95.3 + 6) = 2.0 (com ruído de float)
    assert len(t) == 2
    t = trechos_da_jogada([95.3, 109.2], 91.3, 111.7, 6.0, 6.0, 2.0)
    assert len(t) == 1
    # 128.2 - 6 - (114.2 + 6) dá 1.9999999999999858 em ponto flutuante, mas são 14 s de distância: tem de pular
    assert (128.2 - 6.0) - (114.2 + 6.0) < 2.0
    assert len(trechos_da_jogada([114.2, 128.2], 110.2, 130.7, 6.0, 6.0, 2.0)) == 2
    assert len(trechos_da_jogada([118.2, 132.2], 114.2, 134.7, 6.0, 6.0, 2.0)) == 2


def test_funcao_pura_um_abate_so_nao_muda_nada():
    assert trechos_da_jogada([50.0], 46.0, 52.5, 6.0, 6.0, 2.0) == [(46.0, 52.5)]
    assert trechos_da_jogada([50.0], 46.0, 59.0, 6.0, 6.0, 2.0) == [(46.0, 59.0)]       # o fim (reação) é o que vier


def test_funcao_pura_entrada_fora_de_ordem_e_sem_abates():
    _igual(trechos_da_jogada(list(reversed(ACE)), 91.3, 174.9, 6.0, 6.0, 2.0), ESPERADO["ace"][1])
    assert trechos_da_jogada([], 10.0, 20.0, 6.0, 6.0, 2.0) == [(10.0, 20.0)]


def test_funcao_pura_nenhum_trecho_passa_do_fim_nem_antes_do_comeco():
    t = trechos_da_jogada(QUADRA_2, 208.4, 237.4, 6.0, 6.0, 2.0)
    assert t[0][0] == 208.4 and t[-1][1] == 237.4                  # 233,2 + 6 = 239,2 passaria do fim: fica em 237,4
    assert trechos_da_jogada([10.0, 10.5], 6.0, 11.0, 6.0, 6.0, 2.0) == [(6.0, 11.0)]      # o 2º abate não puxa o começo para trás do início
    _igual(trechos_da_jogada([100.0, 101.0], 96.0, 102.0, 6.0, 6.0, 2.0), [(96.0, 102.0)])  # 100 + 6 passaria do fim (102): fica no fim


def test_funcao_pura_nunca_encolhe_o_que_ja_havia():
    # reação estendida: o trecho de hoje vai até 110.5 (acima dos 6 s depois do abate): vale o maior
    t = trechos_da_jogada([100.0, 150.0], 96.0, 152.5, 6.0, 6.0, 2.0, minimos=[(96.0, 110.5), (146.0, 152.5)])
    _igual(t, [(96.0, 110.5), (144.0, 152.5)])
    # ajuste às falas: o trecho de hoje começa antes dos 6 s
    t = trechos_da_jogada([100.0, 150.0], 96.0, 152.5, 6.0, 6.0, 2.0, minimos=[(96.0, 102.5), (141.0, 152.5)])
    _igual(t, [(96.0, 106.0), (141.0, 152.5)])


def test_funcao_pura_pulo_minimo_zero():
    _igual(trechos_da_jogada([100.0, 112.5], 96.0, 115.0, 6.0, 6.0, 0.0), [(96.0, 106.0), (106.5, 115.0)])   # 0,5 s de sobra: com mínimo 0 já pula
    _igual(trechos_da_jogada([100.0, 112.0], 96.0, 114.5, 6.0, 6.0, 0.0), [(96.0, 114.5)])                   # encostados: um só


def test_funcao_pura_valores_da_regra_mudam_o_resultado():
    _igual(trechos_da_jogada([100.0, 130.0], 96.0, 132.5, 6.0, 6.0, 2.0), [(96.0, 106.0), (124.0, 132.5)])
    _igual(trechos_da_jogada([100.0, 130.0], 96.0, 132.5, 9.0, 6.0, 2.0), [(96.0, 109.0), (124.0, 132.5)])
    _igual(trechos_da_jogada([100.0, 130.0], 96.0, 132.5, 6.0, 8.0, 2.0), [(96.0, 106.0), (122.0, 132.5)])
    _igual(trechos_da_jogada([100.0, 130.0], 96.0, 132.5, 6.0, 6.0, 30.0), [(96.0, 132.5)])
    # o 1º trecho sempre começa no início da jogada (cortes.antes), mesmo com `antes_do_proximo` menor que ele
    _igual(trechos_da_jogada([100.0, 130.0], 96.0, 132.5, 6.0, 2.0, 2.0), [(96.0, 106.0), (128.0, 132.5)])


# ── pela demo ───────────────────────────────────────────────────────────────────────────────────

def _partida(ts, mortes=(), fim_round=400.0):
    rs = [RoundDemo(1, 0.0, fim_round, "T")]
    return PartidaDemo("de_x", {1: "Eu"}, 1, "Eu", rs, [Abate(t, 1, "v", "ak47") for t in ts], list(mortes), [], (1, 0), {1: "T"})


@pytest.mark.parametrize("caso", sorted(ESPERADO))
def test_demo_casos_reais_da_tabela(caso):
    ks, esperado = ESPERADO[caso]
    [m] = momentos_da_demo(_partida(ks))
    _igual(m.janelas, esperado)
    assert (m.start, m.end) == (m.janelas[0][0], m.janelas[-1][1])


def test_demo_ace_de_29_5_s_passa_a_46_s():
    [m] = momentos_da_demo(_partida(ACE))
    assert m.duration == pytest.approx(46.0, abs=1e-6)       # 13,5 + 12 + 12 + 8,5 (antes do N2: 29,5 s; com 4/4: 34 s)


@pytest.mark.parametrize("vao,pula", [(9.0, False), (10.0, False), (13.99, False), (14.0, True)])
def test_demo_vao_entre_dois_abates(vao, pula):
    [m] = momentos_da_demo(_partida([100.0, 100.0 + vao]))
    assert (len(m.janelas) == 2) is pula


def test_demo_um_abate_nao_muda_nada():
    [m] = momentos_da_demo(_partida([100.0]))
    assert m.janelas == [(96.0, 102.5)] and m.segmentos == []


def test_demo_a_morte_continua_limitando_o_fim():
    [m] = momentos_da_demo(_partida([100.0, 140.0, 143.0], mortes=[144.0]))    # morreu 1 s depois do último abate
    assert m.janelas[-1][1] == 145.0 and all(b <= 145.0 for _, b in m.janelas)
    [m] = momentos_da_demo(_partida([100.0, 104.0], mortes=[104.5]))           # o 1º abate também não passa do fim
    assert m.janelas == [(96.0, 105.5)]


def test_demo_usa_os_numeros_que_recebe():
    [m] = momentos_da_demo(_partida([100.0, 130.0]), depois_de_cada_abate=9.0, antes_do_proximo=8.0, pulo_minimo=2.0)
    _igual(m.janelas, [(96.0, 109.0), (122.0, 132.5)])


def test_demo_todos_os_abates_do_round_vao_num_video_so_sem_limite_de_vao():
    """Mirage de 14/11, round 10 (abates a 53 s e 6 s um do outro) e um round com 250 s entre o 1º e o último abate: 1 vídeo só."""
    [m] = momentos_da_demo(_partida([3414.0, 3467.0, 3473.0], fim_round=3500.0))
    assert len(m.kills) == 3 and m.label == "TRIPLE KILL" and _kills_nas_janelas(m)
    [m] = momentos_da_demo(_partida([100.0, 200.0, 350.0], fim_round=400.0))
    assert len(m.kills) == 3 and _kills_nas_janelas(m)


def test_demo_analisar_demo_passa_as_chaves_novas(tmp_path, monkeypatch):
    from cs2clip import demo as d
    p = _partida([100.0, 130.0])
    monkeypatch.setattr(d, "ler_demo", lambda caminho, quem=None: p)
    cfg = Config()
    cfg.cortes.depois_de_cada_abate, cfg.cortes.antes_do_proximo = 9.0, 8.0
    _, ms, _ = d.analisar_demo(tmp_path / "x.dem", cfg, tmp_path / "saida", jogador="Eu")
    _igual(ms[0].janelas, [(96.0, 109.0), (122.0, 132.5)])


def test_editar_demo_passa_as_chaves_novas_e_nao_pular_parado(tmp_path, monkeypatch):
    class Parou(Exception):
        pass

    visto = {}

    def captura(partida, *args, **kw):
        visto["args"], visto["kw"] = args, kw
        raise Parou

    monkeypatch.setattr(ed, "ler_demo", lambda caminho, quem=None: _partida([100.0, 130.0]))
    monkeypatch.setattr(ed, "momentos_da_demo", captura)
    monkeypatch.setattr(ed.video_limpo, "rounds_gravados", lambda grav: [])
    monkeypatch.setattr(ed.video_limpo, "montar_partida_limpa", lambda rounds, destino: (destino, {}))
    cfg = Config()
    cfg.cortes.depois_de_cada_abate, cfg.cortes.antes_do_proximo, cfg.cortes.pulo_minimo = 9.0, 8.0, 3.0
    cfg.cortes.pular_parado = 99.0
    with pytest.raises(Parou):
        ed.editar(tmp_path / "x.dem", cfg, pasta_gravacao=tmp_path, saida_raiz=tmp_path / "saida", imprimir=lambda *a: None)
    assert visto["kw"] == {"depois_de_cada_abate": 9.0, "antes_do_proximo": 8.0, "pulo_minimo": 3.0}
    assert 99.0 not in visto["args"]


# ── pelo vídeo: _unir (unir_sequencias) ─────────────────────────────────────────────────────────

def _partes():
    # banners a cada 110 s: o round 1 vai de 0 a 80 (quadra 1), o 2 de 80 a 190 (ACE e os testes curtos), o 3 de 190 a 300 (quadra 2)
    return dividir_partes([R(80.0 + 110.0 * i, "vitoria", .9) for i in range(5)], [], 700.0)


def _jogada(ts, score=20.0, reacao=0.0):
    """Um corte como o `build_moments` faz: 4 s antes do 1º abate, 2,5 s (+ reação) depois do último."""
    kills = [Event(t, "kill", []) for t in ts]
    return Moment(max(0.0, ts[0] - 4.0), ts[-1] + 2.5 + reacao, "abate", kills=kills, label="KILL", score=score)


def _contem(janelas, esperado):
    """Cada trecho esperado cabe inteiro dentro de algum trecho obtido (podem ser maiores por causa da reação)."""
    for a, b in esperado:
        assert any(x <= a + 1e-6 and b <= y + 1e-6 for x, y in janelas), f"{(a, b)} fora de {janelas}"


def test_unir_ace_reproduz_a_tabela_sem_reacao():
    ms = [_jogada([95.3, 98.8]), _jogada([117.4]), _jogada([143.0]), _jogada([172.4])]
    [m] = unir_sequencias(ms, _partes(), Config().cortes)
    _igual(m.janelas, ESPERADO["ace"][1])
    assert m.duration == pytest.approx(46.0, abs=1e-6) and m.label == "ACE"


@pytest.mark.parametrize("caso", ["quadra1", "quadra2"])
def test_unir_quadras_reproduzem_a_tabela_sem_reacao(caso):
    ks, esperado = ESPERADO[caso]
    grupos, atual = [], [ks[0]]
    for k in ks[1:]:                       # como o detector faz: abates a até 8 s viram uma jogada
        if k - atual[-1] <= 8.0:
            atual.append(k)
        else:
            grupos.append(atual)
            atual = [k]
    grupos.append(atual)
    [m] = unir_sequencias([_jogada(g) for g in grupos], _partes(), Config().cortes)
    _igual(m.janelas, esperado)


def test_unir_com_reacao_os_trechos_da_tabela_ficam_contidos():
    ms = [_jogada([95.3, 98.8], reacao=2.0), _jogada([117.4], reacao=1.0), _jogada([143.0]), _jogada([172.4], reacao=3.5)]
    [m] = unir_sequencias(ms, _partes(), Config().cortes)
    _contem(m.janelas, ESPERADO["ace"][1])
    assert m.janelas[-1][1] == pytest.approx(172.4 + 2.5 + 3.5)       # o fim com a reação continua como hoje
    assert m.janelas[0][0] == pytest.approx(91.3)                      # o começo também


@pytest.mark.parametrize("vao,pula", [(9.0, False), (10.0, False), (13.0, False), (13.99, False), (14.0, True)])
def test_unir_dois_abates_a_13_99_s_ficam_continuos_e_a_14_tem_pulo(vao, pula):
    [m] = unir_sequencias([_jogada([100.0]), _jogada([100.0 + vao])], _partes(), Config().cortes)
    assert (len(m.janelas) == 2) is pula
    assert (m.segmentos != []) is pula


def test_unir_usa_os_numeros_da_config_e_nao_o_pular_parado():
    c = _regra(depois_de_cada_abate=9.0, antes_do_proximo=8.0, pular_parado=0.0)
    [m] = unir_sequencias([_jogada([100.0]), _jogada([130.0])], _partes(), c)
    _igual(m.janelas, [(96.0, 109.0), (122.0, 132.5)])
    [m2] = unir_sequencias([_jogada([100.0]), _jogada([130.0])], _partes(), _regra(pular_parado=100.0))   # pular_parado enorme não junta nada
    _igual(m2.janelas, [(96.0, 106.0), (124.0, 132.5)])


def test_unir_a_morte_no_meio_continua_separando():
    assert len(unir_sequencias([_jogada([100.0]), _jogada([115.0])], _partes(), Config().cortes, mortes=[108.0])) == 2


def test_unir_jogada_de_um_abate_so_passa_sem_mudar():
    a = _jogada([100.0])
    [m] = unir_sequencias([a], _partes(), Config().cortes)
    assert m is a and m.janelas == [(96.0, 102.5)]


# ── a conta do pior caso de `sequencia_max` ─────────────────────────────────────────────────────

def _pior_total(com_fala: bool) -> tuple[float, float]:
    """Varre o vão (igual) entre 5 abates de 0,5 a 40 s, de centésimo em centésimo, e devolve (maior duração, vão em que ocorre).
    Fim com a reação máxima (+4 s do `estender_reacao`) e o ajuste às falas no fim (+3 s) e no começo (-1,5 s); `com_fala`: ainda
    uma palavra cortada no fim de cada trecho do MEIO (+1,5 s, `sequencia._fechar_falas`), o que faz o contínuo valer até 15,49 s."""
    c = Config().cortes
    pior, onde = 0.0, 0.0
    for centi in range(50, 4001):
        vao = centi / 100
        ks = [100.0 + vao * i for i in range(5)]
        t = trechos_da_jogada(ks, ks[0] - c.antes - 1.5, ks[-1] + c.depois + 4.0 + 3.0, **regra_do_corte(c))
        if com_fala:
            palavras = [SimpleNamespace(start=b - 0.1, end=b + 1.5) for _, b in t[:-1]]
            t = _fechar_falas(t, palavras, 0.0, c.pulo_minimo)
        total = sum(b - a for a, b in t)
        if total > pior:
            pior, onde = total, vao
    return pior, onde


def test_sequencia_max_padrao_nem_chega_a_ser_usado_por_jogada_de_ate_5_abates():
    """Pior caso com 6/6/2: sem fala, 4 vãos de 13,99 s contínuos = 5,5 + 4 x 13,99 + 9,5 = 70,96 s; com uma palavra cortada em cada trecho
    do meio, 4 vãos de 15,49 s = 76,96 s. Os dois ficam abaixo de `sequencia_max` (90 s): o padrão não precisa subir (e a regra de
    ouro vale de qualquer jeito: passando do máximo, só a folga encolhe)."""
    c = Config().cortes
    sem, onde_sem = _pior_total(False)
    com, onde_com = _pior_total(True)
    assert (round(sem, 2), onde_sem) == (70.96, 13.99)
    assert (round(com, 2), onde_com) == (76.96, 15.49)
    assert sem < com < c.sequencia_max == 90.0


# ── REGRA DE OURO: nenhum limite tira abate ─────────────────────────────────────────────────────

def _partes_exatas(banners):
    """Partida cujos rounds vêm da demo (exata): `Parte.exata`."""
    rounds = [R(float(b), "vitoria", 1.0) for b in banners]
    return dividir_partes(rounds, [], banners[-1] + 100.0, exatos=[(banners[0] - 100.0, banners[-1] + 50.0)])


def _partes_estimadas(banners):
    return dividir_partes([R(float(b), "vitoria", .9) for b in banners], [], banners[-1] + 100.0)


BANNERS_MIRAGE = [3000, 3110, 3220, 3330, 3490, 3600, 3710, 3820]   # o round vai de 3330 a 3490: 160 s (intervalo "suspeito")
BANNERS_125 = [3000, 3110, 3220, 3330, 3470, 3580, 3690, 3800]      # o round vai de 3330 a 3470: 140 s (não suspeito)


def test_round_de_5_abates_espalhados_em_140_s_com_limite_baixo_fica_com_os_5_abates():
    banners = [60 + 170 * i for i in range(6)]
    ks = [70.0 + 35 * i for i in range(5)]      # 70 a 210, no round 60-230
    [sem] = unir_sequencias([_jogada([k]) for k in ks], _partes_exatas(banners), _regra())
    assert len(sem.kills) == 5 and sem.duration == pytest.approx(10.0 + 3 * 12.0 + 8.5)   # sem o limite apertado: 54,5 s
    [m] = unir_sequencias([_jogada([k]) for k in ks], _partes_exatas(banners), _regra(sequencia_max=25.0))
    assert [k.t for k in m.kills] == ks and m.label == "ACE" and _kills_nas_janelas(m)
    assert m.duration <= 25.0 + 1e-6 and m.duration > 24.0                              # coube encolhendo só a folga, no máximo possível
    assert len(m.janelas) == 5 and all(a <= k <= b for k, (a, b) in zip(ks, m.janelas))


def test_limite_vale_tambem_sem_demo_e_nunca_tira_abate():
    ms = [_jogada([110.0 + 20 * i]) for i in range(4)]
    [m] = unir_sequencias(ms, _partes(), _regra(sequencia_max=15.0))
    assert [k.t for k in m.kills] == [110.0, 130.0, 150.0, 170.0] and m.label == "QUADRA KILL"
    assert _kills_nas_janelas(m) and 14.0 < m.duration <= 15.0 + 1e-6
    assert m.janelas[-1][1] <= 172.5 and m.janelas[0][0] >= 106.0                       # nunca passa do original


def test_limite_impossivel_deixa_todos_os_abates_mesmo_passando_do_maximo():
    ms = [_jogada([110.0 + 20 * i]) for i in range(4)]
    [m] = unir_sequencias(ms, _partes(), _regra(sequencia_max=1.0))
    assert len(m.kills) == 4 and _kills_nas_janelas(m) and m.duration >= 4 * 2 * 0.5 - 1e-6


def test_remover_repetidos_com_limite_baixo_tambem_nao_tira_abate():
    [m] = remover_repetidos([_jogada([100.0, 101.0]), _repetida()], cortes=Config().cortes, maximo=8.0)
    assert [k.t for k in m.kills] == [100.0, 100.5, 101.0, 140.0] and _kills_nas_janelas(m) and m.duration <= 8.0 + 1e-6


def test_short_compacto_tambem_mantem_todos_os_abates():
    ms = [_jogada([100.0 + 10 * i]) for i in range(10)]
    [m] = unir_sequencias(ms, _partes(), Config().cortes)            # 10 abates a 10 s (round 80-190)
    assert len(m.kills) == 10 and m.duration > 60.0
    curto = compactar(m, 20.0, 2.0, 1.5, 6.0)                         # a compacta normal = 10 x 3,5 s = 35 s: ainda passa de 20 s
    assert curto is not m and len(curto.kills) == 10 and _kills_nas_janelas(curto)
    assert 19.0 < curto.duration <= 20.0 + 1e-6
    normal = compactar(m, 40.0, 2.0, 1.5, 6.0)                        # cabe: a folga fica como está
    assert normal.duration == pytest.approx(35.0) and _kills_nas_janelas(normal)
    assert len(m.kills) == 10 and m.duration > 60.0                    # o original não muda


# ── agrupamento por round CERTO (demo): sem limite de vão e sem a trava do intervalo suspeito ─────

def test_parte_exata_junta_os_3_abates_da_mirage_mesmo_com_intervalo_suspeito():
    """Mirage 14/11, round 10: abates em 3414, 3467 e 3473 do VOD ficavam em 2 vídeos por causa do "intervalo suspeito"."""
    partes = _partes_exatas(BANNERS_MIRAGE)
    assert next(x for x in partes if x.tipo == "partida").exata
    [m] = unir_sequencias([_jogada([3414.0]), _jogada([3467.0, 3473.0])], partes, Config().cortes)
    assert [k.t for k in m.kills] == [3414.0, 3467.0, 3473.0] and m.label == "TRIPLE KILL"
    assert len(m.janelas) == 2 and m.janelas[0][1] == pytest.approx(3420.0)       # 3414 + 6


def test_controle_sem_demo_o_intervalo_suspeito_continua_separando():
    partes = _partes_estimadas(BANNERS_MIRAGE)
    assert not next(x for x in partes if x.tipo == "partida").exata
    separados = unir_sequencias([_jogada([3414.0]), _jogada([3467.0, 3473.0])], partes, Config().cortes)
    assert [len(m.kills) for m in separados] == [1, 2]


def test_parte_exata_ignora_o_limite_de_120_s_de_juntar_no_round_e_a_estimada_nao():
    [m] = unir_sequencias([_jogada([3340.0]), _jogada([3465.0])], _partes_exatas(BANNERS_125), Config().cortes)   # 125 s de vão, no mesmo round
    assert len(m.kills) == 2
    separados = unir_sequencias([_jogada([3340.0]), _jogada([3465.0])], _partes_estimadas(BANNERS_125), Config().cortes)
    assert [len(x.kills) for x in separados] == [1, 1]                 # controle: sem demo, a proteção de 120 s fica


def test_parte_exata_a_morte_no_meio_continua_separando():
    ms = [_jogada([3414.0]), _jogada([3467.0, 3473.0])]
    assert len(unir_sequencias(ms, _partes_exatas(BANNERS_MIRAGE), Config().cortes, mortes=[3440.0])) == 2


def test_parte_exata_nao_junta_rounds_diferentes():
    ms = [_jogada([3320.0]), _jogada([3420.0])]                        # o banner do round 3330 fica entre os dois
    assert len(unir_sequencias(ms, _partes_exatas(BANNERS_MIRAGE), Config().cortes)) == 2


def test_juntar_no_round_zero_continua_desligando_tudo_mesmo_com_demo():
    ms = [_jogada([3414.0]), _jogada([3467.0])]
    assert len(unir_sequencias(ms, _partes_exatas(BANNERS_MIRAGE), _regra(juntar_no_round=0))) == 2


# ── remover_repetidos (a fusão de dois cortes que repetem o mesmo trecho) ────────────────────────

def _repetida():
    """Um corte que repete o começo de `_jogada([100, 101])` e traz um abate 40 s depois (dois trechos)."""
    b = Moment(96.5, 142.5, "abate", kills=[Event(100.5, "kill", []), Event(140.0, "kill", [])], label="KILL", score=15.0)
    b.segmentos = [(96.5, 103.0), (136.0, 142.5)]
    return b


def test_remover_repetidos_funde_com_a_regra_nova():
    [m] = remover_repetidos([_jogada([100.0, 101.0]), _repetida()], cortes=Config().cortes)
    assert [k.t for k in m.kills] == [100.0, 100.5, 101.0, 140.0]
    _igual(m.janelas, [(96.0, 107.0), (134.0, 142.5)])      # 101 + 6; 140 - 6


def test_remover_repetidos_usa_os_numeros_da_config():
    c = _regra(depois_de_cada_abate=9.0, antes_do_proximo=8.0, pular_parado=0.0)
    [m] = remover_repetidos([_jogada([100.0, 101.0]), _repetida()], cortes=c)
    _igual(m.janelas, [(96.0, 110.0), (132.0, 142.5)])


def test_remover_repetidos_sem_cortes_usa_os_padroes():
    [m] = remover_repetidos([_jogada([100.0, 101.0]), _repetida()])
    _igual(m.janelas, [(96.0, 107.0), (134.0, 142.5)])


def test_pipeline_passa_cortes_inteiro_para_remover_repetidos():
    fonte = (Path(__file__).resolve().parents[1] / "cs2clip" / "pipeline.py").read_text(encoding="utf-8")
    assert "remover_repetidos(moments, cortes=cfg.cortes, maximo=cfg.cortes.sequencia_max)" in fonte
    assert "pular=cfg.cortes.pular_parado, maximo" not in fonte


# ── short e montagem usam as mesmas janelas ──────────────────────────────────────────────────────

def test_short_so_vira_compacto_acima_do_maximo_e_a_compacta_nao_muda():
    ms = [_jogada([95.3, 98.8]), _jogada([117.4]), _jogada([143.0]), _jogada([172.4])]
    [m] = unir_sequencias(ms, _partes(), Config().cortes)
    assert m.duration == pytest.approx(46.0) and compactar(m, 60.0, 2.0, 1.5, 6.0) is m          # 46 s < 60 s: o short usa as mesmas janelas
    curto = compactar(m, 30.0, 2.0, 1.5, 6.0)                                                      # D4: continua com shorts.antes/depois
    assert curto is not m and curto.duration <= 30.0 and len(curto.kills) == 5 and _kills_nas_janelas(curto)
    assert m.duration == pytest.approx(46.0)


def test_montagem_usa_as_janelas_do_momento_sem_regra_propria():
    """A montagem (`cs2clip/montagem.py`) só junta os cortes já renderizados de `m.janelas`: não monta janelas de jogada."""
    fonte = (Path(__file__).resolve().parents[1] / "cs2clip" / "montagem.py").read_text(encoding="utf-8")
    assert "pular_parado" not in fonte and "janelas" not in fonte and "segmentos" not in fonte


def test_pipeline_so_usa_pular_parado_no_short_compacto():
    fonte = (Path(__file__).resolve().parents[1] / "cs2clip" / "pipeline.py").read_text(encoding="utf-8")
    assert fonte.count("pular_parado") == 1 and "cfg.cortes.pular_parado)" in fonte


# ── --refazer cortes: a impressão do corte muda com as chaves novas ─────────────────────────────

def test_impressao_do_corte_muda_quando_uma_chave_da_regra_muda():
    def impressoes(**kw):
        [m] = unir_sequencias([_jogada([100.0]), _jogada([130.0])], _partes(), _regra(**kw))
        return impressao(m)

    base = impressoes()
    assert impressoes() == base                                        # controle: mesma regra, mesma impressão
    assert impressoes(depois_de_cada_abate=7.0) != base
    assert impressoes(antes_do_proximo=5.0) != base
    assert impressoes(pulo_minimo=40.0) != base


# ── a tela Configurar ───────────────────────────────────────────────────────────────────────────

def test_configurar_mostra_as_tres_chaves_novas_e_esconde_pular_parado():
    from ui.configuracao import CAMPOS
    chaves = {(c.secao, c.chave): c for c in CAMPOS}
    assert ("cortes", "pular_parado") not in chaves
    for nome in ("depois_de_cada_abate", "antes_do_proximo", "pulo_minimo"):
        c = chaves[("cortes", nome)]
        assert c.tipo == "numero" and (c.minimo, c.maximo) == (0, 30) and c.rotulo and c.dica
        assert f"padrão {int(getattr(Config().cortes, nome))})" in c.dica          # o "padrão N" do texto de ajuda é o do código
    assert {f.name for f in dataclasses.fields(Cortes)} >= {"depois_de_cada_abate", "antes_do_proximo", "pulo_minimo", "pular_parado"}
