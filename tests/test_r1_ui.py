"""Revisão R1 (interface): cada teste falhava antes da correção do achado indicado."""
import errno
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import ui.fila as fila_mod
import ui.servidor as servidor_mod
from ui.configuracao import ConfiguracaoProjeto, ErroConfig
from ui.fila import GerenciadorFila, construir_argumentos

from test_ui_configuracao import FakeConfig, load_config
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers
from test_ui_fila import FakeProcess, aguardar

_RUN_REAL = subprocess.run   # o fixture abaixo troca o subprocess.run global; o Node usa este
RAIZ = Path(__file__).resolve().parents[1]
APP_JS = RAIZ / "ui" / "static" / "app.js"


@pytest.fixture(autouse=True)
def taskkill_falso(monkeypatch):
    """Nunca rodar o taskkill de verdade: o PID de um processo falso pode ser o OBS ou o CS2."""
    chamadas = []

    def run_falso(args, *a, **k):
        chamadas.append(list(args))
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(fila_mod.subprocess, "run", run_falso)
    return chamadas


# ── 39: a fila voltava a aceitar arquivos só reiniciando a interface ────────────────────────────

def _fila_com_externo(tmp_path):
    (tmp_path / "saida").mkdir()
    progresso = tmp_path / "saida" / "progresso.json"
    progresso.write_text(json.dumps({"estado": "rodando", "pid": os.getpid()}), encoding="utf-8")
    arquivo = tmp_path / "video.mp4"
    arquivo.write_bytes(b"x")
    manager = GerenciadorFila(tmp_path, popen_factory=lambda *a, **k: FakeProcess(0), command_builder=lambda *a: ["falso"])
    return manager, progresso, arquivo


