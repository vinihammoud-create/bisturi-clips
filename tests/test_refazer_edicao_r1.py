"""R1 (revisão do Opus de 13/11): "Refazer a edição" de um kit de imagem limpa pela interface — um trabalho com SÓ a etapa de editar (não abre o CS2, não pede
troca de conta), que mantém a live (voz e webcam) quando o kit nasceu de uma live + demo. E o "Gerar de novo" deixa de ser oferecido a esse tipo de kit
(mandaria a partida limpa para a análise comum e geraria outro kit errado).

Mais a lacuna do S5 achada aqui: sem a faixa do microfone configurada, a edição quebrava em vez de seguir sem a voz (e a webcam ficava de fora).
"""
import json
import time

import pytest

from cs2clip import entrada as ent

from test_receitas_e2 import (D1, EDITAR_OK, GRAVAR_OK, PRINCIPAL_ID, Conta, FilaFalsa, Roteiro, _demo, _ger, _ger_live, _live_com_analise)
from test_r1_ui import rodar_js
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

NOME = "match730_003849321203751714819_0599033075_201"


def _gravacoes(tmp_path):
    pasta = tmp_path / "raiz" / "gravacao_demo" / NOME
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "registro.json").write_text(json.dumps({"trechos": [], "resultados": []}), encoding="utf-8")


def _kit_pronto_de_uma_demo(tmp_path, rot):
    demo = _demo(tmp_path)
    g = _ger(tmp_path, rot)
    fim = g.esperar(g.iniciar(str(demo))["id"])
    assert fim["estado"] == "pronta"
    _gravacoes(tmp_path)
    return g, demo


def test_refazer_a_edicao_roda_so_o_editar_sem_gravar_e_sem_pedir_troca_de_conta(tmp_path):
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g, demo = _kit_pronto_de_uma_demo(tmp_path, rot)
    antes = list(rot.comandos)
    g.__dict__["_conta_logada"] = Conta(PRINCIPAL_ID)                                                        # Steam na conta PRINCIPAL: gravar seria recusado; editar não depende disso
    r = g.refazer_edicao(NOME + "_limpo")
    assert [e["nome"] for e in r["etapas"]] == ["editar"] and r["titulo"].startswith("Refazer a edição")
    fim = g.esperar(r["id"])
    novos = rot.comandos[len(antes):]
    assert fim["estado"] == "pronta" and [c[c.index("-m") + 1] for c in novos] == ["cs2clip.editar_demo"]      # nenhum gravador_demo, nenhuma troca de conta
    assert novos[0][novos[0].index("--demo") + 1] == str(demo.resolve()) and "--live" not in novos[0]
    assert fim["titulo"].startswith("Refazer a edição")                                                       # o título do placar não o sobrescreve
    assert fim["resultado"] == "C:\\saida\\x_limpo"                                                           # "Abrir o kit" continua funcionando


def test_refazer_mantem_a_live_para_a_voz_e_a_webcam_quando_o_kit_nasceu_de_live_mais_demo(tmp_path):
    live, analise, pasta_demos = _live_com_analise(tmp_path, [D1])
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger_live(tmp_path, rot, pasta_demos, fila=FilaFalsa(["pronto"]))
    assert g.esperar(g.iniciar(str(live))["id"])["estado"] == "pronta"
    _gravacoes(tmp_path)
    n = len(rot.comandos)
    fim = g.esperar(g.refazer_edicao(NOME + "_limpo")["id"])
    edit = rot.comandos[n]
    assert fim["estado"] == "pronta" and len(rot.comandos) == n + 1 and "editar_demo" in " ".join(edit)
    assert edit[edit.index("--live") + 1] == str(live) and edit[edit.index("--analise-da-live") + 1] == str(analise)       # voz e webcam da live continuam


def test_a_live_vai_para_a_edicao_mesmo_sem_a_faixa_do_microfone_para_a_webcam_nao_depender_do_audio(tmp_path):
    live, analise, pasta_demos = _live_com_analise(tmp_path, [D1])
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger_live(tmp_path, rot, pasta_demos, faixa_voz=0, fila=FilaFalsa(["pronto"]))
    fim = g.esperar(g.iniciar(str(live))["id"])
    edit = rot.comandos[-1]
    assert fim["estado"] == "pronta" and "--live" in edit and "faixa_voz" in fim["etapas"][-1]["detalhe"]       # antes: sem --live e, portanto, sem webcam


def test_refazer_recusa_o_que_nao_da_com_frase_clara(tmp_path):
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g, demo = _kit_pronto_de_uma_demo(tmp_path, rot)
    with pytest.raises(ValueError, match="termina em _limpo"):
        g.refazer_edicao("2026-11-04_21-39-10")
    with pytest.raises(ValueError):
        g.refazer_edicao("_limpo")
    with pytest.raises(RuntimeError, match="Não achei as gravações"):
        g.refazer_edicao("match730_000000000000000000000_0000000000_1_limpo")
    demo.unlink()                                                                                            # a demo sumiu do computador
    with pytest.raises(FileNotFoundError, match="demo dessa partida não está mais"):
        g.refazer_edicao(NOME + "_limpo")


def test_nao_duplica_enquanto_a_edicao_ainda_esta_sendo_refeita(tmp_path):
    import threading
    seguro = threading.Event()
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK, (["Refazendo"], 0, seguro)])
    g, demo = _kit_pronto_de_uma_demo(tmp_path, rot)
    r = g.refazer_edicao(NOME + "_limpo")
    for _ in range(300):
        if len(rot.processos) >= 3:
            break
        time.sleep(0.01)
    with pytest.raises(RuntimeError, match="Já estou refazendo"):
        g.refazer_edicao(NOME + "_limpo")
    seguro.set()
    assert g.esperar(r["id"])["estado"] == "pronta"
    assert g.refazer_edicao(NOME + "_limpo")["id"] != r["id"]                                                # terminou: dá para refazer de novo


