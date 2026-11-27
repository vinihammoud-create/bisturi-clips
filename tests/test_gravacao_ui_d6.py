"""D6 (Plano D): a tela "Gravar pela demo" e o gerenciador que roda o gravador e o baixador em segundo plano. Sem CS2, sem Steam, sem rede."""
import json
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from cs2clip import sharecode
from cs2clip.demo import Abate, PartidaDemo, RoundDemo
from ui.gravacao import GerenciadorGravacao

from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

DEMO = "match730_003846488745834446879_2134712485_201.dem"
DEMO2 = "match730_003849321203751714819_0599033075_201.dem"
CODIGO = sharecode.codificar(3230642215713767580, 3230647599455273103, 55788)
T = 64.0


def _raiz_com_demos(tmp_path):
    raiz = tmp_path / "proj"
    (raiz / "demos").mkdir(parents=True)
    (raiz / "demos" / DEMO).write_bytes(b"PBDEMS2" * 171_429)          # ~1,2 MB
    replays = tmp_path / "replays"
    replays.mkdir()
    (replays / DEMO2).write_bytes(b"PBDEMS2" * 300)
    (replays / (DEMO2 + ".info")).write_bytes(b"{}")
    (replays / "match730_ruim.dem").write_bytes(b"x")                       # nome fora do padrão: ignorado
    (replays / "outra_coisa.txt").write_bytes(b"x")
    return raiz, replays


class _Proc:
    """Processo falso: devolve linhas e só termina quando mandarem (ou quando acabar a lista, se `termina=True`)."""
    def __init__(self, linhas=("a", "b"), codigo=0, termina=True):
        self.stdout = iter(linhas)
        self._codigo, self.sinais, self._fim = codigo, [], threading.Event()
        if termina:
            self._fim.set()

    def wait(self, timeout=None):
        self._fim.wait(10)
        return self._codigo

    def send_signal(self, s):
        self.sinais.append(s)
        self._fim.set()

    def terminate(self):
        self.sinais.append("terminate")
        self._fim.set()

    def poll(self):
        return self._codigo if self._fim.is_set() else None


def _espera(g, estado, tempo=5.0):
    fim = time.time() + tempo
    while time.time() < fim:
        j = g.estado()["job"]
        if j and j["estado"] == estado:
            return j
        time.sleep(0.02)
    raise AssertionError(f"não chegou a '{estado}': {g.estado()}")


# ── demos ─────────────────────────────────────────────────────────────────────────────────────────

def test_lista_demos_do_bisturi_e_do_cs2_so_com_nome_de_demo_do_valve_mais_nova_primeiro(tmp_path):
    raiz, replays = _raiz_com_demos(tmp_path)
    g = GerenciadorGravacao(raiz, pasta_replays=lambda: replays)
    demos = g.demos()
    assert {d["nome"] for d in demos} == {DEMO, DEMO2}
    por_nome = {d["nome"]: d for d in demos}
    assert por_nome[DEMO]["origem"] == "Bisturi" and por_nome[DEMO2]["origem"] == "CS2" and por_nome[DEMO]["mb"] > 0
    assert "_ordem" not in demos[0]
    assert GerenciadorGravacao(raiz, pasta_replays=lambda: None).demos()[0]["nome"] == DEMO                  # sem a pasta do CS2 só as do Bisturi


def test_caminho_da_demo_recusa_traversal_nome_fora_do_padrao_e_demo_que_nao_existe(tmp_path):
    raiz, replays = _raiz_com_demos(tmp_path)
    g = GerenciadorGravacao(raiz, pasta_replays=lambda: replays)
    assert g.caminho_da_demo(DEMO) == raiz / "demos" / DEMO and g.caminho_da_demo(DEMO2) == replays / DEMO2         # CONTROLE
    for ruim in ("../x.dem", "..\\..\\Windows\\win.ini", "C:\\x.dem", "match730_ruim.dem", "", None, DEMO + "/../x", DEMO.upper().replace("MATCH730", "match730") + "x"):
        with pytest.raises(ValueError):
            g.caminho_da_demo(ruim)
    with pytest.raises(FileNotFoundError):
        g.caminho_da_demo("match730_000000000000000000001_0000000002_3.dem")


