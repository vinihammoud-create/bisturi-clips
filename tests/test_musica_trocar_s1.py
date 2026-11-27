"""S1 (revisão do Fable, M6): o botão "Trocar a música" solta a faixa presa a UM vídeo; a próxima montagem dele usa outra e os demais vídeos não mudam."""
import json

from cs2clip import montagem

from test_musica_rotacao_e7 import _cfg, _pasta
from test_r1_ui import rodar_js
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers

CHAVE = "MONTAGEM_v1"


def _escolhe(pasta, chave):
    return montagem.musica_da_montagem(_cfg(), chave, 12.0, pasta, 70.0).name


def test_trocar_solta_a_faixa_do_video_e_a_proxima_montagem_dele_usa_outra(tmp_path):
    pasta = _pasta(tmp_path)
    primeira = _escolhe(pasta, CHAVE)
    assert primeira == "faixa0.mp3" and _escolhe(pasta, CHAVE) == primeira                                   # sem trocar, refazer mantém a música
    r = montagem.trocar_musica_do_video(pasta, CHAVE)
    assert r == {"anterior": "faixa0.mp3", "trocada": True}
    segunda = _escolhe(pasta, CHAVE)
    assert segunda != primeira and _escolhe(pasta, CHAVE) == segunda                                          # a nova fica presa de novo
    montagem.trocar_musica_do_video(pasta, CHAVE)                                                             # trocar outra vez: nenhuma das duas já recusadas volta
    terceira = _escolhe(pasta, CHAVE)
    assert len({primeira, segunda, terceira}) == 3


def test_trocar_mexe_so_naquele_video_os_outros_e_o_rodizio_ficam_iguais(tmp_path):
    pasta = _pasta(tmp_path)
    v1, v2, v3 = _escolhe(pasta, "MONTAGEM_v1"), _escolhe(pasta, "MONTAGEM_v2"), _escolhe(pasta, "MONTAGEM_v3")
    antes = json.loads(montagem.arquivo_historico(pasta).read_text(encoding="utf-8"))
    montagem.trocar_musica_do_video(pasta, "MONTAGEM_v2")
    depois = json.loads(montagem.arquivo_historico(pasta).read_text(encoding="utf-8"))
    assert depois["por_chave"]["MONTAGEM_v1"] == v1 and depois["por_chave"]["MONTAGEM_v3"] == v3 and "MONTAGEM_v2" not in depois["por_chave"]
    assert depois["ordem"] == antes["ordem"]                                                                  # a ordem do rodízio não muda
    assert _escolhe(pasta, "MONTAGEM_v1") == v1 and _escolhe(pasta, "MONTAGEM_v3") == v3
    assert _escolhe(pasta, "MONTAGEM_v2") not in {v2}                                                         # e o v2 recebe outra


def test_sem_historico_usa_a_faixa_que_o_arquivo_pronto_tem_de_fato(tmp_path):
    """O histórico pode ter sido apagado (aconteceu com o kit aprovado): o `benchmark.json` do vídeo diz qual música está no arquivo."""
    pasta = _pasta(tmp_path)
    r = montagem.trocar_musica_do_video(pasta, CHAVE, atual="faixa0.mp3")
    assert r == {"anterior": "faixa0.mp3", "trocada": True}
    assert _escolhe(pasta, CHAVE) != "faixa0.mp3"                                                              # mesmo sem histórico, a atual não volta


def test_sem_faixa_conhecida_nada_e_gravado_e_o_vídeo_segue_normal(tmp_path):
    pasta = _pasta(tmp_path)
    assert montagem.trocar_musica_do_video(pasta, CHAVE) == {"anterior": "", "trocada": False}
    assert not montagem.arquivo_historico(pasta).exists()
    assert _escolhe(pasta, CHAVE) == "faixa0.mp3"


