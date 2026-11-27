"""B2 (14/11/2026): com a conta principal VAZIA a trava recusa, em vez de deixar passar.

Antes era `if principal and ...`: com a principal vazia o bloqueio duro sumia, e se a conta do Bisturi tivesse o número da principal o
programa gravava e baixava COM a principal. Com o dono não acontece (as duas estão preenchidas), mas importa para outra pessoa.
Vale para gravar, baixar por código e listar. SteamIDs FALSOS; nada lê o config.toml do dono. Cada teste tem CONTROLE.
"""
import pytest

from cs2clip import baixar_demo as bd
from cs2clip import gravador_demo as gd
from cs2clip import steam_local
from cs2clip.config import Config
from test_conta_aberta_c3 import CAMPO, rodar_js

BISTURI, PRINCIPAL = "76561198182487950", "76561198071376839"          # accountid 222222222 e 111111111
ID_BISTURI, ID_PRINCIPAL, OUTRA = 222222222, 111111111, 333333333


def _falta_principal(e: steam_local.ContaErrada) -> None:
    assert e.de_configuracao is True                                    # a receita para com a mensagem, não fica esperando a troca de conta
    assert str(e).startswith("Falta dizer qual é a sua conta principal")   # o JS mostra o botão "Abrir Configurar" para "Falta dizer qual"
    assert "Configurar → Contas do Steam" in str(e)


@pytest.mark.parametrize("logada", [ID_BISTURI, ID_PRINCIPAL, OUTRA, 0])
def test_gravar_com_a_principal_vazia_recusa_qualquer_que_seja_a_conta_aberta(logada):
    with pytest.raises(steam_local.ContaErrada) as e:
        steam_local.exigir_conta_do_bisturi(BISTURI, "", logada)
    _falta_principal(e.value)


def test_o_buraco_antigo_bisturi_com_o_numero_da_principal_e_principal_vazia():
    with pytest.raises(steam_local.ContaErrada) as e:                   # antes: gravava COM a principal
        steam_local.exigir_conta_do_bisturi(PRINCIPAL, "", ID_PRINCIPAL)
    _falta_principal(e.value)


def test_controle_as_duas_preenchidas_continua_como_antes():
    assert steam_local.exigir_conta_do_bisturi(BISTURI, PRINCIPAL, ID_BISTURI) == ID_BISTURI
    with pytest.raises(steam_local.ContaErrada) as e:                   # principal aberta: bloqueio duro, não é "configuração"
        steam_local.exigir_conta_do_bisturi(BISTURI, PRINCIPAL, ID_PRINCIPAL)
    assert e.value.de_configuracao is False and "nunca grava com ela" in str(e.value)
    with pytest.raises(steam_local.ContaErrada) as e:                   # a do Bisturi vazia continua pedindo a do Bisturi primeiro
        steam_local.exigir_conta_do_bisturi("", "", ID_BISTURI)
    assert str(e.value).startswith("Falta dizer qual é a conta do Bisturi")


def test_o_apelido_da_principal_aparece_e_nao_muda_a_decisao():
    with pytest.raises(steam_local.ContaErrada) as e:
        steam_local.exigir_conta_do_bisturi(BISTURI, "", ID_BISTURI, apelido_principal="Conta de jogo")
    assert e.value.de_configuracao is True and "Conta de jogo" not in str(e.value)   # vazia não tem apelido que valha: a frase é a genérica


def test_checklist_da_gravacao_mostra_que_falta_a_principal():
    cfg = Config()
    cfg.gravacao_demo.conta_steamid = BISTURI
    dep = gd.dependencias_reais()
    dep.conta_logada, dep.jogo_aberto, dep.steam_rodando = (lambda: ID_BISTURI), (lambda: []), (lambda: True)
    dep.firewall = lambda porta: (True, "ok")
    item = {nome: (ok, det) for nome, ok, det in gd.checklist(None, cfg, dep)}["Steam na 2ª conta (conta do Bisturi)"]
    assert item[0] is False and item[1].startswith("Falta dizer qual é a sua conta principal")
    cfg.gravacao_demo.conta_principal_id = PRINCIPAL                    # CONTROLE: com a principal, o item fica certo
    assert {nome: ok for nome, ok, _ in gd.checklist(None, cfg, dep)}["Steam na 2ª conta (conta do Bisturi)"] is True


