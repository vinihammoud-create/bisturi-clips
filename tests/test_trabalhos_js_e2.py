"""E1.2 / E2.2 (Plano E): a lógica da tela nova (trabalhos.js) rodada no Node com um DOM de mentira. Sem navegador."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
JS = RAIZ / "ui" / "static" / "trabalhos.js"
HTML = RAIZ / "ui" / "static" / "index.html"
APP = RAIZ / "ui" / "static" / "app.js"

PREAMBULO = r"""
class No {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ""; this._t = ""; this.ouvintes = {}; this.dataset = {}; this.escondido = false; }
  set textContent(v) { this._t = String(v); }
  get textContent() { return this._t + this.children.map(c => c.textContent).join(""); }
  append(...n) { n.forEach(x => this.children.push(x)); }
  replaceChildren(...n) { this.children = n; }
  get childElementCount() { return this.children.length; }
  addEventListener(t, f) { this.ouvintes[t] = f; }
  setAttribute(k, val) { this[k] = val; }
  get classList() { const s = this; return { add() {}, toggle(c, on) { if (c === "hidden") s.escondido = !!on; }, remove(c) { if (c === "hidden") s.escondido = false; } }; }
  todos(pred, acc = []) { if (pred(this)) acc.push(this); this.children.forEach(c => c.todos(pred, acc)); return acc; }
}
const registro = {};
globalThis.document = {
  querySelector: s => (s.startsWith('meta') ? { content: "tok" } : (registro[s] = registro[s] || new No("div"))),
  createElement: t => new No(t), addEventListener() {}, body: { dataset: { tela: "inicio" } }
};
globalThis.window = { location: { hash: "" }, setTimeout, clearTimeout, setInterval() {} };
globalThis.fetch = async () => { throw new Error("sem rede nos testes"); };
const m = require(process.argv[1]);
const botoes = no => no.todos(n => n.tag === "button").map(b => b.textContent);
"""


def _node(corpo: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node não instalado")
    r = subprocess.run([node, "-e", PREAMBULO + corpo, str(JS)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_cada_estado_do_trabalho_mostra_os_botoes_certos_e_as_etapas():
    saida = _node(r"""
const et = (nome, estado, detalhe = "") => ({ nome, titulo: "Etapa " + nome, estado, detalhe });
const t = (estado, extra = {}) => Object.assign({ id: "a1", titulo: "Partida X", estado, erro: "", resultado: "", etapas: [et("esperar_conta", "ok"), et("gravar", "rodando", "Round 9")] }, extra);
const r = {};
for (const e of ["na_fila", "rodando", "esperando_voce", "pronta", "erro", "cancelada", "interrompida"]) {
  const extra = e === "pronta" ? { resultado: "C:\\proj\\saida\\x_limpo" } : (e === "erro" ? { erro: "CS2 travou" } : {});
  r[e] = botoes(m.cartaoTrabalho(t(e, extra)));
}
const c = m.cartaoTrabalho(t("erro", { erro: "CS2 travou" }));
r.texto = c.textContent;
r.simbolos = c.todos(n => n.className === "etapa-simbolo").map(n => n.textContent);
console.log(JSON.stringify(r));
""")
    assert saida["na_fila"] == saida["rodando"] == saida["esperando_voce"] == ["Cancelar"]
    assert saida["erro"] == saida["cancelada"] == saida["interrompida"] == ["Retomar"]
    assert saida["pronta"] == ["Abrir o kit"]
    assert "CS2 travou" in saida["texto"] and "Round 9" in saida["texto"] and "Etapa gravar" in saida["texto"]
    assert saida["simbolos"] == ["✓", "●"]


def test_cartao_do_plano_aceito_tem_etapas_tempo_e_botao_e_o_recusado_so_a_frase():
    saida = _node(r"""
m.montarPlano({ entrada: { tipo: "demo", aceita: true, mensagem: "", detalhes: { nome: "partida.dem" } }, estimativa_s: 400,
  demo: { partida: "de_mirage 5x13", rounds: [3, 8, 9], minutos_de_jogo: 3.9 },
  etapas: [{ titulo: "Trocar o Steam", estimativa_s: 30 }, { titulo: "Gravar os rounds pelo CS2", estimativa_s: 200 }, { titulo: "Montar", estimativa_s: 150 }] });
