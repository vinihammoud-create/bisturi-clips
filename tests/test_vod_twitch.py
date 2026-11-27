"""C4 (08/11/2026): baixar o VOD da Twitch pelo link para a pasta entrada/. Sem rede: o baixador é trocado por um falso."""
import threading
import time
from pathlib import Path

import pytest

from cs2clip import twitch_vod as tv
from ui.vods import GerenciadorVod

from test_ui_dados_seguranca import cliente, criar_projeto, token_headers


def test_so_link_de_vod_da_twitch_vale():
    boas = ["https://www.twitch.tv/videos/1234567890", "http://twitch.tv/videos/1234567890", "https://m.twitch.tv/videos/1234567890",
            "  https://www.twitch.tv/videos/1234567890?t=1h2m3s  ", "https://www.twitch.tv/videos/1234567890/",
            "twitch.tv/videos/1234567890", "www.twitch.tv/videos/1234567890"]
    assert all(tv.vod_id_de_link(x) == "1234567890" for x in boas)
    ruins = ["", None, "1234567890", "https://www.twitch.tv/canaldeexemplo", "https://clips.twitch.tv/AbcDef", "https://www.twitch.tv/videos/123",
             "https://twitch.tv.evil.com/videos/1234567890", "https://evil.com/?https://www.twitch.tv/videos/1234567890",
             "https://www.youtube.com/watch?v=abc", "file:///C:/Users/x/video.mp4", "C:\\Users\\x\\video.mp4",
             "https://www.twitch.tv/videos/1234567890; calc", "evil.com/twitch.tv/videos/1234567890", "xtwitch.tv/videos/1234567890"]
    assert not any(tv.vod_id_de_link(x) for x in ruins)


def test_comando_nunca_leva_oauth_e_recusa_id_estranho(tmp_path):
    cmd = tv.comando_download("1234567890", tmp_path / "1234567890-twitch.mp4", tmp_path / "tmp")
    assert cmd[1] == "videodownload" and "--id" in cmd and "1234567890" in cmd
    assert "--oauth" not in cmd and "Rename" in cmd                                  # sem token de assinante; não sobrescreve
    assert "--quality" not in cmd and "--quality" in tv.comando_download("1234567890", tmp_path / "a.mp4", tmp_path, "1080p60")
    for ruim in ("123", "abc", "1234567890; calc", "--oauth"):
        with pytest.raises(ValueError):
            tv.comando_download(ruim, tmp_path / "a.mp4", tmp_path)


def test_percentual_da_linha():
    assert tv.percentual_da_linha("[STATUS] - Downloading 47% [3/4]") == 47
    assert tv.percentual_da_linha("[INFO] - Fetching video info") is None
    assert tv.percentual_da_linha("100 %") == 100 and tv.percentual_da_linha("250%") == 100


class _Proc:
    def __init__(self, linhas, codigo=0, ao_terminar=None):
        self.stdout = iter(linhas)
        self._codigo, self._ao_terminar = codigo, ao_terminar

    def wait(self):
        if self._ao_terminar:
            self._ao_terminar()
        return self._codigo

    def terminate(self):
        pass


def _cli_falso(tmp_path):
    cli = tmp_path / "TwitchDownloaderCLI.exe"
    cli.write_bytes(b"fake")
    return cli


def _espera(gerenciador, estado, tempo=5.0):
    fim = time.time() + tempo
    while time.time() < fim:
        itens = gerenciador.listar()["itens"]
        if itens and itens[-1]["estado"] == estado:
            return itens[-1]
        time.sleep(0.02)
    raise AssertionError(f"não chegou a '{estado}': {gerenciador.listar()}")


