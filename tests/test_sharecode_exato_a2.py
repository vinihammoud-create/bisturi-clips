"""A2 (item 5 do auditor): a rota da gravação manual passa o código de partilha como ARGUMENTO de um processo; o `valido` antigo aceitava `-----abc…` e outras formas frouxas.
Agora a rota exige o formato exato (a caixa única já exigia)."""
import pytest

from cs2clip import sharecode

from test_gravacao_ui_d6 import CODIGO, _Proc
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

SEM_PREFIXO = CODIGO.replace("CSGO-", "").replace("-", "")
FROUXOS = ["-----" + SEM_PREFIXO[5:], "--" + CODIGO, CODIGO.replace("-", "", 2), CODIGO + "-", "CSGO-" + SEM_PREFIXO, SEM_PREFIXO, "csgo-" + CODIGO[5:], CODIGO.replace("CSGO-", "CSGO--"),
           "-x " + CODIGO, "", "   ", "CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2x", "CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2xK-aaaaa"]


def test_exato_aceita_so_o_formato_completo_e_o_antigo_valido_continua_frouxo():
    assert sharecode.exato(CODIGO) and sharecode.exato("  " + CODIGO + "\n")
    for ruim in FROUXOS:
        assert not sharecode.exato(ruim), repr(ruim)
    assert not sharecode.exato(None) and not sharecode.exato(123)
    assert sharecode.valido(SEM_PREFIXO)                                                                       # o `valido` segue aceitando sem o CSGO- (usado fora dessa rota)
    assert not sharecode.exato(CODIGO.replace(CODIGO[-1], "0"))                                                # '0' não existe no alfabeto: ainda recusa


@pytest.mark.parametrize("ruim", [f for f in FROUXOS if f.strip()])
def test_a_rota_recusa_codigo_frouxo_e_nenhum_processo_nasce(tmp_path, ruim):
    criar_projeto(tmp_path)
    lancados = []
    with cliente(tmp_path, gravacao_popen_factory=lambda cmd, **kw: lancados.append(cmd) or _Proc(["oi"])) as c:
        r = c.post("/api/gravacao/baixar", json={"codigo": ruim}, headers=token_headers(c))
        assert r.status_code == 400 and "formato certo" in r.json()["erro"]
        assert lancados == []


def test_a_rota_continua_aceitando_o_codigo_certo_o_controle(tmp_path):
    criar_projeto(tmp_path)
    lancados = []
    with cliente(tmp_path, gravacao_popen_factory=lambda cmd, **kw: lancados.append(cmd) or _Proc(["oi"])) as c:
        r = c.post("/api/gravacao/baixar", json={"codigo": "  " + CODIGO + " "}, headers=token_headers(c))
        assert r.status_code == 200
    assert lancados and lancados[0][-3:] == ["codigo", CODIGO, "--baixar"]                                       # o processo recebe o código LIMPO (sem espaços)
