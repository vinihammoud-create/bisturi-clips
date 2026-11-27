"""C6 (Plano V7 integrado): página de comparação cega A/B."""
import re

from cs2clip.comparador_ab import gerar_pagina, pares_da_pasta


def _pasta(base, nome, arquivos):
    p = base / nome
    p.mkdir(parents=True)
    for a in arquivos:
        (p / a).write_bytes(b"x")
    return p


def test_so_entram_pares_em_que_as_duas_versoes_existem(tmp_path):
    p = _pasta(tmp_path, "jogada_2_triple_kill", ["A_normal.mp4", "B_acelerar_entre_abates.mp4", "D_cartao_da_jogada.mp4",
                                                   "E_short_normal.mp4"])          # sem C e sem F
    efeitos = {x["efeito"] for x in pares_da_pasta(p)}
    assert efeitos == {"Acelerar o caminho entre abates", "Cartão da jogada no início"}


def test_ordem_cega_e_fixa_para_a_mesma_jogada_e_varia_entre_jogadas(tmp_path):
    arqs = ["A_normal.mp4", "B_acelerar_entre_abates.mp4", "C_congelar_headshot.mp4", "D_cartao_da_jogada.mp4"]
    p1 = _pasta(tmp_path, "j1", arqs)
    assert pares_da_pasta(p1) == pares_da_pasta(p1)
    ordens = {x["normal_e"] for nome in "abcdefgh" for x in pares_da_pasta(_pasta(tmp_path, f"jogada_{nome}", arqs))}
    assert ordens == {1, 2}                                                          # sorteio de verdade, não sempre o mesmo lado


def test_normal_e_efeito_estao_nos_dois_lados_certos(tmp_path):
    p = _pasta(tmp_path, "j", ["A_normal.mp4", "C_congelar_headshot.mp4"])
    par = pares_da_pasta(p)[0]
    assert {par["um"], par["dois"]} == {"A_normal.mp4", "C_congelar_headshot.mp4"}
    assert (par["um"] if par["normal_e"] == 1 else par["dois"]) == "A_normal.mp4"


def test_pagina_nao_mostra_qual_e_o_efeito_no_texto_visivel(tmp_path):
    p = _pasta(tmp_path, "jogada_9_triple_kill", ["A_normal.mp4", "B_acelerar_entre_abates.mp4"])
    destino = gerar_pagina(tmp_path, [p])
    pagina = destino.read_text(encoding="utf-8")
    visivel = re.sub(r"<script>.*?</script>", "", pagina, flags=re.S)
    visivel = re.sub(r"<video[^>]*>", "", visivel)                                      # o caminho do vídeo fica no src
    for nome in ("Acelerar", "Congelar", "Cartão da jogada", "balança", "A_normal", "speed"):
        assert nome not in visivel                                                      # nenhuma pista de qual é o efeito
    assert "Versão 1" in visivel and "Versão 2" in visivel and "Eu publicaria a 1" in visivel and "Exportar votos" in visivel
    assert "localStorage" in pagina and "votos_ab.csv" in pagina and "com_efeito" in pagina


def test_sem_nenhum_par_nao_cria_pagina(tmp_path):
    p = _pasta(tmp_path, "j", ["A_normal.mp4"])
    assert gerar_pagina(tmp_path, [p]) is None and not (tmp_path / "comparar.html").exists()


def test_nome_da_jogada_com_caracteres_especiais_e_escapado(tmp_path):
    p = _pasta(tmp_path, "jogada_&_x", ["A_normal.mp4", "B_acelerar_entre_abates.mp4"])
    visivel = re.sub(r"<script>.*?</script>", "", gerar_pagina(tmp_path, [p]).read_text(encoding="utf-8"), flags=re.S)
    assert "jogada_&amp;_x" in visivel and "jogada_&_x" not in visivel
