"""Botão "Apagar kit" da Biblioteca (faxina de 23/11): apaga só a pasta do kit e a linha do índice, com confirmação."""
import json
from typing import Any

from test_ui_dados_seguranca import cliente, criar_projeto, token_headers


def _ids(c):
    return [x["id"] for x in c.get("/api/indice", headers=token_headers(c)).json()["itens"]]


def test_apagar_kit_com_confirmacao_tira_pasta_e_indice(tmp_path):
    criar_projeto(tmp_path)
    (tmp_path / "saida" / "v1" / "cortes" / "corte.mp4").write_bytes(b"x" * 1000)
    fonte = tmp_path / "entrada" / "partida.mp4"
    fonte.write_bytes(b"original")
    with cliente(tmp_path) as c:
        r = c.post("/api/videos/v1/apagar", json={"confirmar": "v1"}, headers=token_headers(c))
        assert r.status_code == 200, r.text
        assert r.json()["liberado"] >= 1000
        assert not (tmp_path / "saida" / "v1").exists()
        assert _ids(c) == []
        assert "v1" not in (tmp_path / "saida" / "indice.txt").read_text(encoding="utf-8")
    assert fonte.read_bytes() == b"original"                     # a fonte nunca é tocada
    assert not list((tmp_path / "saida").glob(".apagando-*"))   # não sobra lixo


def test_apagar_kit_sem_confirmacao_nao_apaga(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        for corpo in ({}, {"confirmar": "outro"}, {"confirmar": True}):
            r = c.post("/api/videos/v1/apagar", json=corpo, headers=token_headers(c))
            assert r.status_code == 400
        assert (tmp_path / "saida" / "v1" / "momentos.json").is_file()
        assert _ids(c) == ["v1"]


def test_apagar_kit_recusa_caminho_livre_e_sem_token(tmp_path):
    criar_projeto(tmp_path)
    (tmp_path / "fora").mkdir()
    with cliente(tmp_path) as c:
        assert c.post("/api/videos/v1/apagar", json={"confirmar": "v1"}).status_code in (401, 403)
        for ruim in ("..", "..%5Cfora", "%2E%2E"):
            r = c.post(f"/api/videos/{ruim}/apagar", json={"confirmar": ruim}, headers=token_headers(c))
            assert r.status_code in (400, 403, 404, 405)
        assert (tmp_path / "fora").is_dir() and (tmp_path / "saida" / "v1").is_dir()


def test_apagar_kit_que_so_sobrou_no_indice(tmp_path):
    criar_projeto(tmp_path)
    indice = json.loads((tmp_path / "saida" / "indice.json").read_text(encoding="utf-8"))
    indice.append({"id": "sumiu", "arquivo": "x.mp4", "duracao": 1, "momentos": 0, "abates": 0})
    (tmp_path / "saida" / "indice.json").write_text(json.dumps(indice), encoding="utf-8")
    with cliente(tmp_path) as c:
        r = c.post("/api/videos/sumiu/apagar", json={"confirmar": "sumiu"}, headers=token_headers(c))
        assert r.status_code == 200 and r.json()["liberado"] == 0
        assert _ids(c) == ["v1"]
        r = c.post("/api/videos/nada/apagar", json={"confirmar": "nada"}, headers=token_headers(c))
        assert r.status_code == 404


def test_apagar_kit_recusa_com_processamento_rodando(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        estado: Any = c.app_state                            # Any: o verificador de tipos vê app_state como dict neste arquivo
        fila = estado.fila
        fila._active_id = "outro-job"                        # a fila diz que há um job ativo
        r = c.post("/api/videos/v1/apagar", json={"confirmar": "v1"}, headers=token_headers(c))
        fila._active_id = None
        assert r.status_code == 409
        assert (tmp_path / "saida" / "v1").is_dir()


def test_tela_tem_o_botao_com_confirmacao():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert '"Apagar kit"' in js and "window.confirm(`Apagar o kit" in js and "confirmar: id" in js
