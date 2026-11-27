"""B1 (08/11/2026): achar a demo do CS2 sozinho, casar com a gravação do OBS e descobrir quem é o dono, sem mexer em nada do jogo."""
import datetime
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cs2clip import demos_cs2 as dc


def _varint(n: int) -> bytes:
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        saida.append(b | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def _info(epoch: int) -> bytes:
    """`.dem.info` sintético no formato do CS2: campo 1 (id) varint, campo 2 (matchtime) varint, campo 3 (bloco) com bytes."""
    return b"\x08" + _varint(3846488745834446879) + b"\x10" + _varint(epoch) + b"\x1a\x03abc" + b"\x25\x01\x02\x03\x04"


def _t(texto: str) -> datetime.datetime:
    return datetime.datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")


def _demo(pasta: Path, nome: str, quando: datetime.datetime | None, conteudo: bytes = b"DEMO"):
    dem = pasta / nome
    dem.write_bytes(conteudo)
    if quando is not None:
        (pasta / (nome + ".info")).write_bytes(_info(int(quando.timestamp())))
    return dem


def test_matchtime_do_dem_info_e_lido_e_lixo_nao_derruba(tmp_path):
    quando = _t("2026-10-04 21:12:24")
    arq = tmp_path / "a.dem.info"
    arq.write_bytes(_info(int(quando.timestamp())))
    assert dc.ler_matchtime(arq) == quando
    ruim = tmp_path / "b.dem.info"
    ruim.write_bytes(b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff")
    assert dc.ler_matchtime(ruim) is None
    assert dc.ler_matchtime(tmp_path / "nao_existe.dem.info") is None
    vazio = tmp_path / "c.dem.info"
    vazio.write_bytes(b"")
    assert dc.ler_matchtime(vazio) is None


def test_lista_as_demos_com_data_da_partida_ou_aproximada(tmp_path):
    _demo(tmp_path, "match730_b.dem", _t("2026-10-20 03:33:25"))
    _demo(tmp_path, "match730_a.dem", _t("2026-10-04 21:12:24"))
    _demo(tmp_path, "match730_sem_info.dem", None)
    (tmp_path / "outro.dem").write_bytes(b"x")                       # não é do Valve: ignorado
    achadas = dc.listar_demos(tmp_path)
    por_nome = {d.caminho.name: d for d in achadas}
    assert set(por_nome) == {"match730_a.dem", "match730_b.dem", "match730_sem_info.dem"}
    assert por_nome["match730_a.dem"].quando == _t("2026-10-04 21:12:24") and not por_nome["match730_a.dem"].aproximada
    assert por_nome["match730_sem_info.dem"].aproximada                  # sem .dem.info: data do arquivo, marcada como aproximada
    assert [d.caminho.name for d in achadas][:2] == ["match730_a.dem", "match730_b.dem"]      # em ordem de data
    assert dc.listar_demos(tmp_path / "nao_existe") == []


def test_inicio_da_gravacao_vem_do_nome_do_obs_ou_do_arquivo(tmp_path):
    assert dc.inicio_da_gravacao(Path("2026-11-04 21-39-10.mp4"), 3921) == _t("2026-11-04 21:39:10")
    sem_nome = tmp_path / "partida.mp4"
    sem_nome.write_bytes(b"x")
    os.utime(sem_nome, (_t("2026-11-04 23:00:00").timestamp(),) * 2)
    assert dc.inicio_da_gravacao(sem_nome, 3600) == _t("2026-11-04 22:00:00")      # data do arquivo - duração
    assert dc.inicio_da_gravacao(tmp_path / "nao_existe.mp4", 10) is None


def test_demos_de_outubro_nao_casam_com_a_gravacao_de_novembro_e_a_certa_casa(tmp_path):
    """Controle negativo real: as duas demos que o CS2 já guardou (04/10 e 20/10) não são da gravação de 04/11."""
    video = Path("2026-11-04 21-39-10.mp4")
    out = dc.DemoCS2
    de_outubro = [out(Path("a.dem"), _t("2026-10-04 21:12:24"), False), out(Path("b.dem"), _t("2026-10-20 03:33:25"), False)]
    assert dc.candidatas(video, 3921, de_outubro) == []
    certa = out(Path("c.dem"), _t("2026-11-04 21:50:00"), False)
    perto = out(Path("d.dem"), _t("2026-11-04 22:30:00"), False)
    longe_depois = out(Path("e.dem"), _t("2026-11-05 03:00:00"), False)
    # 90 min antes do início ainda conta (a data da demo pode ser o fim da partida anterior/o início desta)
    folga = out(Path("f.dem"), _t("2026-11-04 20:20:00"), False)
    cedo_demais = out(Path("g.dem"), _t("2026-11-04 19:30:00"), False)
    achadas = dc.candidatas(video, 3921, de_outubro + [certa, perto, longe_depois, folga, cedo_demais])
    nomes = [d.caminho.name for d in achadas]
    assert nomes == ["d.dem", "c.dem", "f.dem"]                        # da mais perto do meio da gravação (22:11) para a mais longe
    assert dc.candidatas(Path("nao_existe_sem_data.mp4"), 100, [certa]) == []      # sem nome do OBS nem arquivo: não adivinha


def test_copia_confere_o_hash_nao_mexe_na_original_e_reaproveita(tmp_path):
    origem = tmp_path / "replays"
    origem.mkdir()
    dem = _demo(origem, "match730_x.dem", _t("2026-11-04 21:50:00"), conteudo=b"A" * 5000)
    destino = tmp_path / "projeto" / "demos"
    copia = dc.copiar_para_projeto(dem, destino)
    assert copia.read_bytes() == dem.read_bytes() and dem.exists()                 # original intacta
    assert (destino / "match730_x.dem.info").is_file()                              # o .dem.info vai junto
    antes = copia.stat().st_mtime_ns
    assert dc.copiar_para_projeto(dem, destino) == copia and copia.stat().st_mtime_ns == antes     # já igual: reaproveita
    assert not list(destino.glob("*.parcial"))


def test_copia_que_nao_bate_o_hash_e_recusada_sem_deixar_lixo(tmp_path, monkeypatch):
    origem = tmp_path / "replays"
    origem.mkdir()
    dem = _demo(origem, "match730_y.dem", None, conteudo=b"B" * 5000)
    import shutil as sh

    def copia_corrompida(src, dst, *a, **k):
        Path(dst).write_bytes(b"C" * 5000)
        return dst
    monkeypatch.setattr(dc.shutil, "copy2", copia_corrompida)
    with pytest.raises(OSError, match="hash"):
        dc.copiar_para_projeto(dem, tmp_path / "demos")
    assert not list((tmp_path / "demos").glob("*.dem*")) and dem.read_bytes() == b"B" * 5000
    assert sh is dc.shutil


def test_pasta_replays_pela_biblioteca_do_steam_sem_ler_segredos(tmp_path):
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    outra = tmp_path / "Jogos"
    replays = outra / dc.RELATIVO_REPLAYS
    replays.mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"' + str(steam).replace("\\", "\\\\") + '"\n\t}\n'
        '\t"1"\n\t{\n\t\t"path"\t\t"' + str(outra).replace("\\", "\\\\") + '"\n\t}\n}\n', encoding="utf-8")
    (steam / "config").mkdir()
    (steam / "config" / "loginusers.vdf").write_text("SEGREDO", encoding="utf-8")        # nunca é aberto
    assert dc.pasta_replays(steam) == replays
    assert dc.pasta_replays(tmp_path / "vazio") is None


