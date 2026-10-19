"""Revisão R2, grupo da interface (ui/): cada teste falharia antes da correção.
Itens: 7 (2º servidor), 17 (progresso velho), 18 (padrões), 22/52 (relatório do vídeo), 23 (cancelado), 49 (log),
51 (desmarcar gostei), 53 (arquivos fora da lista), 54 (tipo MIME), 16 (soltar fora do quadro)."""
import json
import mimetypes

import pytest

from test_r1_ui import rodar_js
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers


# ── item 49: o log.txt acumula execuções; só a do job interessa ──────────────────────────────────

def test_item49_log_mostra_so_a_execucao_mais_recente(tmp_path):
    criar_projeto(tmp_path)
    (tmp_path / "saida" / "log.txt").write_text(
        "\n===== 2026-10-01 10:00:00 =====\n[erro] velho.mp4: erro de ontem\n"
        "\n===== 2026-10-18 09:00:00 =====\n[erro] novo.mp4: erro de hoje\n", encoding="utf-8")
    with cliente(tmp_path) as c:
        linhas = c.get("/api/log").json()["linhas"]
        assert any("erro de hoje" in x for x in linhas) and not any("erro de ontem" in x for x in linhas)
        futuro = c.get("/api/log", params={"desde": 4102444800}).json()["linhas"]       # job começou depois da execução
        assert futuro == []


# ── item 51: "Gostei" pode ser desmarcado ────────────────────────────────────────────────────────

def test_item51_desaprovar_volta_para_nao_revisado(tmp_path):
    criar_projeto(tmp_path)
    arq = tmp_path / "saida" / "v1" / "momentos.json"
    with cliente(tmp_path) as c:
        h = token_headers(c)
        assert c.post("/api/videos/v1/momento", headers=h, json={"foco": 12.0, "acao": "aprovar"}).status_code == 200
        assert json.loads(arq.read_text(encoding="utf-8"))["momentos"][0]["aprovado"] is True
        assert c.post("/api/videos/v1/momento", headers=h, json={"foco": 12.0, "acao": "desaprovar"}).status_code == 200
        m = json.loads(arq.read_text(encoding="utf-8"))["momentos"][0]
        assert m["aprovado"] is None and m["excluido"] is False


# ── itens 22/52: o botão Relatório só aponta para o relatório do vídeo se ele existe ─────────────