def test_fila_aceita_de_novo_quando_o_processamento_de_fora_termina(tmp_path):
    manager, progresso, arquivo = _fila_com_externo(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            manager.adicionar(arquivo)
        progresso.write_text(json.dumps({"estado": "pronto"}), encoding="utf-8")
        job = manager.adicionar(arquivo)
        assert aguardar(manager, lambda: next(j for j in manager.listar()["itens"] if j["id"] == job["id"])["estado"] == "pronto")
    finally:
        manager.encerrar()


def test_listar_para_de_mostrar_o_bloqueio_externo_velho(tmp_path):
    manager, progresso, _ = _fila_com_externo(tmp_path)
    try:
        assert manager.listar()["externo"] is not None
        progresso.write_text(json.dumps({"estado": "pronto"}), encoding="utf-8")
        assert manager.listar()["externo"] is None
    finally:
        manager.encerrar()


# ── 47: o comando usava sempre <raiz>/.venv/Scripts/python.exe ──────────────────────────────────

def test_comando_usa_o_mesmo_python_da_interface(tmp_path):
    arquivo = tmp_path / "a.mp4"
    arquivo.write_bytes(b"x")
    assert construir_argumentos(tmp_path, [arquivo], {})[0] == sys.executable


# ── 49: todo código 1 de demo era "falta o nome" ───────────────────────────────────────────────

def _rodar_demo(tmp_path, saida, opcoes):
    (tmp_path / "saida").mkdir(exist_ok=True)
    demo = tmp_path / "partida.dem"
    demo.write_bytes(b"x")
    manager = GerenciadorFila(tmp_path, popen_factory=lambda *a, **k: FakeProcess(1, saida=saida),
                              command_builder=lambda *a: ["falso"])
    job = manager.adicionar(demo, opcoes)
    assert aguardar(manager, lambda: next(j for j in manager.listar()["itens"] if j["id"] == job["id"])["estado"] not in {"na_fila", "rodando"})
    return manager, next(j for j in manager.listar()["itens"] if j["id"] == job["id"])


def test_nome_digitado_errado_pede_o_nome_de_novo_e_mostra_os_jogadores(tmp_path):
    saida = ("  [erro] partida.dem: Não achei o jogador 'Dolar'. Use --jogador \"nome\" (ou [demo] jogador no config.toml). "
             "Jogadores: Dollar The D0G (20 abates), Outro (3 abates)\n")
    manager, final = _rodar_demo(tmp_path, saida, {"jogador": "Dolar"})
    try:
        assert final["estado"] == "precisa_jogador"
        assert final["jogadores"] == "Dollar The D0G (20 abates), Outro (3 abates)"
        assert manager.informar_jogador(final["id"], "Dollar")["estado"] == "na_fila"   # antes: recusava
    finally:
        manager.encerrar()


def test_demo_corrompida_sem_nome_vira_erro_e_nao_pede_nome(tmp_path):
    manager, final = _rodar_demo(tmp_path, "  [erro] partida.dem: Não consegui ler esta demo: partida.dem\n", {})
    try:
        assert final["estado"] == "erro"
        assert "jogadores" not in final
    finally:
        manager.encerrar()


# ── 45: envio por arrastar falhava em disco sem hard link (exFAT/FAT32, rede) ──────────────────

def test_upload_funciona_sem_hard_link(tmp_path, monkeypatch):
    criar_projeto(tmp_path)

    def link_recusado(*a, **k):
        raise OSError(1, "Função incorreta")       # o que o exFAT responde

    with cliente(tmp_path) as c:
        monkeypatch.setattr(servidor_mod.os, "link", link_recusado)
        dados = b"V" * (1024 * 1024 + 5)
        r1 = c.post("/api/upload", headers=token_headers(c), files=[("files", ("live.mp4", dados, "video/mp4"))])
        assert r1.status_code == 200, r1.text
        assert Path(r1.json()["itens"][0]["caminho"]).read_bytes() == dados
        r2 = c.post("/api/upload", headers=token_headers(c), files=[("files", ("live.mp4", b"outro", "video/mp4"))])
        assert r2.status_code == 200 and r2.json()["itens"][0]["nome"] == "live (2).mp4"
        assert Path(r1.json()["itens"][0]["caminho"]).read_bytes() == dados          # não sobrescreveu
        assert not list((tmp_path / "entrada").glob("*.part"))


def test_upload_falha_sem_espaco_e_falha_comum_tem_mensagens_certas(tmp_path, monkeypatch):
    criar_projeto(tmp_path)

    def falha(codigo):
        def _f(*a, **k):
            raise OSError(codigo, "falhou")
        return _f

    with cliente(tmp_path) as c:
        monkeypatch.setattr(servidor_mod.os, "link", falha(errno.EIO))
        monkeypatch.setattr(servidor_mod.os, "replace", falha(errno.ENOSPC))
        r = c.post("/api/upload", headers=token_headers(c), files=[("files", ("a.mp4", b"x", "video/mp4"))])
        assert r.status_code == 507 and r.json()["erro"] == "Não há espaço livre suficiente no disco."
        monkeypatch.setattr(servidor_mod.os, "replace", falha(errno.EIO))
        r = c.post("/api/upload", headers=token_headers(c), files=[("files", ("a.mp4", b"x", "video/mp4"))])
        assert r.status_code == 507 and "espaço" not in r.json()["erro"]
        assert not [p for p in (tmp_path / "entrada").iterdir()]                    # nem reserva vazia nem .part


# ── 48: marca dos shorts e idioma da fala pela interface ────────────────────────────────────────

def test_configuracao_muda_a_marca_dos_shorts_e_o_idioma(tmp_path):
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    por_nome = {f"{c['secao']}.{c['chave']}": c for c in config.ler()["campos"]}
    assert por_nome["shorts.marca"]["valor"] == "@drd0llar"
    assert por_nome["transcricao.idioma"]["opcoes"] == ["pt", "en", "es"]
    config.salvar({"shorts.marca": "@meunick", "transcricao.idioma": "en"})
    dados = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert dados["shorts"]["marca"] == "@meunick" and dados["transcricao"]["idioma"] == "en"
    config.salvar({"shorts.marca": ""})                                             # vazio = sem texto
    assert tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))["shorts"]["marca"] == ""
    for ruim in ({"shorts.marca": "{\\b1}x"}, {"shorts.marca": "x" * 41}, {"transcricao.idioma": "auto"}):
        with pytest.raises(ErroConfig):
            config.salvar(ruim)


