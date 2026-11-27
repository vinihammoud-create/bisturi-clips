"""S1.5 + S1.6 (Plano Sonnet 04/11): ducking na tela Configurar e o estado de qualidade (qualidade.json) na biblioteca e na revisão."""
import json

import pytest

from test_ui_dados_seguranca import cliente, criar_projeto, token_headers


def _gravar(tmp_path, dados):
    (tmp_path / "saida" / "v1" / "qualidade.json").write_text(json.dumps(dados), encoding="utf-8")


QUALIDADE = {"formato": 1, "status": "PASS_WITH_WARNINGS", "arquivos": {
    "MONTAGEM_v1.mp4": {"status": "PASS", "gates": [{"nome": "audio", "status": "PASS", "motivo": ""}]},
    "MONTAGEM_VOZ_v1.mp4": {"status": "PASS_WITH_WARNINGS", "gates": [
        {"nome": "integridade", "status": "PASS", "motivo": ""},
        {"nome": "audio", "status": "WARNING", "motivo": "pico real -0.4 dBTP acima de -1"}]}}}


# ── servidor ──────────────────────────────────────────────────────────────────────────────────────

def test_api_do_video_traz_a_qualidade_resumida_com_os_avisos(tmp_path):
    criar_projeto(tmp_path)
    _gravar(tmp_path, QUALIDADE)
    with cliente(tmp_path) as c:
        q = c.get("/api/videos/v1", headers=token_headers(c)).json()["qualidade"]
    assert q["status"] == "PASS_WITH_WARNINGS"
    por_nome = {a["nome"]: a for a in q["arquivos"]}
    assert por_nome["MONTAGEM_v1.mp4"] == {"nome": "MONTAGEM_v1.mp4", "status": "PASS", "avisos": []}
    assert por_nome["MONTAGEM_VOZ_v1.mp4"]["avisos"] == ["audio: pico real -0.4 dBTP acima de -1"]


def test_sem_qualidade_json_ou_com_lixo_a_api_devolve_none(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert c.get("/api/videos/v1", headers=token_headers(c)).json()["qualidade"] is None
        (tmp_path / "saida" / "v1" / "qualidade.json").write_text("{lixo", encoding="utf-8")
        assert c.get("/api/videos/v1", headers=token_headers(c)).json()["qualidade"] is None
        _gravar(tmp_path, {"status": "ESTRANHO", "arquivos": "nao-e-dict"})
        assert c.get("/api/videos/v1", headers=token_headers(c)).json()["qualidade"] is None          # estado desconhecido não é mostrado como se fosse válido


def test_motivo_gigante_ou_com_html_e_cortado_e_vai_como_texto(tmp_path):
    criar_projeto(tmp_path)
    ruim = {"status": "FAIL", "arquivos": {"X.mp4": {"status": "FAIL", "gates": [{"nome": "a", "status": "FAIL", "motivo": "<script>" + "x" * 5000}]}}}
    _gravar(tmp_path, ruim)
    with cliente(tmp_path) as c:
        aviso = c.get("/api/videos/v1", headers=token_headers(c)).json()["qualidade"]["arquivos"][0]["avisos"][0]
    assert len(aviso) <= 300                                                 # não despeja 5 KB na tela


def test_indice_da_biblioteca_leva_o_estado_de_qualidade_de_cada_video(tmp_path):
    criar_projeto(tmp_path)
    _gravar(tmp_path, QUALIDADE)
    with cliente(tmp_path) as c:
        itens = c.get("/api/indice", headers=token_headers(c)).json()["itens"]
    assert itens[0]["qualidade"] == "PASS_WITH_WARNINGS"


# ── tela ──────────────────────────────────────────────────────────────────────────────────────────

def test_ui_revisao_mostra_o_selo_e_os_avisos(tmp_path):
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      ui.montarQualidade({ status: "PASS_WITH_WARNINGS", arquivos: [
        { nome: "MONTAGEM_v1.mp4", status: "PASS", avisos: [] },
        { nome: "MONTAGEM_VOZ_v1.mp4", status: "PASS_WITH_WARNINGS", avisos: ["audio: pico real -0,4 dBTP acima de -1"] }] });
      const caixa = $("#result-quality");
      const com = textoDe(caixa);
      const escondido = caixa.classList.contains("hidden");
      ui.montarQualidade(null);
      return { com, escondido, depois: caixa.classList.contains("hidden") };
    """)
    assert "Qualidade OK, com avisos" in r["com"] and "MONTAGEM_VOZ_v1.mp4" in r["com"] and "pico real" in r["com"]
    assert "MONTAGEM_v1.mp4:" not in r["com"]                                # arquivo sem aviso não polui a tela
    assert r["escondido"] is False and r["depois"] is True                    # sem qualidade.json, a faixa some


@pytest.mark.parametrize("status,texto", [("PASS", "Qualidade OK"), ("NEEDS_REVIEW", "Conferir qualidade"), ("FAIL", "Problema de qualidade")])
def test_ui_cada_estado_tem_o_rotulo_certo(tmp_path, status, texto):
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      ui.montarQualidade({ status: "%s", arquivos: [] });
      return textoDe($("#result-quality"));
    """ % status)
    assert texto in r


def test_ui_biblioteca_mostra_o_selo_de_qualidade_so_quando_nao_e_pass(tmp_path):
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      return { aviso: ui.seloQualidade("PASS_WITH_WARNINGS"), ok: ui.seloQualidade("PASS"), nada: ui.seloQualidade(undefined), ruim: ui.seloQualidade("FAIL") };
    """)
    assert r["aviso"] == "Com avisos" and r["ruim"] == "Problema" and r["ok"] is None and r["nada"] is None


# ── configuração ──────────────────────────────────────────────────────────────────────────────────

def test_ducking_esta_na_tela_configurar_com_limites_0_a_6():
    from ui.configuracao import CAMPOS_POR_CHAVE, ConfiguracaoProjeto, ErroConfig
    campo = CAMPOS_POR_CHAVE[("montagem", "ducking_tiros_db")]
    assert campo.tipo == "numero" and campo.minimo == 0 and campo.maximo == 6
    with pytest.raises(ErroConfig):
        ConfiguracaoProjeto.validar(campo, 7)
    assert ConfiguracaoProjeto.validar(campo, 3) == 3
