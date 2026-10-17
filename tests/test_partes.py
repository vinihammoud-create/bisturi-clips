"""Testes de cs2clip/partes.py (divisão do vídeo em partidas, Deathmatch e trechos fora de partida).

A linha do tempo "real" abaixo é a lida no VOD 2894824156 (4:3 esticado) em 17/10/2026: 16
banners, 10 vitórias e 6 derrotas (placar conferido a olho: 10x6)."""
import pytest

from cs2clip.partes import capitulos_youtube, dividir_partes, parte_de, round_de, texto_partes
from cs2clip.rounds import RoundTimelineEntry as R

DURACAO = 3069.0
REAL = [R(t, res, 0.98) for t, res in [
    (858.0, "vitoria"), (903.0, "vitoria"), (999.0, "vitoria"), (1098.0, "vitoria"), (1182.0, "vitoria"),
    (1296.0, "derrota"), (1401.0, "vitoria"), (1515.0, "derrota"), (1638.0, "vitoria"), (1731.0, "derrota"),
    (1830.0, "vitoria"), (1884.0, "vitoria"), (2007.0, "derrota"), (2070.0, "derrota"), (2154.0, "derrota"),
    (2229.0, "vitoria")]]
# abates seus: 8 antes da partida (Deathmatch), 7 dentro, 4 depois (Deathmatch)
ABATES = [191.0, 214.4, 229.1, 237.4, 256.2, 312.3, 485.4, 520.5,
          841.8, 860.2, 896.8, 968.4, 988.6, 1083.4, 2224.5,
          2654.7, 2707.9, 2824.8, 3001.2]


def test_vod_real_vira_deathmatch_partida_deathmatch():
    partes = dividir_partes(REAL, ABATES, DURACAO)
    assert [p.tipo for p in partes] == ["deathmatch", "partida", "deathmatch"]
    meio = partes[1]
    assert meio.placar == "10x6"
    assert len(meio.rounds) == 16
    assert meio.rounds_faltando == 0
    assert meio.abates == 7
    assert 700 < meio.inicio < 800            # ~ 858 s menos a duração típica de um round
    assert 2235 < meio.fim < 2250             # último banner + FIM_BANNER
    assert [p.titulo for p in partes] == ["Deathmatch/aquecimento", "Partida 1 (10x6)", "Deathmatch/aquecimento 2"]


def test_as_partes_cobrem_o_video_sem_buracos_nem_sobreposicao():
    partes = dividir_partes(REAL, ABATES, DURACAO)
    assert partes[0].inicio == 0.0 and partes[-1].fim == DURACAO
    for a, b in zip(partes, partes[1:]):
        assert a.fim == pytest.approx(b.inicio)


def test_banner_solto_nao_e_partida():
    partes = dividir_partes([R(500.0, "derrota", 0.9), R(900.0, "vitoria", 0.9)], [], 1200.0)
    assert [p.tipo for p in partes] == ["fora"]


def test_intervalo_grande_separa_duas_partidas():
    r1 = [R(100.0 + 100 * i, "vitoria", 0.9) for i in range(4)]          # 100..400
    r2 = [R(1000.0 + 100 * i, "derrota", 0.9) for i in range(4)]         # 1000..1300
    partes = dividir_partes(r1 + r2, [], 1500.0)
    partidas = [p for p in partes if p.tipo == "partida"]
    assert [p.placar for p in partidas] == ["4x0", "0x4"]
    assert [p.n for p in partidas] == [1, 2]


def test_trecho_fora_sem_abates_nao_e_deathmatch():
    partes = dividir_partes(REAL, [], DURACAO)
    assert [p.tipo for p in partes] == ["fora", "partida", "fora"]


