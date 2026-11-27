"""A2/A3 (dono, 08/11/2026): montagem e short só com round de 3+ abates do dono (ou resenha). O pedido falado também não fura a regra (E2).

Cada teste tem CONTROLE: com `cortes.min_abates_round = 0` (o critério antigo, só por nota) o defeito reaparece.
"""
import json

import pytest

from cs2clip import pipeline as pl
from cs2clip.config import Config, load_config
from cs2clip.moments import Event, Moment, abates_no_round, passa_regra_do_dono

from test_r1_pipeline import _cfg_render, _info, _render_falso


def _round(idx, tempos, rnd, nota=40.0, parte="Partida 1", **kw):
    """Uma jogada de `len(tempos)` abates no round `rnd` (a primeira kill em tempos[0])."""
    kw.setdefault("label", "KILL")
    return Moment(tempos[0] - 4, tempos[-1] + 3, "abate", kills=[Event(t, "kill") for t in tempos], score=nota,
                  idx=idx, parte=parte, parte_tipo="partida", round_n=rnd, **kw)


def test_padrao_da_regra_e_3_e_zero_desliga(tmp_path):
    assert Config().cortes.min_abates_round == 3
    toml = tmp_path / "c.toml"
    toml.write_text("[cortes]\nmin_abates_round = -1\n", encoding="utf-8")
    try:
        load_config(toml)
        assert False, "deveria recusar valor negativo"
    except ValueError as e:
        assert "min_abates_round" in str(e)


def test_contagem_e_do_proprio_corte_e_nao_soma_pedacos_separados_pelo_programa():
    """Prova de 08/11 (gravação 04/11 23-11-02): o round 13 teve 2 abates + 1 abate 38 s depois, separados de propósito porque o dono
    morreu no meio. Somar pelo número do round dava 3 e deixava o DOUBLE entrar na montagem; contar o corte dá 2."""
    a = _round(1, [100.0, 104.0], rnd=5)
    b = _round(2, [130.0], rnd=5)                             # mesmo número de round, corte separado
    c = _round(3, [300.0, 305.0, 308.0], rnd=6)
    d = _round(4, [100.0, 104.0, 100.04], rnd=5, parte="Partida 2")      # 100.0 e 100.04 são o mesmo abate lido duas vezes
    reacao = Moment(0, 10, "reacao", score=40, idx=9)
    n = abates_no_round([a, b, c, d, reacao])
    assert n[id(a)] == 2 and n[id(b)] == 1 and n[id(c)] == 3 and n[id(d)] == 2 and n[id(reacao)] == 0
    assert not passa_regra_do_dono(a, n[id(a)], 3) and passa_regra_do_dono(c, n[id(c)], 3)


def test_regra_aceita_3_ou_mais_pedido_e_resenha_e_recusa_o_resto():
    dois = _round(1, [10.0, 12.0], rnd=1)
    tres = _round(2, [10.0, 12.0, 14.0], rnd=2)
    reacao = Moment(0, 10, "reacao", score=40, idx=3, emocao="HYPE")
    resenha = Moment(0, 10, "reacao", score=25, idx=4, categoria="resenha")
    pedido_sem_abate = Moment(0, 10, "reacao", score=64, idx=6, pedido=True, emocao="HYPE")
    pedido_2 = _round(5, [10.0, 12.0], rnd=3, pedido=True)
    pedido_3 = _round(7, [10.0, 12.0, 14.0], rnd=4, pedido=True)
    assert not passa_regra_do_dono(dois, 2, 3)
    assert passa_regra_do_dono(tres, 3, 3)
    assert not passa_regra_do_dono(reacao, 0, 3)             # reação comum (mesmo com emoção) não entra
    assert passa_regra_do_dono(resenha, 0, 3)                # resenha é a exceção do dono
    # E2: o "clipa isso" falado NÃO fura a regra: sem abates ou com 2 fica de fora; com 3 entra pela própria regra
    assert not passa_regra_do_dono(pedido_sem_abate, 0, 3) and not passa_regra_do_dono(pedido_2, 2, 3)
    assert passa_regra_do_dono(pedido_3, 3, 3)
    assert passa_regra_do_dono(pedido_sem_abate, 0, 0)       # controle: com a regra desligada o pedido entra (era assim antes)
    assert passa_regra_do_dono(dois, 2, 0)                   # controle: regra desligada = critério antigo


def _roda(monkeypatch, tmp_path, jogadas, minimo, nome="live"):
    _render_falso(monkeypatch)
    cfg = _cfg_render()
    cfg.cortes.min_abates_round = minimo
    cfg.cortes.nota_short = 30.0
    cfg.shorts.versao_efeitos = False   # a versão com efeitos chama o ffmpeg de verdade; estes testes usam render falso
    out = tmp_path / "saida" / nome
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, jogadas, [], None)
    return out


@pytest.mark.lento
def test_partida_so_com_rounds_de_2_abates_nao_gera_montagem_nem_short_e_diz_por_que(monkeypatch, tmp_path, capsys):
    jogadas = [_round(1, [100.0, 104.0], rnd=2), _round(2, [300.0, 305.0], rnd=6)]
    out = _roda(monkeypatch, tmp_path, jogadas, 3)
    texto = capsys.readouterr().out
    assert "Nenhuma jogada com 3 ou mais abates seus" in texto and "a maior teve 2" in texto and "Nada para postar" in texto
    assert (out / "NADA_PARA_POSTAR.txt").read_text(encoding="utf-8").startswith("Nenhuma jogada com 3 ou mais abates")
    assert not list(out.glob("MONTAGEM_*.mp4")) and not list((out / "shorts").glob("*.mp4"))
    assert len(list((out / "cortes").glob("*.mp4"))) == 2     # o corte 16:9 de cada jogada continua existindo
    # CONTROLE: com a regra desligada o programa monta e faz short dessas mesmas jogadas (o defeito que o dono viu)
    antigo = _roda(monkeypatch, tmp_path, jogadas, 0, nome="antigo")
    assert list(antigo.glob("MONTAGEM_*.mp4")) and list((antigo / "shorts").glob("*.mp4"))
    assert not (antigo / "NADA_PARA_POSTAR.txt").exists()


