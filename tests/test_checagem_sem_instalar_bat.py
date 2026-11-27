"""P5: no programa instalado pelo .exe não existe `instalar.bat`: as dicas da checagem mandam abrir o instalador de novo."""
import cs2clip.checagem as ch
from cs2clip import caminhos


def _textos(itens):
    return " | ".join(i.como_resolver for i in itens if i.como_resolver)


def test_modo_instalado_nao_manda_rodar_instalar_bat(monkeypatch, tmp_path):
    monkeypatch.setattr(caminhos, "modo_instalado", lambda: True)
    monkeypatch.setattr(ch, "_tem_modulo", lambda mod: False)
    monkeypatch.setattr(ch.sys, "version_info", (3, 9, 0))
    itens = ch.checar(tmp_path)
    texto = _textos(itens)
    assert "instalar.bat" not in texto and "instalador" in texto


def test_modo_portatil_continua_com_instalar_bat(monkeypatch, tmp_path):
    monkeypatch.setattr(caminhos, "modo_instalado", lambda: False)
    monkeypatch.setattr(ch, "_tem_modulo", lambda mod: False)
    monkeypatch.setattr(ch.sys, "version_info", (3, 9, 0))
    assert "instalar.bat" in _textos(ch.checar(tmp_path))
