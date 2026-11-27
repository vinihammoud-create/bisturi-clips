"""23/11/2026 (retorno de uso real): os 2 Shorts que mais renderam vieram de "lance que começa bem e termina mal, com a reação dele" e da resenha com os
amigos. Opção `cortes.falha_com_reacao` (DESLIGADA por padrão, CONSTITUICAO §3.2): a jogada em que você morre logo depois do último abate e REAGE (palavra de
raiva ou risada) (1) leva o corte até o fim da reação, (2) ganha nota, (3) passa a regra dos 3 abates (basta 1) e vira SHORT (não entra na montagem com música:
ela cobriria a reação, que é a graça) e (4) os FAILS com raiva ou risada também podem virar short. Cada teste tem CONTROLE: sem a opção, nada muda."""
import pytest

from cs2clip import pipeline as pl
from cs2clip.config import Config
from cs2clip.moments import Event, Moment, passa_regra_do_dono
from cs2clip.nota import pontuar
from cs2clip.sequencia import marcar_morte_no_fim
from cs2clip.transcribe import Word

from test_r1_pipeline import _cfg_render, _info, _render_falso


def _jogada(kills=(100.0, 104.0), fim=107.0, morreu=True):
    m = Moment(95.0, fim, "abate", kills=[Event(t, "kill") for t in kills], label="DOUBLE KILL", idx=1, parte_tipo="partida")
    if morreu:
        marcar_morte_no_fim([m], [kills[-1] + 2.0], duracao=600.0)
    return m


# ── a reação depois da morte ─────────────────────────────────────────────────────────────────────

def test_raiva_depois_da_morte_marca_a_reacao_e_estende_o_corte_ate_o_fim_da_fala():
    from cs2clip.fails import marcar_reacao_na_morte
    m = _jogada()                                                    # mata 100 e 104; morre 106; o corte já vai até 107,5
    falas = [Word(106.8, 107.2, "porra"), Word(107.3, 108.0, "não"), Word(108.0, 108.6, "acredito")]
    assert marcar_reacao_na_morte([m], falas, 600.0) == 1
    assert m.reacao_na_morte == "RAIVA" and m.morreu_no_fim
    assert 109.0 <= m.end <= 110.5                                   # até ~1,5 s depois do último termo de raiva ("acredito" termina 108,6)


def test_risada_depois_da_morte_tambem_conta():
    from cs2clip.fails import marcar_reacao_na_morte
    m = _jogada()
    assert marcar_reacao_na_morte([m], [Word(107.0, 107.8, "kkkkkk")], 600.0) == 1 and m.reacao_na_morte == "RISADA"


@pytest.mark.parametrize("falas", [
    [],                                                              # silêncio
    [Word(106.5, 107.0, "meio"), Word(107.0, 107.4, "ali")],         # callout (grito depois de morrer quase sempre é isso: não é reação)
    [Word(99.0, 99.4, "porra")],                                     # raiva ANTES da morte (durante a jogada)
    [Word(116.5, 117.0, "porra")],                                   # raiva 10 s depois: outro assunto
])
def test_sem_reacao_de_verdade_nada_muda(falas):
    from cs2clip.fails import marcar_reacao_na_morte
    m = _jogada()
    fim = m.end
    assert marcar_reacao_na_morte([m], falas, 600.0) == 0 and m.reacao_na_morte == "" and m.end == fim


def test_so_jogada_que_morreu_no_fim_e_considerada():
    from cs2clip.fails import marcar_reacao_na_morte
    m = _jogada(morreu=False)
    assert marcar_reacao_na_morte([m], [Word(106.8, 107.2, "porra")], 600.0) == 0 and m.reacao_na_morte == ""


def test_o_corte_nunca_passa_do_fim_do_video():
    from cs2clip.fails import marcar_reacao_na_morte
    m = _jogada()
    marcar_reacao_na_morte([m], [Word(106.8, 107.2, "porra"), Word(108.0, 108.6, "acredito")], 108.0)
    assert m.end <= 108.0


# ── nota e regra dos 3 abates ────────────────────────────────────────────────────────────────────

def test_nota_ganha_peso_so_com_a_opcao_ligada():
    m1, m2 = _jogada(), _jogada()
    m1.reacao_na_morte = m2.reacao_na_morte = "RAIVA"
    pontuar(m1, [], falha_com_reacao=False)
    pontuar(m2, [], falha_com_reacao=True)
    assert m2.score - m1.score == 20.0 and any("começou bem e terminou mal" in r for r in m2.reasons)
    assert not any("começou bem" in r for r in m1.reasons)
    sem = _jogada()                                                  # morreu no fim mas SEM reação: nada de bônus
    pontuar(sem, [], falha_com_reacao=True)
    assert not any("começou bem" in r for r in sem.reasons)


def test_regra_dos_3_abates_abre_so_para_falha_com_reacao_e_so_com_a_opcao():
    m = _jogada()
    m.reacao_na_morte = "RAIVA"
    assert passa_regra_do_dono(m, 2, 3) is False                                       # como sempre
    assert passa_regra_do_dono(m, 2, 3, falha_com_reacao=False) is False
    assert passa_regra_do_dono(m, 2, 3, falha_com_reacao=True) is True
    m.reacao_na_morte = ""
    assert passa_regra_do_dono(m, 2, 3, falha_com_reacao=True) is False                # sem reação: continua barrado
    outra = _jogada(morreu=False)
    outra.reacao_na_morte = "RAIVA"
    assert passa_regra_do_dono(outra, 2, 3, falha_com_reacao=True) is False            # sem a morte no fim: continua barrado
    assert passa_regra_do_dono(_jogada(kills=(100.0,)), 1, 3, falha_com_reacao=True) is False


