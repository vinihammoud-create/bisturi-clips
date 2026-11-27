"""N1 do plano da regra dos 6 s (15/11/2026): ajustes da revisão do plano do material novo.

1. VOD apagado, expirado ou privado: a tela mostrava `--- End of inner exception stack trace ---`; agora mostra a frase em português.
2. O interruptor do chat da Twitch (decisão D2: sem o chat ligado, nada é perguntado à Twitch) é testado pelo COMPORTAMENTO, não pelo texto do código.
"""
import time
import types
from pathlib import Path

import pytest

from cs2clip import demos_cs2 as dc
from cs2clip import twitch_vod as tv
from ui.vods import GerenciadorVod

# ── 1. a mensagem de erro de um VOD que não existe mais ─────────────────────────────────────────────

FRASE = "esse VOD não existe mais na Twitch (apagado, expirado ou privado)"

# A saída REAL do baixador para o VOD 999999999 (código de saída 127), linha por linha.
SAIDA_REAL = """[STATUS] - Fetching Video Info [1/4]
Unhandled exception. System.AggregateException: One or more errors occurred. (Invalid VOD, deleted/expired VOD possibly?)
 ---> System.NullReferenceException: Invalid VOD, deleted/expired VOD possibly?
   at TwitchDownloaderCore.VideoDownloader.DownloadAsyncImpl(FileInfo outputFileInfo, FileStream outputFs, CancellationToken cancellationToken) in /home/runner/work/TwitchDownloader/TwitchDownloader/TwitchDownloaderCore/VideoDownloader.cs:line 81
   at TwitchDownloaderCore.VideoDownloader.DownloadAsyncImpl(...)
   at TwitchDownloaderCore.VideoDownloader.DownloadAsync(CancellationToken cancellationToken) in .../VideoDownloader.cs:line 44
   at TwitchDownloaderCore.VideoDownloader.DownloadAsync(CancellationToken cancellationToken) in .../VideoDownloader.cs:line 52
   at TwitchDownloaderCore.VideoDownloader.DownloadAsync(CancellationToken cancellationToken) in .../VideoDownloader.cs:line 52
   --- End of inner exception stack trace ---
   at System.Threading.Tasks.Task.ThrowIfExceptional(Boolean includeTaskCanceledExceptions)
   at System.Threading.Tasks.Task.Wait(Int32 millisecondsTimeout, CancellationToken cancellationToken)
   at System.Threading.Tasks.Task.Wait()
   at TwitchDownloaderCLI.Modes.DownloadVideo.Download(VideoDownloadArgs inputOptions) in .../DownloadVideo.cs:line 23
   at CommandLine.ParserResultExtensions.WithParsed[T](ParserResult`1 result, Action`1 action)
   at TwitchDownloaderCLI.Program.Main(String[] args) in .../Program.cs:line 31
"""
LINHAS_REAIS: list[str] = [str(x) for x in SAIDA_REAL.splitlines()]


def test_a_saida_real_de_um_vod_que_nao_existe_vira_a_frase_em_portugues():
    assert len(LINHAS_REAIS) == 15
    assert tv.mensagem_de_erro(LINHAS_REAIS, 127) == FRASE


def test_a_linha_do_fim_do_rastro_e_o_separador_nunca_sao_a_escolhida():
    # sem a frase "Invalid VOD" para disfarçar: a escolhida é a linha que explica, nunca `--- End of …`, `--->` vazio ou `at …`
    linhas = ["Unhandled exception. System.InvalidOperationException: o disco está cheio", " --->", "   at A.B() in x.cs:line 1",
              "   --- End of inner exception stack trace ---", "   at C.D() in y.cs:line 2", "-----", "====="]
    assert tv.mensagem_de_erro(linhas, 1) == "Unhandled exception. System.InvalidOperationException: o disco está cheio"
    # só rastro e separadores: o código de saída
    assert tv.mensagem_de_erro(["   --- End of inner exception stack trace ---", " --->", "   at A.B() in x.cs:line 1", "-----"], 9) == "o baixador terminou com o código 9"
    # a linha de rastro que só CITA "exception" também não ganha
    assert "End of" not in tv.mensagem_de_erro(["Unhandled exception. System.Foo: erro de rede", "   --- End of inner exception stack trace ---"], 1)


