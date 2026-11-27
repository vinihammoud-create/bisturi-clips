"""M-006 (rev2611, lote E): a tela não pergunta tudo a cada segundo. Rápido com trabalho ativo, lento ocioso, bem espaçado com a aba oculta (sem parar de vez:
o vigia do servidor, OCIOSO_S, conta esses pedidos como sinal de vida) e imediato quando a aba volta ou quando uma ação começa um trabalho."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_r1_ui import rodar_js
from test_trabalhos_js_e2 import PREAMBULO

import ui.__main__ as principal

ESTATICOS = Path(__file__).resolve().parents[1] / "ui" / "static"

_PREPARO_APP = """
  const agendados = [];
  win.clearTimeout = () => {};
  const realSetTimeout = win.setTimeout;
  win.setTimeout = (f, ms) => { if (f && f.name === "atualizarFila") { agendados.push(ms); return agendados.length; } return realSetTimeout(f, ms); };
  const fila = (itens, extra = {}) => ({ itens, ativo: null, progresso: {}, ...extra });
  const responde = corpo => { cfg.fetch = async () => ({ ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => corpo }); };
  ui.ligarFila();
  agendados.length = 0;
"""


def test_intervalo_adaptativo_da_fila(tmp_path):
    r = rodar_js(tmp_path, _PREPARO_APP + """
      const saida = {};
      responde(fila([])); await ui.atualizarFila(); saida.ocioso = agendados.pop();
      responde(fila([{ id: "a", estado: "rodando", mensagem: "x" }])); await ui.atualizarFila(); saida.rodando = agendados.pop();
      responde(fila([{ id: "a", estado: "na_fila", mensagem: "x" }])); await ui.atualizarFila(); saida.naFila = agendados.pop();
      responde(fila([{ id: "a", estado: "pronto", mensagem: "x" }])); await ui.atualizarFila(); saida.soPronto = agendados.pop();
      responde(fila([], { externo: { pid: 5 } })); await ui.atualizarFila(); saida.externo = agendados.pop();
      document.hidden = true;
      responde(fila([{ id: "a", estado: "rodando", mensagem: "x" }])); await ui.atualizarFila(); saida.ocultaRodando = agendados.pop();
      document.hidden = false;
      return saida;
    """)
    assert r == {"ocioso": 5000, "rodando": 1000, "naFila": 1000, "soPronto": 5000, "externo": 1000, "ocultaRodando": 30000}


def test_voltar_para_a_aba_atualiza_na_hora_e_ocultar_so_espaca(tmp_path):
    r = rodar_js(tmp_path, _PREPARO_APP + """
      const ouvir = document.ouvintes.visibilitychange;
      document.hidden = true; ouvir.forEach(f => f()); const aoOcultar = agendados.pop();
      document.hidden = false; ouvir.forEach(f => f()); const aoVoltar = agendados.pop();
      return { aoOcultar, aoVoltar, ouvintes: ouvir.length };
    """)
    assert r == {"aoOcultar": 30000, "aoVoltar": 0, "ouvintes": 1}


def test_um_trabalho_que_comeca_nao_espera_o_intervalo_lento(tmp_path):
    r = rodar_js(tmp_path, _PREPARO_APP + """
      responde(fila([])); await ui.atualizarFila();                       // ocioso: o próximo pedido seria em 5 s
      const antes = agendados.pop();
      const pedidos = [];
      cfg.fetch = async (url, op) => { pedidos.push((op.method || "GET") + " " + url);
        const corpo = url === "/api/fila" && op.method === "GET" ? fila([{ id: "n", estado: "na_fila", mensagem: "x" }]) : { itens: [] };
        return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => corpo }; };
      await ui.adicionarFila(["entrada:a.mp4"], {});                      // o dono manda processar
      await espera();
      return { antes, pedidos, depois: agendados.pop() };
    """)
    assert r["antes"] == 5000
    assert r["pedidos"][:2] == ["POST /api/fila", "GET /api/fila"]          # pergunta na hora, sem esperar os 5 s
    assert r["depois"] == 1000                                              # e passa a perguntar rápido


def test_sem_ligar_a_fila_nada_e_agendado(tmp_path):
    r = rodar_js(tmp_path, """
      const agendados = [];
      win.clearTimeout = () => {};
      win.setTimeout = (f, ms) => { agendados.push(ms); return 1; };
      cfg.fetch = async () => ({ ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ itens: [], progresso: {} }) });
      await ui.atualizarFila();
      return agendados;
    """)
    assert r == []


def test_encerrar_para_o_agendamento():
    js = (ESTATICOS / "app.js").read_text(encoding="utf-8")
    assert "setInterval(() => { atualizarFila(); }, 1000)" not in js
    trecho = js.split("async function encerrarPrograma", 1)[1].split("principal = document.createElement", 1)[0]
    assert "state.filaLigada = false" in trecho and "clearTimeout(state.timerFila)" in trecho


def test_o_vigia_do_servidor_aguenta_o_intervalo_com_a_aba_oculta():
    """Com a aba oculta a tela ainda pergunta a cada 30 s; o servidor só dá a página por perdida depois de OCIOSO_S. A folga precisa ser grande."""
    assert principal.OCIOSO_S >= 6 * 30


# ── trabalhos.js ────────────────────────────────────────────────────────────────────────────────────────────────────

def _node(corpo: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node não instalado")
    r = subprocess.run([node, "-e", PREAMBULO + corpo, str(ESTATICOS / "trabalhos.js")], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_trabalhos_js_tambem_adapta_o_intervalo():
    r = _node(r"""
(async () => {
  const agendados = [];
  window.setTimeout = (f, ms) => { agendados.push(ms); return agendados.length; };
  const responde = itens => { globalThis.fetch = async () => ({ ok: true, headers: { get: () => "application/json" }, json: async () => ({ itens }) }); };
  m.ligar();
  agendados.length = 0;
  const saida = {};
  responde([]); await m.atualizar(); saida.ocioso = agendados.pop();
  responde([{ id: "a", estado: "rodando", titulo: "x", etapas: [] }]); await m.atualizar(); saida.rodando = agendados.pop();
  responde([{ id: "a", estado: "esperando_voce", titulo: "x", etapas: [] }]); await m.atualizar(); saida.esperando = agendados.pop();
  responde([{ id: "a", estado: "pronta", titulo: "x", etapas: [] }]); await m.atualizar(); saida.pronta = agendados.pop();
  document.hidden = true; responde([{ id: "a", estado: "rodando", titulo: "x", etapas: [] }]); await m.atualizar(); saida.oculta = agendados.pop();
  globalThis.fetch = async () => { throw new Error("rede"); }; document.hidden = false; await m.atualizar(); saida.aposErro = agendados.pop();
  console.log(JSON.stringify(saida)); process.exit(0);
})();
""")
    assert r == {"ocioso": 6000, "rodando": 2500, "esperando": 2500, "pronta": 6000, "oculta": 30000, "aposErro": 2500}


def test_nenhum_js_usa_mais_setinterval_para_perguntar_ao_servidor():
    for nome in ("app.js", "trabalhos.js", "musicas.js"):
        assert "setInterval(" not in (ESTATICOS / nome).read_text(encoding="utf-8").replace("setInterval() {}", ""), nome
