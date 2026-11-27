"""E2 (Plano E): a receita encadeia baixar → esperar a conta → gravar → editar sem cliques no meio. Tudo com processos, Steam, fila e Twitch FALSOS."""
import os
import json
import threading
import time
from pathlib import Path

import pytest

from cs2clip import entrada as ent
from cs2clip import sharecode
from cs2clip.config import Config
from ui import receitas
from ui.receitas import GerenciadorReceitas

BASE = 76561197960265728
PRINCIPAL_ID, BISTURI_ID = 100, 200
PRINCIPAL, BISTURI = str(BASE + PRINCIPAL_ID), str(BASE + BISTURI_ID)
CODIGO = sharecode.codificar(3230642215713767580, 3230647599455273103, 55788)


class Processo:
    """Imita o Popen: devolve as linhas e o código; `bloqueio` segura a saída até alguém mandar o sinal de cancelar."""

    def __init__(self, linhas, codigo=0, bloqueio=None):
        self._linhas, self._codigo, self._bloqueio = linhas, codigo, bloqueio
        self.sinais = []
        self.stdout = self._ler()

    def _ler(self):
        if self._bloqueio is not None:
            self._bloqueio.wait(10)
        yield from (x + "\n" for x in self._linhas)

    def send_signal(self, sinal):
        self.sinais.append(sinal)
        if self._bloqueio is not None:
            self._bloqueio.set()

    def terminate(self):
        self.send_signal("terminate")

    def poll(self):
        return self._codigo

    def wait(self):
        return self._codigo


class Roteiro:
    """Quem responde cada `python -m cs2clip.<módulo>`: {módulo: lista de (linhas, código[, bloqueio])}, consumidos em ordem."""

    def __init__(self, **por_modulo):
        self.por_modulo = {k: list(v) for k, v in por_modulo.items()}
        self.comandos = []
        self.processos = []

    def __call__(self, cmd, **kw):
        self.comandos.append(cmd)
        modulo = cmd[cmd.index("-m") + 1].split(".")[-1]
        fila = self.por_modulo.get(modulo)
        assert fila, f"comando inesperado: {cmd}"
        item = fila.pop(0) if len(fila) > 1 else fila[0]
        p = Processo(*item)
        self.processos.append(p)
        return p

    def modulos(self):
        return [c[c.index("-m") + 1].split(".")[-1] for c in self.comandos]


class Conta:
    def __init__(self, valor):
        self.valor = valor

    def __call__(self):
        return self.valor


def _cfg(igual=False):
    cfg = Config()
    cfg.gravacao_demo.conta_steamid = PRINCIPAL if igual else BISTURI
    cfg.gravacao_demo.conta_principal_id = PRINCIPAL
    return cfg


def _demo(tmp_path, nome="match730_003849321203751714819_0599033075_201.dem"):
    arq = tmp_path / nome
    arq.write_bytes(ent.CABECALHO_CS2 + b"corpo")
    return arq


def _ger(tmp_path, roteiro, conta=None, **extra):
    return GerenciadorReceitas(tmp_path / "raiz", popen_factory=roteiro, python="PY", conta_logada=conta or Conta(BISTURI_ID), config_fn=lambda: _cfg(),
                               poll_s=0.01, procurar_em=lambda: [tmp_path], **extra)


GRAVAR_OK = ([ "Round 9 pronto", "Pronto. Vídeos em X"], 0)
EDITAR_OK = (["3 jogada(s)", "Edição pronta em C:\\saida\\x_limpo"], 0)


def test_demo_vira_conta_gravar_editar_e_guarda_o_resultado(tmp_path):
    demo = _demo(tmp_path)
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger(tmp_path, rot)
    r = g.iniciar(str(demo), {"rounds": "3,8"})
    assert [e["nome"] for e in r["etapas"]] == ["esperar_conta", "gravar", "editar"]
    fim = g.esperar(r["id"])
    assert fim["estado"] == "pronta" and [e["estado"] for e in fim["etapas"]] == ["ok", "ok", "ok"] and fim["resultado"] == "C:\\saida\\x_limpo"
    grav, edit = rot.comandos
    assert grav[:3] == ["PY", "-m", "cs2clip.gravador_demo"] and grav[grav.index("--demo") + 1] == str(demo.resolve()) and grav[grav.index("--rounds") + 1] == "3,8"
    assert edit[:3] == ["PY", "-m", "cs2clip.editar_demo"] and edit[edit.index("--demo") + 1] == str(demo.resolve())
    salvo = json.loads((tmp_path / "raiz" / "trabalhos" / r["id"] / "receita.json").read_text(encoding="utf-8"))
    assert salvo["estado"] == "pronta"                                                           # o estado fica no disco


def test_espera_a_troca_de_conta_mostra_o_aviso_e_segue_sozinha(tmp_path):
    demo = _demo(tmp_path)
    conta = Conta(PRINCIPAL_ID)
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger(tmp_path, rot, conta)
    r = g.iniciar(str(demo))
    esperando = g.esperar(r["id"], {"esperando_voce"})
    assert esperando["estado"] == "esperando_voce" and "PRINCIPAL" in esperando["etapas"][0]["detalhe"] and rot.comandos == []     # nada gravou ainda
    conta.valor = 0
    for _ in range(500):                                       # espera a thread da receita notar (prazo de 5 s; passa assim que mudar)
        if "fechado" in g.obter(r["id"])["etapas"][0]["detalhe"]:
            break
        time.sleep(0.01)
    assert "fechado" in g.obter(r["id"])["etapas"][0]["detalhe"]                                   # a frase muda com o que o Steam mostra
    conta.valor = BISTURI_ID                                                                         # o dono trocou
    assert g.esperar(r["id"])["estado"] == "pronta" and rot.modulos() == ["gravador_demo", "editar_demo"]


def test_configuracao_errada_nao_fica_esperando_para_sempre(tmp_path):
    g = GerenciadorReceitas(tmp_path / "raiz", popen_factory=Roteiro(), python="PY", conta_logada=Conta(BISTURI_ID), config_fn=lambda: _cfg(igual=True), poll_s=0.01,
                            procurar_em=lambda: [tmp_path])
    r = g.iniciar(str(_demo(tmp_path)))
    fim = g.esperar(r["id"])
    assert fim["estado"] == "erro" and "igual à sua conta principal" in fim["erro"]


