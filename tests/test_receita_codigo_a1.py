"""A1 (13/11): código de partilha na caixa única. A troca para a conta do Bisturi vem ANTES de baixar: com o Steam na conta PRINCIPAL nada é baixado (o boiler não roda), e ao
trocar o trabalho continua sozinho. Antes a receita baixava primeiro e a checagem só via que havia ALGUMA conta logada."""
import json

from test_receitas_e2 import BISTURI_ID, CODIGO, EDITAR_OK, GRAVAR_OK, PRINCIPAL_ID, Conta, Roteiro, _demo, _ger


def _roteiro(tmp_path):
    baixada = _demo(tmp_path, "match730_003849321203751714819_0599033075_999.dem")
    return Roteiro(baixar_demo=[(["Partida 1", f"Pronta: {baixada}"], 0)], gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK]), baixada


def test_com_o_steam_na_conta_principal_o_codigo_espera_a_troca_e_nenhum_download_acontece(tmp_path):
    rot, baixada = _roteiro(tmp_path)
    conta = Conta(PRINCIPAL_ID)
    g = _ger(tmp_path, rot, conta)
    r = g.iniciar(CODIGO)
    assert [e["nome"] for e in r["etapas"]] == ["esperar_conta", "obter_demo", "gravar", "editar"]
    esperando = g.esperar(r["id"], {"esperando_voce"})
    assert esperando["estado"] == "esperando_voce" and "PRINCIPAL" in esperando["etapas"][0]["detalhe"]
    assert rot.comandos == []                                                                                 # NENHUM comando: o boiler (baixar_demo) não rodou na conta principal
    conta.valor = BISTURI_ID                                                                                  # o dono trocou para a conta do Bisturi
    fim = g.esperar(r["id"])
    assert fim["estado"] == "pronta" and rot.modulos() == ["baixar_demo", "gravador_demo", "editar_demo"]
    assert fim["dados"]["demo"] == str(baixada)


def test_o_cancelar_durante_a_espera_da_troca_tambem_nao_baixa_nada(tmp_path):
    rot, _ = _roteiro(tmp_path)
    g = _ger(tmp_path, rot, Conta(PRINCIPAL_ID))
    r = g.iniciar(CODIGO)
    g.esperar(r["id"], {"esperando_voce"})
    g.cancelar(r["id"])
    assert g.esperar(r["id"])["estado"] == "cancelada" and rot.comandos == [] and g._dono_cs2 is None


def test_trabalho_antigo_com_a_ordem_velha_das_etapas_ainda_retoma(tmp_path):
    """Um código iniciado antes do A1 ficou no disco com `obter_demo` ANTES de `esperar_conta`: continua retomável (a trava do download é do próprio baixar_demo)."""
    rot, baixada = _roteiro(tmp_path)
    nova = _ger(tmp_path, rot)
    pasta = nova.pasta / "antiga0001"
    pasta.mkdir(parents=True)

    def et(nome, titulo):
        return {"nome": nome, "titulo": titulo, "estado": "pendente", "detalhe": "", "inicio": None, "fim": None, "estimativa_s": 30}

    antiga = {"id": "antiga0001", "criada_em": 1.0, "titulo": "Partida do código CSGO-xxxxx…", "estado": "interrompida", "erro": "", "resultado": "",
              "entrada": {"tipo": "codigo_partilha", "valor": CODIGO, "detalhes": {}}, "opcoes": {"rounds": "", "todos": False, "sem_demo": False, "limpo": True}, "dados": {},
              "etapas": [et("obter_demo", "Baixar a demo da Valve"), et("esperar_conta", "Trocar o Steam"), et("gravar", "Gravar"), et("editar", "Montar")]}
    (pasta / "receita.json").write_text(json.dumps(antiga, ensure_ascii=False), encoding="utf-8")
    g = _ger(tmp_path, rot)                                                                                   # "reabriu o programa": lê o disco
    assert g.obter("antiga0001")["estado"] == "interrompida"
    g.retomar("antiga0001")
    fim = g.esperar("antiga0001")
    assert fim["estado"] == "pronta" and rot.modulos() == ["baixar_demo", "gravador_demo", "editar_demo"]
