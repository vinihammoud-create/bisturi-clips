"""R-002 + A-002 (rev2611, lote E): depois de reiniciar, o caminho do original fora do projeto dá 403; a tela explica e reabre o seletor de arquivo."""
from typing import Any
from pathlib import Path

from test_r1_ui import rodar_js
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers


def test_servidor_continua_exigindo_o_seletor_e_diz_o_motivo_em_campo_proprio(tmp_path):
    criar_projeto(tmp_path)
    fora = tmp_path.parent / (tmp_path.name + "_fora")
    fora.mkdir()
    video = fora / "obs.mp4"
    video.write_bytes(b"x" * 100)
    with cliente(tmp_path) as c:
        r = c.post("/api/fila", json={"arquivos": [str(video)], "opcoes": {}}, headers=token_headers(c))
        assert r.status_code == 403                                           # não afrouxou: sem o seletor, não entra
        assert r.json()["motivo"] == "precisa_seletor" and "Escolher no computador" in r.json()["erro"]
        # com o seletor (arquivo escolhido nesta sessão) entra normalmente
        estado: Any = c.app_state                      # Any: o verificador de tipos vê app_state como dict neste arquivo
        estado.arquivos_escolhidos.add(str(video.resolve()))
        assert c.post("/api/fila", json={"arquivos": [str(video)], "opcoes": {}}, headers=token_headers(c)).status_code in (200, 409)


def test_outros_403_da_fila_nao_ganham_o_motivo(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        r = c.post("/api/fila", json={"arquivos": ["entrada:../x.mp4"], "opcoes": {}}, headers=token_headers(c))
        assert "motivo" not in r.json()


_CENARIO = """
  const chamadas = [];
  const resp = (status, corpo) => ({ ok: status < 400, status, headers: { get: () => "application/json" }, json: async () => corpo });
  let filaTentativas = 0;
  cfg.fetch = async (url, op) => {
    chamadas.push(url);
    if (url === "/api/fila" && op.method === "POST") {
      filaTentativas++;
      return filaTentativas === 1 ? resp(403, %(erro_fila)s) : resp(200, { itens: [] });
    }
    if (url === "/api/escolher-arquivo") return resp(200, { itens: [{ tipo: "video", caminho: "C:\\\\obs\\\\jogo.mp4", nome: "jogo.mp4" }] });
    return resp(200, { itens: [], progresso: {} });
  };
  ui.state.videoAtual = "v1";
  ui.state.dadosAtuais = { indice: { caminho: "C:\\\\obs\\\\jogo.mp4" }, momentos: { momentos: [] } };
  await ui.adicionarFila(["C:\\\\obs\\\\jogo.mp4"], {}, "aplicar");
  await espera();
  return { chamadas: chamadas, fila: filaTentativas, toasts: $("#toast-region").children.map(textoDe) };
"""


def test_tela_reabre_o_seletor_quando_o_403_traz_o_motivo(tmp_path):
    r = rodar_js(tmp_path, _CENARIO % {"erro_fila": '{ erro: "Escolha o arquivo pelo botão X.", motivo: "precisa_seletor" }'})
    assert r["chamadas"].count("/api/escolher-arquivo") == 1                   # reabriu o seletor
    assert r["fila"] == 2                                                      # e mandou de novo, agora com o arquivo escolhido
    assert any("foi reiniciado" in t for t in r["toasts"])                          # explicou o porquê


def test_tela_nao_entra_em_laco_se_o_403_se_repete(tmp_path):
    r = rodar_js(tmp_path, _CENARIO.replace("filaTentativas === 1 ?", "true ?") % {"erro_fila": '{ erro: "de novo", motivo: "precisa_seletor" }'})
    assert r["chamadas"].count("/api/escolher-arquivo") == 1 and r["fila"] == 2


def test_tela_na_pagina_inicio_so_mostra_o_erro_sem_abrir_o_seletor(tmp_path):
    r = rodar_js(tmp_path, """
      const chamadas = [];
      cfg.fetch = async (url) => { chamadas.push(url);
        return { ok: false, status: 403, headers: { get: () => "application/json" }, json: async () => ({ erro: "Escolha o arquivo.", motivo: "precisa_seletor" }) }; };
      await ui.adicionarFila(["C:\\\\x\\\\a.mp4"], {});
      await espera();
      return chamadas;
    """)
    assert r == ["/api/fila"]


def test_o_servidor_nao_foi_afrouxado_aceita_so_com_seletor():
    codigo = (Path(__file__).resolve().parents[1] / "ui" / "servidor.py").read_text(encoding="utf-8")
    trecho = codigo.split('"/api/fila")', 1)[1].split("fila.adicionar_varios", 1)[0]
    assert "arquivos_escolhidos" in trecho and "indice.json" not in trecho