const ok = document.querySelector("#caixa-plano");
const aceito = { texto: ok.textContent, botoes: botoes(ok), oculto: ok.escondido };
m.montarPlano({ entrada: { tipo: "recusado", aceita: false, mensagem: "Esse link é de um CLIPE da Twitch.", detalhes: {} } });
const rec = document.querySelector("#caixa-plano");
console.log(JSON.stringify({ aceito, recusado: { texto: rec.textContent, botoes: botoes(rec) }, minutos: [m.minutos(0), m.minutos(30), m.minutos(400), m.minutos(3600)] }));
""")
    assert saida["aceito"]["botoes"] == ["Começar"] and "rounds bons: 3, 8, 9" in saida["aceito"]["texto"] and "≈ 7 min" in saida["aceito"]["texto"]
    assert "Demo do CS2" in saida["aceito"]["texto"] and saida["aceito"]["oculto"] is False
    assert saida["recusado"]["botoes"] == [] and "CLIPE da Twitch" in saida["recusado"]["texto"] and "Não posso usar isso" in saida["recusado"]["texto"]
    assert saida["minutos"] == ["", "≈ 1 min", "≈ 7 min", "≈ 60 min"]


def test_html_tem_a_caixa_unica_a_tela_trabalhos_o_aviso_de_conta_e_o_app_conhece_a_tela():
    html, app = HTML.read_text(encoding="utf-8"), APP.read_text(encoding="utf-8")
    for ident in ("caixa-texto", "caixa-plano", "caixa-arquivo", "caixa-ultima", "view-trabalhos", "trabalhos-lista", "nav-trabalhos", "nav-trabalhos-n",
                  "aviso-conta", "aviso-conta-texto", "aviso-conta-ir", "primeiros-passos", "abrir-gravacao-manual"):
        assert f'id="{ident}"' in html, ident
    assert '/static/trabalhos.js' in html and 'data-view="trabalhos"' in html
    assert '"trabalhos"' in app.split("const TELAS", 1)[1].split("\n", 1)[0] and "BisturiTrabalhos" in app
    nav_views = {v for v in __import__("re").findall(r'class="nav-item[^"]*"[^>]*data-view="([a-z]+)"', html)}
    secoes = set(__import__("re").findall(r'id="view-([a-z]+)"', html))
    assert nav_views <= secoes                                                                  # todo botão do menu tem a sua tela


def test_primeiros_passos_so_mostra_o_que_e_essencial_e_oferece_a_acao_certa():
    saida = _node(r"""
const it = (item, ok, detalhe = "") => ({ item, ok, detalhe });
const fw = "não achei regra de entrada...; crie (PowerShell como administrador): New-NetFirewallRule -DisplayName 'Bisturi bloqueia 2121' -Direction Inbound -Action Block -Protocol TCP -LocalPort 2121 -Profile Any";
const tudo = [it("HLAE instalado", true), it("Firewall bloqueia a porta 2121 para fora", true), it("CS2 e OBS fechados", false, "obs64.exe"), it("Steam aberto", false, "abra o Steam"),
  it("Steam na 2ª conta (conta do Bisturi)", false, "O Steam está fechado..."), it("demo", false, "diga --demo")];
const r1 = m.classificarChecklist(tudo);                       // só situacional pendente: nada a preparar
m.montarPrimeirosPassos(tudo);
const ok = document.querySelector("#primeiros-passos");
const textoOk = ok.textContent;
const falta = [it("HLAE instalado", false, "python -m cs2clip.terceiros instalar hlae"), it("Firewall bloqueia a porta 2121 para fora", false, fw),
  it("Steam na 2ª conta (conta do Bisturi)", false, "Falta dizer qual é a conta do Bisturi. Diga em Configurar → Contas do Steam.")];
const r2 = m.classificarChecklist(falta);
m.montarPrimeirosPassos(falta);
const caixa = document.querySelector("#primeiros-passos");
console.log(JSON.stringify({ r1: r1.length, textoOk, r2: r2.map(x => [x.item, x.acao || ""]), botoes: botoes(caixa), texto: caixa.textContent, cmd: m.comandoDoFirewall(fw), semCmd: m.comandoDoFirewall("nada") }));
""")
    assert saida["r1"] == 0 and "tudo pronto" in saida["textoOk"]
    assert saida["r2"] == [["HLAE instalado", ""], ["Firewall bloqueia a porta 2121 para fora", "firewall"], ["Steam na 2ª conta (conta do Bisturi)", "configurar"]]
    assert saida["botoes"] == ["Copiar o comando", "Abrir Configurar", "Verificar de novo"] and "Falta preparar" in saida["texto"]
    assert saida["cmd"].startswith("New-NetFirewallRule -DisplayName 'Bisturi bloqueia 2121'") and saida["cmd"].endswith("-Profile Any") and saida["semCmd"] == ""


def test_cartao_do_plano_de_video_oferece_a_regravacao_limpa_e_o_de_demo_nao():
    saida = _node(r"""