def test_sem_a_conta_do_bisturi_configurada_a_receita_para_e_aponta_a_tela_de_contas(tmp_path):
    """C1: o erro de configuração é reconhecido pela marca da exceção (`de_configuracao`), não pelo texto da frase."""
    def sem_conta():
        cfg = Config()
        cfg.gravacao_demo.conta_principal_id = PRINCIPAL
        return cfg
    g = GerenciadorReceitas(tmp_path / "raiz", popen_factory=Roteiro(), python="PY", conta_logada=Conta(BISTURI_ID), config_fn=sem_conta, poll_s=0.01, procurar_em=lambda: [tmp_path])
    r = g.iniciar(str(_demo(tmp_path)))
    fim = g.esperar(r["id"])
    assert fim["estado"] == "erro" and fim["erro"].startswith("Falta dizer qual") and "Contas do Steam" in fim["erro"] and "config.toml" not in fim["erro"]


def test_erro_na_gravacao_para_diz_o_que_fazer_solta_a_trava_e_retomar_continua_do_ponto(tmp_path):
    demo = _demo(tmp_path)
    rot = Roteiro(gravador_demo=[(["Abrindo o CS2", "[parei] O HLAE não começou a gravar o round 9."], 1), GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger(tmp_path, rot)
    r = g.iniciar(str(demo))
    fim = g.esperar(r["id"])
    assert fim["estado"] == "erro" and "HLAE não começou a gravar" in fim["erro"] and [e["estado"] for e in fim["etapas"]] == ["ok", "erro", "pendente"]
    assert rot.modulos() == ["gravador_demo"]                                                           # a edição nem começou
    assert g._dono_cs2 is None and g._trava_cs2.acquire(blocking=False)                                 # a trava do CS2 foi solta
    g._trava_cs2.release()
    g.retomar(r["id"])
    fim2 = g.esperar(r["id"])
    assert fim2["estado"] == "pronta" and rot.modulos() == ["gravador_demo", "gravador_demo", "editar_demo"]      # não repetiu a etapa que já estava ok


def test_cancelar_enquanto_espera_a_conta_e_enquanto_grava(tmp_path):
    demo = _demo(tmp_path)
    rot = Roteiro()
    g = _ger(tmp_path, rot, Conta(PRINCIPAL_ID))
    r = g.iniciar(str(demo))
    g.esperar(r["id"], {"esperando_voce"})
    g.cancelar(r["id"])
    fim = g.esperar(r["id"])
    assert fim["estado"] == "cancelada" and fim["etapas"][0]["estado"] == "cancelada" and rot.comandos == [] and g._dono_cs2 is None
    # durante a gravação: o cancelar manda o sinal ao processo (que fecha o jogo) e a receita termina cancelada
    seguro = threading.Event()
    rot2 = Roteiro(gravador_demo=[(["Round 9: gravando"], 0, seguro)], editar_demo=[EDITAR_OK])
    g2 = _ger(tmp_path, rot2)
    r2 = g2.iniciar(str(demo))
    for _ in range(300):
        if rot2.processos:
            break
        time.sleep(0.01)
    assert rot2.processos
    g2.cancelar(r2["id"])
    fim2 = g2.esperar(r2["id"])
    assert fim2["estado"] == "cancelada" and rot2.processos[0].sinais and rot2.modulos() == ["gravador_demo"]


def test_receitas_vivas_viram_interrompidas_ao_reabrir_e_podem_ser_retomadas(tmp_path):
    demo = _demo(tmp_path)
    g1 = _ger(tmp_path, Roteiro(), Conta(PRINCIPAL_ID))
    r = g1.iniciar(str(demo))
    g1.esperar(r["id"], {"esperando_voce"})
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g2 = _ger(tmp_path, rot)                                                                            # "reabriu o programa": lê o disco
    ant = g2.obter(r["id"])
    assert ant["estado"] == "interrompida" and ant["etapas"][0]["estado"] == "pendente" and "Retomar" in ant["etapas"][0]["detalhe"]
    g2.retomar(r["id"])
    assert g2.esperar(r["id"])["estado"] == "pronta"
    with pytest.raises(RuntimeError, match="já terminou"):
        g2.retomar(r["id"])
    with pytest.raises(FileNotFoundError):
        g2.retomar("naoexiste")


def test_codigo_de_partilha_baixa_a_demo_e_usa_o_arquivo_que_o_download_informou(tmp_path):
    baixada = _demo(tmp_path, "match730_003849321203751714819_0599033075_999.dem")
    rot = Roteiro(baixar_demo=[(["Partida 1", f"Pronta: {baixada}"], 0)], gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger(tmp_path, rot)
    r = g.iniciar(f"steam://rungame/730/76561202255233023/+csgo_download_match%20{CODIGO}")
    assert [e["nome"] for e in r["etapas"]] == ["esperar_conta", "obter_demo", "gravar", "editar"]         # A1: a troca de conta vem ANTES de baixar
    assert g.esperar(r["id"])["estado"] == "pronta"
    baixar, grav, _ = rot.comandos
    assert baixar[-3:] == ["codigo", CODIGO, "--baixar"] and grav[grav.index("--demo") + 1] == str(baixada)
    # CONTROLE: download "pronto" mas o arquivo não existe → erro claro
    rot2 = Roteiro(baixar_demo=[(["Pronta: C:\\nao\\existe.dem"], 0)])
    g2 = _ger(tmp_path / "outro", rot2) if (tmp_path / "outro").mkdir() is None else None
    fim = g2.esperar(g2.iniciar(CODIGO)["id"])
    assert fim["estado"] == "erro" and "não está em" in fim["erro"]
    rot3 = Roteiro(baixar_demo=[(["[parei] A Valve já apagou essa demo (passou de ~30 dias)."], 1)])
    g3 = _ger(tmp_path / "terceiro", rot3) if (tmp_path / "terceiro").mkdir() is None else None
    fim3 = g3.esperar(g3.iniciar(CODIGO)["id"])
    assert fim3["estado"] == "erro" and "Valve já apagou" in fim3["erro"] and rot3.modulos() == ["baixar_demo"]


def test_so_uma_gravacao_por_vez_a_segunda_espera_o_cs2(tmp_path):
    d1, d2 = _demo(tmp_path), _demo(tmp_path, "match730_003852947612143452464_1002297918_202.dem")
    seguro = threading.Event()
    rot = Roteiro(gravador_demo=[(["gravando a 1"], 0, seguro), GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger(tmp_path, rot)
    r1 = g.iniciar(str(d1))
    for _ in range(300):
        if rot.processos:
            break
        time.sleep(0.01)
    r2 = g.iniciar(str(d2))
    for _ in range(300):
        if "Esperando outra gravação" in g.obter(r2["id"])["etapas"][0]["detalhe"]:
            break
        time.sleep(0.01)
    assert "Esperando outra gravação" in g.obter(r2["id"])["etapas"][0]["detalhe"] and rot.modulos().count("gravador_demo") == 1   # a 2ª não abriu o CS2
    seguro.set()
    assert g.esperar(r1["id"])["estado"] == "pronta" and g.esperar(r2["id"])["estado"] == "pronta"
    assert rot.modulos().count("gravador_demo") == 2


class FilaFalsa:
    def __init__(self, estados):
        self.estados, self.adicionados, self.cancelados = list(estados), [], []

    def adicionar(self, arquivo, opcoes):
        self.adicionados.append(arquivo)
        return {"id": f"job{len(self.adicionados)}"}

    def listar(self):
        est = self.estados.pop(0) if len(self.estados) > 1 else self.estados[0]
        return {"itens": [{"id": f"job{len(self.adicionados)}", "estado": est, "mensagem": f"fila: {est}"}]}

    def cancelar(self, job_id):
        self.cancelados.append(job_id)


def test_video_e_pasta_vao_para_a_fila_de_analise_e_esperam_terminar(tmp_path):
    video = tmp_path / "2026-11-08 16-30-10.mkv"
    video.write_bytes(b"x" * 50)
    fila = FilaFalsa(["na_fila", "rodando", "pronto"])
    g = _ger(tmp_path, Roteiro(), fila=fila)
    r = g.iniciar(str(video))
    assert [e["nome"] for e in r["etapas"]] == ["analisar", "parear"] and g.esperar(r["id"])["estado"] == "pronta" and fila.adicionados == [str(video.resolve())]
    pasta = tmp_path / "lives"
    pasta.mkdir()
    for n in ("a.mp4", "b.mkv"):
        (pasta / n).write_bytes(b"x")
    fila2 = FilaFalsa(["pronto"])
    g2 = _ger(tmp_path / "x", Roteiro(), fila=fila2) if (tmp_path / "x").mkdir() is None else None
    assert g2.esperar(g2.iniciar(str(pasta))["id"])["estado"] == "pronta" and [Path(a).name for a in fila2.adicionados] == ["a.mp4", "b.mkv"]
    fila3 = FilaFalsa(["rodando", "precisa_jogador"])                                                  # CONTROLE: a fila pede ajuda do dono
    g3 = _ger(tmp_path / "y", Roteiro(), fila=fila3) if (tmp_path / "y").mkdir() is None else None
    fim = g3.esperar(g3.iniciar(str(video))["id"])
    assert fim["estado"] == "erro" and "escolha o jogador" in fim["erro"]


class VodFalso:
    def __init__(self, estados, arquivo="1234567890-twitch.mp4"):
        self.estados, self.arquivo, self.iniciados = list(estados), arquivo, []

    def iniciar(self, link):
        self.iniciados.append(link)
        return {"id": "vod1"}

    def listar(self):
        est = self.estados.pop(0) if len(self.estados) > 1 else self.estados[0]
        return {"itens": [{"id": "vod1", "estado": est, "percentual": 40, "arquivo": self.arquivo, "erro": "sem espaço" if est == "erro" else ""}]}

    def cancelar(self):
        pass


def test_vod_da_twitch_baixa_e_manda_o_arquivo_baixado_para_a_analise(tmp_path):
    vod, fila = VodFalso(["baixando", "pronto"]), FilaFalsa(["pronto"])
    g = _ger(tmp_path, Roteiro(), vods=vod, fila=fila)
    r = g.iniciar("https://www.twitch.tv/videos/1234567890?t=1h")
    assert [e["nome"] for e in r["etapas"]] == ["obter_video", "analisar", "parear"]
    assert g.esperar(r["id"])["estado"] == "pronta"
    assert vod.iniciados == ["https://www.twitch.tv/videos/1234567890"] and Path(fila.adicionados[0]).name == "1234567890-twitch.mp4"
    g2 = _ger(tmp_path / "e", Roteiro(), vods=VodFalso(["erro"]), fila=FilaFalsa(["pronto"])) if (tmp_path / "e").mkdir() is None else None
    fim = g2.esperar(g2.iniciar("twitch.tv/videos/1234567890")["id"])
    assert fim["estado"] == "erro" and "sem espaço" in fim["erro"]


def _espera_candidato(g, rid, tempo=10.0):
    fim = time.monotonic() + tempo
    while time.monotonic() < fim:
        r = g.obter(rid)
        if r["dados"].get("candidato"):
            return r
        time.sleep(0.01)
    raise AssertionError("nenhum arquivo candidato apareceu")


def test_youtube_vigia_a_pasta_downloads_e_segue_quando_o_arquivo_novo_para_de_crescer(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "antigo.mp4").write_bytes(b"x" * 300_000)                                             # já existia: não conta
    fila = FilaFalsa(["pronto"])
    g = _ger(tmp_path, Roteiro(), fila=fila, pasta_downloads=downloads)
    r = g.iniciar("https://youtu.be/dQw4w9WgXcQ")
    assert [e["nome"] for e in r["etapas"]] == ["aguardar_download", "analisar", "parear"]
    espera = g.esperar(r["id"], {"esperando_voce"})
    assert "studio.youtube.com/video/dQw4w9WgXcQ/edit" in espera["etapas"][0]["detalhe"] and fila.adicionados == []
    (downloads / "meu video.mp4.crdownload").write_bytes(b"x" * 10)                                    # download em andamento do navegador: ignorado
    novo = downloads / "minha live.mp4"
    novo.write_bytes(b"x" * 200_000)
    time.sleep(0.05)
    novo.write_bytes(b"x" * 400_000)                                                                   # ainda crescendo
    pergunta = _espera_candidato(g, r["id"])
    assert pergunta["dados"]["candidato"]["nome"] == "minha live.mp4" and "É este o vídeo?" in pergunta["etapas"][0]["detalhe"] and fila.adicionados == []   # M5: pergunta ao dono
    g.confirmar(r["id"])
    assert g.esperar(r["id"])["estado"] == "pronta" and Path(fila.adicionados[0]).name == "minha live.mp4"


def test_iniciar_recusa_entrada_que_nao_serve_e_rounds_invalidos(tmp_path):
    g = _ger(tmp_path, Roteiro())
    with pytest.raises(ValueError, match="CLIPE"):
        g.iniciar("https://clips.twitch.tv/AbcDefGhi")
    with pytest.raises(ValueError, match="Aceito:"):
        g.iniciar("oi")
    with pytest.raises(ValueError, match="separados por vírgula"):
        g.iniciar(str(_demo(tmp_path)), {"rounds": "3; quit"})
    assert g.listar() == []                                                                            # nada foi criado


def test_plano_mostra_etapas_e_estimativa_sem_iniciar_nada(tmp_path, monkeypatch):
    demo = _demo(tmp_path)
    monkeypatch.setattr(receitas, "estimar_demo", lambda caminho, cfg: {"partida": "de_mirage 5x13", "rounds": [3, 8, 9], "minutos_de_jogo": 3.9, "gravar_s": 200})
    g = _ger(tmp_path, Roteiro())
    p = g.plano(str(demo))
    assert p["entrada"]["tipo"] == "demo" and [e["nome"] for e in p["etapas"]] == ["esperar_conta", "gravar", "editar"] and p["demo"]["rounds"] == [3, 8, 9]
    assert p["estimativa_s"] == 30 + 200 + 150 and g.listar() == []
    ruim = g.plano("oi")
    assert ruim["entrada"]["aceita"] is False and "etapas" not in ruim


def test_retomar_nao_baixa_a_demo_de_novo_quando_so_a_gravacao_falhou(tmp_path):
    baixada = _demo(tmp_path, "match730_003849321203751714819_0599033075_999.dem")
    rot = Roteiro(baixar_demo=[(["Pronta: " + str(baixada)], 0)], gravador_demo=[(["[parei] CS2 travou."], 1), GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger(tmp_path, rot)
    r = g.iniciar(CODIGO)
    assert g.esperar(r["id"])["estado"] == "erro"
    g.retomar(r["id"])
    assert g.esperar(r["id"])["estado"] == "pronta"
    assert rot.modulos() == ["baixar_demo", "gravador_demo", "gravador_demo", "editar_demo"]          # o download (já ok) NÃO repetiu
    assert g.obter(r["id"])["dados"]["demo"] == str(baixada)                                           # e a demo baixada ficou guardada para a gravação


def test_partidas_novas_avisa_so_das_recentes_que_ainda_nao_viraram_trabalho_nem_foram_dispensadas(tmp_path):
    import os
    pasta = tmp_path / "replays"
    pasta.mkdir()
    agora = time.time()
    nomes = {"nova": "match730_003852947612143452464_1002297918_202.dem", "nova2": "match730_003852959236472439075_1636155224_202.dem",
             "velha": "match730_003846488745834446879_2134712485_201.dem", DEMO_FEITA: DEMO_FEITA}
    for chave, atraso_h in (("nova", 2), ("nova2", 1), ("velha", 24 * 20)):
        arq = pasta / nomes[chave]
        arq.write_bytes(ent.CABECALHO_CS2 + b"x" * 2000)
        os.utime(arq, (agora - atraso_h * 3600, agora - atraso_h * 3600))
    feita = pasta / DEMO_FEITA
    feita.write_bytes(ent.CABECALHO_CS2 + b"x" * 2000)
    os.utime(feita, (agora - 3 * 3600, agora - 3 * 3600))
    (pasta / "outra.txt").write_bytes(b"x")
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = GerenciadorReceitas(tmp_path / "raiz", popen_factory=rot, python="PY", conta_logada=Conta(BISTURI_ID), config_fn=lambda: _cfg(), poll_s=0.01,
                            procurar_em=lambda: [pasta])
    novas = g.partidas_novas()
    assert [n["nome"] for n in novas] == [nomes["nova2"], nomes["nova"], DEMO_FEITA]                  # da mais nova; a de 20 dias fica de fora
    g.dispensar(nomes["nova2"])                                                                       # "Agora não"
    assert [n["nome"] for n in g.partidas_novas()] == [nomes["nova"], DEMO_FEITA]
    r = g.iniciar(str(feita))                                                                         # virou trabalho: não é mais "nova"
    assert g.esperar(r["id"])["estado"] == "pronta"
    assert [n["nome"] for n in g.partidas_novas()] == [nomes["nova"]]
    g2 = GerenciadorReceitas(tmp_path / "raiz", popen_factory=rot, python="PY", conta_logada=Conta(BISTURI_ID), config_fn=lambda: _cfg(), procurar_em=lambda: [pasta])
    assert [n["nome"] for n in g2.partidas_novas()] == [nomes["nova"]]                                # a dispensa e o trabalho sobrevivem ao reabrir
    with pytest.raises(ValueError):
        g.dispensar("../../x")
    assert len(g.partidas_novas(limite=1)) == 1


DEMO_FEITA = "match730_003849321203751714819_0599033075_201.dem"


def test_analisar_pede_a_demo_automatica_a_menos_que_o_dono_desligue(tmp_path):
    video = tmp_path / "2026-11-08 16-30-10.mkv"
    video.write_bytes(b"x" * 50)
    fila = FilaFalsa(["pronto"])
    opcoes_vistas = []
    original = fila.adicionar
    fila.adicionar = lambda arquivo, opcoes: opcoes_vistas.append(dict(opcoes)) or original(arquivo, opcoes)
    g = _ger(tmp_path, Roteiro(), fila=fila)
    assert g.esperar(g.iniciar(str(video))["id"])["estado"] == "pronta"
    assert g.esperar(g.iniciar(str(video), {"sem_demo": True})["id"])["estado"] == "pronta"
    assert opcoes_vistas == [{"demo_auto": True}, {"demo_auto": False}]


# ── E4: live + demo ──────────────────────────────────────────────────────────────────────────────

def _live_com_analise(tmp_path, demos, sincronia=None, na_pasta_de_demos=True):
    """Uma live, a pasta saida/<id> da análise dela e (opcional) as demos confirmadas no sincronia.json."""
    raiz = tmp_path / "raiz"
    live = tmp_path / "2026-11-08 16-30-10.mkv"
    live.write_bytes(b"x" * 80)
    analise = raiz / "saida" / "2026-11-08_16-30-10"
    analise.mkdir(parents=True)
    (raiz / "saida" / "indice.json").write_text(json.dumps([{"id": "2026-11-08_16-30-10", "arquivo": live.name, "caminho": str(live)}]), encoding="utf-8")
    if sincronia is None:
        sincronia = [{"demo": d, "usada": True, "deslocamento": 123.4} for d in demos]
    (analise / "sincronia.json").write_text(json.dumps(sincronia), encoding="utf-8")
    pasta_demos = tmp_path / "replays"
    pasta_demos.mkdir(exist_ok=True)
    if na_pasta_de_demos:
        for d in demos:
            (pasta_demos / d).write_bytes(ent.CABECALHO_CS2 + b"x")
    return live, analise, pasta_demos


def _ger_live(tmp_path, rot, pasta_demos, faixa_voz=3, **extra):
    def cfg():
        c = _cfg()
        c.audio.faixa_voz = faixa_voz
        return c
    return GerenciadorReceitas(tmp_path / "raiz", popen_factory=rot, python="PY", conta_logada=Conta(BISTURI_ID), config_fn=cfg, poll_s=0.01,
                               procurar_em=lambda: [pasta_demos], **extra)


D1, D2 = "match730_003849321203751714819_0599033075_201.dem", "match730_003852947612143452464_1002297918_202.dem"


def test_live_com_demo_confirmada_regrava_em_imagem_limpa_e_junta_a_voz_da_live(tmp_path):
    live, analise, pasta_demos = _live_com_analise(tmp_path, [D1])
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger_live(tmp_path, rot, pasta_demos, fila=FilaFalsa(["pronto"]))
    r = g.iniciar(str(live))
    assert [e["nome"] for e in r["etapas"]] == ["analisar", "parear"]
    fim = g.esperar(r["id"])
    assert fim["estado"] == "pronta" and [e["nome"] for e in fim["etapas"]] == ["analisar", "parear", "esperar_conta", "gravar", "editar"]
    assert all(e["estado"] == "ok" for e in fim["etapas"]) and "Demo confirmada" in fim["etapas"][1]["detalhe"]
    grav, edit = rot.comandos
    assert grav[grav.index("--demo") + 1] == str(pasta_demos / D1)
    assert edit[edit.index("--demo") + 1] == str(pasta_demos / D1) and edit[edit.index("--live") + 1] == str(live) and edit[edit.index("--analise-da-live") + 1] == str(analise)
    assert fim["dados"]["pares"][0]["deslocamento"] == 123.4


def test_sem_a_faixa_do_microfone_a_edicao_sai_sem_a_voz_e_avisa_como_configurar(tmp_path):
    live, analise, pasta_demos = _live_com_analise(tmp_path, [D1])
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger_live(tmp_path, rot, pasta_demos, faixa_voz=0, fila=FilaFalsa(["pronto"]))
    fim = g.esperar(g.iniciar(str(live))["id"])
    edit = rot.comandos[-1]
    assert fim["estado"] == "pronta" and "--live" in edit and "faixa_voz" in fim["etapas"][-1]["detalhe"]      # R1: a live vai sempre (a webcam não depende do microfone); o editar_demo decide a voz


def test_duas_partidas_na_mesma_live_viram_duas_regravacoes_em_ordem(tmp_path):
    live, analise, pasta_demos = _live_com_analise(tmp_path, [D1, D2])
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger_live(tmp_path, rot, pasta_demos, fila=FilaFalsa(["pronto"]))
    fim = g.esperar(g.iniciar(str(live))["id"])
    assert fim["estado"] == "pronta" and [e["nome"] for e in fim["etapas"]] == ["analisar", "parear"] + ["esperar_conta", "gravar", "editar"] * 2
    assert "(partida 1 de 2)" in fim["etapas"][3]["titulo"] and "(partida 2 de 2)" in fim["etapas"][6]["titulo"]
    demos_gravadas = [c[c.index("--demo") + 1] for c in rot.comandos if "gravador_demo" in " ".join(c)]
    assert demos_gravadas == [str(pasta_demos / D1), str(pasta_demos / D2)]


def test_sem_demo_confirmada_ou_sumida_ou_desligado_fica_so_o_kit_da_live(tmp_path):
    # análise não confirmou (usada = False) → nada a regravar
    live, analise, pasta_demos = _live_com_analise(tmp_path, [D1], sincronia=[{"demo": D1, "usada": False, "deslocamento": None}])
    rot = Roteiro()
    g = _ger_live(tmp_path, rot, pasta_demos, fila=FilaFalsa(["pronto"]))
    fim = g.esperar(g.iniciar(str(live))["id"])
    assert fim["estado"] == "pronta" and [e["nome"] for e in fim["etapas"]] == ["analisar", "parear"] and rot.comandos == []
    assert "fica só o kit da live" in fim["etapas"][1]["detalhe"]
    # demo confirmada mas apagada do computador
    outra = tmp_path / "b"
    outra.mkdir()
    live2, _, pasta2 = _live_com_analise(outra, [D1], na_pasta_de_demos=False)
    g2 = _ger_live(outra, rot, pasta2, fila=FilaFalsa(["pronto"]))
    fim2 = g2.esperar(g2.iniciar(str(live2))["id"])
    assert [e["nome"] for e in fim2["etapas"]] == ["analisar", "parear"] and "não está mais no computador" in fim2["etapas"][1]["detalhe"]
    # o dono desligou (sem_demo ou limpo=False): o passo nem existe
    outra2 = tmp_path / "c"
    outra2.mkdir()
    live3, _, pasta3 = _live_com_analise(outra2, [D1])
    g3 = _ger_live(outra2, rot, pasta3, fila=FilaFalsa(["pronto"]))
    assert [e["nome"] for e in g3.iniciar(str(live3), {"limpo": False})["etapas"]] == ["analisar"]
    assert [e["nome"] for e in g3.iniciar(str(live3), {"sem_demo": True})["etapas"]] == ["analisar"]
    assert rot.comandos == []


def test_retomar_uma_receita_de_live_mantem_as_etapas_inseridas_e_nao_reanalisa(tmp_path):
    live, analise, pasta_demos = _live_com_analise(tmp_path, [D1])
    rot = Roteiro(gravador_demo=[(["[parei] CS2 travou."], 1), GRAVAR_OK], editar_demo=[EDITAR_OK])
    fila = FilaFalsa(["pronto"])
    g = _ger_live(tmp_path, rot, pasta_demos, fila=fila)
    r = g.iniciar(str(live))
    assert g.esperar(r["id"])["estado"] == "erro"
    g.retomar(r["id"])
    fim = g.esperar(r["id"])
    assert fim["estado"] == "pronta" and len(fila.adicionados) == 1                           # a live NÃO foi analisada de novo
    assert [e["nome"] for e in fim["etapas"]] == ["analisar", "parear", "esperar_conta", "gravar", "editar"] and rot.modulos() == ["gravador_demo", "gravador_demo", "editar_demo"]
    g2 = _ger_live(tmp_path, rot, pasta_demos, fila=fila)                                      # reabrir o programa também mantém o desenho
    assert [e["nome"] for e in g2.obter(r["id"])["etapas"]] == ["analisar", "parear", "esperar_conta", "gravar", "editar"]


def test_aviso_de_live_sem_a_faixa_do_microfone_aparece_no_detalhe_da_etapa_de_edicao(tmp_path):
    live, analise, pasta_demos = _live_com_analise(tmp_path, [D1])
    aviso = "[aviso] A live tem só 1 faixa(s) de áudio e o microfone está na faixa 2 (audio.faixa_voz): sigo SEM a sua voz."
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[([aviso, "Edição pronta em saida/x_limpo"], 0)])
    g = _ger_live(tmp_path, rot, pasta_demos, fila=FilaFalsa(["pronto"]))
    fim = g.esperar(g.iniciar(str(live))["id"])
    assert fim["estado"] == "pronta" and "sem a sua voz" in fim["etapas"][-1]["detalhe"] and "1 faixa(s) de áudio" in fim["etapas"][-1]["detalhe"]


def test_youtube_nao_usa_o_arquivo_achado_sem_o_dono_confirmar_e_o_nao_e_este_volta_a_vigiar(tmp_path):
    """Achado M5 da revisão: o arquivo novo em Downloads pode ser outro download do dono."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    fila = FilaFalsa(["pronto"])
    g = _ger(tmp_path, Roteiro(), fila=fila, pasta_downloads=downloads)
    r = g.iniciar("https://youtu.be/dQw4w9WgXcQ")
    g.esperar(r["id"], {"esperando_voce"})
    with pytest.raises(RuntimeError, match="esperando a sua confirmação"):
        g.confirmar(r["id"])                                                                          # ainda não há candidato
    (downloads / "outra coisa.mp4").write_bytes(b"x" * 300_000)                                         # um download qualquer do dono
    cand = _espera_candidato(g, r["id"])
    time.sleep(0.15)
    assert g.obter(r["id"])["estado"] == "esperando_voce" and fila.adicionados == []                    # sem confirmar, nada acontece
    g.confirmar(r["id"], aceitar=False)                                                                 # "Não é este"
    fim = time.monotonic() + 5
    while time.monotonic() < fim and g.obter(r["id"])["dados"].get("candidato"):
        time.sleep(0.01)
    assert "candidato" not in g.obter(r["id"])["dados"] and g.obter(r["id"])["dados"]["ignorados"] == ["outra coisa.mp4"]
    (downloads / "minha live.mp4").write_bytes(b"x" * 300_000)                                          # o certo
    cand2 = _espera_candidato(g, r["id"])
    assert cand2["dados"]["candidato"]["nome"] == "minha live.mp4"                                      # o recusado não volta a ser perguntado
    g.confirmar(r["id"])
    assert g.esperar(r["id"])["estado"] == "pronta" and Path(fila.adicionados[0]).name == "minha live.mp4" and cand["dados"]["candidato"]["mb"] > 0


def test_vod_da_twitch_que_ja_esta_na_pasta_entrada_e_usado_sem_baixar_de_novo(tmp_path):
    """Achado M1: colar de novo o link de um VOD já baixado dava erro."""
    entrada = tmp_path / "raiz" / "entrada"
    entrada.mkdir(parents=True)
    (entrada / "1234567890-twitch.mp4").write_bytes(b"x" * 300_000)
    vod, fila = VodFalso(["baixando"]), FilaFalsa(["pronto"])
    g = _ger(tmp_path, Roteiro(), vods=vod, fila=fila)
    fim = g.esperar(g.iniciar("twitch.tv/videos/1234567890")["id"])
    assert fim["estado"] == "pronta" and vod.iniciados == [] and "já estava baixado" in fim["etapas"][0]["detalhe"]
    assert Path(fila.adicionados[0]).name == "1234567890-twitch.mp4"
    (entrada / "1234567890-twitch.mp4").write_bytes(b"x" * 10)                                          # CONTROLE: arquivo pequeno demais (download interrompido) não conta
    vod2 = VodFalso(["pronto"])
    g2 = _ger(tmp_path / "b", Roteiro(), vods=vod2, fila=FilaFalsa(["pronto"])) if (tmp_path / "b").mkdir() is None else None
    (tmp_path / "b" / "raiz" / "entrada").mkdir(parents=True)
    (tmp_path / "b" / "raiz" / "entrada" / "1234567890-twitch.mp4").write_bytes(b"x" * 10)
    g2.esperar(g2.iniciar("twitch.tv/videos/1234567890")["id"])
    assert vod2.iniciados == ["https://www.twitch.tv/videos/1234567890"]


def test_falha_momentanea_ao_gravar_o_estado_nao_derruba_o_trabalho(tmp_path, monkeypatch):
    """Achado M4: no Windows a troca do arquivo de estado pode ser negada por um instante."""
    demo = _demo(tmp_path)
    g = _ger(tmp_path, Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK]))
    original = os.replace
    falhas = {"n": 0}

    def _temporario_da_receita(origem):                # G-004 (26/11): o temporário tem nome único, `receita.json.<8 letras>.tmp`
        nome = Path(origem).name
        return nome.startswith("receita.json.") and nome.endswith(".tmp")

    def replace_ruim(origem, alvo):
        if _temporario_da_receita(origem) and falhas["n"] < 6:                                            # nega as 6 primeiras trocas
            falhas["n"] += 1
            raise PermissionError("negado por um instante")
        return original(origem, alvo)
    monkeypatch.setattr("cs2clip.tools.time", type("SemEspera", (), {"sleep": staticmethod(lambda s: None), "__getattr__": lambda self, n: getattr(time, n)})())      # sem esperar de verdade entre as tentativas
    monkeypatch.setattr(os, "replace", replace_ruim)                                                     # a troca passa por tools.substituir_arquivo (F3): é o os.replace que nega
    fim = g.esperar(g.iniciar(str(demo))["id"])
    assert fim["estado"] == "pronta" and falhas["n"] == 6                                                # o trabalho terminou mesmo com as falhas
    # CONTROLE: com o disco negando SEMPRE, o trabalho ainda termina na memória e avisa que o disco falhou

    def sempre_nega(origem, alvo):
        if _temporario_da_receita(origem):
            raise PermissionError("sempre")
        return original(origem, alvo)
    monkeypatch.setattr(os, "replace", sempre_nega)
    g2 = _ger(tmp_path / "b", Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])) if (tmp_path / "b").mkdir() is None else None
    fim2 = g2.esperar(g2.iniciar(str(demo))["id"])
    assert fim2["estado"] == "pronta" and fim2.get("disco_falhou") is True


def test_o_estado_nao_vai_para_o_disco_a_cada_linha_da_saida(tmp_path, monkeypatch):
    demo = _demo(tmp_path)
    linhas = [f"Round {i}: gravando" for i in range(200)]
    g = _ger(tmp_path, Roteiro(gravador_demo=[(linhas, 0)], editar_demo=[EDITAR_OK]))
    gravacoes = []
    original = g._salvar
    monkeypatch.setattr(g, "_salvar", lambda r: gravacoes.append(1) or original(r))
    assert g.esperar(g.iniciar(str(demo))["id"])["estado"] == "pronta"
    assert len(gravacoes) < 40, len(gravacoes)                                                           # antes: >200 só por causa da saída do gravador


def test_aviso_de_troca_de_conta_diz_que_o_bisturi_continua_sozinho(tmp_path):
    g = _ger(tmp_path, Roteiro(), Conta(PRINCIPAL_ID))
    r = g.iniciar(str(_demo(tmp_path)))
    detalhe = g.esperar(r["id"], {"esperando_voce"})["etapas"][0]["detalhe"]
    assert "continua sozinho" in detalhe and "clique em Continuar" not in detalhe and "PRINCIPAL" in detalhe
    g.cancelar(r["id"])
    g.esperar(r["id"])


def test_ao_reabrir_carrega_os_trabalhos_mais_recentes_nao_os_ultimos_por_nome(tmp_path):
    import os
    raiz = tmp_path / "raiz" / "trabalhos"
    for i in range(65):
        rid = f"{(i * 37) % 65:04d}"                                                                     # nomes fora de ordem de tempo
        pasta = raiz / rid
        pasta.mkdir(parents=True)
        arq = pasta / "receita.json"
        arq.write_text(json.dumps({"id": rid, "criada_em": float(i), "titulo": rid, "estado": "pronta", "etapas": [], "dados": {}, "entrada": {}, "opcoes": {}}), encoding="utf-8")
        os.utime(arq, (1000 + i, 1000 + i))                                                              # o i-ésimo criado é mais recente que o anterior
    g = _ger(tmp_path, Roteiro())
    ids = {x["id"] for x in g.listar()}
    esperados_recentes = {f"{(i * 37) % 65:04d}" for i in range(5, 65)}                                  # os 60 mais recentes (descarta os 5 mais antigos)
    assert {r for r in g._receitas} == esperados_recentes and ids <= esperados_recentes



def test_retomar_no_instante_em_que_a_execucao_anterior_ainda_solta_a_trava_do_cs2_nao_duplica_a_gravacao(tmp_path):
    """B6 (revisão de 11/11): o estado vira `erro` antes de o `finally` da execução antiga soltar a trava. Retomar nessa janela fazia a execução nova achar que já era
    a dona da trava e a antiga a soltava depois: outro trabalho podia abrir o CS2 por cima. Agora o Retomar espera a execução anterior terminar de vez."""
    demo = _demo(tmp_path)
    seguro = threading.Event()
    rot = Roteiro(gravador_demo=[(["[parei] O HLAE não começou a gravar o round 9."], 1), (["Round 9: gravando"], 0, seguro)], editar_demo=[EDITAR_OK])
    g = _ger(tmp_path, rot)
    g.espera_fim_s = 0.2
    no_finally, libera = threading.Event(), threading.Event()
    soltar_original = g._soltar_cs2

    def soltar_devagar(rid):                                                                              # só o `finally` do erro: nesse ponto o estado já é `erro`
        if g.obter(rid)["estado"] == "erro":
            no_finally.set()
            libera.wait(10)
        soltar_original(rid)

    g._soltar_cs2 = soltar_devagar
    r = g.iniciar(str(demo))
    assert no_finally.wait(10) and g.obter(r["id"])["estado"] == "erro" and g._dono_cs2 == r["id"]       # a janela do defeito: erro, mas a trava ainda é da execução antiga
    with pytest.raises(RuntimeError, match="ainda está terminando"):
        g.retomar(r["id"])
    assert rot.modulos() == ["gravador_demo"] and g.obter(r["id"])["estado"] == "erro"                   # nada novo começou
    libera.set()
    g._threads[r["id"]].join(10)
    assert g._dono_cs2 is None and not g._trava_cs2.locked()
    g._soltar_cs2 = soltar_original
    g.retomar(r["id"])
    for _ in range(300):                                                                                   # a execução nova pega a trava de verdade e a segura durante a gravação
        if g._dono_cs2 == r["id"] and len(rot.processos) == 2:
            break
        time.sleep(0.01)
    assert g._dono_cs2 == r["id"] and g._trava_cs2.locked()
    seguro.set()
    assert g.esperar(r["id"])["estado"] == "pronta" and g._dono_cs2 is None and not g._trava_cs2.locked()


# ── S2 (B3): título legível "Mirage 11x13 · 08/11" em vez de "match730_0038…" ──────────────────────────────────────────────────────
def _com_data(arq, ano=2026, mes=11, dia=8):
    import datetime
    import os
    ts = datetime.datetime(ano, mes, dia, 15, 0).timestamp()
    os.utime(arq, (ts, ts))
    return arq


def test_nome_do_mapa_conhecido_e_desconhecido():
    assert receitas.nome_do_mapa("de_mirage") == "Mirage" and receitas.nome_do_mapa("DE_DUST2") == "Dust2" and receitas.nome_do_mapa("de_inferno") == "Inferno"
    assert receitas.nome_do_mapa("de_novo_mapa") == "Novo Mapa" and receitas.nome_do_mapa("cs_office") == "Office" and receitas.nome_do_mapa("") == ""


def test_titulo_da_demo_usa_mapa_placar_e_dia_e_nunca_fica_vazio(tmp_path, monkeypatch):
    demo = _com_data(_demo(tmp_path))
    monkeypatch.setattr(receitas, "_mapa_da_demo", lambda c: "de_mirage")
    assert receitas.titulo_da_demo(demo) == "Mirage · 08/11"
    assert receitas.titulo_da_demo(demo, (11, 13)) == "Mirage 11x13 · 08/11"
    # a data da PARTIDA (do .dem.info do lado) vale mais que a data do arquivo
    import datetime
    info = demo.with_name(demo.name + ".info")
    info.write_text(json.dumps({"matchtime": int(datetime.datetime(2026, 10, 30, 21, 0).timestamp())}), encoding="utf-8")
    assert receitas.titulo_da_demo(demo, (13, 5)) == "Mirage 13x5 · 30/10"
    # sem mapa lido (demo estragada): "Partida · dia"; sem nada (arquivo que não existe): o nome do arquivo
    monkeypatch.setattr(receitas, "_mapa_da_demo", lambda c: "")
    info.unlink()
    assert receitas.titulo_da_demo(demo) == "Partida · 08/11"
    assert receitas.titulo_da_demo(tmp_path / "sumiu.dem") == "sumiu.dem"


def test_titulo_da_demo_com_o_leitor_real_numa_demo_estragada_nao_quebra(tmp_path):
    demo = _com_data(_demo(tmp_path))                                                                      # cabeçalho certo, corpo falso: o leitor real falha
    assert receitas._mapa_da_demo(demo) == "" and receitas.titulo_da_demo(demo) == "Partida · 08/11"
    assert receitas.placar_da_demo(demo, _cfg()) is None


def test_o_titulo_nasce_rapido_com_mapa_e_dia_e_ganha_o_placar_na_execucao_nao_no_clique(tmp_path, monkeypatch):
    demo = _com_data(_demo(tmp_path))
    monkeypatch.setattr(receitas, "_mapa_da_demo", lambda c: "de_mirage")
    chamadas = []

    def placar_falso(caminho, cfg):
        chamadas.append(threading.current_thread().name)
        return (11, 13)

    monkeypatch.setattr(receitas, "placar_da_demo", placar_falso)
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger(tmp_path, rot)
    r = g.iniciar(str(demo))
    assert r["titulo"] == "Mirage · 08/11"                                                                 # no clique: só o cabeçalho (a leitura inteira da demo NÃO roda aqui)
    fim = g.esperar(r["id"])
    assert fim["estado"] == "pronta" and fim["titulo"] == "Mirage 11x13 · 08/11"
    assert chamadas and all(n.startswith("receita-") for n in chamadas) and len(chamadas) == 1             # só na thread do trabalho, 1 vez
    salvo = json.loads((tmp_path / "raiz" / "trabalhos" / r["id"] / "receita.json").read_text(encoding="utf-8"))
    assert salvo["titulo"] == "Mirage 11x13 · 08/11"


def test_sem_placar_o_titulo_fica_sem_ele_e_o_trabalho_segue(tmp_path, monkeypatch):
    demo = _com_data(_demo(tmp_path))
    monkeypatch.setattr(receitas, "_mapa_da_demo", lambda c: "de_nuke")
    monkeypatch.setattr(receitas, "placar_da_demo", lambda c, cfg: None)
    g = _ger(tmp_path, Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK]))
    fim = g.esperar(g.iniciar(str(demo))["id"])
    assert fim["estado"] == "pronta" and fim["titulo"] == "Nuke · 08/11"


def test_codigo_de_partilha_ganha_o_titulo_da_partida_depois_do_download(tmp_path, monkeypatch):
    baixada = _com_data(_demo(tmp_path, "match730_003849321203751714819_0599033075_999.dem"), dia=9)
    monkeypatch.setattr(receitas, "_mapa_da_demo", lambda c: "de_ancient")
    monkeypatch.setattr(receitas, "placar_da_demo", lambda c, cfg: (13, 9))
    rot = Roteiro(baixar_demo=[(["Partida 1", f"Pronta: {baixada}"], 0)], gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    g = _ger(tmp_path, rot)
    r = g.iniciar(CODIGO)
    assert r["titulo"].startswith("Partida do código")                                                     # antes do download não há demo para ler
    fim = g.esperar(r["id"])
    assert fim["estado"] == "pronta" and fim["titulo"] == "Ancient 13x9 · 09/11"


def test_um_erro_ao_montar_o_titulo_nunca_derruba_o_trabalho(tmp_path, monkeypatch):
    demo = _com_data(_demo(tmp_path))
    monkeypatch.setattr(receitas, "_mapa_da_demo", lambda c: "de_mirage")

    def quebra(caminho, cfg):
        raise ValueError("defeito de mentira")

    monkeypatch.setattr(receitas, "placar_da_demo", quebra)
    g = _ger(tmp_path, Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK]))
    fim = g.esperar(g.iniciar(str(demo))["id"])
    assert fim["estado"] == "pronta" and fim["titulo"] == "Mirage · 08/11"


def test_o_aviso_de_troca_de_conta_da_receita_cita_o_apelido(tmp_path):
    """C2: o texto que a tela mostra (e que o aviso de conta repete) traz o apelido; a receita continua esperando a mesma conta."""
    def com_apelido():
        cfg = _cfg()
        cfg.gravacao_demo.conta_apelido, cfg.gravacao_demo.conta_principal_apelido = "Smurf do Bisturi", "Conta de jogo"
        return cfg
    rot = Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK])
    conta = Conta(PRINCIPAL_ID)
    g = GerenciadorReceitas(tmp_path / "raiz", popen_factory=rot, python="PY", conta_logada=conta, config_fn=com_apelido, poll_s=0.01, procurar_em=lambda: [tmp_path])
    r = g.iniciar(str(_demo(tmp_path)))
    esperando = g.esperar(r["id"], {"esperando_voce"})
    detalhe = esperando["etapas"][0]["detalhe"]
    assert "PRINCIPAL" in detalhe and "(Conta de jogo)" in detalhe and "(Smurf do Bisturi)" in detalhe and rot.comandos == []
    conta.valor = BISTURI_ID                                                                        # CONTROLE: a mesma troca de sempre libera
    assert g.esperar(r["id"])["estado"] == "pronta"
