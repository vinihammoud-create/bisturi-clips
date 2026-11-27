"""C3 (14/11/2026): botão "Usar a conta aberta agora no Steam" na tela Configurar.

A rota só LÊ a conta aberta (registro ActiveUser, via `steam_local.conta_logada`) e devolve o SteamID64: nunca grava nada, nunca mexe no Steam.
SteamIDs FALSOS: 76561198071376839 (conta principal, accountid 111111111) e 76561198182487950 (conta do Bisturi, accountid 222222222).
O JavaScript roda no Node com DOM de mentira (mesmo harness de tests/test_r1_ui.py).
"""
import os
from pathlib import Path

import pytest

from cs2clip import steam_local
from cs2clip.config import load_config
from test_r1_ui import APP_JS, rodar_js
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

PRINCIPAL = "76561198071376839"
BISTURI = "76561198182487950"
ROTA = "/api/steam/conta-aberta"
FECHADO = "O Steam está fechado. Abra o Steam na conta que você quer usar e clique de novo."


def _arvore(raiz: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(raiz)): (p.stat().st_size, p.stat().st_mtime_ns) for p in raiz.rglob("*") if p.is_file()}


# ── a rota ───────────────────────────────────────────────────────────────────────────────────────

def test_steam_fechado_devolve_o_motivo_em_portugues(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path, steam_conta_aberta=lambda: 0) as c:
        r = c.get(ROTA, headers=token_headers(c))
        assert r.status_code == 200 and r.json() == {"aberta": False, "motivo": FECHADO}


@pytest.mark.parametrize("accountid,steamid64", [(222222222, BISTURI), (111111111, PRINCIPAL)])
def test_conta_aberta_devolve_o_steamid64(tmp_path, accountid, steamid64):
    criar_projeto(tmp_path)
    with cliente(tmp_path, steam_conta_aberta=lambda: accountid) as c:
        r = c.get(ROTA, headers=token_headers(c))
        assert r.status_code == 200 and r.json() == {"aberta": True, "steamid64": steamid64}
        assert r.json()["steamid64"] == str(steam_local.steamid64_da_conta(accountid))                   # a conversão que já existe


def test_sem_token_ou_com_token_errado_e_recusado_e_nao_vaza_o_numero(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path, steam_conta_aberta=lambda: 222222222) as c:
        for cab in ({}, {"X-Token": ""}, {"X-Token": "errado"}):
            r = c.get(ROTA, headers=cab)
            assert r.status_code == 403 and BISTURI not in r.text and "steamid64" not in r.text
        assert c.get(ROTA, headers=token_headers(c)).status_code == 200                                  # CONTROLE: com o token da sessão passa