def test_nucleo_de_verdade_aceita_marca_vazia_e_idioma(tmp_path):
    config = ConfiguracaoProjeto(tmp_path)
    config.salvar({"shorts.marca": "", "transcricao.idioma": "es"})
    from cs2clip.config import load_config as load_real
    cfg = load_real(tmp_path / "config.toml")
    assert cfg.shorts.marca == "" and cfg.transcricao.idioma == "es"


def test_exemplo_do_nome_no_jogo_nao_e_o_do_dono():
    html = (RAIZ / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "Dollar The D0G" not in html


# ── 52: o modo app do Edge nunca abria (msedge.exe fora do PATH) ───────────────────────────────

def test_acha_o_edge_fora_do_path_e_abre_com_o_caminho_completo(tmp_path, monkeypatch):
    import ui.__main__ as inicio
    edge = tmp_path / "pf" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")
    monkeypatch.setattr(inicio.shutil, "which", lambda nome: None)
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "nada"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nada2"))
    assert inicio.caminho_edge() == str(edge)

    abertos, navegador = [], []

    class Resposta:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    class Servidor:
        started = True
        should_exit = False

    monkeypatch.setattr(inicio.urllib.request, "urlopen", lambda *a, **k: Resposta())
    monkeypatch.setattr(inicio.subprocess, "Popen", lambda args, **k: abertos.append(args))
    monkeypatch.setattr(inicio.webbrowser, "open", lambda *a, **k: navegador.append(a))
    inicio.abrir_navegador(Servidor(), "http://127.0.0.1:8765")
    assert abertos == [[str(edge), "--app=http://127.0.0.1:8765"]] and navegador == []


def test_sem_edge_cai_no_navegador_padrao_e_porta_diferente_avisa(tmp_path, monkeypatch):
    import ui.__main__ as inicio
    monkeypatch.setattr(inicio.shutil, "which", lambda nome: None)
    for variavel in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.setenv(variavel, str(tmp_path / "nada"))
    assert inicio.caminho_edge() is None
    assert "8766" in inicio.aviso_porta(8766) and "Revisão" in inicio.aviso_porta(8766)


# ── 44: resultado de demo pela biblioteca (o núcleo grava no índice; aqui, a parte da interface) ─

def test_api_entrega_o_resumo_da_demo_listada_no_indice(tmp_path):
    criar_projeto(tmp_path)
    pasta = tmp_path / "saida" / "partida"
    pasta.mkdir()
    (pasta / "momentos.json").write_text(json.dumps({"formato": 1, "video": "partida", "momentos": []}), encoding="utf-8")
    (pasta / "demo.json").write_text(json.dumps({"formato": 1, "mapa": "de_mirage", "jogador": "Dollar", "placar": [13, 9],
                                                 "abates": [], "clutches": []}), encoding="utf-8")
    indice = json.loads((tmp_path / "saida" / "indice.json").read_text(encoding="utf-8"))
    indice.append({"id": "partida", "arquivo": "partida.dem", "caminho": str(tmp_path / "partida.dem"), "duracao": 1800,
                   "momentos": 0, "abates": 0})
    (tmp_path / "saida" / "indice.json").write_text(json.dumps(indice), encoding="utf-8")
    with cliente(tmp_path) as c:
        assert any(i["id"] == "partida" for i in c.get("/api/indice").json()["itens"])
        r = c.get("/api/videos/partida").json()
        assert r["demo"]["mapa"] == "de_mirage" and r["demo"]["jogador"] == "Dollar"


# ── 46: arrastar grava o vídeo duas vezes (TEMP do sistema + entrada/) ──────────────────────────

def test_tela_avisa_que_arrastar_copia_o_arquivo():
    html = (RAIZ / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "Arrastar faz uma cópia" in html


# ── testes do app.js (Node com um DOM mínimo) ───────────────────────────────────────────────────

HARNESS = r"""
const vm = require("vm"); const fs = require("fs");
class Elem {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase(); this.children = []; this.style = {}; this.dataset = {}; this.attrs = {};
    this._cls = new Set(); this.textContent = ""; this.value = ""; this.listeners = {}; this.parentNode = null;
    const s = this._cls;
    this.classList = { add: (...c) => c.forEach(x => s.add(x)), remove: (...c) => c.forEach(x => s.delete(x)), contains: c => s.has(c),
      toggle: (c, f) => { const on = f === undefined ? !s.has(c) : Boolean(f); if (on) s.add(c); else s.delete(c); return on; } };
  }
  set className(v) { this._cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach(x => this._cls.add(x)); }
  get className() { return [...this._cls].join(" "); }
  append(...n) { n.forEach(x => { if (x && typeof x === "object") { x.parentNode = this; this.children.push(x); } }); }
  replaceChildren(...n) { this.children = []; this.append(...n); }
  after() {} remove() {} setSelectionRange() {} closest() { return null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  dispatch(t) { (this.listeners[t] || []).forEach(f => f({ target: this, preventDefault() {} })); }
  focus() { document.activeElement = this; }
  querySelectorAll() { return []; } querySelector() { return null; }
}
const cache = new Map();
const document = {
  activeElement: null, createElement: t => new Elem(t), addEventListener() {}, querySelectorAll: () => [],
  querySelector: s => { if (!cache.has(s)) cache.set(s, new Elem("div")); return cache.get(s); }
};
const acharTag = (no, tag, out = []) => { if (no.tagName === tag) out.push(no); no.children.forEach(f => acharTag(f, tag, out)); return out; };
const textoDe = no => [no.textContent, ...no.children.map(textoDe)].join(" ");
const fetches = [];
const fetch = async url => { fetches.push(url); return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ linhas: ["linha do log\n"] }) }; };
const mod = { exports: {} };
const ctx = { module: mod, document, window: { setTimeout, setInterval, clearInterval }, console, Headers, fetch, setTimeout };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const $ = s => document.querySelector(s);
const espera = () => new Promise(r => setTimeout(r, 15));
const corpo = fs.readFileSync(process.argv[3], "utf8");
const cenario = new Function("ui", "document", "$", "acharTag", "textoDe", "fetches", "espera", "return (async () => {" + corpo + "})();");
cenario(mod.exports, document, $, acharTag, textoDe, fetches, espera)
  .then(r => process.stdout.write(JSON.stringify(r)))
  .catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def rodar_js(tmp_path, corpo):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node não instalado: os testes do app.js precisam dele.")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    (tmp_path / "cenario.js").write_text(corpo, encoding="utf-8")
    r = _RUN_REAL([node, str(tmp_path / "harness.js"), str(APP_JS), str(tmp_path / "cenario.js")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_js_campo_do_nome_nao_some_a_cada_atualizacao_da_fila(tmp_path):   # 40 (+ 49: lista de jogadores)
    r = rodar_js(tmp_path, """
      const job = { id: "j1", nome: "partida.dem", tipo: "demo", estado: "precisa_jogador", mensagem: "Digite", jogadores: "Dollar (20 abates)" };
      ui.renderizarFila({ itens: [job], ativo: null, progresso: {} });
      const lista = $("#queue-list");
      const campo1 = acharTag(lista, "INPUT")[0];
      campo1.focus(); campo1.value = "Dol"; campo1.dispatch("input");
      ui.renderizarFila({ itens: [{ ...job }], ativo: null, progresso: {} });
      const campo2 = acharTag(lista, "INPUT")[0];
      ui.renderizarFila({ itens: [{ ...job, mensagem: "outra" }], ativo: null, progresso: {} });
      const campo3 = acharTag(lista, "INPUT")[0];
      return { mesmo: campo1 === campo2, valor2: campo2.value, redesenhou: campo3 !== campo1, valor3: campo3.value,
               foco: document.activeElement === campo3, jogadores: textoDe(lista).includes("Dollar (20 abates)") };
    """)
    assert r == {"mesmo": True, "valor2": "Dol", "redesenhou": True, "valor3": "Dol", "foco": True, "jogadores": True}


def test_js_andamento_mostra_o_job_que_deu_erro_e_seus_detalhes(tmp_path):   # 42
    r = rodar_js(tmp_path, """
      const A = { id: "a", nome: "A.mp4", estado: "erro", mensagem: "Não deu certo.", iniciado_em: 5, terminado_em: 10, detalhes: ["Traceback do A"], progresso: { video: "A.mp4" } };
      const B = { id: "b", nome: "B.mp4", estado: "pronto", mensagem: "Pronto!", iniciado_em: 12, terminado_em: 20, detalhes: [], progresso: {} };
      const resp = { itens: [A, B], ativo: null, progresso: { video: "B.mp4", estado: "pronto" } };
      ui.renderizarProgresso(resp); await espera();
      ui.renderizarProgresso(resp); await espera();
      ui.renderizarProgresso(resp);
      const terminado = { titulo: $("#progress-video").textContent, selo: $("#progress-state").textContent,
        detalhes: $("#details-text").textContent, buscasLog: fetches.length };
      ui.renderizarProgresso({ itens: [], ativo: null, externo: { pid: 1 }, progresso: { estado: "rodando", video: "X.mp4", etapa: "Transcrevendo" } });
      return { ...terminado, externo: $("#progress-video").textContent, seloExterno: $("#progress-state").textContent };
    """)
    assert r["titulo"] == "A.mp4" and r["selo"] == "ERRO"
    assert "Traceback do A" in r["detalhes"] and "linha do log" in r["detalhes"]
    assert r["buscasLog"] == 1
    assert r["externo"] == "X.mp4" and r["seloExterno"] == "PROCESSANDO"


def test_js_so_partidas_nao_esconde_tudo_quando_nao_ha_partida(tmp_path):   # 43
    r = rodar_js(tmp_path, """
      const f = { tipo: "todos", emocao: "todas", soPartidas: true, soAprovados: false, mostrarDescartados: false };
      const sem = ui.filtrarMomentos([{ tipo: "abate", parte_tipo: "deathmatch" }, { tipo: "abate" }], f);
      const com = ui.filtrarMomentos([{ tipo: "abate", parte_tipo: "partida" }, { tipo: "abate", parte_tipo: "deathmatch" }], f);
      return { sem: sem.itens.length, semPartida: sem.semPartida, com: com.itens.length, comAviso: com.semPartida };
    """)
    assert r == {"sem": 2, "semPartida": True, "com": 1, "comAviso": False}


def test_js_faixas_da_linha_do_tempo_ficam_no_lugar_certo(tmp_path):   # 51
    r = rodar_js(tmp_path, "return [ui.posicaoParte(30, 60, 120), ui.posicaoParte(110, 200, 120)];")
    assert r == [{"left": 25, "width": 25}, {"left": 110 / 120 * 100, "width": 100 - 110 / 120 * 100}]


def test_js_espaco_no_player_toca_em_vez_de_fechar(tmp_path):   # 50
    r = rodar_js(tmp_path, 'return ["BUTTON", "VIDEO", "INPUT", "TEXTAREA"].map(t => ui.espacoTocaPlayer(t));')
    assert r == [True, True, False, False]
    js = APP_JS.read_text(encoding="utf-8")
    assert '$("#close-player").focus()' not in js and '$("#moment-player").focus()' in js


def test_js_biblioteca_marca_resultado_de_demo(tmp_path):   # 44
    r = rodar_js(tmp_path, """
      ui.state.biblioteca = [{ id: "partida", arquivo: "partida.dem", duracao: 1800, momentos: 3, abates: 7 }, { id: "v1", arquivo: "live.mp4" }];
      ui.renderizarBiblioteca();
      const cartoes = $("#library-grid").children;
      return cartoes.map(c => textoDe(c).includes("DEMO"));
    """)
    assert r == [True, False]


def test_js_avisa_envio_grande_por_arrastar(tmp_path):   # 46
    r = rodar_js(tmp_path, "return [ui.avisoEnvioGrande(3 * 1024 ** 3), ui.avisoEnvioGrande(1024)];")
    assert "Escolher no computador" in r[0] and r[1] == ""
