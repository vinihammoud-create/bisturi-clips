"""M3 (plano do material novo, 15/11/2026): o VOD baixado pelo painel da Twitch (`<id>-<canal>-<uuid>.mp4`) acha a demo certa, inclusive 2 partidas.

Antes: o nome não era reconhecido como VOD, a data do arquivo (a do download) enganava e `candidatas` devolvia lista vazia. Agora: o nome é reconhecido; a hora real da
live vem de uma consulta ao Twitch só daquele VOD (`TwitchDownloaderCLI info --id`, que não baixa vídeo); só as demos cuja partida cai na live são candidatas.
"""
import datetime
import json
import subprocess
from pathlib import Path

import pytest

from cs2clip import demos_cs2 as dc
from cs2clip import twitch_vod as tv

ID_PAINEL = "1234571000-9876543210-00000000-0000-4000-8000-0000000000a2"
INFO_REAL = ('[STATUS] - Fetching Video Info [1/1]\n{"data":{"video":{"title":"x","createdAt":"2026-11-14T00:35:15Z","lengthSeconds":6148,'
             '"owner":{"id":"1","displayName":"D","login":"d"},"viewCount":5}}}\n')


def _t(texto: str) -> datetime.datetime:
    return datetime.datetime.strptime(texto, "%Y-%m-%d %H:%M:%S")


def _demo(tmp_path, quando: str, nome: str) -> dc.DemoCS2:
    return dc.DemoCS2(tmp_path / f"match730_{nome}.dem", _t(quando), False)


# ── 1. o nome do arquivo ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nome, esperado", [
    (f"{ID_PAINEL}.mp4", "1234571000"),                       # baixado pelo painel da Twitch
    (f"{ID_PAINEL} (1).mp4", "1234571000"),                   # cópia repetida
    ("1234570000-twitch.mp4", "1234570000"),                  # baixado pelo Bisturi
    ("1234570000-TWITCH (12).mp4", "1234570000"),             # maiúsculas e cópia repetida
    (f"{ID_PAINEL.upper()}.MP4", "1234571000"),               # uuid em maiúsculas, extensão em maiúsculas
    ("1234570000-twitch (2).mp4", "1234570000"),
])
def test_o_nome_de_um_vod_da_twitch_e_reconhecido(tmp_path, nome, esperado):
    assert dc.vod_id_do_arquivo(tmp_path / nome) == esperado and dc.e_vod_da_twitch(tmp_path / nome)


@pytest.mark.parametrize("nome", ["2026-11-13 11-29-31.mp4", "20261017_201533.mp4", "123-twitch.mp4", "12345-twitch.mp4", "123-twitch-copia.mp4",
                                  "match730_003854107564698501134_0851608187_201.dem", "1234571000.mp4", "partida final.mp4", "1234571000-.mp4",
                                  "12345710001234-twitch.mp4",
                                  # revisão do qa: nome de arquivo do dono que só COMEÇA com 9 a 11 dígitos não é VOD (antes o ramo <id>-<palavra> aceitava)
                                  "2026111311-gameplay.mp4", "2026111311-clip.mp4", "20261113114-gameplay.mp4", "1234567890-final.mp4", "1234567890-reacao.mkv",
                                  "1234570000-twitch_corte.mp4", "1234570000-9876543210.mp4", "1234570000-canaldeexemplo.mp4", "１２３４５７１０００-twitch.mp4"])
def test_controle_o_que_nao_e_vod_continua_nao_sendo(tmp_path, nome):
    assert dc.vod_id_do_arquivo(tmp_path / nome) is None and not dc.e_vod_da_twitch(tmp_path / nome)


# ── 2. a consulta ao Twitch (sem rede nos testes) ────────────────────────────────────────────────

def _cli(tmp_path):
    cli = tmp_path / "TwitchDownloaderCLI.exe"
    cli.write_bytes(b"fake")
    return cli


def _executor(saida: str = INFO_REAL, codigo: int = 0, erro: Exception | None = None, chamadas: list | None = None):
    def executar(cmd, **kw):
        if chamadas is not None:
            chamadas.append((list(cmd), kw))
        if erro:
            raise erro
        return subprocess.CompletedProcess(cmd, codigo, stdout=saida, stderr="")
    return executar