def test_host_de_fora_ou_site_cruzado_tambem_sao_recusados(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path, steam_conta_aberta=lambda: 222222222) as c:
        assert c.get(ROTA, headers={**token_headers(c), "Host": "exemplo.com"}).status_code == 403
        assert c.get(ROTA, headers={**token_headers(c), "Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert c.get(ROTA, headers={**token_headers(c), "Origin": "http://exemplo.com"}).status_code == 403
        assert c.get(ROTA, headers=token_headers(c)).status_code == 200                                  # CONTROLE


def test_a_conta_e_lida_na_hora_de_cada_chamada(tmp_path):
    criar_projeto(tmp_path)
    estado = {"conta": 0}
    with cliente(tmp_path, steam_conta_aberta=lambda: estado["conta"]) as c:
        h = token_headers(c)
        assert c.get(ROTA, headers=h).json()["aberta"] is False
        estado["conta"] = 222222222
        assert c.get(ROTA, headers=h).json() == {"aberta": True, "steamid64": BISTURI}
        estado["conta"] = 111111111
        assert c.get(ROTA, headers=h).json() == {"aberta": True, "steamid64": PRINCIPAL}


def test_sem_injecao_usa_o_steam_local_lido_na_hora(tmp_path, monkeypatch):
    criar_projeto(tmp_path)
    lidas: list[int] = []
    monkeypatch.setattr(steam_local, "conta_logada", lambda: lidas.append(1) or 222222222)
    with cliente(tmp_path) as c:
        assert c.get(ROTA, headers=token_headers(c)).json() == {"aberta": True, "steamid64": BISTURI}
    assert lidas == [1]


def test_falha_ao_ler_o_registro_vira_aviso_e_nao_erro_500(tmp_path):
    criar_projeto(tmp_path)

    def quebrada() -> int:
        raise OSError("registro indisponível")
    with cliente(tmp_path, steam_conta_aberta=quebrada) as c:
        r = c.get(ROTA, headers=token_headers(c))
        assert r.status_code == 200 and r.json()["aberta"] is False and "Steam" in r.json()["motivo"] and "indispon" not in r.json()["motivo"]


def test_a_rota_nunca_grava_nada(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path, steam_conta_aberta=lambda: 222222222) as c:
        antes = _arvore(tmp_path)
        for _ in range(3):
            assert c.get(ROTA, headers=token_headers(c)).status_code == 200
        depois = _arvore(tmp_path)
    assert depois == antes                                                                                 # nenhum arquivo criado, mudado ou apagado
    assert not (tmp_path / "config.toml").exists()


def test_a_rota_so_aceita_get(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path, steam_conta_aberta=lambda: 222222222) as c:
        assert c.post(ROTA, headers=token_headers(c), json={}).status_code == 405


# ── a tela (JavaScript) ──────────────────────────────────────────────────────────────────────────

CAMPO = """const campo = (chave) => ({ secao: "gravacao_demo", chave, tipo: "texto", rotulo: "SteamID64", dica: "dica", valor: "", comprimento: 17, opcoes: [], minimo: null, maximo: null });
const achar = (no, tag) => acharTag(no, tag);
ui.state.configAlteracoes = {};
const resposta = (obj, ok = true) => ({ ok, headers: { get: () => "application/json" }, json: async () => obj });
"""


def test_botao_preenche_o_campo_com_o_numero_da_conta_aberta_e_marca_como_alterado(tmp_path):
    r = rodar_js(tmp_path, CAMPO + """
      $("#cfg-gravacao_demo-conta_principal_id").value = "76561198071376839";   // B2: a principal já está na tela
      const pedidos = [];
      cfg.fetch = async (url, opcoes) => { pedidos.push([url, (opcoes.method || "GET"), opcoes.headers.has("X-Token")]); return resposta({ aberta: true, steamid64: "76561198182487950" }); };
      const linha = ui.criarCampoConfig(campo("conta_steamid"));
      const botoes = achar(linha, "BUTTON"), entrada = achar(linha, "INPUT")[0];
      botoes[0].dispatch("click"); await espera();
      return { rotulos: botoes.map(b => b.textContent), valor: entrada.value, alt: ui.state.configAlteracoes, pedidos };
    """)
    assert r["rotulos"] == ["Usar a conta aberta agora no Steam"]
    assert r["valor"] == "76561198182487950" and r["alt"] == {"gravacao_demo.conta_steamid": "76561198182487950"}   # só falta o usuário clicar em Salvar
    assert r["pedidos"] == [["/api/steam/conta-aberta", "GET", True]]                                              # GET, com o token da sessão


def test_os_dois_campos_de_conta_ganham_o_botao_e_os_demais_campos_nao(tmp_path):
    r = rodar_js(tmp_path, CAMPO + """
      const com = {};
      for (const chave of ["conta_steamid", "conta_principal_id", "conta_apelido", "conta_principal_apelido", "so_rounds_bons"]) {
        com[chave] = achar(ui.criarCampoConfig(campo(chave)), "BUTTON").length;
      }
      const outro = ui.criarCampoConfig({ secao: "cortes", chave: "conta_steamid", tipo: "texto", rotulo: "x", dica: "", valor: "" });
      return { com, outraSecao: achar(outro, "BUTTON").length };
    """)
    assert r["com"] == {"conta_steamid": 1, "conta_principal_id": 1, "conta_apelido": 0, "conta_principal_apelido": 0, "so_rounds_bons": 0}
    assert r["outraSecao"] == 0


def test_numero_igual_ao_da_outra_conta_mostra_o_aviso_e_nao_preenche(tmp_path):
    r = rodar_js(tmp_path, CAMPO + """
      cfg.fetch = async () => resposta({ aberta: true, steamid64: "76561198071376839" });
      $("#cfg-gravacao_demo-conta_steamid").value = "76561198071376839";             // a outra conta já tem este número (na tela, ainda sem salvar)
      const linha = ui.criarCampoConfig(campo("conta_principal_id"));
      const entrada = achar(linha, "INPUT")[0];
      achar(linha, "BUTTON")[0].dispatch("click"); await espera();
      const aviso = $("#config-message");
      const mostrado = { aviso: aviso.textContent, escondido: aviso.classList.contains("hidden"), classe: aviso.className, entrada: entrada.value, alt: { ...ui.state.configAlteracoes } };
      // CONTROLE: com número diferente na outra conta, preenche
      $("#cfg-gravacao_demo-conta_steamid").value = "76561198182487950";
      achar(linha, "BUTTON")[0].dispatch("click"); await espera();
      return { ...mostrado, depois: entrada.value, alt2: ui.state.configAlteracoes };
    """)
    assert r["aviso"].startswith("As duas contas não podem ser iguais") and r["escondido"] is False and "notice-error" in r["classe"]
    assert r["entrada"] == "" and r["alt"] == {}                                                                             # nada foi preenchido no 1º clique
    assert r["depois"] == "76561198071376839" and r["alt2"] == {"gravacao_demo.conta_principal_id": "76561198071376839"}      # só no 2º clique (CONTROLE)


def test_steam_fechado_mostra_o_motivo_e_deixa_o_campo_como_estava(tmp_path):
    r = rodar_js(tmp_path, CAMPO + """
      $("#cfg-gravacao_demo-conta_principal_id").value = "76561198071376839";   // B2: a principal já está na tela
      cfg.fetch = async () => resposta({ aberta: false, motivo: "O Steam está fechado. Abra o Steam na conta que você quer usar e clique de novo." });
      const c = campo("conta_steamid"); c.valor = "76561198182487950";
      const linha = ui.criarCampoConfig(c);
      const entrada = achar(linha, "INPUT")[0];
      achar(linha, "BUTTON")[0].dispatch("click"); await espera();
      const falha = { aviso: $("#config-message").textContent };
      cfg.fetch = async () => { throw new Error("sem rede"); };
      achar(linha, "BUTTON")[0].dispatch("click"); await espera();
      return { ...falha, valor: entrada.value, alt: ui.state.configAlteracoes, erro: $("#config-message").textContent };
    """)
    assert r["aviso"] == FECHADO and r["valor"] == "76561198182487950" and r["alt"] == {} and r["erro"] == "sem rede"


def test_resposta_estranha_nao_preenche_o_campo(tmp_path):
    r = rodar_js(tmp_path, CAMPO + """
      $("#cfg-gravacao_demo-conta_principal_id").value = "76561198071376839";   // B2: a principal já está na tela
      const linha = ui.criarCampoConfig(campo("conta_steamid"));
      const entrada = achar(linha, "INPUT")[0];
      const respostas = [{ aberta: true, steamid64: "<b>x</b>" }, { aberta: true, steamid64: "123" }, { aberta: true }, { aberta: true, steamid64: 7656119818248795 }, {}];
      for (const resp of respostas) { cfg.fetch = async () => resposta(resp); achar(linha, "BUTTON")[0].dispatch("click"); await espera(); }
      return { valor: entrada.value, alt: ui.state.configAlteracoes };
    """)
    assert r == {"valor": "", "alt": {}}                                                                           # só aceita 17 números


def test_apelido_com_html_aparece_como_texto_na_tela(tmp_path):
    r = rodar_js(tmp_path, CAMPO + """
      $("#cfg-gravacao_demo-conta_principal_id").value = "76561198071376839";   // B2: a principal já está na tela
      const c = campo("conta_apelido"); c.valor = "<b>Smurf</b>"; c.comprimento = 40;
      const entrada = achar(ui.criarCampoConfig(c), "INPUT")[0];
      return { valor: entrada.value, maxLength: entrada.maxLength };
    """)
    assert r == {"valor": "<b>Smurf</b>", "maxLength": 40}
    assert ".innerHTML" not in APP_JS.read_text(encoding="utf-8")                                                  # a tela é montada com el(), nunca com HTML


# ── a dica e a mensagem de "iguais" ──────────────────────────────────────────────────────────────

def test_a_dica_nao_manda_mais_para_site_externo_e_o_aviso_do_js_e_o_mesmo_da_validacao(tmp_path):
    from ui.configuracao import CAMPOS_POR_CHAVE
    for chave in ("conta_steamid", "conta_principal_id"):
        dica = CAMPOS_POR_CHAVE[("gravacao_demo", chave)].dica
        assert "steamid.io" not in dica and "http" not in dica.lower() and "ou digite o SteamID64 (17 números)" in dica
        assert "Usar a conta aberta agora no Steam" in dica
    js = APP_JS.read_text(encoding="utf-8")
    p = tmp_path / "config.toml"
    p.write_text(f'[gravacao_demo]\nconta_steamid = "{PRINCIPAL}"\nconta_principal_id = "{PRINCIPAL}"\n', encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_config(p)
    assert str(e.value) in js and os.path.basename(str(p)) == "config.toml"                                        # a frase do JS é a da validação (uma só)