def test_baixar_por_codigo_com_a_principal_vazia_recusa_antes_do_boiler():
    for contas in ((BISTURI, ""), (PRINCIPAL, "")):
        with pytest.raises(bd.ErroDownload, match="Falta dizer qual é a sua conta principal"):
            bd.exigir_conta_para_baixar(lambda: ID_BISTURI, lambda c=contas: c)
    bd.exigir_conta_para_baixar(lambda: ID_BISTURI, lambda: (BISTURI, PRINCIPAL))   # CONTROLE: as duas preenchidas, a do Bisturi aberta: passa


def test_listar_com_a_principal_vazia_recusa_antes_do_boiler(tmp_path):
    exe = tmp_path / "boiler-writter.exe"
    exe.write_bytes(b"MZ")
    rodou = []
    with pytest.raises(bd.ErroDownload, match="Falta dizer qual é a sua conta principal"):
        bd.listar_ultimas(boiler=exe, pasta_tmp=tmp_path, jogo_aberto=lambda: [], conta=lambda: ID_PRINCIPAL, rodar=lambda *a, **k: rodou.append(1),
                          conta_do_bisturi=lambda: ID_BISTURI, permitida=lambda: True, contas=lambda: (BISTURI, ""))
    assert rodou == []                                                  # o boiler nem foi chamado


def test_listar_le_as_dependencias_na_hora_da_chamada(monkeypatch):
    import inspect
    padroes = {n: p.default for n, p in inspect.signature(bd.listar_ultimas).parameters.items()}
    assert padroes["rodar"] is None and padroes["conta_do_bisturi"] is None and padroes["contas"] is None   # nada preso na definição
    chamadas = []
    monkeypatch.setattr(bd, "conta_do_bisturi", lambda: chamadas.append("bisturi") or 0)
    monkeypatch.setattr(bd, "lista_permitida", lambda: False)
    with pytest.raises(bd.ErroDownload):
        bd.listar_ultimas()                                             # desligada por padrão: recusa antes de qualquer leitura do Steam
    assert chamadas == []


# ── o botão "Usar a conta aberta agora no Steam" ─────────────────────────────────────────────────

def test_botao_da_conta_do_bisturi_recusa_enquanto_a_principal_estiver_vazia(tmp_path):
    r = rodar_js(tmp_path, CAMPO + """
      const pedidos = [];
      cfg.fetch = async (url) => { pedidos.push(url); return resposta({ aberta: true, steamid64: "76561198182487950" }); };
      const principal = $("#cfg-gravacao_demo-conta_principal_id");
      principal.value = "";                                          // a principal ainda vazia na tela
      const linha = ui.criarCampoConfig(campo("conta_steamid"));
      const entrada = achar(linha, "INPUT")[0];
      achar(linha, "BUTTON")[0].dispatch("click"); await espera();
      const vazia = { valor: entrada.value, pedidos: pedidos.length, aviso: $("#config-message").textContent };
      principal.value = "76561198071376839";                       // CONTROLE: com a principal na tela, o botão preenche
      achar(linha, "BUTTON")[0].dispatch("click"); await espera();
      return { vazia, depois: entrada.value };
    """)
    assert r["vazia"]["valor"] == "" and r["vazia"]["pedidos"] == 0          # nem pergunta ao Steam
    assert "conta principal" in r["vazia"]["aviso"]
    assert r["depois"] == BISTURI


def test_botao_da_principal_funciona_mesmo_com_a_do_bisturi_vazia(tmp_path):
    r = rodar_js(tmp_path, CAMPO + """
      cfg.fetch = async () => resposta({ aberta: true, steamid64: "76561198071376839" });
      const linha = ui.criarCampoConfig(campo("conta_principal_id"));
      achar(linha, "BUTTON")[0].dispatch("click"); await espera();
      return achar(linha, "INPUT")[0].value;
    """)
    assert r == PRINCIPAL                                                    # CONTROLE: a ordem certa é primeiro a principal