def test_info_do_vod_le_a_hora_da_live_e_a_duracao(tmp_path):
    chamadas: list = []
    info = tv.info_do_vod("1234571000", cli=_cli(tmp_path), executar=_executor(chamadas=chamadas))
    assert info == {"inicio_utc": "2026-11-14T00:35:15+00:00", "duracao_s": 6148}
    cmd, kw = chamadas[0]
    assert cmd[1] == "info" and "--id" in cmd and "1234571000" in cmd and "--format" in cmd and "Raw" in cmd and "--oauth" not in cmd
    assert 0 < kw["timeout"] <= 30                                                       # prazo curto: sem internet não demora


@pytest.mark.parametrize("executor", [
    _executor(erro=subprocess.TimeoutExpired("x", 20)), _executor(erro=OSError("sem o programa")), _executor(codigo=1, saida="[ERROR] - x"),
    _executor(saida="lixo sem json"), _executor(saida='{"data":{"video":null}}'), _executor(saida='{"data":{"video":{"createdAt":"ontem"}}}'),
    _executor(saida='{"data":{"video":{"createdAt":"2026-11-14T00:35:15Z","lengthSeconds":"muito"}}}'),
    _executor(saida='{"data":{"video":{"createdAt":"2026-11-14T00:35:15Z","lengthSeconds":0}}}'),
], ids=["prazo", "sem-exe", "codigo-1", "sem-json", "video-nulo", "data-ruim", "duracao-ruim", "duracao-zero"])
def test_info_do_vod_nunca_levanta_erro_e_devolve_none(tmp_path, executor):
    assert tv.info_do_vod("1234571000", cli=_cli(tmp_path), executar=executor) is None


def test_info_do_vod_recusa_id_estranho_sem_chamar_nada(tmp_path):
    chamadas: list = []
    for ruim in ("123", "abc", "1234571000; calc", "--oauth", ""):
        assert tv.info_do_vod(ruim, cli=_cli(tmp_path), executar=_executor(chamadas=chamadas)) is None
    assert chamadas == []
    assert tv.info_do_vod("1234571000", cli=tmp_path / "nao_existe.exe", executar=_executor(chamadas=chamadas)) is None and chamadas == []


# ── 3. a hora do vídeo (com cache e sem internet) ────────────────────────────────────────────────

def test_inicio_do_vod_converte_utc_para_hora_local_e_guarda_o_resultado(tmp_path, monkeypatch):
    video = tmp_path / f"{ID_PAINEL}.mp4"
    chamadas: list = []
    monkeypatch.setattr(tv, "info_do_vod", lambda vod, **kw: chamadas.append(vod) or {"inicio_utc": "2026-11-14T00:35:15+00:00", "duracao_s": 6148})
    cache = tmp_path / "cache_vod"
    esperado = datetime.datetime(2026, 11, 14, 0, 35, 15, tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)
    assert dc.inicio_do_vod(video, pasta_cache=cache) == esperado
    assert dc.inicio_do_vod(video, pasta_cache=cache) == esperado and chamadas == ["1234571000"]      # 2ª vez: do cache, sem consultar
    assert json.loads((cache / "1234571000.json").read_text(encoding="utf-8"))["inicio_utc"] == "2026-11-14T00:35:15+00:00"


def test_inicio_do_vod_sem_consulta_ou_sem_internet_devolve_none_e_nao_repete_a_tentativa(tmp_path, monkeypatch):
    video = tmp_path / f"{ID_PAINEL}.mp4"
    chamadas: list = []
    monkeypatch.setattr(tv, "info_do_vod", lambda vod, **kw: chamadas.append(vod) or None)
    cache = tmp_path / "cache_vod"
    assert dc.inicio_do_vod(video, pasta_cache=cache, consultar=False) is None and chamadas == []        # D2: interruptor desligado
    assert dc.inicio_do_vod(video, pasta_cache=cache) is None and dc.inicio_do_vod(video, pasta_cache=cache) is None
    assert chamadas == ["1234571000"]                                                    # falhou uma vez: não insiste a cada chamada
    assert not cache.exists() or not list(cache.glob("*.json"))                          # falha não vira cache
    assert dc.inicio_do_vod(tmp_path / "2026-11-13 11-29-31.mp4", pasta_cache=cache) is None


def test_cache_corrompido_e_ignorado(tmp_path, monkeypatch):
    video = tmp_path / f"{ID_PAINEL}.mp4"
    cache = tmp_path / "cache_vod"
    cache.mkdir()
    (cache / "1234571000.json").write_text("{quebrado", encoding="utf-8")
    monkeypatch.setattr(tv, "info_do_vod", lambda vod, **kw: {"inicio_utc": "2026-11-14T00:35:15+00:00", "duracao_s": 6148})
    assert dc.inicio_do_vod(video, pasta_cache=cache) is not None


