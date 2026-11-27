"""E1.2 / E2.2 (Plano E): as rotas da caixa única e dos trabalhos. Servidor real (TestClient), processos e Steam falsos."""
import json
import time
from pathlib import Path

from cs2clip import entrada as ent
from cs2clip import sharecode

from test_receitas_e2 import BISTURI, BISTURI_ID, EDITAR_OK, GRAVAR_OK, PRINCIPAL, Roteiro
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

CODIGO = sharecode.codificar(3230642215713767580, 3230647599455273103, 55788)
DEMO = "match730_003849321203751714819_0599033075_201.dem"


def _replays(tmp_path):
    pasta = tmp_path / "replays"
    pasta.mkdir()
    for nome, atraso in (("match730_003846488745834446879_2134712485_201.dem", 300), (DEMO, 100), ("match730_003852947612143452464_1002297918_202.dem", 0)):
        arq = pasta / nome
        arq.write_bytes(ent.CABECALHO_CS2 + b"corpo")
        agora = time.time() - atraso
        import os
        os.utime(arq, (agora, agora))
    return pasta


def test_reconhecer_devolve_o_tipo_e_o_plano_e_recusa_com_a_frase(tmp_path):
    criar_projeto(tmp_path)
    replays = _replays(tmp_path)
    with cliente(tmp_path, gravacao_replays=lambda: replays, receita_popen_factory=Roteiro(), receita_conta_logada=lambda: BISTURI_ID) as c:
        h = token_headers(c)
        r = c.post("/api/entrada/reconhecer", json={"texto": CODIGO}, headers=h).json()
        assert r["entrada"]["tipo"] == "codigo_partilha" and [e["nome"] for e in r["etapas"]] == ["esperar_conta", "obter_demo", "gravar", "editar"]
        assert r["estimativa_s"] > 0
        v = c.post("/api/entrada/reconhecer", json={"texto": "https://www.twitch.tv/canaldeexemplo"}, headers=h).json()
        assert v["entrada"]["aceita"] is False and "canal" in v["entrada"]["mensagem"] and "etapas" not in v
        pelo_nome = c.post("/api/entrada/reconhecer", json={"texto": DEMO}, headers=h).json()          # só o nome: achado na pasta de replays
        assert pelo_nome["entrada"]["tipo"] == "demo" and Path(pelo_nome["entrada"]["valor"]) == (replays / DEMO).resolve()
        assert c.post("/api/entrada/reconhecer", json={"texto": CODIGO}).status_code == 403              # sem o token da sessão: recusado


def test_ultima_demo_e_a_mais_nova_da_pasta_do_cs2(tmp_path):
    criar_projeto(tmp_path)
    replays = _replays(tmp_path)
    with cliente(tmp_path, gravacao_replays=lambda: replays, receita_popen_factory=Roteiro()) as c:
        d = c.get("/api/entrada/ultima-demo", headers=token_headers(c)).json()["demo"]
        assert d["nome"] == "match730_003852947612143452464_1002297918_202.dem" and d["data"]
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    with cliente(tmp_path, gravacao_replays=lambda: vazia, receita_popen_factory=Roteiro()) as c:
        assert c.get("/api/entrada/ultima-demo", headers=token_headers(c)).json() == {"demo": None}


def test_criar_acompanhar_cancelar_e_retomar_um_trabalho_pelo_servidor(tmp_path):
    criar_projeto(tmp_path)
    replays = _replays(tmp_path)
    (tmp_path / "config.toml").write_text(f'[gravacao_demo]\nconta_steamid = "{BISTURI}"\nconta_principal_id = "{PRINCIPAL}"\n', encoding="utf-8")
    rot = Roteiro(gravador_demo=[(["[parei] CS2 travou."], 1), GRAVAR_OK], editar_demo=[EDITAR_OK])
    with cliente(tmp_path, gravacao_replays=lambda: replays, receita_popen_factory=rot, receita_conta_logada=lambda: BISTURI_ID) as c:
        h = token_headers(c)
        assert c.post("/api/trabalhos", json={"texto": "oi"}, headers=h).status_code == 400                    # entrada que não serve
        assert c.post("/api/trabalhos", json={"texto": DEMO, "rounds": "3; quit"}, headers=h).status_code == 400
        criado = c.post("/api/trabalhos", json={"texto": DEMO, "rounds": "3,8"}, headers=h)
        assert criado.status_code == 200
        tid = criado.json()["id"]
        for _ in range(300):
            t = c.get(f"/api/trabalhos/{tid}", headers=token_headers(c)).json()
            if t["estado"] in ("erro", "pronta"):
                break
            time.sleep(0.01)
        assert t["estado"] == "erro" and "CS2 travou" in t["erro"]
        assert c.post(f"/api/trabalhos/{tid}/cancelar", headers=h).status_code == 409                          # não está rodando
        assert c.post(f"/api/trabalhos/{tid}/retomar", headers=h).status_code == 200
        for _ in range(300):
            t = c.get(f"/api/trabalhos/{tid}", headers=token_headers(c)).json()
            if t["estado"] == "pronta":
                break
            time.sleep(0.01)
        assert t["estado"] == "pronta" and t["resultado"].endswith("x_limpo")
        itens = c.get("/api/trabalhos", headers=token_headers(c)).json()["itens"]
        assert [i["id"] for i in itens] == [tid] and itens[0]["etapas"][1]["nome"] == "gravar"
        assert c.get("/api/trabalhos/naoexiste", headers=token_headers(c)).status_code == 404
        assert c.post("/api/trabalhos/naoexiste/retomar", headers=h).status_code == 404
        assert c.post(f"/api/trabalhos/{tid}/retomar", headers=h).status_code == 409                          # já terminou