def test_demo_de_campeonato_com_nome_livre_so_vale_na_pasta_do_bisturi(tmp_path):
    # pedido do dono (17/11/2026): gravar uma demo de campeonato (HLTV), que não tem nome match730_
    raiz, replays = _raiz_com_demos(tmp_path)
    (raiz / "demos" / "timea-vs-timeb-m3-mirage.dem").write_bytes(b"PBDEMS2" * 300)
    (replays / "outra-pro.dem").write_bytes(b"PBDEMS2" * 300)                     # na pasta do CS2, nome livre continua ignorado
    g = GerenciadorGravacao(raiz, pasta_replays=lambda: replays)
    nomes = {d["nome"] for d in g.demos()}
    assert "timea-vs-timeb-m3-mirage.dem" in nomes and "outra-pro.dem" not in nomes and DEMO in nomes
    assert g.caminho_da_demo("timea-vs-timeb-m3-mirage.dem") == raiz / "demos" / "timea-vs-timeb-m3-mirage.dem"
    for ruim in ("outra-pro.dem", "a b.dem", "x;y.dem", "-x.dem", "a.b.dem", "../timea-vs-timeb-m3-mirage.dem"):
        with pytest.raises(ValueError):
            g.caminho_da_demo(ruim)


def test_escolher_o_jogador_observado_no_plano_e_na_gravacao(tmp_path, monkeypatch):
    raiz, replays = _raiz_com_demos(tmp_path)
    pedidos = []

    def ler(caminho, quem=None):
        pedidos.append(quem)
        p = _partida()
        if quem == "ninguem":
            p.jogador = None
        else:
            p.nome = "Astro" if quem == "765" else p.nome
        return p
    monkeypatch.setattr("cs2clip.demo.ler_demo", ler)
    monkeypatch.setattr("cs2clip.demo.listar_jogadores", lambda caminho: [("Astro", 765, 20), ("Brisa", 766, 9)])
    comandos = []
    g = GerenciadorGravacao(raiz, popen_factory=lambda cmd, **kw: comandos.append(cmd) or _Proc(), python="PY", pasta_replays=lambda: replays)
    assert g.jogadores(DEMO) == [{"nome": "Astro", "steamid": "765", "abates": 20}, {"nome": "Brisa", "steamid": "766", "abates": 9}]
    p = g.plano(DEMO, "765")
    assert pedidos[-1] == "765" and p["jogador"] == "Astro" and p["escolhido"] is True
    assert g.plano(DEMO)["escolhido"] is False                                     # CONTROLE: sem escolha, segue você
    with pytest.raises(ValueError, match="Não achei o jogador"):
        g.plano(DEMO, "ninguem")
    with pytest.raises(ValueError):
        g.plano(DEMO, "x" * 61)
    g.iniciar_gravacao(DEMO, todos=True, jogador="-Astro")
    _espera(g, "pronto")
    assert "--jogador=-Astro" in comandos[0]                                    # com "=": nome com "-" não vira opção
    g.iniciar_gravacao(DEMO, todos=True)
    _espera(g, "pronto")
    assert not any(a.startswith("--jogador") for a in comandos[1])
    with pytest.raises(ValueError):
        g.iniciar_gravacao(DEMO, jogador="a\nb")


def test_gravador_aceita_jogador_e_lista_os_jogadores(tmp_path, monkeypatch, capsys):
    from cs2clip import gravador_demo
    cfg = ["--config", str(tmp_path / "config.toml")]          # nunca o config.toml do dono
    monkeypatch.setattr("cs2clip.demo.listar_jogadores", lambda caminho: [("Astro", 765, 20)])
    assert gravador_demo.main(["--demo", "x.dem", "--listar-jogadores", *cfg]) == 0
    assert "Astro" in capsys.readouterr().out

    def ler(caminho, quem=None):
        p = _partida()
        p.jogador = None
        p.jogadores = {765: "Astro"}
        return p
    monkeypatch.setattr("cs2clip.demo.ler_demo", ler)
    assert gravador_demo.main(["--demo", "x.dem", "--jogador=ninguem", "--so-checar", *cfg]) == 1
    assert 'Não achei o jogador "ninguem"' in capsys.readouterr().out