# ── quem é o dono ────────────────────────────────────────────────────────────────────────────────

def _falsa_demo(monkeypatch, jogadores: dict[int, tuple[str, list[float]]]):
    """Troca a leitura real de demo (demoparser2) por jogadores sintéticos: {steamid: (nome, tempos dos abates na demo)}."""
    import cs2clip.demo as demo_mod

    def listar(caminho):
        return sorted(((n, s, len(t)) for s, (n, t) in jogadores.items()), key=lambda x: -x[2])

    def ler(caminho, quem=None):
        for sid, (nome, tempos) in jogadores.items():
            if quem in (sid, str(sid), nome) or (isinstance(quem, str) and quem.lower() in nome.lower()):
                return SimpleNamespace(jogador=sid, nome=nome, abates=[SimpleNamespace(t=x) for x in tempos])
        return SimpleNamespace(jogador=None, nome="", abates=[])
    monkeypatch.setattr(demo_mod, "listar_jogadores", listar)
    monkeypatch.setattr(demo_mod, "ler_demo", ler)


def test_escolhe_o_dono_pelos_abates_que_casam_com_o_kill_feed(monkeypatch):
    dono = [100.0, 130.0, 190.0, 260.0, 300.0, 410.0, 520.0]
    outro_a = [50.0, 90.0, 220.0, 340.0, 480.0, 600.0, 700.0, 800.0]          # mais abates, mas em outros instantes
    outro_b = [105.0, 600.0, 650.0, 705.0]
    _falsa_demo(monkeypatch, {1: ("Fulano", outro_a), 2: ("Dono Exemplo", dono), 3: ("Beltrano", outro_b)})
    video = [t + 125.3 for t in dono]                                            # o vídeo começou 125,3 s antes da demo
    escolhido = dc.escolher_dono(Path("x.dem"), video)
    assert escolhido is not None and escolhido.nome == "Dono Exemplo" and escolhido.steamid == 2
    assert escolhido.casados == len(dono) and escolhido.confianca in ("alta", "media")
    # com o nome dado só ele é testado
    so_ele = dc.escolher_dono(Path("x.dem"), video, "Dono Exemplo")
    assert so_ele is not None and so_ele.steamid == 2
    assert dc.escolher_dono(Path("x.dem"), video, "Fulano") is None              # o nome errado não casa com este vídeo