def test_sem_receita_de_origem_acha_a_demo_pela_pasta_de_demos_e_edita_sem_live(tmp_path):
    """Kit feito pela linha de comando (sem trabalho no app): a demo é achada pelo nome nas pastas conhecidas."""
    rot = Roteiro(editar_demo=[EDITAR_OK])
    g = _ger(tmp_path, rot)
    _demo(tmp_path)                                                                                          # a demo está numa pasta conhecida (procurar_em = tmp_path)
    _gravacoes(tmp_path)
    fim = g.esperar(g.refazer_edicao(NOME + "_limpo")["id"])
    assert fim["estado"] == "pronta" and rot.modulos() == ["editar_demo"]


def test_a_rota_exige_token_valida_o_id_e_cria_o_trabalho(tmp_path):
    criar_projeto(tmp_path)
    (tmp_path / "saida" / (NOME + "_limpo")).mkdir()
    (tmp_path / "demos").mkdir(exist_ok=True)
    (tmp_path / "demos" / (NOME + ".dem")).write_bytes(ent.CABECALHO_CS2 + b"corpo")
    grav = tmp_path / "gravacao_demo" / NOME
    grav.mkdir(parents=True)
    (grav / "registro.json").write_text("{}", encoding="utf-8")
    rot = Roteiro(editar_demo=[EDITAR_OK])
    with cliente(tmp_path, receita_popen_factory=rot) as c:
        h = token_headers(c)
        assert c.post(f"/api/videos/{NOME}_limpo/refazer-edicao").status_code == 403                          # sem token
        assert c.post("/api/videos/nao_existe_limpo/refazer-edicao", headers=h).status_code == 404
        assert c.post("/api/videos/v1/refazer-edicao", headers=h).status_code == 400                          # kit comum: "Gerar de novo"
        r = c.post(f"/api/videos/{NOME}_limpo/refazer-edicao", headers=h)
        assert r.status_code == 200 and [e["nome"] for e in r.json()["etapas"]] == ["editar"]
        for _ in range(300):
            t = c.get(f"/api/trabalhos/{r.json()['id']}", headers=token_headers(c)).json()
            if t["estado"] in ("pronta", "erro"):
                break
            time.sleep(0.01)
        assert t["estado"] == "pronta" and rot.modulos() == ["editar_demo"]


def test_o_botao_certo_aparece_para_cada_tipo_de_kit_e_o_clique_leva_para_trabalhos(tmp_path):
    r = rodar_js(tmp_path, """
      ui.montarAcoesResultado({ id: "match730_x_limpo", links: { montagem: true, montagem_arquivo: "M.mp4" } });
      const limpo = acharTag($("#result-actions"), "BUTTON").map(b => textoDe(b).trim());
      ui.montarAcoesResultado({ id: "2026-11-04_21-39-10", links: { montagem: true, montagem_arquivo: "M.mp4" } });
      const comum = acharTag($("#result-actions"), "BUTTON").map(b => textoDe(b).trim());
      const pedidos = [];
      cfg.fetch = async (url, opcoes) => { pedidos.push([url, opcoes.method]); return { ok: true, headers: { get: () => "application/json" }, json: async () => ({ id: "t1" }) }; };
      win.location = { hash: "" };
      await ui.refazerEdicaoDoVideo("match730_x_limpo"); await espera();
      return { limpo, comum, pedidos, hash: win.location.hash };
    """)
    assert any("Refazer a edição" in b for b in r["limpo"]) and not any("Gerar de novo" in b for b in r["limpo"])
    assert any("Gerar de novo" in b for b in r["comum"]) and not any("Refazer a edição" in b for b in r["comum"])
    assert r["pedidos"] == [["/api/videos/match730_x_limpo/refazer-edicao", "POST"]] and r["hash"] == "#trabalhos"


def test_refazer_kit_de_camera_em_outro_jogador_passa_o_jogador_do_registro(tmp_path):
    # revisão de 17/11 (A2/B4): a gravação com a câmera em outro jogador fica em `<demo>__<steamid>`; o refazer passa --jogador= para a edição
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK, EDITAR_OK])
    g, demo = _kit_pronto_de_uma_demo(tmp_path, rot)
    pasta = tmp_path / "raiz" / "gravacao_demo" / (NOME + "__765")
    pasta.mkdir(parents=True)
    (pasta / "registro.json").write_text(json.dumps({"demo": str(demo), "jogador": 765, "trechos": [], "resultados": []}), encoding="utf-8")
    n = len(rot.comandos)
    fim = g.esperar(g.refazer_edicao(NOME + "__765_limpo")["id"])
    edit = rot.comandos[n]
    assert fim["estado"] == "pronta" and "--jogador=765" in edit and edit[edit.index("--demo") + 1] == str(demo.resolve())
    # CONTROLE: o kit de sempre (sem "__") não leva --jogador
    _gravacoes(tmp_path)
    n = len(rot.comandos)
    g.esperar(g.refazer_edicao(NOME + "_limpo")["id"])
    assert not any(a.startswith("--jogador") for a in rot.comandos[n])
    # registro que não diz o jogador (ou diz outro): recusa em vez de editar com a câmera errada
    (pasta / "registro.json").write_text(json.dumps({"demo": str(demo), "jogador": 999, "trechos": [], "resultados": []}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="qual jogador"):
        g.refazer_edicao(NOME + "__765_limpo")