def test_pagina_inicial_serve_a_caixa_unica_e_o_script_novo_sem_quebrar_a_politica_de_seguranca(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path, receita_popen_factory=Roteiro()) as c:
        pagina = c.get("/")
        assert pagina.status_code == 200 and 'id="caixa-texto"' in pagina.text and "/static/trabalhos.js" in pagina.text
        assert "__UI_TOKEN__" not in pagina.text                                                      # o token foi colocado na página
        assert "script-src 'self'" in pagina.headers["content-security-policy"]                       # o script novo é arquivo próprio, sem JS na página
        js = c.get("/static/trabalhos.js")
        assert js.status_code == 200 and "BisturiTrabalhos" in js.text and "/api/trabalhos" in js.text


def test_rotas_de_partida_nova_listam_e_dispensam(tmp_path):
    criar_projeto(tmp_path)
    replays = _replays(tmp_path)
    with cliente(tmp_path, gravacao_replays=lambda: replays, receita_popen_factory=Roteiro()) as c:
        h = token_headers(c)
        itens = c.get("/api/partidas-novas", headers=token_headers(c)).json()["itens"]
        assert len(itens) == 3 and itens[0]["nome"] == "match730_003852947612143452464_1002297918_202.dem"
        assert c.post("/api/partidas-novas/dispensar", json={"nome": itens[0]["nome"]}, headers=h).json() == {"sucesso": True}
        assert [i["nome"] for i in c.get("/api/partidas-novas", headers=token_headers(c)).json()["itens"]] == [i["nome"] for i in itens[1:]]
        assert c.post("/api/partidas-novas/dispensar", json={"nome": "../x"}, headers=h).status_code == 400
        assert c.post("/api/partidas-novas/dispensar", json={"nome": itens[1]["nome"]}).status_code == 403          # sem token


def test_inventario_de_rotas_da_interface_nao_perde_nem_ganha_rota_sem_querer(tmp_path):
    """Prova da separação das rotas novas em ui/rotas_novas.py (E8.4): as 51 rotas de antes da mudança continuam as mesmas.
    Rota nova de propósito? Atualize tests/fixtures/rotas_ui.json junto."""
    criar_projeto(tmp_path)
    with cliente(tmp_path, receita_popen_factory=Roteiro()) as c:
        agora = sorted({(m, r.path) for r in c.app.routes if hasattr(r, "methods") for m in r.methods if m not in ("HEAD", "OPTIONS")})
    esperado = sorted(tuple(x) for x in json.loads((Path(__file__).parent / "fixtures" / "rotas_ui.json").read_text(encoding="utf-8")))
    assert agora == esperado, (set(agora) ^ set(esperado))
    assert any(r == ("POST", "/api/musicas/adicionar") for r in agora) and any(r == ("GET", "/api/trabalhos/{trabalho_id}") for r in agora)


def test_rota_confirmar_responde_409_sem_candidato_404_sem_trabalho_e_403_sem_token(tmp_path):
    criar_projeto(tmp_path)
    replays = _replays(tmp_path)
    (tmp_path / "config.toml").write_text(f'[gravacao_demo]\nconta_steamid = "{BISTURI}"\nconta_principal_id = "{PRINCIPAL}"\n', encoding="utf-8")
    with cliente(tmp_path, gravacao_replays=lambda: replays, receita_popen_factory=Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK]),
                 receita_conta_logada=lambda: BISTURI_ID) as c:
        h = token_headers(c)
        r = c.post("/api/trabalhos", json={"texto": DEMO}, headers=h).json()
        assert c.post(f"/api/trabalhos/{r['id']}/confirmar", json={"aceitar": True}, headers=h).status_code == 409
        assert c.post("/api/trabalhos/naoexiste/confirmar", json={}, headers=h).status_code == 404
        assert c.post(f"/api/trabalhos/{r['id']}/confirmar", json={}).status_code == 403


def test_rota_da_gravacao_informa_se_a_lista_das_ultimas_esta_ligada(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path, receita_popen_factory=Roteiro(), gravacao_replays=lambda: None) as c:
        r = c.get("/api/gravacao", headers=token_headers(c)).json()
        assert r["lista_ultimas"] is False                                                           # desligada por padrão (decisão do dono, 08/11)

