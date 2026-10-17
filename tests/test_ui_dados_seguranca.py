import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ui.servidor import create_app
from ui.dados import DadosProjeto
from ui.seguranca import caminho_midia_seguro


PORT = 8765


def criar_projeto(raiz: Path) -> Path:
    (raiz / "saida" / "v1" / "cortes").mkdir(parents=True)
    (raiz / "saida" / "v1" / "miniaturas").mkdir()
    (raiz / "entrada").mkdir()
    (raiz / "saida" / "indice.json").write_text(json.dumps([
        {"id": "v1", "arquivo": "partida.mp4", "duracao": 120, "momentos": 1, "abates": 1}
    ], ensure_ascii=False), encoding="utf-8")
    documento = {
        "formato": 1,
        "video": "v1",
        "duracao": 120,
        "momentos": [{"n": 1, "tipo": "abate", "rotulo": "HEADSHOT", "nota": 42,
                      "inicio": 10, "fim": 15, "foco": 12.0, "abates": [{"icones": ["headshot"]}],
                      "aprovado": None, "excluido": False, "arquivos": {"corte": "cortes/corte.mp4"}}]
    }
    (raiz / "saida" / "v1" / "momentos.json").write_text(json.dumps(documento, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (raiz / "saida" / "v1" / "partes.json").write_text('[{"tipo":"partida","inicio":0,"fim":120}]', encoding="utf-8")
    (raiz / "saida" / "v1" / "cortes" / "corte.mp4").write_bytes(b"0123456789ABCDEFGHIJ")
    (raiz / "saida" / "v1" / "miniaturas" / "01.jpg").write_bytes(b"FAKEJPG")
    (raiz / "saida" / "progresso.json").write_text('{"formato":1,"estado":"pronto"}', encoding="utf-8")
    return raiz


def cliente(raiz: Path, **kwargs):
    app = create_app(root_dir=raiz, port=PORT, **kwargs)
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    c.app_state = app.state
    return c


def token_headers(client):
    return {"X-Token": client.app_state.token}


def test_rotas_leitura_e_tolerancia_a_json_ausente_ou_ilegiveis(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = c.get("/api/indice")
        assert r.status_code == 200
        assert r.json()["itens"][0]["id"] == "v1"
        video = c.get("/api/videos/v1")
        assert video.status_code == 200
        assert video.json()["momentos"]["momentos"][0]["rotulo"] == "HEADSHOT"
        (tmp_path / "saida" / "v1" / "momentos.json").write_text("{ilegivel", encoding="utf-8")
        quebrado = c.get("/api/videos/v1")
        assert quebrado.status_code == 200
        assert quebrado.json()["momentos"]["momentos"] == []
        (tmp_path / "saida" / "indice.json").unlink()
        assert c.get("/api/indice").json() == {"itens": []}
        (tmp_path / "saida" / "v1" / "partes.json").unlink()
        assert c.get("/api/videos/v1").json()["partes"] == []


def test_midia_200_range_206_e_404(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        url = "/media/v1/cortes/corte.mp4"
        completo = c.get(url)
        assert completo.status_code == 200
        assert completo.content == b"0123456789ABCDEFGHIJ"
        parcial = c.get(url, headers={"Range": "bytes=2-5"})
        assert parcial.status_code == 206
        assert parcial.content == b"2345"
        assert parcial.headers["content-range"] == "bytes 2-5/20"
        assert c.get("/media/v1/cortes/inexistente.mp4").status_code == 404


def test_midia_bloqueia_travessia_absoluto_cache_extensao_e_link_externo(tmp_path):
    criar_projeto(tmp_path)
    fora = tmp_path / "fora.txt"
    fora.write_text("não servir", encoding="utf-8")
    try:
        (tmp_path / "saida" / "v1" / "cortes" / "link.mp4").symlink_to(fora)
    except (OSError, NotImplementedError):
        pass
    with cliente(tmp_path) as c:
        assert c.get("/media/v1/../fora.txt").status_code in (400, 403, 404)
        assert c.get("/media/v1/%2Fetc%2Fpasswd").status_code in (400, 403, 404)
        assert c.get("/media/v1/.cache/segredo.json").status_code in (403, 404)
        assert c.get("/media/v1/cortes/segredo.ini").status_code == 403
        if (tmp_path / "saida" / "v1" / "cortes" / "link.mp4").is_symlink():
            assert c.get("/media/v1/cortes/link.mp4").status_code == 403
        with pytest.raises((PermissionError, FileNotFoundError)):
            caminho_midia_seguro(tmp_path / "saida", "v1", "../fora.txt")


def test_seguridad_token_host_origin_e_host_local(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        sem_token = c.post("/api/fila/cancelar-tudo", json={})
        assert sem_token.status_code == 403
        host_ruim = c.get("/health", headers={"Host": "example.com"})
        assert host_ruim.status_code == 403
        origem_ruim = c.post("/api/fila/cancelar-tudo", headers={**token_headers(c), "Origin": "https://evil.example"}, json={})
        assert origem_ruim.status_code == 403
        permitido = c.get("/health")
        assert permitido.status_code == 200
        assert c.get("/", headers={"Host": f"localhost:{PORT}"}).status_code == 200
        assert c.app_state.port == PORT


def test_edicao_muda_somente_campos_permitidos_backup_unico_e_tolerancia_foco(tmp_path):
    criar_projeto(tmp_path)
    caminho = tmp_path / "saida" / "v1" / "momentos.json"
    antes = json.loads(caminho.read_text(encoding="utf-8"))
    with cliente(tmp_path) as c:
        headers = token_headers(c)
        r = c.post("/api/videos/v1/momento", headers=headers, json={"foco": 12.04, "acao": "aprovar"})
        assert r.status_code == 200
        depois_aprovar = json.loads(caminho.read_text(encoding="utf-8"))
        assert depois_aprovar["momentos"][0]["aprovado"] is True
        assert depois_aprovar["momentos"][0]["rotulo"] == antes["momentos"][0]["rotulo"]
        assert depois_aprovar["momentos"][0]["nota"] == antes["momentos"][0]["nota"]
        assert list(depois_aprovar["momentos"][0].keys()) == list(antes["momentos"][0].keys())
        backup = caminho.with_name("momentos.json.bak")
        assert backup.exists()
        assert json.loads(backup.read_text(encoding="utf-8")) == antes
        c.post("/api/videos/v1/momento", headers=headers, json={"foco": 12.0, "acao": "descartar"})
        depois_descartar = json.loads(caminho.read_text(encoding="utf-8"))
        assert depois_descartar["momentos"][0]["excluido"] is True
        assert json.loads(backup.read_text(encoding="utf-8")) == antes
        c.post("/api/videos/v1/momento", headers=headers, json={"foco": 12.0, "acao": "restaurar"})
        restaurado = json.loads(caminho.read_text(encoding="utf-8"))
        assert restaurado["momentos"][0]["excluido"] is False


def test_edicao_recusada_durante_fila_e_json_ilegivel(tmp_path):
    criar_projeto(tmp_path)
    caminho = tmp_path / "saida" / "v1" / "momentos.json"
    with cliente(tmp_path) as c:
        c.app_state.fila._active_id = "outro-job"
        resposta = c.post("/api/videos/v1/momento", headers=token_headers(c), json={"foco": 12, "acao": "descartar"})
        assert resposta.status_code == 409
        c.app_state.fila._active_id = None
        caminho.write_text("{não-json", encoding="utf-8")
        resposta = c.post("/api/videos/v1/momento", headers=token_headers(c), json={"foco": 12, "acao": "descartar"})
        assert resposta.status_code == 400
        assert caminho.read_text(encoding="utf-8") == "{não-json"


def test_upload_em_blocos_nome_seguro_sem_sobrescrever_e_extensao_invalida(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        dados = b"A" * (2 * 1024 * 1024 + 17)
        r1 = c.post("/api/upload", headers=token_headers(c), files=[("files", ("partida com acento-á.mp4", dados, "video/mp4"))])
        assert r1.status_code == 200
        item1 = r1.json()["itens"][0]
        arquivo1 = Path(item1["caminho"])
        assert arquivo1.read_bytes() == dados
        assert ".." not in arquivo1.name and "/" not in arquivo1.name and "\\" not in arquivo1.name
        r2 = c.post("/api/upload", headers=token_headers(c), files=[("files", ("partida com acento-á.mp4", b"B", "video/mp4"))])
        assert r2.status_code == 200
        assert r2.json()["itens"][0]["nome"] != item1["nome"]
        ruim = c.post("/api/upload", headers=token_headers(c), files=[("files", ("segredo.exe", b"x", "application/octet-stream"))])
        assert ruim.status_code == 415


def test_js_nao_usa_innerhtml_com_dados_de_usuario():
    js = (Path(__file__).parents[1] / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert ".innerHTML" not in js
    assert "textContent" in js


def test_inicializacao_fica_restrita_ao_loopback_e_bat_tem_crlf():
    raiz = Path(__file__).parents[1]
    inicializador = (raiz / "ui" / "__main__.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in inicializador
    assert 'host="0.0.0.0"' not in inicializador
    for nome in ("iniciar.bat", "instalar_ui.bat"):
        conteudo = (raiz / "ui" / nome).read_bytes()
        assert b"\r\n" in conteudo
        assert b"\n" not in conteudo.replace(b"\r\n", b"")
        assert not conteudo.startswith(b"\xef\xbb\xbf")
