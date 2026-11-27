"""P5: no programa instalado pelo .exe não há `python -m ...` no PATH: a dica manda abrir o instalador e marcar a tarefa."""
from cs2clip import caminhos, terceiros


def test_dica_no_modo_instalado_manda_abrir_o_instalador(monkeypatch):
    monkeypatch.setattr(caminhos, "modo_instalado", lambda: True)
    d = terceiros.dica_instalar("hlae")
    assert "python -m" not in d and "instalador" in d and "Gravar pela demo" in d


def test_dica_no_modo_portatil_mostra_o_comando(monkeypatch):
    monkeypatch.setattr(caminhos, "modo_instalado", lambda: False)
    assert terceiros.dica_instalar("hlae") == "python -m cs2clip.terceiros instalar hlae"