def test_demo_de_outra_partida_nao_acha_dono(monkeypatch):
    """Controle: abates de um vídeo que não é desta partida não casam com ninguém → None (a demo é descartada)."""
    _falsa_demo(monkeypatch, {1: ("A", [100.0, 130.0, 190.0, 260.0, 300.0]), 2: ("B", [110.0, 170.0, 230.0, 320.0, 400.0])})
    outro_video = [11.0, 77.0, 143.0, 520.0, 999.0, 1500.0, 2100.0]
    assert dc.escolher_dono(Path("x.dem"), outro_video) is None
    assert dc.escolher_dono(Path("x.dem"), []) is None


# ── ligação com o programa (B1) ──────────────────────────────────────────────────────────────────

def test_config_demo_automatica_nasce_desligada():
    from cs2clip.config import Config
    assert Config().demo.automatica is False


def test_resolver_demos_auto_usa_o_dono_achado_e_descarta_demo_de_outra_gravacao(monkeypatch, capsys):
    from cs2clip import pipeline as pl
    from cs2clip.moments import Event
    import cs2clip.demo as demo_mod

    donos = {"boa.dem": dc.Dono("Dono Exemplo", 76561198000000001, 12, 13, 0.1, "alta"), "outra.dem": None}
    monkeypatch.setattr(dc, "escolher_dono", lambda demo, tempos, quem=None, **_extra: donos[Path(demo).name])
    monkeypatch.setattr(demo_mod, "ler_demo", lambda demo, ident=None: SimpleNamespace(nome="Dono Exemplo", jogador=ident))
    eventos = [Event(10.0, "kill"), Event(20.0, "assist"), Event(30.0, "kill")]
    saida = pl._resolver_demos_auto([Path("boa.dem"), Path("outra.dem")], eventos, "")
    texto = capsys.readouterr().out
    assert [nome for _, nome in saida] == ["boa.dem"]                                    # a outra foi descartada
    assert "outra.dem" in texto and "não é desta gravação" in texto
    assert "você é 'Dono Exemplo'" in texto and "[demo] jogador" in texto                      # sem o nome no config: sugere gravar


def test_dono_recebe_so_os_abates_e_o_nome_dado_nao_gera_dica(monkeypatch, capsys):
    from cs2clip import pipeline as pl
    from cs2clip.moments import Event
    import cs2clip.demo as demo_mod

    vistos = {}

    def dono(demo, tempos, quem=None, **_extra):
        vistos["tempos"], vistos["quem"] = list(tempos), quem
        return dc.Dono("Dono Exemplo", 1, 5, 5, 0.1, "alta")
    monkeypatch.setattr(dc, "escolher_dono", dono)
    monkeypatch.setattr(demo_mod, "ler_demo", lambda demo, ident=None: SimpleNamespace(nome="Dono Exemplo", jogador=ident))
    pl._resolver_demos_auto([Path("x.dem")], [Event(10.0, "kill"), Event(20.0, "assist"), Event(30.0, "death")], "Dono Exemplo")
    assert vistos == {"tempos": [10.0], "quem": "Dono Exemplo"}                              # assistência e morte não entram na sincronia
    assert "[demo] jogador" not in capsys.readouterr().out


