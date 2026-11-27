"""QB4 / teste intermitente (item 5): `Registro.gravar` (gravador_demo) trocava o `.tmp` pelo arquivo final sem tolerar a recusa momentânea do Windows.

Sob carga (antivírus/indexador segurando o arquivo por milissegundos) saía `PermissionError: [WinError 5]` em `registro.json.tmp -> registro.json`;
visto em 13/11 em `test_guarda_foto_depois_diferente...` e reproduzido aqui em `test_regravar_so_um_round...` (1 em 120 rodadas com a máquina cheia).
É falha do CÓDIGO (uma gravação de verdade também morreria por isso), então o conserto é no código: tentar de novo algumas vezes.
"""
import os

import pytest

from cs2clip import gravador_demo as gd
from cs2clip import tools


@pytest.fixture
def recusa_antes(monkeypatch):
    """Faz `os.replace` recusar (PermissionError) as primeiras `n` vezes e depois funcionar de verdade. Sem espera real."""
    real = os.replace
    estado = {"chamadas": 0, "recusar": 0}

    def falso(origem, destino):
        estado["chamadas"] += 1
        if estado["chamadas"] <= estado["recusar"]:
            raise PermissionError(5, "Acesso negado")
        return real(origem, destino)
    monkeypatch.setattr(os, "replace", falso)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return estado


def test_registro_gravar_tolera_recusa_momentanea_do_windows(tmp_path, recusa_antes):
    recusa_antes["recusar"] = 3
    reg = gd.Registro(tmp_path / "saida" / "registro.json")
    reg.gravar(demo="x.dem")
    assert (tmp_path / "saida" / "registro.json").read_text(encoding="utf-8").count("x.dem") == 1
    assert not (tmp_path / "saida" / "registro.json.tmp").exists()          # o .tmp foi trocado, não ficou para trás
    assert recusa_antes["chamadas"] == 4


def test_gravar_atomico_tolera_recusa_momentanea_do_windows(tmp_path, recusa_antes):
    recusa_antes["recusar"] = 2
    tools.gravar_atomico(tmp_path / "a.json", '{"ok": 1}')
    assert (tmp_path / "a.json").read_text(encoding="utf-8") == '{"ok": 1}'


def test_controle_recusa_que_nao_passa_continua_dando_erro(tmp_path, recusa_antes):
    """Não é para engolir erro de verdade: depois das tentativas o PermissionError sobe, e o arquivo antigo fica inteiro."""
    (tmp_path / "a.json").write_text("antigo", encoding="utf-8")
    recusa_antes["recusar"] = 10**6
    with pytest.raises(PermissionError):
        tools.gravar_atomico(tmp_path / "a.json", "novo")
    assert (tmp_path / "a.json").read_text(encoding="utf-8") == "antigo"
    assert recusa_antes["chamadas"] == tools.TENTATIVAS_TROCA


def test_controle_sem_recusa_troca_de_primeira(tmp_path, recusa_antes):
    tools.gravar_atomico(tmp_path / "a.json", "x")
    assert recusa_antes["chamadas"] == 1 and (tmp_path / "a.json").read_text(encoding="utf-8") == "x"