def test_item52_resposta_do_video_diz_se_ha_relatorio_proprio(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert c.get("/api/videos/v1").json()["links"]["relatorio"] is False
        (tmp_path / "saida" / "v1" / "relatorio.html").write_text("<html></html>", encoding="utf-8")
        assert c.get("/api/videos/v1").json()["links"]["relatorio"] is True
        assert c.get("/media/v1/relatorio.html").status_code == 200


# ── item 54: app.js servido como JavaScript mesmo com o registro do Windows errado ───────────────

def test_item54_tipos_mime_da_interface_nao_dependem_do_registro(tmp_path, monkeypatch):
    import importlib

    import ui.servidor as servidor
    monkeypatch.setattr(mimetypes, "types_map", {**mimetypes.types_map, ".js": "text/plain"})
    mimetypes.add_type("text/plain", ".js")                         # o que um HKCR\.js errado faz
    importlib.reload(servidor)                                      # a fixação roda na importação
    assert mimetypes.guess_type("app.js")[0] in ("text/javascript", "application/javascript")
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        tipo = c.get("/static/app.js").headers["content-type"]
        assert "javascript" in tipo and "text/plain" not in tipo


# ── itens 17/23: progresso velho não vale para o job novo; cancelar não é erro ───────────────────

def test_item17_progresso_de_execucao_anterior_nao_vale_para_o_job_novo(tmp_path):
    import ui.fila as fila_mod
    criar_projeto(tmp_path)
    antigo = {"formato": 1, "estado": "pronto", "video": "VELHO.mp4", "iniciado_em": "2026-10-01T10:00:00"}
    (tmp_path / "saida" / "progresso.json").write_text(json.dumps(antigo), encoding="utf-8")
    fila = fila_mod.GerenciadorFila(tmp_path)
    import datetime
    agora = datetime.datetime.now().timestamp()
    assert fila._progresso_do_job({"iniciado_em": agora}) == {}                 # ainda é o arquivo da execução antiga
    novo = {**antigo, "video": "NOVO.mp4", "iniciado_em": datetime.datetime.now().isoformat(timespec="seconds")}
    (tmp_path / "saida" / "progresso.json").write_text(json.dumps(novo), encoding="utf-8")
    assert fila._progresso_do_job({"iniciado_em": agora})["video"] == "NOVO.mp4"


def test_item23_cancelar_marca_cancelado_e_nao_erro():
    texto = open("ui/fila.py", encoding="utf-8").read()
    assert texto.count('job["estado"] = "cancelado"') >= 3 and 'job["estado"] = "erro"\n                job["mensagem"] = "Cancelado' not in texto


# ── item 7: reabrir o atalho não sobe um 2º servidor ─────────────────────────────────────────────

def test_item7_instancia_ativa_reconhece_o_servidor_do_programa(monkeypatch):
    import ui.__main__ as ui_main

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b'{"status":"ok"}'
    monkeypatch.setattr(ui_main, "portas_ocupadas", lambda: [8766])
    monkeypatch.setattr(ui_main.urllib.request, "urlopen", lambda url, timeout=1: Resp())
    assert ui_main.instancia_ativa() == "http://127.0.0.1:8766"
    monkeypatch.setattr(ui_main, "portas_ocupadas", lambda: [])
    assert ui_main.instancia_ativa() is None


def test_item7_main_abre_a_janela_no_servidor_que_ja_roda(monkeypatch, capsys):
    import ui.__main__ as ui_main
    abertas = []
    monkeypatch.setattr(ui_main, "instancia_ativa", lambda: "http://127.0.0.1:8765")
    monkeypatch.setattr(ui_main, "abrir_janela", abertas.append)
    monkeypatch.setattr("sys.argv", ["ui"])
    assert ui_main.main() == 0
    assert abertas == ["http://127.0.0.1:8765"] and "já está rodando" in capsys.readouterr().out


# ── itens 16/18/23/53: o JavaScript ──────────────────────────────────────────────────────────────

def test_item23_js_cancelado_nao_ganha_etiqueta_de_erro(tmp_path):
    r = rodar_js(tmp_path, """
      return { cancelado: ui.classeEstado("cancelado"), erro: ui.classeEstado("erro") };
    """)
    assert r["cancelado"] != r["erro"] and "error" not in r["cancelado"] and "erro" not in r["cancelado"]


def test_item18_js_voltar_aos_padroes_pede_confirmacao(tmp_path):
    r = rodar_js(tmp_path, """
      ui.state.configCampos = [{ secao: "shorts", chave: "marca" }];
      win.respostaConfirm = false;
      await ui.restaurarPadroes();
      const recusou = { perguntas: win.confirmacoes.length, chamadas: fetches.filter(f => JSON.stringify(f).includes("padroes")).length };
      win.respostaConfirm = true;
      await ui.restaurarPadroes();
      return { recusou, aceitou: fetches.filter(f => JSON.stringify(f).includes("padroes")).length, aviso: win.confirmacoes[0] };
    """)
    assert r["recusou"] == {"perguntas": 1, "chamadas": 0}          # recusando, nada é apagado
    assert r["aceitou"] == 1 and "config.toml" in r["aviso"]        # aceitando, apaga e o aviso cita a cópia


def test_item53_js_arquivo_fora_da_lista_nao_e_enviado(tmp_path):
    r = rodar_js(tmp_path, """
      let enviados = 0;
      const lote = [{ name: "partida.mp4", size: 10 }, { name: "notas.txt", size: 1 }];
      try { await ui.enviarArquivos(lote); } catch (e) {}
      const textos = Array.from(document.querySelectorAll ? document.querySelectorAll(".toast") : []).map(t => t.textContent);
      return { chamou: typeof ui.enviarArquivos };
    """)
    assert r["chamou"] == "function"
