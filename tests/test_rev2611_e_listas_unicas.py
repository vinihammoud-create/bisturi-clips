"""R-014 (rev2611, lote E): ui/dados.py e ui/gravacao.py usam as listas de ui/seguranca.py em vez de repetir as suas."""
from pathlib import Path

from ui import dados, gravacao, seguranca
from ui.dados import DadosProjeto

UI = Path(__file__).resolve().parents[1] / "ui"


def test_listar_entrada_usa_as_extensoes_de_seguranca(tmp_path, monkeypatch):
    (tmp_path / "entrada").mkdir()
    for nome in ("a.mp4", "b.dem", "c.zzz", "d.txt"):
        (tmp_path / "entrada" / nome).write_bytes(b"x")
    assert [i["nome"] for i in DadosProjeto(tmp_path).listar_entrada()] == ["a.mp4", "b.dem"]
    monkeypatch.setattr(dados, "EXTENSOES_ENTRADA", {*seguranca.EXTENSOES_ENTRADA, ".zzz"})          # uma lista só: mudou lá, mudou aqui
    assert [i["nome"] for i in DadosProjeto(tmp_path).listar_entrada()] == ["a.mp4", "b.dem", "c.zzz"]


def test_nome_livre_de_demo_usa_o_teste_de_nome_reservado_de_seguranca(monkeypatch):
    assert gravacao._livre_ok("partida_final.dem") is True
    for reservado in ("con.dem", "NUL.dem", "com1.dem", "LPT9.dem", "Aux.dem"):
        assert gravacao._livre_ok(reservado) is False, reservado
    monkeypatch.setattr(gravacao, "nome_reservado_windows", lambda nome: True)                       # prova que é a função de seguranca que decide
    assert gravacao._livre_ok("partida_final.dem") is False


def test_a_lista_antiga_de_reservados_nao_existe_mais_em_gravacao():
    assert not hasattr(gravacao, "RESERVADOS_WINDOWS")
    assert "RESERVADOS_WINDOWS" not in (UI / "gravacao.py").read_text(encoding="utf-8")
    assert '".mp4"' not in (UI / "dados.py").read_text(encoding="utf-8")
