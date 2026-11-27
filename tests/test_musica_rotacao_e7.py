"""E7 (Plano E): a música gira entre os vídeos (evita as 2 últimas), cada vídeo mantém a sua, e faixa desligada pelo dono nunca sai."""
import json

from cs2clip import montagem
from cs2clip.config import Config


def _pasta(tmp_path, n=5, desligadas=(), drop=40.0):
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    faixas = []
    for i in range(n):
        nome = f"faixa{i}.mp3"
        (pasta / nome).write_bytes(b"x" * 60_000)
        f = {"arquivo": nome, "licenca": "CC0 1.0", "duracao_s": 200.0, "bpm": 120.0, "drop_s": drop, "emocao": 1.0 - 0.2 * i, "energia": 0.8}
        if nome in desligadas:
            f["desligada"] = True
        faixas.append(f)
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": faixas}), encoding="utf-8")
    return pasta


def _cfg():
    cfg = Config()
    cfg.montagem.musica = "auto"
    return cfg


def test_mesmo_video_mantem_a_faixa_e_videos_novos_giram_sem_repetir_as_2_ultimas(tmp_path):
    pasta = _pasta(tmp_path)
    cfg = _cfg()
    primeira = montagem.musica_da_montagem(cfg, "MONTAGEM_v1", 12.0, pasta, 70.0)
    assert primeira.name == "faixa0.mp3"                                                       # a de maior emoção
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_v1", 12.0, pasta, 70.0) == primeira      # refazer o mesmo vídeo não troca a música
    segunda = montagem.musica_da_montagem(cfg, "MONTAGEM_v2", 12.0, pasta, 70.0)
    terceira = montagem.musica_da_montagem(cfg, "MONTAGEM_v3", 12.0, pasta, 70.0)
    quarta = montagem.musica_da_montagem(cfg, "MONTAGEM_v4", 12.0, pasta, 70.0)
    assert [segunda.name, terceira.name, quarta.name] == ["faixa1.mp3", "faixa2.mp3", "faixa0.mp3"]   # nunca igual às 2 últimas; a antiga volta depois
    assert len({primeira.name, segunda.name, terceira.name}) == 3
    # o histórico fica no disco e sobrevive a "reabrir"
    assert json.loads(montagem.arquivo_historico(pasta).read_text(encoding="utf-8"))["por_chave"]["MONTAGEM_v2"] == "faixa1.mp3"


def test_catalogo_pequeno_nao_fica_sem_musica_por_causa_do_rodizio(tmp_path):
    pasta = _pasta(tmp_path, n=1)
    cfg = _cfg()
    assert [montagem.musica_da_montagem(cfg, f"MONTAGEM_v{i}", 12.0, pasta, 70.0).name for i in range(3)] == ["faixa0.mp3"] * 3
    pasta2 = _pasta(tmp_path / "dois", n=0) if (tmp_path / "dois").mkdir() is None else None
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_x", 12.0, pasta2, 70.0) is None            # sem faixa nenhuma: sem música, sem erro


def test_faixa_desligada_nunca_e_sorteada_e_a_fixada_que_foi_desligada_e_trocada(tmp_path):
    pasta = _pasta(tmp_path, desligadas=("faixa0.mp3",))
    cfg = _cfg()
    assert [p.name for p in montagem.musicas_liberadas(pasta)] == ["faixa1.mp3", "faixa2.mp3", "faixa3.mp3", "faixa4.mp3"]
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_a", 12.0, pasta, 70.0).name == "faixa1.mp3"
    cat = json.loads((pasta / "catalogo.json").read_text(encoding="utf-8"))
    cat["faixas"][1]["desligada"] = True                                                         # o dono desliga a que o vídeo já tinha
    (pasta / "catalogo.json").write_text(json.dumps(cat), encoding="utf-8")
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_a", 12.0, pasta, 70.0).name == "faixa2.mp3"   # troca para uma ligada


def test_historico_ilegivel_nao_derruba_a_montagem(tmp_path):
    pasta = _pasta(tmp_path)
    montagem.arquivo_historico(pasta).write_text("{lixo", encoding="utf-8")
    assert montagem.musica_da_montagem(_cfg(), "MONTAGEM_v1", 12.0, pasta, 70.0).name == "faixa0.mp3"
    assert json.loads(montagem.arquivo_historico(pasta).read_text(encoding="utf-8"))["ordem"] == ["MONTAGEM_v1"]   # e o arquivo é refeito


def test_os_testes_nunca_tocam_no_historico_real_da_pasta_de_musicas():
    real = montagem.PASTA_MUSICAS / "historico.json"
    assert montagem.arquivo_historico(montagem.PASTA_MUSICAS) != real                                # o conftest desviou o histórico para uma pasta temporária
    antes = real.read_bytes() if real.exists() else None
    cfg = _cfg()
    montagem.musica_da_montagem(cfg, "MONTAGEM_teste_isolamento", 12.0, montagem.PASTA_MUSICAS, 70.0)
    assert (real.read_bytes() if real.exists() else None) == antes


def test_musica_escolhida_por_nome_nao_entra_no_rodizio(tmp_path):
    pasta = _pasta(tmp_path)
    cfg = Config()
    cfg.montagem.musica = "faixa3.mp3"
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_v1", 12.0, pasta, 70.0).name == "faixa3.mp3"
    assert not montagem.arquivo_historico(pasta).exists()


def test_musica_presa_ao_video_e_reconferida_se_a_montagem_cresceu_e_ela_nao_cabe_mais(tmp_path):
    """Achado M6: a fixação voltava antes da conferência "toca a montagem inteira sem voltar ao começo" (R2)."""
    pasta = _pasta(tmp_path, n=0)
    import json as _j
    faixas = [{"arquivo": "curta.mp3", "licenca": "CC0", "duracao_s": 100.0, "bpm": 120.0, "drop_s": 40.0, "emocao": 0.95, "energia": 0.8},
              {"arquivo": "longa.mp3", "licenca": "CC0", "duracao_s": 400.0, "bpm": 120.0, "drop_s": 40.0, "emocao": 0.60, "energia": 0.8}]
    for f in faixas:
        (pasta / f["arquivo"]).write_bytes(b"x" * 60_000)
    (pasta / "catalogo.json").write_text(_j.dumps({"faixas": faixas}), encoding="utf-8")
    cfg = _cfg()
    # montagem curta (50 s): a "curta" (maior emoção) cabe: pulo 34 + 50 = 84 <= 100
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_v1", 12.0, pasta, 50.0).name == "curta.mp3"
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_v1", 12.0, pasta, 50.0).name == "curta.mp3"          # refazer igual: mantém
    # a montagem do MESMO vídeo cresceu para 120 s: 34 + 120 = 154 > 100, a "curta" voltaria ao começo; a "longa" cabe
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_v1", 12.0, pasta, 120.0).name == "longa.mp3"
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_v1", 12.0, pasta, 120.0).name == "longa.mp3"          # e a nova escolha fica presa
    # CONTROLE: se nenhuma faixa cabe, mantém a que o vídeo já tinha (não fica sem música)
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_v1", 12.0, pasta, 900.0).name == "longa.mp3"

