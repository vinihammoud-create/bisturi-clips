"""Testes de cs2clip/refazer.py: quais etapas ignoram o cache."""
import pytest

from cs2clip.refazer import ETAPAS, parse_refazer, refaz_varredura


def test_sem_pedido_nao_refaz_nada():
    assert parse_refazer(None) == frozenset()
    assert parse_refazer(False) == frozenset()


def test_pedido_sozinho_ou_tudo_refaz_todas_as_etapas():
    assert parse_refazer(True) == frozenset(ETAPAS)
    assert parse_refazer("tudo") == frozenset(ETAPAS)
    assert parse_refazer("") == frozenset(ETAPAS)


def test_lista_de_etapas_com_virgula_e_espaco():
    assert parse_refazer("falas") == {"falas"}
    assert parse_refazer("kills, Voz") == {"kills", "voz"}


def test_etapa_desconhecida_da_erro_claro():
    with pytest.raises(ValueError, match="desconhecida.*bobagem"):
        parse_refazer("kills,bobagem")


def test_kills_e_rounds_refazem_a_mesma_varredura():
    assert refaz_varredura(parse_refazer("kills"))
    assert refaz_varredura(parse_refazer("rounds"))
    assert not refaz_varredura(parse_refazer("falas"))
    assert not refaz_varredura(frozenset())


def test_conjunto_ja_pronto_passa_direto():
    """Regressão: o main converte o texto e o analyze chama parse_refazer de novo com o conjunto."""
    pronto = parse_refazer("falas,voz")
    assert parse_refazer(pronto) == pronto
    assert parse_refazer(frozenset()) == frozenset()
    assert parse_refazer({"kills"}) == frozenset({"kills"})


# ── reaproveitar cortes e shorts já renderizados ──────────────────────────────────────────

from cs2clip.config import Config  # noqa: E402
from cs2clip.refazer import hash_corte, hash_short, render_desatualizado  # noqa: E402


def test_mesma_config_mesmo_hash():
    assert hash_corte(Config()) == hash_corte(Config())
    assert hash_short(Config()) == hash_short(Config())


def test_config_do_short_muda_so_o_hash_do_short():
    """Regressão (R1 #28): mexer num campo do short refazia também todos os cortes 16:9."""
    cfg = Config()
    cfg.shorts.zoom_gameplay = 1.5
    assert hash_short(cfg) != hash_short(Config())
    assert hash_corte(cfg) == hash_corte(Config())


def test_qualidade_muda_corte_e_short():
    cfg = Config()
    cfg.qualidade.cq = 19
    assert hash_corte(cfg) != hash_corte(Config())
    assert hash_short(cfg) != hash_short(Config())


def test_ligar_desligar_e_paralelismo_nao_mudam_o_hash(tmp_path):
    """Regressão (R1 #28): desmarcar 'gerar shorts'/'gerar montagem' ou trocar renders_paralelos mandava
    todos os cortes de volta para a GPU."""
    cfg = Config()
    cfg.shorts.gerar = False
    cfg.shorts.quantidade = 3
    cfg.shorts.versao_efeitos = False
    cfg.montagem.gerar = False
    cfg.montagem.ordem = "melhores"
    cfg.qualidade.renders_paralelos = 4
    cfg.chat.atraso = 9.0                  # não muda cortes nem shorts
    cfg.transcricao.modelo = "medium"
    assert hash_corte(cfg) == hash_corte(Config())
    assert hash_short(cfg) == hash_short(Config())
    assert not render_desatualizado(tmp_path, cfg, frozenset())   # config não força mais o render inteiro


def test_refazer_cortes_ou_falas_forca_render(tmp_path):
    cfg = Config()
    assert render_desatualizado(tmp_path, cfg, frozenset({"cortes"}))
    assert render_desatualizado(tmp_path, cfg, frozenset({"falas"}))     # a legenda dos shorts vem das falas
    assert not render_desatualizado(tmp_path, cfg, frozenset({"kills", "voz"}))