def test_tela_tem_a_escolha_do_jogador():
    raiz = Path(__file__).resolve().parent.parent / "ui" / "static"
    html = (raiz / "index.html").read_text(encoding="utf-8")
    js = (raiz / "app.js").read_text(encoding="utf-8")
    assert 'id="grav-jogador"' in html and "/api/gravacao/jogadores" in js and "jogador: jogadorDoPlano()" in js


def test_js_plano_velho_de_outro_jogador_nunca_vale_e_o_clique_grava_o_jogador_do_plano(tmp_path):
    # revisão de 17/11 (M2): trocar X -> Y no "Câmera em"; o plano de X chega DEPOIS do de Y. O botão tem de gravar os rounds de Y com a câmera em Y.
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      const json = d => ({ ok: true, headers: { get: () => "application/json" }, json: async () => d });
      let soltarX; const planoX = new Promise(res => { soltarX = res; });
      const gravados = [];
      cfg.fetch = async (url, op) => {
        if (url.includes("/api/gravacao/plano") && url.includes("jogador=X")) { await planoX;
          return json({ partida: "m", jogador: "X", escolhido: true, steamid: "X", minimo_abates: 3, rounds: [{ round: 17, abates: 3, duracao_s: 50, bom: true }], minutos_bons: 1, minutos_todos: 30 }); }
        if (url.includes("/api/gravacao/plano")) return json({ partida: "m", jogador: "Y", escolhido: true, steamid: "Y", minimo_abates: 3,
          rounds: [{ round: 5, abates: 4, duracao_s: 60, bom: true }], minutos_bons: 1, minutos_todos: 30 });
        if (url.includes("/api/gravacao/gravar")) { gravados.push(JSON.parse(op.body)); return json({}); }
        return json({ itens: [], jogadores: [], existe: false, linhas: [] });
      };
      ui.grav.demo = "d.dem";
      const sel = $("#grav-jogador");
      sel.value = "X"; sel.dispatch("change");
      sel.value = "Y"; sel.dispatch("change");
      for (let i = 0; i < 20; i++) await new Promise(r => setTimeout(r, 0));
      soltarX();                                                   // a resposta velha (X) chega por último
      for (let i = 0; i < 20; i++) await new Promise(r => setTimeout(r, 0));
      $("#grav-gravar-bons").dispatch("click");
      for (let i = 0; i < 20; i++) await new Promise(r => setTimeout(r, 0));
      return { plano: ui.grav.plano && ui.grav.plano.steamid, gravou: gravados[0] || null };
    """)
    assert r["plano"] == "Y"
    assert r["gravou"]["rounds"] == "5" and r["gravou"]["jogador"] == "Y"


# ── plano e checklist ─────────────────────────────────────────────────────────────────────────────

def _partida():
    rounds = [RoundDemo(n, 100.0 * n, 100.0 * n + 60.0, "CT", "x", tick_inicio=int(100 * n * T), tick_fim=int((100 * n + 60) * T)) for n in (1, 2, 3)]
    p = PartidaDemo("de_mirage", {1: "eu"}, 1, "Fulano Exemplo", rounds, patch_version="14191", user_id=5, accountid=111111111)
    for n, k in {1: 1, 2: 4, 3: 3}.items():
        for i in range(k):
            t = 100.0 * n + 10 + 4 * i
            p.abates.append(Abate(t, n, "x", "ak47", tick=int(t * T)))
    p.placar = (5, 13)
    return p


def test_plano_mostra_os_rounds_bons_e_o_tempo_e_recusa_quem_nao_aparece_na_demo(tmp_path, monkeypatch):
    raiz, replays = _raiz_com_demos(tmp_path)
    monkeypatch.setattr("cs2clip.demo.ler_demo", lambda caminho, quem=None: _partida())
    g = GerenciadorGravacao(raiz, pasta_replays=lambda: replays)
    p = g.plano(DEMO)
    assert p["partida"] == "de_mirage 5x13" and p["jogador"] == "Fulano Exemplo" and p["patch"] == "14191" and p["minimo_abates"] == 3
    assert [(r["round"], r["abates"], r["bom"]) for r in p["rounds"]] == [(1, 1, False), (2, 4, True), (3, 3, True)]
    assert p["minutos_bons"] == pytest.approx(2.3, abs=0.1) and p["minutos_todos"] > p["minutos_bons"]               # 2 rounds bons de 65 s contra 3
    sem_jogador = _partida()
    sem_jogador.jogador = None
    monkeypatch.setattr("cs2clip.demo.ler_demo", lambda caminho, quem=None: sem_jogador)
    with pytest.raises(ValueError, match="não aparece"):
        g.plano(DEMO)


def test_plano_com_demo_que_o_leitor_nao_entende_vira_mensagem_e_nao_erro(tmp_path, monkeypatch):
    raiz, replays = _raiz_com_demos(tmp_path)
    g = GerenciadorGravacao(raiz, pasta_replays=lambda: replays)

    def quebra(caminho, quem=None):
        raise KeyError("coluna que a demo nova não tem")
    monkeypatch.setattr("cs2clip.demo.ler_demo", quebra)
    with pytest.raises(ValueError, match="Não consegui ler essa demo"):
        g.plano(DEMO)
    monkeypatch.setattr("cs2clip.demo.ler_demo", lambda caminho, quem=None: _partida())          # CONTROLE: demo boa continua funcionando
    assert g.plano(DEMO)["partida"] == "de_mirage 5x13"


def test_checklist_devolve_itens_simples(tmp_path, monkeypatch):
    raiz, replays = _raiz_com_demos(tmp_path)
    from cs2clip import gravador_demo
    monkeypatch.setattr(gravador_demo, "checklist", lambda demo, cfg, dep=None: [("HLAE instalado", True, "ok"), ("Steam aberto", False, "abra o Steam")])
    g = GerenciadorGravacao(raiz, pasta_replays=lambda: replays)
    assert g.checklist(DEMO) == [{"item": "HLAE instalado", "ok": True, "detalhe": "ok"}, {"item": "Steam aberto", "ok": False, "detalhe": "abra o Steam"}]
    assert len(g.checklist()) == 2                                                                              # sem demo escolhida também funciona


# ── trabalhos em segundo plano ────────────────────────────────────────────────────────────────────

def test_iniciar_gravacao_monta_o_comando_certo_le_o_log_e_marca_pronto_ou_erro(tmp_path):
    raiz, replays = _raiz_com_demos(tmp_path)
    comandos = []

    def popen(cmd, **kw):
        comandos.append((cmd, kw))
        return _Proc(["Abrindo o CS2 pelo HLAE...", "Round 3: gravando"], codigo=0)
    g = GerenciadorGravacao(raiz, popen_factory=popen, python="PY", pasta_replays=lambda: replays)
    g.iniciar_gravacao(DEMO2, rounds="3,8,9")
    j = _espera(g, "pronto")
    cmd, kw = comandos[0]
    assert cmd == ["PY", "-m", "cs2clip.gravador_demo", "--demo", str(replays / DEMO2), "--rounds", "3,8,9"] and kw["cwd"] == str(raiz)
    assert g.estado()["linhas"] == ["Abrindo o CS2 pelo HLAE...", "Round 3: gravando"] and j["codigo"] == 0
    g.iniciar_gravacao(DEMO, todos=True)                                                                          # tudo: --todos, sem --rounds
    assert comandos[1][0][-1] == "--todos" and "--rounds" not in comandos[1][0]
    _espera(g, "pronto")
    g2 = GerenciadorGravacao(raiz, popen_factory=lambda cmd, **kw: _Proc(["falhou"], codigo=1), pasta_replays=lambda: replays)       # CONTROLE: erro
    g2.iniciar_gravacao(DEMO)
    assert _espera(g2, "erro")["codigo"] == 1


def test_rounds_invalidos_codigo_ruim_e_demo_desconhecida_nunca_chegam_ao_processo(tmp_path):
    raiz, replays = _raiz_com_demos(tmp_path)
    lancados = []
    g = GerenciadorGravacao(raiz, popen_factory=lambda cmd, **kw: lancados.append(cmd) or _Proc(), pasta_replays=lambda: replays)
    for rounds in ("3;calc", "a,b", "3,,4", "1" * 6, "3 4", "$(x)"):
        with pytest.raises(ValueError):
            g.iniciar_gravacao(DEMO, rounds=rounds)
    with pytest.raises(ValueError):
        g.iniciar_gravacao("../../x.dem")
    for codigo in ("CSGO-curto", "x; calc", "CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2x!"):
        with pytest.raises(ValueError, match="formato"):
            g.iniciar_download(codigo=codigo)
    with pytest.raises(ValueError, match="1 a 8"):
        g.iniciar_download(ultima=9)
    assert lancados == []


def test_download_por_codigo_por_numero_e_so_listar(tmp_path):
    raiz, replays = _raiz_com_demos(tmp_path)
    comandos = []
    g = GerenciadorGravacao(raiz, popen_factory=lambda cmd, **kw: comandos.append(cmd) or _Proc(), python="PY", pasta_replays=lambda: replays)
    g.iniciar_download(codigo=f"  {CODIGO}  ")
    _espera(g, "pronto")
    g.iniciar_download(ultima=2)
    _espera(g, "pronto")
    g.iniciar_download(so_listar=True)
    _espera(g, "pronto")
    assert comandos == [["PY", "-m", "cs2clip.baixar_demo", "codigo", CODIGO, "--baixar"], ["PY", "-m", "cs2clip.baixar_demo", "ultimas", "--baixar", "2"],
                        ["PY", "-m", "cs2clip.baixar_demo", "ultimas"]]


def test_um_trabalho_de_cada_vez_e_cancelar_manda_o_sinal_certo(tmp_path):
    raiz, replays = _raiz_com_demos(tmp_path)
    proc = _Proc(termina=False)
    g = GerenciadorGravacao(raiz, popen_factory=lambda cmd, **kw: proc, pasta_replays=lambda: replays)
    g.iniciar_gravacao(DEMO)
    with pytest.raises(RuntimeError, match="em andamento"):
        g.iniciar_gravacao(DEMO)
    with pytest.raises(RuntimeError, match="em andamento"):
        g.iniciar_download(so_listar=True)
    assert g.cancelar()["cancelado"] is True
    assert _espera(g, "cancelado")["estado"] == "cancelado"
    esperado = signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT
    assert proc.sinais[0] == esperado                                                                           # o gravador fecha o jogo antes de sair
    with pytest.raises(RuntimeError, match="Não há trabalho"):
        g.cancelar()
    g.iniciar_gravacao(DEMO)                                                                                    # e a fila voltou a aceitar


def test_resultados_leem_o_registro_da_gravacao_da_demo(tmp_path):
    raiz, replays = _raiz_com_demos(tmp_path)
    g = GerenciadorGravacao(raiz, pasta_replays=lambda: replays)
    assert g.resultados(DEMO) == {"existe": False}
    pasta = raiz / "gravacao_demo" / Path(DEMO).stem
    pasta.mkdir(parents=True)
    (pasta / "registro.json").write_text(json.dumps({"resultados": [{"round": 12, "duracao_s": 108.0, "velocidade_x": 1.8, "camera_ok": True, "problemas": []}],
                                                     "alertas": ["x"], "guarda": {"ok": True, "vermelho": []}}), encoding="utf-8")
    r = g.resultados(DEMO)
    assert r["existe"] and r["resultados"][0]["round"] == 12 and r["guarda"]["ok"] and r["pasta"].endswith("rounds")
    (pasta / "registro.json").write_text("{quebrado", encoding="utf-8")
    assert g.resultados(DEMO) == {"existe": False}


# ── endpoints e tela ──────────────────────────────────────────────────────────────────────────────

def test_endpoints_exigem_token_no_post_recusam_demo_desconhecida_e_devolvem_o_estado(tmp_path, monkeypatch):
    criar_projeto(tmp_path)
    raiz, replays = tmp_path, tmp_path / "replays"
    replays.mkdir()
    (replays / DEMO2).write_bytes(b"PBDEMS2" * 10)
    lancados = []
    monkeypatch.setattr("cs2clip.demo.ler_demo", lambda caminho, quem=None: _partida())
    with cliente(raiz, gravacao_popen_factory=lambda cmd, **kw: lancados.append(cmd) or _Proc(["oi"]), gravacao_replays=lambda: replays) as c:
        h = token_headers(c)
        r = c.get("/api/gravacao", headers=token_headers(c)).json()
        assert [d["nome"] for d in r["demos"]] == [DEMO2] and r["job"] is None
        plano = c.get("/api/gravacao/plano", params={"demo": DEMO2}, headers=token_headers(c)).json()
        assert [x["round"] for x in plano["rounds"] if x["bom"]] == [2, 3]
        assert c.get("/api/gravacao/plano", params={"demo": "../x.dem"}, headers=token_headers(c)).status_code == 400
        assert c.get("/api/gravacao/plano", params={"demo": "match730_000000000000000000001_0000000002_3.dem"}, headers=token_headers(c)).status_code == 404
        assert c.post("/api/gravacao/gravar", json={"demo": DEMO2, "rounds": "2,3"}).status_code == 403                  # sem token
        assert c.post("/api/gravacao/gravar", json={"demo": "../x.dem"}, headers=h).status_code == 400
        assert c.post("/api/gravacao/gravar", json={"demo": DEMO2, "rounds": "2;calc"}, headers=h).status_code == 400
        assert c.post("/api/gravacao/baixar", json={"codigo": "CSGO-ruim"}, headers=h).status_code == 400
        assert lancados == []                                                                                             # nada ruim chegou ao processo
        ok = c.post("/api/gravacao/gravar", json={"demo": DEMO2, "rounds": "2,3"}, headers=h)
        assert ok.status_code == 200 and ok.json()["tipo"] == "gravacao"
        fim = time.time() + 5
        while time.time() < fim and (c.get("/api/gravacao/estado", headers=token_headers(c)).json()["job"] or {}).get("estado") == "rodando":
            time.sleep(0.02)
        estado = c.get("/api/gravacao/estado", headers=token_headers(c)).json()
        assert estado["job"]["estado"] == "pronto" and estado["linhas"] == ["oi"]
        assert c.post("/api/gravacao/cancelar", json={}, headers=h).status_code == 409                                  # nada em andamento
        assert c.get("/api/gravacao/resultados", params={"demo": DEMO2}, headers=token_headers(c)).json() == {"existe": False}


def test_tela_tem_o_menu_os_tres_passos_e_o_js_chama_as_rotas():
    raiz = Path(__file__).resolve().parents[1] / "ui" / "static"
    html, js = (raiz / "index.html").read_text(encoding="utf-8"), (raiz / "app.js").read_text(encoding="utf-8")
    for trecho in ('id="nav-gravar"', 'id="view-gravar"', 'id="grav-demos"', 'id="grav-codigo"', 'id="grav-gravar-bons"', 'id="grav-gravar-todos"',
                   'id="grav-cancelar"', 'id="grav-log"', "conta do Bisturi", "administrador"):
        assert trecho in html
    assert "http://" not in html and "https://" not in html
    for trecho in ("/api/gravacao/plano", "/api/gravacao/checklist", "/api/gravacao/gravar", "/api/gravacao/baixar", "/api/gravacao/cancelar",
                   '"gravar"', "carregarGravacao"):
        assert trecho in js
    assert 'rounds: bons' in js                                                                                          # grava só os rounds bons do plano


def test_gravador_instala_o_tratador_de_ctrl_break_no_windows():
    """O cancelar da tela manda CTRL+BREAK; sem este tratador o Python morreria sem fechar o jogo nem conferir os arquivos."""
    from cs2clip import gravador_demo
    with pytest.raises(KeyboardInterrupt):
        gravador_demo._quebra_vira_interrupcao()
    fonte = Path(gravador_demo.__file__).read_text(encoding="utf-8")
    assert "signal.SIGBREAK" in fonte and "_quebra_vira_interrupcao" in fonte


def test_gravar_ja_montando_passa_o_editar_e_a_tela_tem_a_caixa(tmp_path):
    raiz, replays = _raiz_com_demos(tmp_path)
    comandos = []
    g = GerenciadorGravacao(raiz, popen_factory=lambda cmd, **kw: comandos.append(cmd) or _Proc(), python="PY", pasta_replays=lambda: replays)
    g.iniciar_gravacao(DEMO2, rounds="3,8", editar=True)
    _espera(g, "pronto")
    assert comandos[0][-3:] == ["--rounds", "3,8", "--editar"]
    g.iniciar_gravacao(DEMO2, rounds="3,8")                                                                       # CONTROLE: sem a opção, sem --editar
    _espera(g, "pronto")
    assert "--editar" not in comandos[1]
    raiz_estatica = Path(__file__).resolve().parents[1] / "ui" / "static"
    assert 'id="grav-editar"' in (raiz_estatica / "index.html").read_text(encoding="utf-8")
    assert "editar: $(\"#grav-editar\").checked" in (raiz_estatica / "app.js").read_text(encoding="utf-8")


# ── revisão de 17/11: pasta por jogador, câmera, nome ambíguo e nomes reservados ──────────────────

def test_pasta_por_jogador_e_registro_de_outro_jogador_nunca_e_reaproveitado(tmp_path):
    from cs2clip.demo import pasta_da_partida
    from cs2clip.gravador_demo import _rounds_ja_gravados
    assert pasta_da_partida("d") == "d" and pasta_da_partida("d", 765) == "d__765" and pasta_da_partida("d", "765") == "d__765"
    video = tmp_path / "r17.mp4"
    video.write_bytes(b"x")
    reg = tmp_path / "registro.json"
    reg.write_text(json.dumps({"jogador": 765, "trechos": [{"round": 17}], "resultados": [{"round": 17, "arquivo": str(video)}]}), encoding="utf-8")
    assert _rounds_ja_gravados(reg, {5}, 765)["resultados"]                 # mesmo jogador: o round 17 fica
    assert _rounds_ja_gravados(reg, {5}, 766) == {"trechos": [], "resultados": []}   # outro jogador: nada é reaproveitado
    assert _rounds_ja_gravados(reg, {5})["resultados"]                      # CONTROLE: chamada antiga (sem jogador) como antes


def test_resultados_da_tela_leem_a_pasta_do_jogador(tmp_path):
    raiz, replays = _raiz_com_demos(tmp_path)
    for pasta, rodada in ((Path(DEMO).stem, 3), (Path(DEMO).stem + "__765", 17)):
        (raiz / "gravacao_demo" / pasta).mkdir(parents=True)
        (raiz / "gravacao_demo" / pasta / "registro.json").write_text(json.dumps({"resultados": [{"round": rodada}]}), encoding="utf-8")
    g = GerenciadorGravacao(raiz, pasta_replays=lambda: replays)
    assert g.resultados(DEMO)["resultados"] == [{"round": 3}] and g.resultados(DEMO, "765")["resultados"] == [{"round": 17}]
    with pytest.raises(ValueError):
        g.resultados(DEMO, "../x")


def test_camera_confirma_so_com_os_abates_do_jogador_no_mesmo_ritmo(monkeypatch, tmp_path):
    from types import SimpleNamespace as NS
    from cs2clip import gravador_demo as gd, scan
    from cs2clip.demo import TrechoGravacao
    assert gd.abates_que_batem([12.0, 20.5, 31.2], [10.0, 18.4, 29.0]) == 3                  # vídeo 2 s adiantado: o ritmo bate
    assert gd.abates_que_batem([12.0, 40.0], [10.0, 18.4, 29.0]) == 1                        # só uma caixa no ritmo
    assert gd.abates_que_batem([], [10.0]) == 0
    tr = TrechoGravacao(17, 0, 64 * 60, 3, [64 * 10, int(64 * 18.4), 64 * 29])
    caixas = []
    monkeypatch.setattr(scan, "probe", lambda p: None)
    monkeypatch.setattr(scan, "scan_video_fast", lambda info: [NS(first=t) for t in caixas])
    caixas[:] = [11.0, 19.4, 30.0, 33.0]                                                      # os 3 abates + a própria morte
    assert gd._verificar_camera_por_kill_feed(tmp_path / "c.mp4", tr)[0] is True
    caixas[:] = [11.0, 19.4]                                                                  # perdeu 1 caixa num round de 3: ainda confirma
    assert gd._verificar_camera_por_kill_feed(tmp_path / "c.mp4", tr)[0] is True
    caixas[:] = [45.0]                                                                        # outro jogador com 1 abate: antes "confirmada"; agora "confira"
    assert gd._verificar_camera_por_kill_feed(tmp_path / "c.mp4", tr)[0] is None
    monkeypatch.setattr(scan, "scan_video_fast", lambda info: [])
    monkeypatch.setattr(scan, "scan_video", lambda info: [])
    assert gd._verificar_camera_por_kill_feed(tmp_path / "c.mp4", tr)[0] is False            # nenhuma caixa: câmera errada (tenta a próxima)


def test_nome_ambiguo_do_jogador_vira_erro_com_a_lista(tmp_path, monkeypatch, capsys):
    from cs2clip import gravador_demo
    js = {1: "Astro", 2: "sh1ro", 3: "yuki"}
    assert gravador_demo.jogadores_ambiguos(js, "s") == ["Astro", "sh1ro"]
    assert gravador_demo.jogadores_ambiguos(js, "sh1ro") == [] and gravador_demo.jogadores_ambiguos(js, "2") == [] and gravador_demo.jogadores_ambiguos(js, "yuu") == []

    def ler(caminho, quem=None):
        p = _partida()
        p.jogadores = js
        return p
    monkeypatch.setattr("cs2clip.demo.ler_demo", ler)
    assert gravador_demo.main(["--demo", "x.dem", "--jogador=s", "--so-checar", "--config", str(tmp_path / "c.toml")]) == 1
    assert "mais de um jogador" in capsys.readouterr().out


def test_nomes_reservados_do_windows_sao_recusados(tmp_path):
    raiz, replays = _raiz_com_demos(tmp_path)
    g = GerenciadorGravacao(raiz, pasta_replays=lambda: replays)
    for ruim in ("CON.dem", "nul.dem", "com1.dem", "LPT9.dem", "aux.dem"):
        with pytest.raises(ValueError):
            g.caminho_da_demo(ruim)
    from ui.gravacao import _livre_ok
    assert not any(_livre_ok(n) for n in ("CON.dem", "nul.dem", "com1.dem", "LPT9.dem", "aux.dem", "prn.dem"))
    assert _livre_ok("console.dem") and _livre_ok("timea-vs-timeb-m3-mirage.dem")
    (raiz / "demos" / "console.dem").write_bytes(b"PBDEMS2" * 10)                        # CONTROLE: parecido, mas não reservado
    assert g.caminho_da_demo("console.dem").name == "console.dem"


def test_editar_demo_aceita_jogador(monkeypatch):
    from cs2clip import editar_demo
    pedidos = []
    monkeypatch.setattr(editar_demo, "editar", lambda demo, cfg, **kw: pedidos.append(kw.get("jogador")) or Path("x"))
    monkeypatch.setattr("cs2clip.config.load_config", lambda p: None)
    assert editar_demo.main(["--demo", "d.dem", "--jogador=765"]) == 0 and editar_demo.main(["--demo", "d.dem"]) == 0
    assert pedidos == ["765", None]