def test_download_termina_e_o_arquivo_final_so_aparece_inteiro(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    vistos = {}

    def popen(cmd, **kw):
        vistos["cmd"] = cmd
        destino = Path(cmd[cmd.index("-o") + 1])
        return _Proc(["[STATUS] - Downloading 10%", "[STATUS] - Downloading 90%"], 0, lambda: destino.write_bytes(b"v" * 200_000))
    g = GerenciadorVod(entrada, popen_factory=popen, cli=_cli_falso(tmp_path))
    g.iniciar("https://www.twitch.tv/videos/1234567890")
    pronto = _espera(g, "pronto")
    assert pronto["percentual"] == 100 and pronto["arquivo"] == "1234567890-twitch.mp4"
    assert (entrada / "1234567890-twitch.mp4").stat().st_size == 200_000 and not list(entrada.glob("*.parcial"))
    assert vistos["cmd"][0].endswith("TwitchDownloaderCLI.exe") and "--oauth" not in vistos["cmd"]
    with pytest.raises(RuntimeError, match="já está na pasta entrada"):                    # o mesmo VOD não baixa duas vezes
        g.iniciar("https://www.twitch.tv/videos/1234567890")
    from cs2clip.chat import vod_id_from_name
    assert vod_id_from_name(pronto["arquivo"]) == "1234567890"                             # o chat da Twitch acha o VOD pelo nome


def test_download_que_falha_nao_deixa_arquivo_e_diz_o_motivo(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()

    def popen(cmd, **kw):
        destino = Path(cmd[cmd.index("-o") + 1])
        return _Proc(["[ERROR] - VOD is sub-only"], 1, lambda: destino.write_bytes(b"meio"))
    g = GerenciadorVod(entrada, popen_factory=popen, cli=_cli_falso(tmp_path))
    g.iniciar("https://www.twitch.tv/videos/1234567890")
    erro = _espera(g, "erro")
    assert "sub-only" in erro["erro"]
    assert not list(entrada.glob("*.mp4")) and not list(entrada.glob("*.parcial"))         # nada pela metade na pasta entrada


def test_um_download_de_cada_vez_link_ruim_e_baixador_ausente(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    liberar = []

    def popen(cmd, **kw):
        destino = Path(cmd[cmd.index("-o") + 1])

        class Lento(_Proc):
            def wait(self):
                while not liberar:
                    time.sleep(0.01)
                destino.write_bytes(b"v" * 200_000)
                return 0
        return Lento([])
    g = GerenciadorVod(entrada, popen_factory=popen, cli=_cli_falso(tmp_path))
    g.iniciar("https://www.twitch.tv/videos/1234567890")
    with pytest.raises(RuntimeError, match="em andamento"):
        g.iniciar("https://www.twitch.tv/videos/1234567891")
    liberar.append(1)
    _espera(g, "pronto")
    with pytest.raises(ValueError, match="twitch.tv/videos"):
        g.iniciar("https://www.youtube.com/watch?v=abc")
    with pytest.raises(FileNotFoundError):
        GerenciadorVod(entrada, cli=tmp_path / "nao_existe.exe").iniciar("https://www.twitch.tv/videos/1234567892")


def test_endpoint_exige_token_e_devolve_erros_claros(tmp_path):
    criar_projeto(tmp_path)

    def popen(cmd, **kw):
        destino = Path(cmd[cmd.index("-o") + 1])
        return _Proc(["Downloading 50%"], 0, lambda: destino.write_bytes(b"v" * 200_000))
    with cliente(tmp_path, vod_popen_factory=popen, vod_cli=_cli_falso(tmp_path)) as c:
        assert c.post("/api/baixar-vod", json={"link": "https://www.twitch.tv/videos/1234567890"}).status_code == 403     # sem token
        h = token_headers(c)
        r = c.post("/api/baixar-vod", json={"link": "https://www.youtube.com/watch?v=abc"}, headers=h)
        assert r.status_code == 400 and "twitch.tv/videos" in r.json()["erro"]
        assert c.post("/api/baixar-vod", json={"link": 5}, headers=h).status_code == 400
        ok = c.post("/api/baixar-vod", json={"link": "https://www.twitch.tv/videos/1234567890"}, headers=h)
        assert ok.status_code == 200 and ok.json()["vod"] == "1234567890"
        fim = time.time() + 5
        while time.time() < fim and c.get("/api/baixar-vod", headers=token_headers(c)).json()["itens"][-1]["estado"] != "pronto":
            time.sleep(0.02)
        assert c.get("/api/baixar-vod", headers=token_headers(c)).json()["itens"][-1]["estado"] == "pronto"
        assert (tmp_path / "entrada" / "1234567890-twitch.mp4").is_file()
        assert c.post("/api/baixar-vod", json={"link": "https://www.twitch.tv/videos/1234567890"}, headers=h).status_code == 409
    with cliente(tmp_path, vod_cli=tmp_path / "nao_existe.exe") as c2:
        r = c2.post("/api/baixar-vod", json={"link": "https://www.twitch.tv/videos/1234568208"}, headers=token_headers(c2))
        assert r.status_code == 404


def test_interface_tem_o_campo_do_link_e_o_js_chama_a_rota():
    raiz = Path(__file__).resolve().parents[1] / "ui" / "static"
    html, js = (raiz / "index.html").read_text(encoding="utf-8"), (raiz / "app.js").read_text(encoding="utf-8")
    assert 'id="vod-link"' in html and 'id="vod-baixar"' in html and "YouTube Studio" in html
    assert "/api/baixar-vod" in js and "acompanharVod" in js


class _Travado:
    """Baixador que não termina sozinho: só quando recebe terminate(), como um processo de verdade."""

    def __init__(self):
        self._parar = threading.Event()
        self.terminado = False
        self.stdout = self._linhas()

    def _linhas(self):
        yield "[STATUS] - Downloading 3%"
        self._parar.wait(10)

    def terminate(self):
        self.terminado = True
        self._parar.set()

    def wait(self):
        self._parar.wait(10)
        return 1


def test_e4_cancelar_encerra_o_baixador_e_libera_a_fila(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    proc = _Travado()
    g = GerenciadorVod(entrada, popen_factory=lambda cmd, **kw: proc, cli=_cli_falso(tmp_path))
    g.iniciar("twitch.tv/videos/1234567890")
    fim = time.time() + 5
    while time.time() < fim and not g._procs:
        time.sleep(0.01)
    assert g.cancelar()["vod"] == "1234567890"
    cancelado = _espera(g, "cancelado")
    assert proc.terminado and cancelado["erro"] == "" and not list(entrada.glob("*.parcial")) and not list(entrada.glob("*.mp4"))
    with pytest.raises(RuntimeError, match="Não há download"):                 # nada mais para cancelar
        g.cancelar()
    g.iniciar("twitch.tv/videos/1234567891")                                   # e a fila voltou a aceitar outro download
    g.cancelar()
    _espera(g, "cancelado")


def test_e4_download_travado_e_encerrado_pelo_tempo_maximo(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    proc = _Travado()
    g = GerenciadorVod(entrada, popen_factory=lambda cmd, **kw: proc, cli=_cli_falso(tmp_path), tempo_maximo=0.3)
    g.iniciar("twitch.tv/videos/1234567890")
    erro = _espera(g, "erro", tempo=5)
    assert proc.terminado and "tempo máximo" in erro["erro"]
    # CONTROLE: sem estourar o tempo (e com o baixador terminando bem), o download conclui
    g2 = GerenciadorVod(entrada, popen_factory=lambda cmd, **kw: _Proc(["50%"], 0, lambda: Path(cmd[cmd.index("-o") + 1]).write_bytes(b"v" * 200_000)),
                        cli=_cli_falso(tmp_path), tempo_maximo=60)
    g2.iniciar("twitch.tv/videos/1234567892")
    _espera(g2, "pronto")


def test_e4_endpoint_de_cancelar_e_botao_na_tela(tmp_path):
    criar_projeto(tmp_path)
    proc = _Travado()
    with cliente(tmp_path, vod_popen_factory=lambda cmd, **kw: proc, vod_cli=_cli_falso(tmp_path)) as c:
        h = token_headers(c)
        assert c.post("/api/baixar-vod/cancelar", json={}, headers=h).status_code == 409           # nada em andamento
        assert c.post("/api/baixar-vod", json={"link": "twitch.tv/videos/1234567890"}, headers=h).status_code == 200
        assert c.post("/api/baixar-vod/cancelar", json={}, headers=h).status_code == 200
        assert c.post("/api/baixar-vod/cancelar", json={}).status_code == 403                      # sem token
        fim = time.time() + 5
        while time.time() < fim and c.get("/api/baixar-vod", headers=token_headers(c)).json()["itens"][-1]["estado"] != "cancelado":
            time.sleep(0.02)
        assert c.get("/api/baixar-vod", headers=token_headers(c)).json()["itens"][-1]["estado"] == "cancelado"
    raiz = Path(__file__).resolve().parents[1] / "ui" / "static"
    assert 'id="vod-cancelar"' in (raiz / "index.html").read_text(encoding="utf-8")
    assert "/api/baixar-vod/cancelar" in (raiz / "app.js").read_text(encoding="utf-8")


def test_e6_nada_para_postar_aparece_na_pagina_do_video(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert c.get("/api/videos/v1", headers=token_headers(c)).json()["nada_para_postar"] is None                            # controle: sem o arquivo, nada
        frase = "Nenhuma jogada com 3 ou mais abates seus nesta partida (a maior teve 2). Nada para postar."
        (tmp_path / "saida" / "v1" / "NADA_PARA_POSTAR.txt").write_text(frase + chr(10), encoding="utf-8")
        assert c.get("/api/videos/v1", headers=token_headers(c)).json()["nada_para_postar"] == frase
        (tmp_path / "saida" / "v1" / "NADA_PARA_POSTAR.txt").write_text("x" * 900, encoding="utf-8")
        assert len(c.get("/api/videos/v1", headers=token_headers(c)).json()["nada_para_postar"]) == 400                        # texto longo não estoura a tela
    raiz = Path(__file__).resolve().parents[1] / "ui" / "static"
    assert 'id="nada-notice"' in (raiz / "index.html").read_text(encoding="utf-8")
    js = (raiz / "app.js").read_text(encoding="utf-8")
    assert "montarNadaParaPostar(resposta.nada_para_postar)" in js and ".notice-warn" in (raiz / "style.css").read_text(encoding="utf-8")


def test_r4_resto_de_download_interrompido_nao_atrapalha_o_proximo_e_a_temporaria_some(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / ".baixando").mkdir(parents=True)
    (entrada / ".baixando" / "parte_01.ts").write_bytes(b"x" * 10)
    velho = entrada / "1234567890-twitch.mp4.parcial"
    velho.write_bytes(b"meio" * 10)                                     # o programa foi fechado no meio de um download

    def popen(cmd, **kw):
        destino = Path(cmd[cmd.index("-o") + 1])
        # o baixador de verdade, com --collision Rename, não sobrescreve: se o nome existe, grava noutro (e o Bisturi concluiria "falhou")
        alvo = destino if not destino.exists() else destino.with_name(destino.stem + " (1)" + destino.suffix)
        return _Proc(["Downloading 60%"], 0, lambda: alvo.write_bytes(b"v" * 200_000))
    g = GerenciadorVod(entrada, popen_factory=popen, cli=_cli_falso(tmp_path))
    g.iniciar("twitch.tv/videos/1234567890")
    _espera(g, "pronto")
    assert (entrada / "1234567890-twitch.mp4").stat().st_size == 200_000
    assert not velho.exists() and not (entrada / ".baixando").exists()
    assert not list(entrada.glob("*.parcial"))
    # CONTROLE: sem a limpeza (comportamento anterior) o resto continua lá, o baixador grava noutro nome e o download "falha"
    (entrada / ".baixando").mkdir(exist_ok=True)
    velho = entrada / ".baixando" / "1234567890-twitch.mp4"           # M2: o parcial agora mora em .baixando; é lá que um resto atrapalharia o baixador
    velho.write_bytes(b"meio" * 10)
    (entrada / "1234567890-twitch.mp4").unlink()
    g2 = GerenciadorVod(entrada, popen_factory=popen, cli=_cli_falso(tmp_path))
    g2._limpar_restos = lambda destino: None
    g2.iniciar("twitch.tv/videos/1234567890")
    assert _espera(g2, "erro")["estado"] == "erro"
    assert not (entrada / "1234567890-twitch.mp4").exists()


def test_r4_cancelar_apaga_a_pasta_temporaria(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    proc = _Travado()

    def popen(cmd, **kw):
        (entrada / ".baixando").mkdir(exist_ok=True)
        (entrada / ".baixando" / "parte.ts").write_bytes(b"x")
        return proc
    g = GerenciadorVod(entrada, popen_factory=popen, cli=_cli_falso(tmp_path))
    g.iniciar("twitch.tv/videos/1234567890")
    fim = time.time() + 5
    while time.time() < fim and not g._procs:
        time.sleep(0.01)
    g.cancelar()
    _espera(g, "cancelado")
    assert not (entrada / ".baixando").exists()


# ── M2 (plano do material novo, 15/11/2026): o baixador DE VERDADE só aceita saída .mp4/.m4a; o programa pedia ".mp4.parcial" e nunca baixou nada ──────────

RASTRO_REAL = ["Unhandled exception. System.ArgumentException: Only MP4 and M4A audio files are supported.",
               "   at TwitchDownloaderCLI.Modes.DownloadVideo.GetDownloadOptions(DownloadOptions inputOptions) in /home/runner/work/DownloadVideo.cs:line 57",
               "   at TwitchDownloaderCLI.Modes.DownloadVideo.Download(DownloadOptions inputOptions) in /home/runner/work/DownloadVideo.cs:line 30",
               "   at TwitchDownloaderCLI.Program.Main(String[] args) in /home/runner/work/TwitchDownloader/TwitchDownloader/TwitchDownloaderCLI/Program.cs:line 31"]


def _popen_realista(visto=None, durante=None):
    """O baixador falso FIEL ao de verdade: decide o formato pela extensão do `-o` e só aceita .mp4 e .m4a (senão, o mesmo rastro de exceção, código 1)."""
    def popen(cmd, **kw):
        destino = Path(cmd[cmd.index("-o") + 1])
        if visto is not None:
            visto["cmd"] = list(cmd)
            visto["cache_existia"] = Path(cmd[cmd.index("--temp-path") + 1]).is_dir()      # o baixador recebe a pasta de cache já criada
        if destino.suffix.lower() not in (".mp4", ".m4a"):
            return _Proc(RASTRO_REAL, 1)

        def escrever():
            if durante:
                durante()
            destino.write_bytes(b"v" * 200_000)
        return _Proc(["[STATUS] - Downloading 50%"], 0, escrever)
    return popen


def test_m2_com_o_baixador_fiel_o_download_termina_e_o_arquivo_final_aparece(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    g = GerenciadorVod(entrada, popen_factory=_popen_realista(), cli=_cli_falso(tmp_path))
    g.iniciar("https://www.twitch.tv/videos/1234570000")
    pronto = _espera(g, "pronto")                                                       # antes do conserto: 'erro' (Only MP4 and M4A…)
    assert pronto["arquivo"] == "1234570000-twitch.mp4" and (entrada / "1234570000-twitch.mp4").stat().st_size == 200_000
    assert not list(entrada.glob("*.parcial")) and not (entrada / ".baixando").exists()


def test_m2_o_comando_pede_saida_mp4_dentro_da_pasta_escondida_e_o_cache_em_outra_subpasta(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    visto = {}
    g = GerenciadorVod(entrada, popen_factory=_popen_realista(visto), cli=_cli_falso(tmp_path))
    g.iniciar("twitch.tv/videos/1234570000")
    _espera(g, "pronto")
    cmd = visto["cmd"]
    saida, temp = Path(cmd[cmd.index("-o") + 1]), Path(cmd[cmd.index("--temp-path") + 1])
    assert saida.suffix == ".mp4" and saida.parent.name == ".baixando" and saida.parent.parent == entrada
    assert temp != saida.parent and saida.parent in temp.parents                         # o cache do baixador não se mistura com o arquivo de saída
    assert "--oauth" not in cmd and "Rename" in cmd
    assert visto["cache_existia"] is True


def test_m2_durante_o_download_nada_com_cara_de_video_pronto_aparece_na_pasta_entrada(tmp_path):
    from ui.dados import DadosProjeto
    raiz = tmp_path
    entrada = raiz / "entrada"
    entrada.mkdir()
    (raiz / "saida").mkdir()
    vistos = {}

    def durante():
        vistos["arquivos_soltos"] = [p.name for p in entrada.iterdir() if p.is_file()]      # o que a lista de vídeos da tela enxergaria
        vistos["lista_da_tela"] = DadosProjeto(raiz).listar_entrada()
    g = GerenciadorVod(entrada, popen_factory=_popen_realista(durante=durante), cli=_cli_falso(tmp_path))
    g.iniciar("twitch.tv/videos/1234570000")
    _espera(g, "pronto")
    assert vistos["arquivos_soltos"] == [] and vistos["lista_da_tela"] == []
    assert [i["nome"] for i in DadosProjeto(raiz).listar_entrada()] == ["1234570000-twitch.mp4"]      # só o final, inteiro, entra na lista


def test_m2_cancelar_continua_limpando_tudo_com_o_novo_lugar_do_parcial(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    proc = _Travado()

    def popen(cmd, **kw):
        saida = Path(cmd[cmd.index("-o") + 1])
        saida.write_bytes(b"meio")                                                       # o baixador já tinha gravado um pedaço
        return proc
    g = GerenciadorVod(entrada, popen_factory=popen, cli=_cli_falso(tmp_path))
    g.iniciar("twitch.tv/videos/1234570000")
    fim = time.time() + 5
    while time.time() < fim and not g._procs:
        time.sleep(0.01)
    g.cancelar()
    _espera(g, "cancelado")
    assert list(entrada.iterdir()) == []                                                 # nem arquivo, nem .baixando


def test_m2_resto_do_formato_antigo_e_do_novo_e_apagado_antes_de_baixar(tmp_path):
    entrada = tmp_path / "entrada"
    (entrada / ".baixando").mkdir(parents=True)
    novo = entrada / ".baixando" / "1234570000-twitch.mp4"
    novo.write_bytes(b"meio" * 10)                                                       # resto de um download interrompido (lugar novo)
    antigo = entrada / "1234570000-twitch.mp4.parcial"
    antigo.write_bytes(b"meio" * 10)                                                     # resto do programa antigo
    g = GerenciadorVod(entrada, popen_factory=_popen_realista(), cli=_cli_falso(tmp_path))
    g.iniciar("twitch.tv/videos/1234570000")
    _espera(g, "pronto")
    assert not antigo.exists() and not (entrada / ".baixando").exists()
    assert (entrada / "1234570000-twitch.mp4").stat().st_size == 200_000


# ── a mensagem de erro: a linha que explica, nunca a última linha do rastro ───────────────────

def test_m2_mensagem_de_erro_pega_a_excecao_e_nao_a_ultima_linha_do_rastro():
    texto = tv.mensagem_de_erro(RASTRO_REAL, 1)
    assert "Program.cs" not in texto and not texto.startswith("at ")
    assert "MP4" in texto and "M4A" in texto


def test_m2_mensagem_de_erro_casos():
    assert "sub-only" in tv.mensagem_de_erro(["[INFO] - x", "[ERROR] - VOD is sub-only"], 1)
    assert "Unable to find" in tv.mensagem_de_erro(["[ERROR] - Unable to find video 123"], 1)
    assert tv.mensagem_de_erro([], 7) == "o baixador terminou com o código 7"
    assert tv.mensagem_de_erro(["   at Algo.Metodo() in x.cs:line 1", "   at Outro() in y.cs:line 2"], 1) == "o baixador terminou com o código 1"
    assert tv.mensagem_de_erro(["linha comum sem erro"], 1) == "linha comum sem erro"                 # sem marca de erro: a última linha que não é rastro
    assert len(tv.mensagem_de_erro(["[ERROR] - " + "x" * 900], 1)) <= 300
    # linha do rastro que só CITA a palavra Exception (ThrowException) não pode ganhar da linha que explica
    assert tv.mensagem_de_erro(["Unhandled exception. System.InvalidOperationException: algo deu errado", "   at System.Throw.ThrowException() in x.cs:line 1"], 1).startswith("Unhandled exception")


def test_m2_o_erro_que_a_tela_mostra_e_o_legivel(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()

    def popen(cmd, **kw):
        return _Proc(RASTRO_REAL, 1)
    g = GerenciadorVod(entrada, popen_factory=popen, cli=_cli_falso(tmp_path))
    g.iniciar("twitch.tv/videos/1234570000")
    erro = _espera(g, "erro")["erro"]
    assert "Program.cs" not in erro and "MP4" in erro


# ── links: também /<canal>/v/<id> e /<canal>/video/<id> (formas que o site da Twitch ainda usa) ────────

def test_m2_links_com_canal_no_caminho_tambem_valem_e_os_ruins_continuam_recusados():
    for bom in ("https://www.twitch.tv/canaldeexemplo/video/1234570000", "https://www.twitch.tv/canaldeexemplo/v/1234570000", "twitch.tv/canaldeexemplo/v/1234570000?t=1h",
                "https://www.twitch.tv/videos/1234570000"):
        assert tv.vod_id_de_link(bom) == "1234570000", bom
    for ruim in ("https://www.twitch.tv/canaldeexemplo/videos", "https://www.twitch.tv/canaldeexemplo/clip/AbcDef", "https://www.twitch.tv/canaldeexemplo/video/123",
                 "https://twitch.tv.evil.com/canaldeexemplo/v/1234570000", "https://evil.com/?https://www.twitch.tv/canaldeexemplo/v/1234570000",
                 "https://www.twitch.tv/canaldeexemplo/v/1234570000; calc", "https://www.twitch.tv/a/b/v/1234570000"):
        assert tv.vod_id_de_link(ruim) is None, ruim


def test_m2_a_caixa_da_tela_inicio_reconhece_o_link_com_canal():
    from cs2clip import entrada as ent
    e = ent.reconhecer("https://www.twitch.tv/canaldeexemplo/v/1234570000", [])
    assert e.tipo == "twitch_vod" and e.valor == "1234570000" and e.aceita


# ── revisão do qa do M2 ───────────────────────────────────────────────────────────────────────

def test_m2_limpeza_que_falha_no_windows_nao_deixa_o_job_preso_em_baixando(tmp_path, monkeypatch):
    """O antivírus pode segurar o parcial: `unlink` levanta PermissionError. O estado final tem de sair de `baixando` (senão todo download seguinte
    diria "Já existe um download em andamento" até o programa ser reiniciado)."""
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    real = Path.unlink

    def unlink_negado(self, *a, **k):
        if self.name == "1234570000-twitch.mp4" and self.parent.name == ".baixando":
            raise PermissionError(13, "arquivo em uso")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", unlink_negado)

    def popen(cmd, **kw):
        destino = Path(cmd[cmd.index("-o") + 1])
        return _Proc(["[ERROR] - VOD is sub-only"], 1, lambda: destino.write_bytes(b"meio"))
    g = GerenciadorVod(entrada, popen_factory=popen, cli=_cli_falso(tmp_path))
    g.iniciar("twitch.tv/videos/1234570000")
    erro = _espera(g, "erro")
    assert "sub-only" in erro["erro"]
    g.iniciar("twitch.tv/videos/1234570001")                                             # o próximo download NÃO é barrado por "em andamento"
    _espera(g, "erro")


def test_m2_digitos_que_nao_sao_0_a_9_nao_valem_no_link_nem_no_comando(tmp_path):
    assert tv.vod_id_de_link("https://www.twitch.tv/videos/\u0662\u0669\u0662\u0668\u0661\u0661\u0666\u0664\u0669\u0664") is None      # dígitos árabe-índicos
    assert tv.vod_id_de_link("https://www.twitch.tv/videos/１２３４５７１０００") is None                                                    # largura total
    with pytest.raises(ValueError):
        tv.comando_download("１２３４５７１０００", tmp_path / "a.mp4", tmp_path)