def test_config_nasce_desligada_e_aparece_em_configurar():
    from ui.configuracao import CAMPOS_POR_CHAVE
    assert Config().cortes.falha_com_reacao is False
    assert ("cortes", "falha_com_reacao") in CAMPOS_POR_CHAVE


# ── ligação com o render (shorts e montagem) ─────────────────────────────────────────────────────

def _roda(monkeypatch, tmp_path, jogadas, nome, ligada):
    _render_falso(monkeypatch)
    cfg = _cfg_render()
    cfg.cortes.min_abates_round = 3
    cfg.cortes.nota_short = 30.0
    cfg.cortes.falha_com_reacao = ligada
    out = tmp_path / "saida" / nome
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, jogadas, [], None)
    return out


def _com_reacao():
    m = _jogada()
    m.reacao_na_morte = "RAIVA"
    m.emocao, m.confianca = "RAIVA", "alta"
    pontuar(m, [], falha_com_reacao=True)
    return m


@pytest.mark.lento
def test_falha_com_reacao_vira_short_mas_nao_entra_na_montagem_com_musica(monkeypatch, tmp_path):
    ligada = _roda(monkeypatch, tmp_path, [_com_reacao()], "a", True)
    assert len(list((ligada / "shorts").glob("*_vertical.mp4"))) == 1                  # o short existe
    assert not list(ligada.glob("MONTAGEM_*.mp4"))                                     # a música cobriria a reação
    # CONTROLE: a mesma jogada com a opção DESLIGADA (a regra dos 3 abates barra, como sempre)
    m = _com_reacao()
    desligada = _roda(monkeypatch, tmp_path, [m], "b", False)
    assert not list((desligada / "shorts").glob("*_vertical.mp4")) and not list(desligada.glob("MONTAGEM_*.mp4"))


@pytest.mark.lento
def test_jogada_normal_de_3_abates_continua_na_montagem_com_a_opcao_ligada(monkeypatch, tmp_path):
    m = _jogada(kills=(100.0, 103.0, 106.0), fim=109.0, morreu=False)
    m.emocao, m.confianca = "HYPE", "alta"
    pontuar(m, [], falha_com_reacao=True)
    out = _roda(monkeypatch, tmp_path, [m], "c", True)
    assert list(out.glob("MONTAGEM_*.mp4")) and list((out / "shorts").glob("*_vertical.mp4"))


@pytest.mark.lento
def test_fail_com_raiva_ou_risada_vira_short_so_com_a_opcao(monkeypatch, tmp_path):
    def fail(categoria):
        f = Moment(200.0, 212.0, "fail", pico=205.0, label="FAIL", idx=1, parte_tipo="partida", categoria=categoria, score=42.0)
        f.emocao, f.confianca = "RAIVA", "alta"
        return f
    ligada = _roda(monkeypatch, tmp_path, [fail("morte_com_raiva")], "d", True)
    assert len(list((ligada / "shorts").glob("*_vertical.mp4"))) == 1
    solta = _roda(monkeypatch, tmp_path, [fail("risada_solta")], "e", True)            # risada sem morte sua precisa do OK do dono: nunca automático
    assert not list((solta / "shorts").glob("*_vertical.mp4"))
    desligada = _roda(monkeypatch, tmp_path, [fail("morte_com_raiva")], "f", False)    # CONTROLE: como sempre, FAIL só na compilação
    assert not list((desligada / "shorts").glob("*_vertical.mp4"))


def test_short_compacto_de_jogada_longa_mantem_a_morte_e_a_reacao():
    # revisão de 23/11 (alto): abates 100 e 145, morte 146,5, risada até 150,5: o corte tinha 62 s e o short compacto parava na morte
    from cs2clip.sequencia import compactar
    m = Moment(90.0, 152.0, "abate", kills=[Event(100.0, "kill"), Event(145.0, "kill")], label="DOUBLE KILL", idx=1)
    m.morreu_no_fim, m.morte_t, m.reacao_na_morte = True, 146.5, "RISADA"
    curto = compactar(m, 60.0, 2.0, 1.5, 6.0)
    assert curto.duration <= 60.0 and curto.janelas[-1][1] == 152.0                    # vai até o fim da reação
    assert any(a <= 100.0 <= b for a, b in curto.janelas) and any(a <= 145.0 <= b for a, b in curto.janelas)
    sem = Moment(90.0, 152.0, "abate", kills=[Event(100.0, "kill"), Event(145.0, "kill")], label="DOUBLE KILL", idx=1)
    assert compactar(sem, 60.0, 2.0, 1.5, 6.0).janelas[-1][1] == 146.5                  # CONTROLE: sem a morte no fim, o compacto de sempre


def test_titulo_sugerido_conta_a_historia_do_lance_e_sem_a_reacao_fica_o_de_sempre():
    # revisão final de 23/11 (kit real do VOD 1234573000): o short "começou bem e terminou mal" saía com o título "Que tiro"
    from cs2clip.publicar import titulos
    m = _com_reacao()
    ts = titulos(m)
    assert ts[0] == "Começou bem e terminou..." and len(ts) == 3 and all(len(t) <= 60 for t in ts)
    assert not any(c in "".join(ts) for c in "🔥😂😡")                                  # sem emoji (regra da marca)
    normal = _jogada(morreu=False)
    assert titulos(normal)[0].startswith("DOUBLE KILL")                                # CONTROLE: sem a reação, o título de sempre
    so_morreu = _jogada()                                                              # morreu no fim mas sem reação: também o de sempre
    assert titulos(so_morreu)[0].startswith("DOUBLE KILL")
