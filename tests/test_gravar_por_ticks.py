"""23/11/2026: gravar uma jogada pelos TICKS da demo (ex.: de 106500 a 110100), não o round inteiro.
Os números dos rounds anotados à mão estavam 1 abaixo da contagem antiga do Bisturi (os ticks estavam certos: caem em jogadas de 4 e 3 abates)."""
import pytest

from cs2clip.demo import Abate, PartidaDemo, RoundDemo, trechos_por_ticks

T = 64.0


def _partida():
    rounds = [RoundDemo(12, 98197 / T, 101462 / T, "T", "x", tick_inicio=98197, tick_fim=101462),
              RoundDemo(13, 103190 / T, 109891 / T, "T", "x", tick_inicio=103190, tick_fim=109891)]
    p = PartidaDemo("de_dust2", {1: "molodoy"}, 1, "molodoy", rounds, patch_version="14197", user_id=3, accountid=1)
    for tk, rnd in ((100410, 12), (107487, 13), (108794, 13), (109039, 13), (109891, 13)):
        p.abates.append(Abate(tk / T, rnd, "x", "awp", tick=tk))
    return p


def test_ticks_viram_um_trecho_do_round_certo_com_os_abates_dentro():
    t = trechos_por_ticks(_partida(), "106500-110100")
    assert len(t) == 1
    x = t[0]
    assert (x.round, x.tick_ini, x.tick_fim, x.abates) == (13, 106500, 110100, 4) and x.ticks_abates == [107487, 108794, 109039, 109891]
    assert abs(x.duracao_s() - 56.25) < 1e-6


def test_varios_trechos_e_espacos_na_lista():
    p = _partida()
    t = trechos_por_ticks(p, " 99000-101000 , 106500-110100 ")
    assert [(x.round, x.abates) for x in t] == [(12, 1), (13, 4)]


@pytest.mark.parametrize("ruim, trecho", [
    ("110100-106500", "o fim vem antes do começo"), ("abc", "formato"), ("106500", "formato"), ("-5-100", "formato"),
    ("106500-106500", "o fim vem antes"), ("106500-" + str(106500 + 64 * 601), "mais de 10 minutos"),
    ("500000-510000", "nenhum round"), ("", "vazio")])
def test_ticks_ruins_dao_erro_claro(ruim, trecho):
    with pytest.raises(ValueError):
        trechos_por_ticks(_partida(), ruim)


def test_dois_trechos_no_mesmo_round_sao_recusados():
    # o arquivo do round é um por round (`round_13.mp4`): dois trechos se sobrescreveriam
    with pytest.raises(ValueError, match="mesmo round"):
        trechos_por_ticks(_partida(), "104000-106000,107000-109000")


def test_linha_de_comando_so_checar_mostra_o_trecho_por_ticks(tmp_path, monkeypatch, capsys):
    from cs2clip import gravador_demo
    monkeypatch.setattr("cs2clip.demo.ler_demo", lambda caminho, quem=None: _partida())
    r = gravador_demo.main(["--demo", "x.dem", "--jogador=molodoy", "--ticks", "106500-110100", "--so-checar", "--config", str(tmp_path / "c.toml")])
    saida = capsys.readouterr().out
    assert "Rounds a gravar (1): 13 (4 abates, 56 s)" in saida and r == 0
    # ticks inválidos: para antes de abrir qualquer coisa, com mensagem
    assert gravador_demo.main(["--demo", "x.dem", "--jogador=molodoy", "--ticks", "1-2-3", "--so-checar", "--config", str(tmp_path / "c.toml")]) == 1
    assert "ticks" in capsys.readouterr().out.lower()


def test_tick_no_intervalo_entre_dois_rounds_fica_no_round_que_mais_cobre():
    # revisão de 23/11: entre o fim do round 12 (101462) e a ação do 13 (103190) as janelas se sobrepunham; o tick 101910 caía no 12, com 0 abates
    t = trechos_por_ticks(_partida(), "101910-108000")
    assert (t[0].round, t[0].abates) == (13, 1)                                          # só o abate de 107487 cai dentro (o de 108794 não)
