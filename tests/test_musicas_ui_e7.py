"""E7.1 / E7.2 (Plano E) + regra G-004 das rotas novas: a tela Músicas e a autorização de arquivos. Servidor real (TestClient), áudio e medição falsos."""
import json
import os
import time

from cs2clip import entrada as ent
from cs2clip import musicas_catalogo as mc

from test_receitas_e2 import BISTURI, BISTURI_ID, EDITAR_OK, GRAVAR_OK, PRINCIPAL, Roteiro
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

DEMO = "match730_003849321203751714819_0599033075_201.dem"


def _projeto(tmp_path):
    criar_projeto(tmp_path)
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    (pasta / "a.mp3").write_bytes(b"x" * 70_000)
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": [
        {"arquivo": "a.mp3", "titulo": "Faixa A", "autor": "Autor A", "licenca": "CC0 1.0", "bpm": 120.0, "drop_s": 30.0, "emocao": 0.8, "duracao_s": 150.0}]}), encoding="utf-8")
    return pasta


def test_listar_ativar_e_tocar_so_o_que_esta_no_catalogo(tmp_path):
    _projeto(tmp_path)
    with cliente(tmp_path, receita_popen_factory=Roteiro()) as c:
        h = token_headers(c)
        r = c.get("/api/musicas", headers=token_headers(c)).json()
        assert r["resumo"] == {"total": 1, "ativas": 1, "com_drop": 1, "no_sorteio": 1} and r["faixas"][0]["titulo"] == "Faixa A"
        assert {x["chave"] for x in r["licencas"]} == {"cc0", "pixabay", "ncs", "streambeats", "yt_audio", "cc_by", "outra"}
        assert next(x for x in r["licencas"] if x["chave"] == "ncs")["credito"] is True and next(x for x in r["licencas"] if x["chave"] == "yt_audio")["credito"] is None
        off = c.post("/api/musicas/ativar", json={"arquivo": "a.mp3", "ativa": False}, headers=h)
        assert off.status_code == 200 and c.get("/api/musicas", headers=token_headers(c)).json()["resumo"]["ativas"] == 0
        assert c.post("/api/musicas/ativar", json={"arquivo": "nao.mp3", "ativa": True}, headers=h).status_code == 404
        assert c.post("/api/musicas/ativar", json={"arquivo": "a.mp3", "ativa": True}).status_code == 403          # sem token
        audio = c.get("/api/musicas/audio/a.mp3")
        assert audio.status_code == 200 and len(audio.content) == 70_000
        assert c.get("/api/musicas/audio/nao_listada.mp3").status_code == 404
        assert c.get("/api/musicas/audio/..%2Fconfig.toml").status_code in (404, 400)                                 # nada de caminho livre
        (tmp_path / "musicas" / "solto.mp3").write_bytes(b"x" * 70_000)                                              # arquivo na pasta mas fora do catálogo
        assert c.get("/api/musicas/audio/solto.mp3").status_code == 404


def test_adicionar_musica_exige_o_seletor_a_licenca_e_o_link_quando_a_licenca_pede(tmp_path, monkeypatch):
    pasta = _projeto(tmp_path)
    origem = tmp_path / "Downloads" / "Fade.mp3"
    origem.parent.mkdir()
    origem.write_bytes(b"x" * 90_000)
    monkeypatch.setattr(mc, "medir_faixa", lambda arq: {"duracao_s": 150.0, "bpm": 128.0, "batidas_detectadas": 300, "energia": 0.8, "drop_s": 40.0,
                                                         "impacto_db": 9.0, "emocao": 0.7})
    com = {"caminho": str(origem), "licenca": "ncs", "autor": "Alan Walker", "titulo": "Fade", "link": "https://ncs.io/Fade"}
    with cliente(tmp_path, receita_popen_factory=Roteiro(), music_chooser=lambda: [{"caminho": str(origem), "nome": "Fade.mp3", "tamanho": 90_000}]) as c:
        h = token_headers(c)
        assert c.post("/api/musicas/adicionar", json=com, headers=h).status_code == 403                               # não veio do seletor
        assert c.post("/api/musicas/escolher", headers=h).json()["itens"][0]["nome"] == "Fade.mp3"                    # agora está autorizado
        sem_link = c.post("/api/musicas/adicionar", json={**com, "link": ""}, headers=h)
        assert sem_link.status_code == 400 and "link da página" in sem_link.json()["erro"]
        sem_lic = c.post("/api/musicas/adicionar", json={**com, "licenca": "comercial"}, headers=h)
        assert sem_lic.status_code == 400 and "nunca" in sem_lic.json()["erro"]
        ok = c.post("/api/musicas/adicionar", json=com, headers=h)
        assert ok.status_code == 200, ok.text
        faixa = ok.json()["faixa"]
        assert faixa["exige_credito"] is True and faixa["credito"].startswith("Música: Alan Walker — Fade") and (pasta / "Fade.mp3").is_file()
        lista = c.get("/api/musicas", headers=token_headers(c)).json()
        assert lista["resumo"]["total"] == 2 and any(f["arquivo"] == "Fade.mp3" and f["exige_credito"] for f in lista["faixas"])
        assert c.post("/api/musicas/adicionar", json=com, headers=h).status_code == 400                               # repetida


def test_arquivo_digitado_fora_do_projeto_so_vale_se_veio_do_seletor_mas_a_demo_do_cs2_vale(tmp_path):
    criar_projeto(tmp_path)
    replays = tmp_path / "replays"
    replays.mkdir()
    (replays / DEMO).write_bytes(ent.CABECALHO_CS2 + b"corpo")
    fora = tmp_path.parent / f"{tmp_path.name}_fora" / DEMO
    fora.parent.mkdir(exist_ok=True)
    fora.write_bytes(ent.CABECALHO_CS2 + b"corpo")
    (tmp_path / "config.toml").write_text(f'[gravacao_demo]\nconta_steamid = "{BISTURI}"\nconta_principal_id = "{PRINCIPAL}"\n', encoding="utf-8")
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    with cliente(tmp_path, gravacao_replays=lambda: replays, receita_popen_factory=rot, receita_conta_logada=lambda: BISTURI_ID) as c:
        h = token_headers(c)
        plano = c.post("/api/entrada/reconhecer", json={"texto": str(fora)}, headers=h).json()
        assert plano["entrada"]["aceita"] is False and "Escolher arquivo" in plano["entrada"]["mensagem"] and "etapas" not in plano
        negado = c.post("/api/trabalhos", json={"texto": str(fora)}, headers=h)
        assert negado.status_code == 403 and "Escolher arquivo" in negado.json()["erro"] and c.get("/api/trabalhos", headers=token_headers(c)).json()["itens"] == []
        c.app_state.arquivos_escolhidos.add(str(fora.resolve()))                                                     # o seletor nativo autorizou
        assert c.post("/api/entrada/reconhecer", json={"texto": str(fora)}, headers=h).json()["entrada"]["aceita"] is True
        # a demo da pasta de replays do CS2 vale sem seletor
        r = c.post("/api/trabalhos", json={"texto": str(replays / DEMO)}, headers=h)
        assert r.status_code == 200
        for _ in range(300):
            t = c.get(f"/api/trabalhos/{r.json()['id']}", headers=token_headers(c)).json()
            if t["estado"] in ("pronta", "erro"):
                break
            time.sleep(0.01)
        assert t["estado"] == "pronta"
    os.remove(fora)
