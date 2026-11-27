"""R-007 (parte da tela, lote E): o texto de confirmação do "Voltar aos padrões" diz o que de fato volta ao padrão e o que fica."""
import re
from pathlib import Path

from test_r1_ui import rodar_js

from ui.configuracao import PROTEGIDOS_NO_RESET

APP = Path(__file__).resolve().parents[1] / "ui" / "static" / "app.js"

_CAMPOS = """
      ui.state.configCampos = [{ secao: "shorts", chave: "marca" }, { secao: "shorts", chave: "idioma" },
        { secao: "gravacao_demo", chave: "conta_steamid" }, { secao: "gravacao_demo", chave: "conta_apelido" },
        { secao: "gravacao_demo", chave: "conta_principal_id" }, { secao: "gravacao_demo", chave: "conta_principal_apelido" },
        { secao: "demo", chave: "jogador" }];
"""


def test_confirmacao_diz_o_que_fica_e_onde_ficam_as_copias(tmp_path):
    r = rodar_js(tmp_path, _CAMPOS + """
      win.respostaConfirm = false;
      await ui.restaurarPadroes();
      return win.confirmacoes[0];
    """)
    texto = r.lower()
    assert "contas do steam" in texto and "nome" in texto and "não mudam" in texto          # o que NÃO volta
    assert "config.toml.bak-antes-dos-padroes" in r and "config.toml.bak" in r             # onde ficam as cópias
    assert "2 opções" in r                                                                 # e quantas voltam (as 5 protegidas não contam)
    assert "todas as opções" not in texto and "nome na demo, idioma" not in texto          # o texto antigo prometia apagar tudo, inclusive as contas


def test_lista_de_protegidos_da_tela_e_a_do_servidor():
    js = APP.read_text(encoding="utf-8")
    bloco = js.split("const PROTEGIDOS_NO_RESET", 1)[1].split("]);", 1)[0]
    da_tela = set(re.findall(r'"([a-z_]+\.[a-z_]+)"', bloco))
    assert da_tela == {f"{s}.{c}" for s, c in PROTEGIDOS_NO_RESET}


def test_400_dos_campos_protegidos_mostra_so_a_mensagem_do_servidor(tmp_path):
    msg = "As contas do Steam e o nome na demo não voltam ao padrão por aqui (são seus). Para limpar uma delas, apague o texto e salve."
    r = rodar_js(tmp_path, _CAMPOS + """
      cfg.fetch = async (url, op) => ({ ok: false, status: 400, headers: { get: () => "application/json" }, json: async () => ({ erro: %r }) });
      win.respostaConfirm = true;
      await ui.restaurarPadroes();
      const caixa = $("#config-message");
      return { texto: caixa.textContent, classe: caixa.className, tocouNaConfig: fetches.filter(u => u === "/api/config").length };
    """ % msg)
    assert r["texto"] == msg and "notice-error" in r["classe"] and r["tocouNaConfig"] == 0