# ── 4. quais demos são candidatas ────────────────────────────────────────────────────────────────

def _demos_de_novembro(tmp_path):
    return [_demo(tmp_path, "2026-11-11 20:58:29", "a_11_20"), _demo(tmp_path, "2026-11-11 21:54:33", "b_11_21"),
            _demo(tmp_path, "2026-11-13 22:31:39", "c_cache"), _demo(tmp_path, "2026-11-14 22:39:29", "d_anubis"),
            _demo(tmp_path, "2026-11-14 23:17:29", "e_mirage"), _demo(tmp_path, "2026-10-04 21:12:24", "f_inferno")]


def test_com_a_hora_da_live_so_as_demos_que_caem_nela_sao_candidatas(tmp_path):
    demos = _demos_de_novembro(tmp_path)
    vod_dois_jogos = tmp_path / "1234572000-9876543210-00000000-0000-4000-8000-0000000000a3.mp4"          # live de 14/11 22:36:28, 1h17m44s
    c = dc.candidatas(vod_dois_jogos, 4664.0, demos, inicio_vod=_t("2026-11-14 22:36:28"))
    assert [d.caminho.name for d in c] == ["match730_d_anubis.dem", "match730_e_mirage.dem"]              # as 2 partidas, em ordem
    vod_um_jogo = tmp_path / f"{ID_PAINEL}.mp4"                                                          # live de 13/11 21:35:15, 1h42m28s
    c2 = dc.candidatas(vod_um_jogo, 6148.0, demos, inicio_vod=_t("2026-11-13 21:35:15"))
    assert [d.caminho.name for d in c2] == ["match730_c_cache.dem"]                                      # nem as de 11/11 nem as de 14/11


def test_partida_que_comecou_um_pouco_antes_da_live_ainda_e_candidata_e_a_que_comecou_depois_do_fim_nao(tmp_path):
    vod = tmp_path / f"{ID_PAINEL}.mp4"
    inicio = _t("2026-11-13 21:00:00")
    antes = _demo(tmp_path, "2026-11-13 20:30:00", "antes")            # começou 30 min antes e ainda estava no começo da live
    muito_antes = _demo(tmp_path, "2026-11-13 18:00:00", "muito_antes")
    depois_do_fim = _demo(tmp_path, "2026-11-13 23:00:00", "depois")   # a live (1 h) acabou às 22:00
    dentro = _demo(tmp_path, "2026-11-13 21:40:00", "dentro")
    c = dc.candidatas(vod, 3600.0, [depois_do_fim, muito_antes, dentro, antes], inicio_vod=inicio)
    assert [d.caminho.name for d in c] == ["match730_antes.dem", "match730_dentro.dem"]


def test_sem_a_hora_da_live_o_comportamento_de_antes_continua_as_mais_novas(tmp_path):
    base = datetime.datetime(2026, 11, 8, 20, 0, 0)
    demos = [dc.DemoCS2(tmp_path / f"match730_{i}.dem", base - datetime.timedelta(days=i), False) for i in range(1, 10)]
    vod = tmp_path / f"{ID_PAINEL}.mp4"
    c = dc.candidatas(vod, 3600.0, demos)                              # inicio_vod=None (sem internet ou consulta desligada)
    assert [d.caminho.name for d in c] == [d.caminho.name for d in demos[:6]]


def test_demos_da_gravacao_do_vod_usa_a_hora_da_live_e_nao_copia_nada(tmp_path, monkeypatch):
    pasta = tmp_path / "replays"
    pasta.mkdir()
    quando = {"c_cache": "2026-11-13 22:31:39", "d_anubis": "2026-11-14 22:39:29", "e_mirage": "2026-11-14 23:17:29"}
    for nome, q in quando.items():
        dem = pasta / f"match730_{nome}.dem"
        dem.write_bytes(b"PBDEMS2" + b"x" * 50)
        epoch = int(_t(q).timestamp())
        (pasta / f"match730_{nome}.dem.info").write_bytes(b"\x08\x01\x10" + dc_varint(epoch))
    monkeypatch.setattr(dc, "copiar_para_projeto", lambda d, destino=None: pytest.fail("VOD não copia demo para o projeto"))
    monkeypatch.setattr(tv, "info_do_vod", lambda vod, **kw: {"inicio_utc": _t("2026-11-14 22:36:28").astimezone(datetime.timezone.utc).isoformat(), "duracao_s": 4664})
    vod = tmp_path / "1234572000-9876543210-00000000-0000-4000-8000-0000000000a3.mp4"
    vod.write_bytes(b"x")
    achadas = dc.demos_da_gravacao(vod, 4664.0, pasta, pasta_cache=tmp_path / "cache")
    assert [a.name for a in achadas] == ["match730_d_anubis.dem", "match730_e_mirage.dem"]
    # CONTROLE: com a consulta desligada (D2: sem falar com a Twitch) cai nas mais novas, como antes
    achadas2 = dc.demos_da_gravacao(vod, 4664.0, pasta, consultar_twitch=False, pasta_cache=tmp_path / "cache2")
    assert len(achadas2) == 3


