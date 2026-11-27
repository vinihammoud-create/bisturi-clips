"""N1b (15/11/2026): ressalvas do QA no N1 (mensagem de erro do baixador de VOD).

1. `ui/vods.py` só usa a primeira linha de erro guardada quando as últimas 15 linhas NÃO têm nenhuma (uma `[ERROR] … retrying` passageira no começo não pode
   vencer a exceção de verdade do fim).
2. Lacunas de teste: `---> System.X: texto` como única linha que explica; linhas só de separador; a primeira de duas linhas de erro vence na tela.
3. `_sem_rastro`: ` ---> --- End of …` é rastro; mensagem legítima que COMEÇA com `at ` ou `---` não é rastro.
"""
import time

import pytest

from cs2clip import twitch_vod as tv
from ui.vods import GerenciadorVod

DISCO = "Unhandled exception. System.IO.IOException: There is not enough space on the disk"
PASSAGEIRA = "[ERROR] - Failed to download part 12, retrying (1/10)"


# ── _sem_rastro / linha_de_erro / mensagem_de_erro ──────────────────────────────────────────────────────

def test_seta_com_texto_sozinha_e_a_linha_que_explica():
    # a ÚNICA linha que explica é ` ---> System.X: texto`: devolve sem o `--->`
    assert tv._sem_rastro(" ---> System.IO.IOException: sem espaço") == "System.IO.IOException: sem espaço"
    assert tv.linha_de_erro(" ---> System.IO.IOException: sem espaço") is True
    assert tv.mensagem_de_erro(["[STATUS] - x", " ---> System.IO.IOException: sem espaço", "   at A.B() in x.cs:line 1"], 1) == "System.IO.IOException: sem espaço"


def test_linhas_so_de_separador_no_fallback_nao_viram_mensagem():
    for sep in ("=====", "***", "-----", "_ _ _", " - = * _ "):
        assert tv._sem_rastro(sep) == ""
        assert tv.linha_de_erro(sep) is False
    # sem nenhuma outra linha útil: o código de saída (o fallback `uteis[-1]` não pode devolver separador)
    assert tv.mensagem_de_erro(["=====", "***", "-----"], 3) == "o baixador terminou com o código 3"
    # com uma linha útil sem marca de erro: ela, e não o separador que vem depois
    assert tv.mensagem_de_erro(["[STATUS] - Fetching Video Info", "=====", "***", "-----"], 3) == "[STATUS] - Fetching Video Info"


def test_seta_seguida_de_fim_de_rastro_e_rastro():
    assert tv._sem_rastro(" ---> --- End of inner exception stack trace ---") == ""
    assert tv._sem_rastro(" ---> ---> --- End of inner exception stack trace ---") == ""
    assert tv.linha_de_erro(" ---> --- End of inner exception stack trace ---") is False
    assert tv.mensagem_de_erro([" ---> --- End of inner exception stack trace ---"], 5) == "o baixador terminou com o código 5"
    assert tv.mensagem_de_erro([DISCO, " ---> --- End of inner exception stack trace ---"], 1) == DISCO


@pytest.mark.parametrize("msg", ["at least 10 GB [ERROR]", "at least 10 GB of free space required: error", "--- Error: algo ---", "---error: no space---"])
def test_mensagem_legitima_que_comeca_com_at_ou_tracos_nao_e_rastro(msg):
    assert tv._sem_rastro(msg) == msg
    assert tv.linha_de_erro(msg) is True
    assert tv.mensagem_de_erro([msg], 1) == msg


@pytest.mark.parametrize("rastro", [
    "   at TwitchDownloaderCore.VideoDownloader.DownloadAsyncImpl(FileInfo outputFileInfo) in /home/runner/work/VideoDownloader.cs:line 81",
    "   at System.Threading.Tasks.Task.Wait()",
    "   at CommandLine.ParserResultExtensions.WithParsed[T](ParserResult`1 result, Action`1 action)",
    "   at TwitchDownloaderCLI.Program.Main(String[] args) in .../Program.cs:line 31",
    "   at Programa.Funcao in C:\\x\\y.cs:line 7",
    "   --- End of inner exception stack trace ---",
    "--- End of stack trace from previous location ---",
])
def test_rastro_de_pilha_do_dotnet_continua_descartado(rastro):
    assert tv._sem_rastro(rastro) == ""
    assert tv.linha_de_erro(rastro) is False


# ── a tela (ui/vods.py) ─────────────────────────────────────────────────────────────────────────────────

class _Proc:
    def __init__(self, linhas, codigo):
        self.stdout = iter(linhas)
        self._codigo = codigo

    def wait(self):
        return self._codigo

    def terminate(self):
        pass


def _roda(tmp_path, linhas, codigo=1):
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


def test_erro_passageira_no_comeco_nao_vence_a_excecao_de_verdade_do_fim(tmp_path):
    # a simulação exata do QA: `[ERROR] … retrying` no começo, muito progresso, e a exceção de disco cheio no fim (dentro das últimas 15 linhas)
    linhas = [PASSAGEIRA] + [f"[STATUS] - Downloading {i % 100}%" for i in range(60)] + [DISCO, "   at A.B() in x.cs:line 1", "   at C.D()"]
    job = _roda(tmp_path, linhas)
    assert job["estado"] == "erro" and "not enough space on the disk" in job["erro"] and "retrying" not in job["erro"]


def test_primeira_de_duas_linhas_de_erro_diferentes_vence_quando_as_ultimas_15_nao_tem_erro(tmp_path):
    # as duas ficam longe do fim (o rastro longo as empurra para fora das 15 guardadas): vale a PRIMEIRA
    linhas = ["[ERROR] - primeiro erro: sem permissão na pasta"] + [f"[STATUS] - Downloading {i % 100}%" for i in range(30)] + \
        ["[ERROR] - segundo erro: outra coisa"] + [f"   at Empilha.Chamada{i}() in /x/y.cs:line {i}" for i in range(40)]
    job = _roda(tmp_path, linhas)
    assert job["estado"] == "erro" and job["erro"] == "[ERROR] - primeiro erro: sem permissão na pasta"


def test_vod_apagado_com_rastro_longo_continua_mostrando_a_frase(tmp_path):
    linhas = ["[STATUS] - Fetching Video Info [1/4]",
              "Unhandled exception. System.AggregateException: One or more errors occurred. (Invalid VOD, deleted/expired VOD possibly?)",
              " ---> System.NullReferenceException: Invalid VOD, deleted/expired VOD possibly?"] + \
        [f"   at Empilha.Chamada{i}() in /x/y.cs:line {i}" for i in range(60)] + ["   --- End of inner exception stack trace ---"] + \
        [f"   at Outra.Chamada{i}() in /x/z.cs:line {i}" for i in range(30)]
    job = _roda(tmp_path, linhas, 127)
    assert job["estado"] == "erro" and job["erro"] == "esse VOD não existe mais na Twitch (apagado, expirado ou privado)"


def test_erro_que_sobra_nas_ultimas_15_nao_e_duplicado(tmp_path):
    job = _roda(tmp_path, ["[ERROR] - VOD is sub-only"])
    assert job["erro"] == "[ERROR] - VOD is sub-only"