def test_a_lista_do_que_evitar_sobrevive_a_novos_registros_e_nao_cresce_sem_fim(tmp_path):
    pasta = _pasta(tmp_path, n=5)
    for _ in range(3):
        montagem.trocar_musica_do_video(pasta, CHAVE, atual=_escolhe(pasta, CHAVE))
    h = json.loads(montagem.arquivo_historico(pasta).read_text(encoding="utf-8"))
    assert len(set(h["evitar"][CHAVE])) == 3 and CHAVE not in h["por_chave"]                                   # as 3 recusadas continuam na lista depois dos novos registros
    assert _escolhe(pasta, CHAVE) not in h["evitar"][CHAVE]                                                    # e a próxima escolha é uma que ele ainda não recusou
    for i in range(montagem.HISTORICO_MAX + 5):                                                               # muitos vídeos depois: o que é antigo sai do histórico e da lista
        _escolhe(pasta, f"MONTAGEM_outro{i}")
    h2 = json.loads(montagem.arquivo_historico(pasta).read_text(encoding="utf-8"))
    assert CHAVE not in h2["evitar"] and len(h2["ordem"]) <= montagem.HISTORICO_MAX


def test_catalogo_com_uma_faixa_so_nao_quebra_a_troca(tmp_path):
    pasta = _pasta(tmp_path, n=1)
    assert _escolhe(pasta, CHAVE) == "faixa0.mp3"
    assert montagem.trocar_musica_do_video(pasta, CHAVE)["trocada"] is True
    assert _escolhe(pasta, CHAVE) == "faixa0.mp3"                                                              # só há uma: melhor ela que ficar sem música


def test_rota_trocar_musica_exige_token_acha_o_video_e_usa_a_musica_do_benchmark(tmp_path):
    criar_projeto(tmp_path)
    pasta = _pasta(tmp_path)                                                                                  # musicas/ do projeto de teste
    (tmp_path / "saida" / "v1" / "benchmark.json").write_text(json.dumps({"montagem_musica": "faixa0.mp3"}), encoding="utf-8")
    with cliente(tmp_path) as c:
        h = token_headers(c)
        assert c.post("/api/videos/v1/trocar-musica").status_code == 403                                      # sem token
        assert c.post("/api/videos/nao_existe/trocar-musica", headers=h).status_code == 404
        r = c.post("/api/videos/v1/trocar-musica", headers=h)
        assert r.status_code == 200 and r.json() == {"anterior": "faixa0.mp3", "trocada": True, "limpo": False}
        assert _escolhe(pasta, "MONTAGEM_v1") != "faixa0.mp3"
        (tmp_path / "saida" / "partida_limpo").mkdir()
        assert c.post("/api/videos/partida_limpo/trocar-musica", headers=h).json() == {"anterior": "", "trocada": False, "limpo": True}


def test_botao_trocar_a_musica_so_aparece_com_montagem_e_o_aviso_diz_como_aplicar(tmp_path):
    r = rodar_js(tmp_path, """
      const com = { id: "v1", links: { montagem: true, montagem_arquivo: "MONTAGEM_v1.mp4" } };
      ui.montarAcoesResultado(com);
      const botoesCom = acharTag($("#result-actions"), "BUTTON").map(b => textoDe(b).trim());
      ui.montarAcoesResultado({ id: "v2", links: {} });
      const botoesSem = acharTag($("#result-actions"), "BUTTON").map(b => textoDe(b).trim());
      const pedidos = [];
      cfg.fetch = async (url, opcoes) => { pedidos.push([url, opcoes.method]); return { ok: true, headers: { get: () => "application/json" }, json: async () => cfg.resposta }; };
      cfg.resposta = { trocada: true, anterior: "Komiku - 15 - Victory.mp3", limpo: false };
      await ui.trocarMusicaDoVideo("v1"); await espera();
      const aviso1 = textoDe($("#toast-region").children[0] || { textContent: "", children: [] });
      return { botoesCom, botoesSem, pedidos, aviso1 };
    """)
    assert any("Trocar a música" in b for b in r["botoesCom"]) and not any("Trocar a música" in b for b in r["botoesSem"])
    assert r["pedidos"] == [["/api/videos/v1/trocar-musica", "POST"]]
    assert "Komiku - 15 - Victory" in r["aviso1"] and ".mp3" not in r["aviso1"] and "Gerar de novo" in r["aviso1"]