@pytest.mark.lento
def test_round_de_3_abates_entra_e_o_de_2_fica_de_fora(monkeypatch, tmp_path):
    bom = _round(1, [100.0, 103.0, 106.0], rnd=2)
    fraco = _round(2, [300.0, 305.0], rnd=6)
    out = _roda(monkeypatch, tmp_path, [bom, fraco], 3)
    assert not (out / "NADA_PARA_POSTAR.txt").exists()
    assert list(out.glob("MONTAGEM_*.mp4"))
    assert len(list((out / "shorts").glob("*_vertical.mp4"))) == 1            # só o round de 3 abates virou short
    dados = json.loads((out / "momentos.json").read_text(encoding="utf-8"))["momentos"]
    com_short = [m for m in dados if m["arquivos"].get("short")]
    assert len(com_short) == 1 and len(com_short[0]["abates"]) == 3
    assert len(list((out / "cortes").glob("*.mp4"))) == 2                      # os dois cortes 16:9 existem


@pytest.mark.lento
def test_aviso_antigo_de_nenhum_short_continua_com_a_regra_desligada(monkeypatch, tmp_path, capsys):
    jogadas = [_round(1, [100.0, 104.0], rnd=2, nota=20.0)]
    _roda(monkeypatch, tmp_path, jogadas, 0)
    texto = capsys.readouterr().out
    assert "nenhum short" in texto and "nota_short" in texto and "regra dos" not in texto


def test_esports_blast_monta_em_ordem_cronologica():
    from cs2clip.perfis import aplicar_perfil
    assert aplicar_perfil(Config(), "esports_blast").montagem.ordem == "cronologica"


@pytest.mark.lento
def test_e2_pedido_falado_sem_abate_nao_vira_short_nem_montagem_mas_o_corte_continua(monkeypatch, tmp_path, capsys):
    """Kit de 04/11: o trecho 'HYPE' (nota 64, +30 do 'clipa isso', 0 abates) virou short e entrou na montagem. O dono: 'não tem kill, não serve'."""
    hype = Moment(1770.0, 1790.0, "reacao", label="HYPE", score=63.6, idx=1, parte_tipo="partida", emocao="HYPE", pedido=True, round_n=20)
    triplo = _round(2, [1227.0, 1280.0, 1288.0], rnd=14, nota=61.0, label="TRIPLE KILL")
    out = _roda(monkeypatch, tmp_path, [hype, triplo], 3, nome="e2")
    shorts = sorted(p.name for p in (out / "shorts").glob("*_vertical.mp4"))
    assert len(shorts) == 1                                                                             # só o TRIPLE virou short
    dados = json.loads((out / "momentos.json").read_text(encoding="utf-8"))["momentos"]
    com_short = [m for m in dados if m["arquivos"].get("short")]
    assert [m["tipo"] for m in com_short] == ["abate"]
    assert len(list((out / "cortes").glob("*.mp4"))) == 2                                               # o corte 16:9 do pedido continua
    edl = (out / "marcadores_live.edl").read_text(encoding="utf-8")
    assert "HYPE" not in edl and "TRIPLE KILL" in edl                                                    # fora da montagem
    # CONTROLE: com a regra desligada o pedido sem abate entrava (o defeito que o dono viu)
    antigo = _roda(monkeypatch, tmp_path, [hype, triplo], 0, nome="e2_antigo")
    assert "HYPE" in (antigo / "marcadores_live.edl").read_text(encoding="utf-8")
    # e uma partida só com o pedido sem abate: nada para postar, dito em palavras
    _roda(monkeypatch, tmp_path, [hype], 3, nome="e2_so_pedido")
    assert "Nada para postar" in capsys.readouterr().out


def test_e1_mudar_o_minimo_de_abates_invalida_o_short_mas_nao_o_corte():
    """Sem isto, o short antigo (sem o selo TRIPLE) seria reaproveitado depois da mudança do selo."""
    from cs2clip.refazer import hash_corte, hash_short
    a, b = Config(), Config()
    b.cortes.min_abates_round = 0                      # selo desde o 2º abate
    assert hash_short(a) != hash_short(b)
    assert hash_corte(a) == hash_corte(b)              # o corte 16:9 não tem selo: não é refeito
    c = Config()
    c.cortes.min_abates_round = 4
    assert hash_short(a) != hash_short(c)
    d = Config()
    d.cortes.min_abates_round = 2                      # 0, 1 e 2 dão o mesmo selo (a partir do 2º): mesma impressão
    e = Config()
    e.cortes.min_abates_round = 1
    assert hash_short(d) == hash_short(e)


def test_r3_trocar_o_titulo_de_abertura_invalida_o_short_mas_nao_o_corte():
    """RF-03: trocar de perfil (esports_blast desliga o título de abertura) reaproveitava o short antigo."""
    from cs2clip.perfis import aplicar_perfil
    from cs2clip.refazer import hash_corte, hash_short
    a, b = Config(), Config()
    b.shorts.gancho_titulo = False
    assert hash_short(a) != hash_short(b) and hash_corte(a) == hash_corte(b)
    assert hash_short(aplicar_perfil(Config(), "esports_blast")) != hash_short(aplicar_perfil(Config(), "shorts_viral"))