def test_demos_automaticas_do_video_avisa_quando_nao_acha_e_copia_quando_acha(monkeypatch, tmp_path, capsys):
    from cs2clip import __main__ as cli
    replays = tmp_path / "replays"
    replays.mkdir()
    monkeypatch.setattr(dc, "pasta_replays", lambda steam=None: replays)
    monkeypatch.setattr(dc, "PASTA_DEMOS_PROJETO", tmp_path / "projeto" / "demos")
    import cs2clip.scan as scan_mod
    monkeypatch.setattr(scan_mod, "probe", lambda caminho: SimpleNamespace(duration=3921.0))
    video = tmp_path / "2026-11-04 21-39-10.mp4"
    video.write_bytes(b"v")
    _demo(replays, "match730_velha.dem", _t("2026-10-04 21:12:24"))                  # de outubro: não é desta gravação
    assert cli._demos_automaticas(video) == []
    assert "nenhuma demo do CS2 cai na hora" in capsys.readouterr().out
    _demo(replays, "match730_certa.dem", _t("2026-11-04 22:05:00"), conteudo=b"DEMO-DE-NOVEMBRO")
    achadas = cli._demos_automaticas(video)
    assert [d.name for d in achadas] == ["match730_certa.dem"] and achadas[0].parent == tmp_path / "projeto" / "demos"
    assert (replays / "match730_certa.dem").exists() and (replays / "match730_velha.dem").exists()      # originais intactas
    monkeypatch.setattr(dc, "pasta_replays", lambda steam=None: None)
    assert cli._demos_automaticas(video) == []
    assert "não achei a pasta de demos do CS2" in capsys.readouterr().out


def test_linha_de_comando_aceita_demo_auto_e_recusa_outros_valores():
    from cs2clip import __main__ as cli
    with pytest.raises(SystemExit) as erro:
        cli.main(["--demo", "qualquer"])
    assert erro.value.code == 2


def test_vod_da_twitch_testa_as_demos_mais_novas_porque_a_data_do_arquivo_e_a_do_download(tmp_path):
    base = datetime.datetime(2026, 11, 8, 20, 0, 0)
    demos = [dc.DemoCS2(tmp_path / f"match730_{i:021d}_{i:010d}_20{i}.dem", base - datetime.timedelta(days=i), False) for i in range(1, 10)]
    vod = tmp_path / "1234567890-twitch.mp4"
    vod.write_bytes(b"x")                                                          # mtime = agora: nada a ver com a live
    c = dc.candidatas(vod, 3600.0, demos)
    assert [d.caminho.name for d in c] == [d.caminho.name for d in demos[:6]] and dc.e_vod_da_twitch(vod)
    comum = tmp_path / "2026-11-04 21-39-10.mkv"                                   # CONTROLE: arquivo comum continua pela hora (aqui, nenhuma demo cai nela)
    comum.write_bytes(b"x")
    assert dc.candidatas(comum, 3600.0, demos) == [] and not dc.e_vod_da_twitch(comum)
    assert not dc.e_vod_da_twitch(tmp_path / "123-twitch-copia.mp4") and not dc.e_vod_da_twitch(tmp_path / "12345-twitch.mp4")


def test_demos_da_gravacao_do_vod_nao_copia_nada_para_o_projeto(tmp_path, monkeypatch):
    pasta = tmp_path / "replays"
    pasta.mkdir()
    for i in range(1, 4):
        (pasta / f"match730_{i:021d}_{i:010d}_20{i}.dem").write_bytes(b"PBDEMS2" + b"x" * 100)
    copias = []
    monkeypatch.setattr(dc, "copiar_para_projeto", lambda d, destino=None: copias.append(d) or d)
    vod = tmp_path / "1234567890-twitch.mp4"
    vod.write_bytes(b"x")
    achadas = dc.demos_da_gravacao(vod, 3600.0, pasta)
    assert len(achadas) == 3 and all(a.parent == pasta for a in achadas) and copias == []

