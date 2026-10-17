"""U1 + S5: primeira abertura (boas-vindas com o @ do canal), capa da biblioteca e 'Pronto para postar'."""
import json
import tomllib
from pathlib import Path

from test_r1_ui import rodar_js
from test_ui_configuracao import FakeConfig, load_config
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

RAIZ = Path(__file__).resolve().parents[1]


def _cliente(tmp_path):
    criar_projeto(tmp_path)
    return cliente(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)


# ── primeira abertura ───────────────────────────────────────────────────────────────────────────

def test_boas_vindas_aparece_so_na_primeira_vez_e_grava_o_arroba(tmp_path):
    with _cliente(tmp_path) as c:
        r = c.get("/api/boas-vindas").json()
        assert r == {"mostrar": True, "marca": "@drd0llar"}            # já vem com o @ atual (padrão do programa)
        assert c.post("/api/boas-vindas", headers=token_headers(c), json={"marca": "@meucanal"}).json() == {"sucesso": True}
        assert c.get("/api/boas-vindas").json() == {"mostrar": False}
    assert (tmp_path / "dados" / "boas_vindas.json").is_file()       # marca de "já viu" em dados/ (não versionado)
    assert tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))["shorts"]["marca"] == "@meucanal"
    with cliente(tmp_path, load_config_fn=load_config, config_cls=FakeConfig) as c:   # abrir de novo: não aparece
        assert c.get("/api/boas-vindas").json() == {"mostrar": False}


def test_boas_vindas_aceita_arroba_vazio_e_pular_nao_mexe_na_configuracao(tmp_path):
    with _cliente(tmp_path) as c:
        assert c.post("/api/boas-vindas", headers=token_headers(c), json={}).status_code == 200    # Pular
        assert not (tmp_path / "config.toml").exists()
    (tmp_path / "dados" / "boas_vindas.json").unlink()
    with cliente(tmp_path, load_config_fn=load_config, config_cls=FakeConfig) as c:
        assert c.post("/api/boas-vindas", headers=token_headers(c), json={"marca": "  "}).status_code == 200
    assert tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))["shorts"]["marca"] == ""


def test_boas_vindas_recusa_arroba_invalido_e_continua_pendente(tmp_path):
    with _cliente(tmp_path) as c:
        for ruim in ({"marca": "{\\b1}x"}, {"marca": "x" * 41}, {"marca": 5}):
            r = c.post("/api/boas-vindas", headers=token_headers(c), json=ruim)
            assert r.status_code == 400 and r.json()["erro"]
        assert c.get("/api/boas-vindas").json()["mostrar"] is True
        assert c.post("/api/boas-vindas", json={}).status_code == 403             # sem token: recusado
    assert not (tmp_path / "dados").exists()


# ── capa da biblioteca e "Pronto para postar" ────────────────────────────────────────────────────

def test_capa_da_biblioteca_e_publicar(tmp_path):
    with _cliente(tmp_path) as c:
        assert c.get("/api/indice").json()["itens"][0]["capa"] is None            # momento sem miniatura
        documento = json.loads((tmp_path / "saida" / "v1" / "momentos.json").read_text(encoding="utf-8"))
        documento["momentos"][0]["arquivos"]["miniatura"] = "miniaturas/01.jpg"
        (tmp_path / "saida" / "v1" / "momentos.json").write_text(json.dumps(documento), encoding="utf-8")
        assert c.get("/api/indice").json()["itens"][0]["capa"] == "miniaturas/01.jpg"
        assert c.get("/api/videos/v1").json()["publicar"] is None
        (tmp_path / "saida" / "v1" / "miniaturas" / "02.jpg").write_bytes(b"JPG2")
        publicar = {"video": "v1", "itens": [{"n": 1, "short": "shorts/a.mp4", "capa": "../../config.toml"},
                                              {"n": 1, "short": "shorts/a.mp4", "capa": "miniaturas/02.jpg"}, "lixo"]}
        (tmp_path / "saida" / "v1" / "publicar.json").write_text(json.dumps(publicar), encoding="utf-8")
        assert c.get("/api/indice").json()["itens"][0]["capa"] == "miniaturas/02.jpg"   # caminho para fora é ignorado
        itens = c.get("/api/videos/v1").json()["publicar"]["itens"]
        assert len(itens) == 2 and all(isinstance(i, dict) for i in itens)