def test_a_primeira_linha_que_explica_vence_a_ultima_marcada():
    # `--->` com texto é a exceção de dentro: continua sendo linha que explica (sem o `--->` na frente)
    linhas = ["[STATUS] - x", "Unhandled exception. System.AggregateException: um ou mais erros", " ---> System.IO.IOException: sem espaço em disco",
              "   at A.B() in x.cs:line 1", "   --- End of inner exception stack trace ---", "   at C.D() in y.cs:line 2"]
    escolhida = tv.mensagem_de_erro(linhas, 1)
    assert escolhida.startswith("Unhandled exception") and "End of" not in escolhida and not escolhida.startswith("at ")


def test_vod_apagado_em_varias_formas_do_texto():
    assert tv.mensagem_de_erro(["[ERROR] - Invalid VOD, deleted/expired VOD possibly?"], 1) == FRASE
    assert tv.mensagem_de_erro(["Unhandled exception. System.NullReferenceException: invalid vod"], 1) == FRASE      # maiúsculas não importam


def test_o_caso_que_ja_funcionava_continua_igual():
    linhas = ["Unhandled exception. System.ArgumentException: Only MP4 and M4A audio files are supported.",
              "   at TwitchDownloaderCLI.Modes.DownloadVideo.Download(DownloadOptions inputOptions) in /home/runner/work/DownloadVideo.cs:line 30"]
    texto = tv.mensagem_de_erro(linhas, 1)
    assert texto.startswith("o baixador recusou o formato") and "MP4" in texto and "M4A" in texto


# ── a tela (ui/vods.py) só guarda as últimas 15 linhas: a que explica vem no começo ────────────────────

class _Proc:
    def __init__(self, linhas, codigo):
        self.stdout = iter(linhas)
        self._codigo = codigo

    def wait(self):
        return self._codigo

    def terminate(self):
        pass


def _roda(tmp_path, linhas, codigo=127):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    cli = tmp_path / "TwitchDownloaderCLI.exe"
    cli.write_bytes(b"fake")
    g = GerenciadorVod(entrada, popen_factory=lambda cmd, **kw: _Proc([x + "\n" for x in linhas], codigo), cli=cli)
    g.iniciar("https://www.twitch.tv/videos/999999999")
    fim = time.time() + 5
    while time.time() < fim:
        itens = g.listar()["itens"]
        if itens and itens[-1]["estado"] != "baixando":
            return itens[-1]
        time.sleep(0.02)
    raise AssertionError(f"o job não terminou: {g.listar()}")


def test_na_tela_a_saida_real_vira_a_frase(tmp_path):
    job = _roda(tmp_path, LINHAS_REAIS)
    assert job["estado"] == "erro" and job["erro"] == FRASE


def test_rastro_mais_longo_que_as_15_linhas_guardadas_nao_perde_a_linha_que_explica(tmp_path):
    rastro_longo = LINHAS_REAIS[:3] + [f"   at Empilha.Chamada{i}() in /x/y.cs:line {i}" for i in range(40)] + ["   --- End of inner exception stack trace ---"] + \
        [f"   at Outra.Chamada{i}() in /x/z.cs:line {i}" for i in range(20)]
    assert len(rastro_longo) > 60
    job = _roda(tmp_path, rastro_longo)
    assert job["estado"] == "erro" and job["erro"] == FRASE


def test_a_linha_que_explica_no_meio_de_muita_linha_de_progresso_tambem_nao_se_perde(tmp_path):
    linhas = [f"[STATUS] - Downloading {i % 100}%" for i in range(300)] + ["Unhandled exception. System.IO.IOException: sem espaço em disco"] + \
        [f"   at Empilha.Chamada{i}() in /x/y.cs:line {i}" for i in range(40)]
    job = _roda(tmp_path, linhas, 1)
    assert job["estado"] == "erro" and "sem espaço em disco" in job["erro"] and "Empilha" not in job["erro"]


def test_erro_curto_continua_aparecendo_uma_vez_so(tmp_path):
    job = _roda(tmp_path, ["[ERROR] - VOD is sub-only"], 1)
    assert job["erro"] == "[ERROR] - VOD is sub-only"