def test_round_perdido_na_leitura_estima_rounds_faltando():
    # banner do round 3 não foi lido: o intervalo de 200 s vale ~2 rounds
    rs = [R(100.0, "vitoria", 0.9), R(200.0, "vitoria", 0.9), R(400.0, "derrota", 0.9),
          R(500.0, "vitoria", 0.9), R(600.0, "vitoria", 0.9)]
    partidas = [x for x in dividir_partes(rs, [], 700.0) if x.tipo == "partida"]
    assert len(partidas) == 1 and partidas[0].rounds_faltando == 1


def test_round_de_um_momento_dentro_da_partida():
    partes = dividir_partes(REAL, ABATES, DURACAO)
    p, n, res = round_de(1083.4, partes)
    assert p.tipo == "partida" and n == 4 and res == "vitoria"      # termina no banner de 1098 s
    p, n, res = round_de(1275.9, partes)
    assert n == 6 and res == "derrota"                              # banner de 1296 s


def test_round_de_fora_da_partida_nao_tem_round():
    partes = dividir_partes(REAL, ABATES, DURACAO)
    p, n, res = round_de(300.0, partes)            # Deathmatch antes da partida
    assert p.tipo == "deathmatch" and n is None and res is None
    p, n, res = round_de(2400.0, partes)           # depois da partida
    assert n is None and res is None


def test_parte_de_borda_final_exclusiva():
    partes = dividir_partes(REAL, ABATES, DURACAO)
    assert parte_de(0.0, partes).tipo == "deathmatch"
    assert parte_de(DURACAO + 1, partes) is None


def test_capitulos_youtube_validos():
    partes = dividir_partes(REAL, ABATES, DURACAO)
    caps = capitulos_youtube(partes, DURACAO)
    assert caps[0][0] == 0.0 and len(caps) == 3
    assert all(b[0] - a[0] >= 10 for a, b in zip(caps, caps[1:]))
    assert caps[1][1] == "Partida 1 (10x6)"


def test_capitulos_youtube_vazio_se_nao_der_para_montar():
    # só 1 parte -> o YouTube exige 3 capítulos no mínimo; devolve vazio em vez de lista inválida
    assert capitulos_youtube(dividir_partes([], [], 600.0), 600.0) == []


def test_texto_partes_mostra_placar_aproximado():
    txt = texto_partes(dividir_partes(REAL, ABATES, DURACAO), "2894824156", DURACAO)
    assert "Partida 1 (10x6)" in txt and "placar aproximado" in txt and "16 rounds lidos" in txt
    assert txt.startswith("Partes do vídeo 2894824156 (51:09)")


def test_gravar_e_carregar_partes_ida_e_volta(tmp_path):
    from cs2clip.partes import carregar_partes, gravar_partes
    partes = dividir_partes(REAL, ABATES, DURACAO)
    gravar_partes(tmp_path, partes, "2894824156", DURACAO)
    de_volta = carregar_partes(tmp_path)
    assert [(p.tipo, p.titulo, len(p.rounds)) for p in de_volta] == [(p.tipo, p.titulo, len(p.rounds)) for p in partes]
    assert (tmp_path / "partes.txt").read_text(encoding="utf-8").startswith("Partes do vídeo 2894824156")


def test_carregar_partes_sem_arquivo_devolve_vazio(tmp_path):
    from cs2clip.partes import carregar_partes
    assert carregar_partes(tmp_path) == []


def test_linhas_de_capitulos_no_formato_do_youtube():
    from cs2clip.partes import linhas_capitulos
    assert linhas_capitulos([(0.0, "Início"), (764.0, "Partida 1 (10x6)"), (3725.0, "Fim")]) == [
        "00:00 Início", "12:44 Partida 1 (10x6)", "1:02:05 Fim"]


def test_sem_nenhuma_partida_nao_chama_de_deathmatch():
    """Um clipe de uma partida competitiva (sem banners) tem muitos abates, mas não é Deathmatch."""
    partes = dividir_partes([], [10.0, 20.0, 30.0, 40.0, 50.0], 600.0)
    assert [p.tipo for p in partes] == ["fora"]
    assert partes[0].abates == 5