const base = { estimativa_s: 600, etapas: [{ titulo: "Analisar o vídeo", estimativa_s: 600 }] };
m.montarPlano(Object.assign({ entrada: { tipo: "video", aceita: true, mensagem: "", detalhes: { nome: "live.mkv" } } }, base));
const live = document.querySelector("#caixa-plano").textContent;
m.montarPlano(Object.assign({ entrada: { tipo: "demo", aceita: true, mensagem: "", detalhes: { nome: "p.dem" } } }, base));
const demo = document.querySelector("#caixa-plano").textContent;
console.log(JSON.stringify({ live, demo }));
""")
    assert "regravar os rounds bons em imagem limpa" in saida["live"] and "juntar a minha voz" in saida["live"]
    assert "regravar os rounds bons" not in saida["demo"]


def test_link_do_youtube_studio_so_vira_link_na_etapa_de_espera_e_so_para_o_endereco_certo():
    saida = _node(r"""
const et = (nome, detalhe) => ({ nome, titulo: "Etapa", estado: "esperando_voce", detalhe });
const tr = (etapa) => ({ id: "a", titulo: "Live", estado: "esperando_voce", erro: "", resultado: "", etapas: [etapa] });
const links = no => no.todos(n => n.tag === "a").map(a => a.href);
const bom = m.cartaoTrabalho(tr(et("aguardar_download", "Abra https://studio.youtube.com/video/dQw4w9WgXcQ/edit → ⋮ → Baixar. Estou vigiando a pasta Downloads.")));
const outro = m.cartaoTrabalho(tr(et("aguardar_download", "Abra https://evil.example.com/video/dQw4w9WgXcQ/edit agora")));
const outraEtapa = m.cartaoTrabalho(tr(et("gravar", "https://studio.youtube.com/video/dQw4w9WgXcQ/edit")));
console.log(JSON.stringify({ bom: links(bom), outro: links(outro), outraEtapa: links(outraEtapa), textoOutro: outro.textContent }));
""")
    assert saida["bom"] == ["https://studio.youtube.com/video/dQw4w9WgXcQ/edit"]
    assert saida["outro"] == [] and saida["outraEtapa"] == [] and "evil.example.com" in saida["textoOutro"]       # texto puro, sem link


def test_pergunta_do_arquivo_do_youtube_mostra_os_dois_botoes_so_quando_ha_candidato():
    saida = _node(r"""
const t = (dados, estado = "esperando_voce") => ({ id: "a", titulo: "Live", estado, erro: "", resultado: "", dados,
  etapas: [{ nome: "aguardar_download", titulo: "Baixar o vídeo", estado: "esperando_voce", detalhe: "Achei x.mp4 (30 MB). É este o vídeo?" }] });
console.log(JSON.stringify({ com: botoes(m.cartaoTrabalho(t({ candidato: { nome: "x.mp4", mb: 30, caminho: "C:/x.mp4" } }))), sem: botoes(m.cartaoTrabalho(t({}))),
  pronta: botoes(m.cartaoTrabalho(t({ candidato: { nome: "x.mp4" } }, "pronta"))) }));
""")
    assert saida["com"] == ["Cancelar", "É este arquivo", "Não é este"] and saida["sem"] == ["Cancelar"] and saida["pronta"] == []


def test_modo_manual_recolhido_a_caixa_e_a_entrada_e_nenhum_texto_manda_digitar_ou_arrastar_para_a_caixa():
    html = HTML.read_text(encoding="utf-8")
    assert '<details class="modo-manual" id="modo-manual">' in html and "</details>" in html
    manual = html.split('<details class="modo-manual"', 1)[1].split("</details>", 1)[0]
    for ident in ("drop-zone", "choose-files", "vod-link", "process-button", "step-1", "step-2", "step-3"):
        assert f'id="{ident}"' in manual, ident                                                  # os 3 passos antigos continuam, só que dentro do modo manual
    antes = html.split('<details class="modo-manual"', 1)[0]
    assert 'id="caixa-texto"' in antes and 'id="queue-list"' in html.split("</details>", 1)[1]    # a caixa vem antes; o Andamento fica visível
    assert "partida.dem" not in html and "o caminho de uma demo" not in html
    from cs2clip import entrada
    for r in (entrada.reconhecer(""), entrada.reconhecer("pasta/nao_existe/x.mp4"), entrada.reconhecer("//servidor/pasta/x.dem")):
        assert "arraste" not in r.mensagem.lower() and "Escolher arquivo" in r.mensagem


def test_a_tela_manual_esconde_o_botao_da_lista_das_ultimas_quando_ela_esta_desligada():
    app = (RAIZ / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert "lista_ultimas === false" in app and '#grav-listar' in app