# ── 2. o interruptor do chat da Twitch, pelo comportamento ──────────────────────────────────────────────

def _duplo_demos_da_gravacao(monkeypatch):
    vistos: list[dict] = []

    def duplo(video, duracao_s, pasta=None, consultar_twitch=True, pasta_cache=None):
        vistos.append({"video": Path(video).name, "consultar_twitch": consultar_twitch})
        return []

    monkeypatch.setattr(dc, "demos_da_gravacao", duplo)
    return vistos


def _com_pasta_de_replays(monkeypatch, tmp_path):
    import cs2clip.scan as scan
    pasta = tmp_path / "replays"
    pasta.mkdir(exist_ok=True)
    monkeypatch.setattr(dc, "pasta_replays", lambda steam=None: pasta)
    monkeypatch.setattr(scan, "probe", lambda p: types.SimpleNamespace(duration=100.0))
    return pasta


@pytest.mark.parametrize("ligado", [False, True])
def test_demos_automaticas_repassa_o_interruptor_ao_buscador_de_demos(tmp_path, monkeypatch, ligado):
    from cs2clip.__main__ import _demos_automaticas
    _com_pasta_de_replays(monkeypatch, tmp_path)
    vistos = _duplo_demos_da_gravacao(monkeypatch)
    vod = tmp_path / "1000000001-twitch.mp4"
    _demos_automaticas(vod, consultar_twitch=ligado)
    assert vistos == [{"video": "1000000001-twitch.mp4", "consultar_twitch": ligado}]


def _main_com_chat(tmp_path, monkeypatch, usar: bool):
    """Roda `main` de verdade (`--demo auto`) sobre um 'VOD' com o config `[chat] usar = <usar>` num arquivo temporário (nunca o config.toml real)."""
    import cs2clip.pipeline as pl
    from cs2clip import checagem
    from cs2clip.__main__ import main
    _com_pasta_de_replays(monkeypatch, tmp_path)
    monkeypatch.setattr(checagem, "processos_ativos", lambda: set())
    monkeypatch.setattr(pl, "analyze", lambda p, cfg, o, pd, R, **extras: (types.SimpleNamespace(duration=10.0), "vod", tmp_path / "o", [], [], None))
    monkeypatch.setattr(pl, "render_all", lambda *a, **k: ([], [], []))
    cfg = tmp_path / "config_teste.toml"
    cfg.write_text(f"[chat]\nusar = {'true' if usar else 'false'}\n", encoding="utf-8")
    vod = tmp_path / "1000000001-twitch.mp4"
    vod.write_bytes(b"x")
    assert main([str(vod), "--demo", "auto", "--config", str(cfg), "--saida", str(tmp_path / "s")]) == 0


@pytest.mark.parametrize("usar", [False, True])
def test_main_so_deixa_o_buscador_consultar_a_twitch_com_o_chat_ligado(tmp_path, monkeypatch, usar):
    vistos = _duplo_demos_da_gravacao(monkeypatch)
    _main_com_chat(tmp_path, monkeypatch, usar)
    assert vistos == [{"video": "1000000001-twitch.mp4", "consultar_twitch": usar}]


def test_main_com_o_chat_desligado_nunca_chega_a_info_do_vod(tmp_path, monkeypatch):
    """Ponta a ponta: o buscador de demos de verdade (sem duplo) e a Twitch trocada por um espião."""
    perguntas: list[str] = []
    monkeypatch.setattr(tv, "info_do_vod", lambda vod, **kw: perguntas.append(vod) or None)
    monkeypatch.setattr(dc, "PASTA_CACHE_VOD", tmp_path / "cache_vod")
    monkeypatch.setattr(dc, "_SEM_RESPOSTA", set())
    _main_com_chat(tmp_path, monkeypatch, False)
    assert perguntas == []
    # CONTROLE: com o chat ligado a mesma linha de comando pergunta à Twitch (prova que o espião é alcançável)
    monkeypatch.setattr(dc, "PASTA_CACHE_VOD", tmp_path / "cache_vod2")
    _main_com_chat(tmp_path, monkeypatch, True)
    assert perguntas == ["1000000001"]