def dc_varint(n: int) -> bytes:
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        saida.append(b | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def test_a_linha_de_comando_so_consulta_a_twitch_quando_o_chat_esta_ligado():
    import inspect

    from cs2clip import __main__ as cli
    assert "consultar_twitch=cfg.chat.usar" in inspect.getsource(cli.main)                # D2: o interruptor é o do chat da Twitch
    assert "consultar_twitch" in inspect.getsource(cli._demos_automaticas)


def test_a_suite_nunca_fala_com_a_twitch_por_engano(tmp_path):
    """O conftest aponta o baixador padrão para um arquivo que não existe: `info_do_vod` sem `cli=` explícito devolve None sem rede."""
    assert tv.info_do_vod("1234571000") is None


# ── revisão do qa do M3: bordas ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("saida", ['{"data":{"video":{"createdAt":"2026-11-14T00:35:15Z","lengthSeconds":1e999}}}',
                                   '{"data":{"video":{"createdAt":"0001-01-01T00:00:00Z","lengthSeconds":100}}}',
                                   '{"data":{"video":{"createdAt":"9999-12-31T23:59:59Z","lengthSeconds":100}}}',
                                   "[" * 100000 + "]" * 100000], ids=["infinito", "ano-1", "ano-9999", "json-aninhado"])
def test_info_do_vod_valores_absurdos_tambem_dao_none_sem_erro(tmp_path, saida):
    assert tv.info_do_vod("1234571000", cli=_cli(tmp_path), executar=_executor(saida=saida)) is None


def test_cache_sem_fuso_ou_com_data_absurda_e_ignorado_e_refeito(tmp_path, monkeypatch):
    video = tmp_path / f"{ID_PAINEL}.mp4"
    cache = tmp_path / "cache_vod"
    cache.mkdir()
    chamadas: list = []
    monkeypatch.setattr(tv, "info_do_vod", lambda vod, **kw: chamadas.append(vod) or {"inicio_utc": "2026-11-14T00:35:15+00:00", "duracao_s": 6148})
    for ruim in ("2026-11-14T00:35:15", "0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59+00:00"):      # sem fuso (leria 3 h errado) e anos absurdos
        (cache / "1234571000.json").write_text(json.dumps({"inicio_utc": ruim, "duracao_s": 1}), encoding="utf-8")
        chamadas.clear()
        resposta = dc.inicio_do_vod(video, pasta_cache=cache)
        assert resposta == datetime.datetime(2026, 11, 14, 0, 35, 15, tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None) and chamadas == ["1234571000"]


def test_no_maximo_8_partidas_por_live_e_o_nome_vale_em_maiusculas(tmp_path):
    demos = [_demo(tmp_path, f"2026-11-14 {22 + i // 60:02d}:{i % 60:02d}:00".replace("24:", "23:"), f"n{i:02d}") for i in range(12)]
    vod = tmp_path / f"{ID_PAINEL}.mp4"
    c = dc.candidatas(vod, 8 * 3600.0, demos, inicio_vod=_t("2026-11-14 21:50:00"))
    assert len(c) == dc.LIMITE_DEMOS_COM_HORA == 8
    assert [d.caminho.name for d in c] == [d.caminho.name for d in sorted(demos, key=lambda d: d.quando)[:8]]


def test_o_readme_e_o_checar_dizem_que_a_hora_da_live_e_perguntada_a_twitch_so_com_o_chat_ligado():
    raiz = Path(__file__).resolve().parents[1]
    readme = (raiz / "README.md").read_text(encoding="utf-8")
    assert "hora da live" in readme and "chat" in readme.split("hora da live")[1][:400].lower()
    from cs2clip import checagem
    fonte = Path(checagem.__file__).read_text(encoding="utf-8")
    assert "hora da live" in fonte