# ── tela ─────────────────────────────────────────────────────────────────────────────────────────

def test_tela_tem_nome_logo_tres_passos_boas_vindas_e_credito():
    html = (RAIZ / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    for trecho in ("<title>Bisturi Clips</title>", "/static/logo.png", "/static/emblema.png", 'id="step-1"', 'id="step-2"',
                   'id="step-3"', "Escolher no computador", 'id="welcome-modal"', 'id="welcome-marca"',
                   "feito por Vinicius Hammoud", "licença MIT"):
        assert trecho in html
    assert "http://" not in html and "https://" not in html                       # nada de recurso externo
    css = (RAIZ / "ui" / "static" / "style.css").read_text(encoding="utf-8")
    assert "@import" not in css and "https://" not in css
    celular = css.split("@media (max-width: 760px)", 1)[1]
    assert ".dirty-banner { bottom: 86px; }" in celular                          # não fica atrás da navegação de baixo
    for arquivo in ("logo.png", "emblema.png", "icone.ico", "fontes/Anton-Regular.ttf", "fontes/OFL-Anton.txt"):
        assert (RAIZ / "ui" / "static" / arquivo).is_file()


def test_js_arroba_nome_do_video_e_passos(tmp_path):
    r = rodar_js(tmp_path, """
      const marcas = ["", "  ", "drd0llar", "@drd0llar", " @x ", "a".repeat(39), "a".repeat(40)].map(ui.marcaDoCanal);
      const placares = [ui.resumoPlacar([{ placar: "0x7" }, { placar: "3x6" }], ""), ui.resumoPlacar([{ placar: "13x9" }], ""),
                        ui.resumoPlacar([], "13 × 11"), ui.resumoPlacar(null, ""), ui.resumoPlacar([{}, {}], "")];
      const nomes = [{ arquivo: "2903084427-1393405983-493d28c3-a12b-4615-bb52-4c4e58339a58.mp4" }, { arquivo: "cs2 twitch.mp4" },
                     { arquivo: "minha_live.mkv" }, { id: "v9" }].map(ui.nomeVideo);
      const passos = () => [1, 2, 3].map(n => $(`#step-${n}`).classList.contains("is-active"));
      ui.state.arquivos = []; ui.state.fila = []; ui.atualizarPassos(); const p1 = passos();
      ui.state.arquivos = [{ nome: "a.mp4" }]; ui.atualizarPassos(); const p2 = passos();
      ui.state.arquivos = []; ui.state.fila = [{ estado: "pronto" }]; ui.atualizarPassos(); const p3 = passos();
      return { marcas, placares, nomes, p1, p2, p3 };
    """)
    assert r["marcas"] == ["", "", "@drd0llar", "@drd0llar", "@x", "@" + "a" * 39, "a" * 40]   # nunca passa de 40
    assert r["placares"] == ["2 partidas: 0x7 · 3x6", "Placar 13x9", "Placar 13 × 11", "", "2 partidas"]
    assert r["nomes"] == ["Live 2903084427", "cs2 twitch", "minha live", "v9"]
    assert r["p1"] == [True, False, False] and r["p2"] == [False, True, False] and r["p3"] == [False, False, True]


def test_js_pronto_para_postar_mostra_os_shorts(tmp_path):
    r = rodar_js(tmp_path, """
      ui.state.videoAtual = "v1"; ui.state.momentos = [];
      ui.montarPublicar({ publicar: { itens: [{ n: 1, short: "shorts/a.mp4", capa: "miniaturas/a.jpg", titulos: ["ACE no pistol", "outro"],
        hashtags: ["#CS2"], descricao: "ACE\\n#CS2", nota: 47.6 }, { n: 2, titulos: ["sem short"] }, "lixo"] } });
      const vazio = $("#post-panel").classList.contains("hidden");
      const texto = textoDe($("#post-list"));
      ui.montarPublicar({ publicar: null });
      return { cartoes: $("#post-list").children.length, vazio, texto, escondeSemNada: $("#post-panel").classList.contains("hidden") };
    """)
    assert r["vazio"] is False and "ACE no pistol" in r["texto"] and "NOTA 48" in r["texto"] and "#CS2" in r["texto"]
    assert r["cartoes"] == 0 and r["escondeSemNada"] is True
