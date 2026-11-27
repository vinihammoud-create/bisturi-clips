"""D4 (Plano D), parte 2: o roteiro inteiro da gravação, testado com um JOGO FALSO (nenhum CS2 nem HLAE de verdade é aberto).

O jogo falso interpreta as linhas do console remoto como o HLAE faria: ao `demo_resume`, cria `take000N\\bisturi\\video.mp4` e `audio.wav`
de verdade (ffmpeg). Cada teste tem CONTROLE.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from cs2clip import gravador_demo as gd
from cs2clip import netcon, steam_local, terceiros
from cs2clip.config import Config
from cs2clip.demo import Abate, PartidaDemo, RoundDemo
from cs2clip.tools import FFMPEG, FFPROBE

SEGUNDA = "76561198182487950"
PRINCIPAL = "76561198071376839"
ID_SEGUNDA, ID_PRINCIPAL = 222222222, 111111111
T = 64.0


class Relogio:
    def __init__(self):
        self.t = 0.0

    def agora(self):
        return self.t

    def dormir(self, s):
        self.t += s


class JogoFalso:
    """Faz o papel do CS2 + HLAE: aceita as linhas do console remoto e grava vídeos de verdade quando manda o `demo_resume`."""

    def __init__(self, relogio: Relogio, wav=True, fim_agendado_dispara=True, velocidade=4.0):
        self.linhas: list[str] = []
        self.relogio, self.wav, self.dispara, self.velocidade = relogio, wav, fim_agendado_dispara, velocidade
        self.pasta: Path | None = None
        self.ini = self.fim = None
        self.n_take = 0
        self.camera_atual: list[str] = []
        self.take_pendente: Path | None = None
        self.camera_por_take: dict[str, list[str]] = {}
        self.fechado = False

    # -- interface do ClienteNetcon
    def conectar(self, tempo_maximo=60.0, intervalo=0.5):
        pass

    def esperar(self, texto, tempo_maximo=10.0):
        return True

    def lido(self):
        return ""

    def fechar(self):
        pass

    def enviar(self, linha: str):
        netcon.validar_linha(linha)                         # o jogo falso só recebe o que a lista branca deixa passar
        self.linhas.append(linha)
        if linha.startswith("spec_") or linha.startswith("mirv_script"):
            self.camera_atual.append(linha)
        m = re.match(r'mirv_streams record name "(.+)"$', linha)
        if m:
            self.pasta = Path(m.group(1))
        m = re.match(r'mirv_cmd addAtTick (\d+) "mirv_streams record start"$', linha)
        if m:
            self.ini = int(m.group(1))
        m = re.match(r'mirv_cmd addAtTick (\d+) "mirv_streams record end; demo_pause"$', linha)
        if m:
            self.fim = int(m.group(1))
        if linha == "demo_goto" or linha.startswith("demo_goto"):
            self.camera_atual = []                          # pular zera a câmera, como uma nova posição
            m = re.match(r"demo_goto (\d+) ", linha)
            self.tick_pausado = int(m.group(1)) if m else None
        if linha == "mirv_streams record start" and self.ini is None:
            self.ini = self.tick_pausado                    # gravar pausado: o começo é o tick do pulo
        if linha == "demo_resume" and self.ini is not None and self.fim is not None:
            self._gravar()
        if linha == "mirv_streams record end" and self.take_pendente is not None:
            self._finalizar(self.take_pendente)
        if linha == "quit":
            self.fechado = True

    def _gravar(self):
        assert self.pasta is not None, "o gravador mandou gravar sem `record name`"
        take = self.pasta / f"take{self.n_take:04d}"
        self.n_take += 1
        (take / "bisturi").mkdir(parents=True)
        self.camera_por_take[take.name] = list(self.camera_atual)
        dur = (self.fim - self.ini + 2) / T
        self.relogio.t += dur / self.velocidade
        self._duracao_take = dur
        if self.dispara:
            self._finalizar(take)
        else:
            (take / "bisturi" / "video.mp4").write_bytes(b"incompleto sem moov")   # o ffmpeg ainda está escrevendo
            self.take_pendente = take
        self.ini = self.fim = None

    def _finalizar(self, take: Path):
        dur = getattr(self, "_duracao_take", 2.0)
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=320x180:r=10:d={dur}", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", str(take / "bisturi" / "video.mp4")], check=True, capture_output=True)
        if self.wav:
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:d={dur}", "-ar", "48000", str(take / "audio.wav")],
                           check=True, capture_output=True)
        self.take_pendente = None


def _partida(trechos_abates={1: 3, 2: 4}):
    rounds = [RoundDemo(n, 20.0 * n, 20.0 * n + 3.0, "CT", "t_killed", tick_inicio=int(20 * n * T), tick_fim=int((20 * n + 3) * T))
              for n in (1, 2)]
    p = PartidaDemo("de_teste", {1: "eu"}, int(PRINCIPAL), "eu", rounds, accountid=ID_PRINCIPAL, user_id=9, entity_id=533, patch_version="14188")
    for n, k in trechos_abates.items():
        for i in range(k):
            t = 20.0 * n + 1 + 0.5 * i
            p.abates.append(Abate(t, n, "x", "ak47", tick=int(t * T)))
    return p


def _cfg():
    cfg = Config()
    cfg.gravacao_demo.conta_steamid = SEGUNDA
    cfg.gravacao_demo.conta_principal_id = PRINCIPAL
    return cfg


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Instalações e Steam falsos: nada do PC real é usado."""
    bin_dir = tmp_path / "bin"
    (bin_dir / "hlae" / "ffmpeg" / "bin").mkdir(parents=True)
    (bin_dir / "hlae" / "HLAE.exe").write_bytes(b"MZ")
    (bin_dir / "hlae" / "ffmpeg" / "bin" / "ffmpeg.exe").write_bytes(b"MZ")
    monkeypatch.setattr(terceiros, "PASTA_BIN", bin_dir)
    monkeypatch.setattr(terceiros, "caminho_hlae", lambda bin_dir_=None: bin_dir / "hlae" / "HLAE.exe")
    cs2 = tmp_path / "cs2.exe"
    cs2.write_bytes(b"MZ")
    monkeypatch.setattr(steam_local, "cs2_exe", lambda steam=None: cs2)
    monkeypatch.setattr(steam_local, "versao_cs2", lambda pasta=None: {"PatchVersion": "1.41.9.6"})
    monkeypatch.setattr(steam_local, "pasta_cs2", lambda steam=None: None)
    demo = tmp_path / "demos" / "match730_1_2_3.dem"
    demo.parent.mkdir()
    demo.write_bytes(b"PBDEMS2")
    return tmp_path, demo


def _dependencias(jogo: JogoFalso, relogio: Relogio, **troca):
    lancados = []
    verificacoes = iter(troca.pop("verificacoes", []))

    class Foto:
        arquivos = {}
        ausentes = []

        def __len__(self):
            return 0
    foto = troca.pop("foto", Foto())
    dep = gd.Dependencias(
        lancar=lambda cmd: lancados.append(cmd), netcon=lambda porta: jogo, escuta_so_em_local=lambda porta: (True, ["127.0.0.1"]),
        tirar_foto=lambda a, b: foto, comparar=steam_local.comparar, jogo_aberto=lambda: [],
        conta_logada=lambda: ID_SEGUNDA, steam_rodando=lambda: True, dormir=relogio.dormir, agora=relogio.agora,
        duracao_do_video=gd._duracao_ffprobe, quadro_escuro=lambda p: False, juntar=gd._juntar_av,
        verificar_camera=lambda clip, tr: next(verificacoes, (True, "ok")), processo_do_jogo_vivo=lambda: not jogo.fechado,
        encerrar_jogo=lambda: setattr(jogo, "fechado", True), sem_admin=lambda: None)
    for k, v in troca.items():
        setattr(dep, k, v)
    dep.lancados = lancados
    return dep


def _roteiro():
    return gd.Roteiro(espera_carregar_s=0.0, espera_pulo_s=0.0, espera_repulo_s=0.0, espera_camera_s=0.0, poll_s=0.5, folga_relogio_s=20.0, espera_fechar_s=2.0)


def _audios(arq: Path) -> int:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(arq)],
                       capture_output=True, text=True)
    return len([x for x in r.stdout.splitlines() if x.strip()])


# ── pré-checagens (rápidas, sem ffmpeg): nada é lançado quando algo está errado ───────────────────

def test_conta_principal_logada_bloqueia_e_o_jogo_nunca_e_lancado(ambiente):
    tmp, demo = ambiente
    rel, jogo = Relogio(), JogoFalso(Relogio())
    dep = _dependencias(jogo, rel, conta_logada=lambda: ID_PRINCIPAL)
    with pytest.raises(gd.ErroGravacao, match="PRINCIPAL") as e:
        gd.gravar(demo, _partida(), _cfg(), tmp / "saida", dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert e.value.etapa == "conta" and dep.lancados == [] and jogo.linhas == []


def test_cs2_ou_obs_aberto_steam_fechado_admin_e_demo_nova_demais_tambem_barram(ambiente):
    tmp, demo = ambiente
    cfg = _cfg()
    cenarios = [("aberto", {"jogo_aberto": lambda: ["cs2.exe"]}, "cs2.exe"), ("steam", {"steam_rodando": lambda: False}, "Steam está fechado"),
                ("firewall", {"firewall": lambda porta: (False, "sem regra")}, "New-NetFirewallRule"),
                ("conta0", {"conta_logada": lambda: 0}, "fechado ou sem conta"),
                ("admin", {"sem_admin": lambda: (_ for _ in ()).throw(terceiros.ErroTerceiros("rodando como administrador"))}, "administrador")]
    for nome, troca, trecho in cenarios:
        rel = Relogio()
        dep = _dependencias(JogoFalso(rel), rel, **troca)
        with pytest.raises(gd.ErroGravacao, match=trecho):
            gd.gravar(demo, _partida(), cfg, tmp / f"s_{nome}", dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
        assert dep.lancados == []
    nova = _partida()
    nova.patch_version = "14999"
    rel = Relogio()
    with pytest.raises(gd.ErroGravacao, match="mais nova"):
        gd.gravar(demo, nova, cfg, tmp / "s_nova", dep=_dependencias(JogoFalso(rel), rel), roteiro=_roteiro(), imprimir=lambda *a: None)
    sem_ident = _partida()
    sem_ident.user_id = sem_ident.entity_id = sem_ident.accountid = None
    with pytest.raises(gd.ErroGravacao, match="identidade"):
        gd.gravar(demo, sem_ident, cfg, tmp / "s_id", dep=_dependencias(JogoFalso(rel), rel), roteiro=_roteiro(), imprimir=lambda *a: None)
    with pytest.raises(gd.ErroGravacao, match="Nenhum round"):
        gd.gravar(demo, _partida({1: 1}), cfg, tmp / "s_vazio", dep=_dependencias(JogoFalso(rel), rel), roteiro=_roteiro(), imprimir=lambda *a: None)


def test_controle_tudo_certo_nas_prechecagens_lanca_o_jogo_com_a_linha_segura(ambiente):
    tmp, demo = ambiente
    rel, jogo = Relogio(), JogoFalso(Relogio())
    dep = _dependencias(jogo, rel)
    # o jogo falso daqui não grava nada (sem `demo_resume` com agenda): só confere que a linha lançada é segura e o fluxo chega ao console
    jogo.enviar = lambda linha: (netcon.validar_linha(linha), jogo.linhas.append(linha))[1]
    with pytest.raises(gd.ErroGravacao):                                  # sem vídeo gravado o roteiro termina em erro, mas depois do lançamento
        gd.gravar(demo, _partida({1: 3}), _cfg(), tmp / "saida", dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    [cmd] = dep.lancados
    assert cmd[1:4] == ["-customLoader", "-noGui", "-autoStart"] and "-insecure" in cmd[cmd.index("-cmdLine") + 1].split()
    assert "+connect" not in " ".join(cmd) and jogo.linhas[-1] == "quit"


def test_console_aberto_para_a_rede_para_tudo_e_fecha_o_jogo(ambiente):
    tmp, demo = ambiente
    rel, jogo = Relogio(), JogoFalso(Relogio())
    dep = _dependencias(jogo, rel, escuta_so_em_local=lambda porta: (False, ["0.0.0.0"]))
    with pytest.raises(gd.ErroGravacao, match="0.0.0.0") as e:
        gd.gravar(demo, _partida(), _cfg(), tmp / "saida", dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert e.value.etapa == "netcon" and "quit" in jogo.linhas and jogo.fechado
    assert not any(linha.startswith("mirv_streams") for linha in jogo.linhas)        # nada de preparo foi mandado


def test_guarda_foto_depois_diferente_vira_alerta_e_o_controle_igual_nao(ambiente):
    tmp, demo = ambiente
    from cs2clip.steam_local import ArquivoFoto, Foto
    antes = Foto({"csgo/cfg/autoexec.cfg": ArquivoFoto(10, 1, "aaa")})
    depois = Foto({"csgo/cfg/autoexec.cfg": ArquivoFoto(10, 2, "bbb")})
    fotos = iter([antes, depois])
    rel, jogo = Relogio(), JogoFalso(Relogio())
    dep = _dependencias(jogo, rel, escuta_so_em_local=lambda porta: (False, ["0.0.0.0"]))
    dep.tirar_foto = lambda a, b: next(fotos)
    saidas = []
    with pytest.raises(gd.ErroGravacao):
        gd.gravar(demo, _partida(), _cfg(), tmp / "saida", dep=dep, roteiro=_roteiro(), imprimir=saidas.append)
    import json
    guarda = json.loads((tmp / "saida" / "registro.json").read_text(encoding="utf-8"))["guarda"]
    assert guarda["ok"] is False and any("autoexec.cfg" in x for x in guarda["vermelho"])
    assert any("ALERTA VERMELHO" in s for s in saidas)
    # CONTROLE: fotos iguais → guarda ok
    rel2, jogo2 = Relogio(), JogoFalso(Relogio())
    dep2 = _dependencias(jogo2, rel2, escuta_so_em_local=lambda porta: (False, ["0.0.0.0"]), foto=antes)
    with pytest.raises(gd.ErroGravacao):
        gd.gravar(demo, _partida(), _cfg(), tmp / "saida2", dep=dep2, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert json.loads((tmp / "saida2" / "registro.json").read_text(encoding="utf-8"))["guarda"]["ok"] is True


# ── gravação de ponta a ponta com o jogo falso (ffmpeg de verdade) ────────────────────────────────

@pytest.mark.lento
def test_grava_dois_rounds_junta_audio_em_duas_faixas_e_registra_a_velocidade(ambiente):
    import json
    tmp, demo = ambiente
    rel = Relogio()
    jogo = JogoFalso(rel, velocidade=5.0)
    dep = _dependencias(jogo, rel)
    reg = gd.gravar(demo, _partida(), _cfg(), tmp / "saida", dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    rounds = sorted((tmp / "saida" / "rounds").glob("round_*.mp4"))
    assert [r.name for r in rounds] == ["round_01.mp4", "round_02.mp4"]
    for arq in rounds:
        assert _audios(arq) == 2                                         # mistura + jogo, o mesmo formato dos cortes
        dur = gd._duracao_ffprobe(arq)
        assert 4.0 < dur < 16.0                                           # a janela vai até 5 s depois do último abate + 3 s de margem
    res = reg["resultados"]
    assert [x["round"] for x in res] == [1, 2] and all(x["camera_ok"] is True and not x["problemas"] for x in res)
    assert all(x["velocidade_x"] and x["velocidade_x"] > 1.0 for x in res)         # mais rápido que o tempo real (o jogo falso renderiza 5x)
    # as linhas mandadas ao jogo: preparo uma vez, depois por round: limpar, pular pausado, câmera, agendar, retomar; e por fim `quit`
    assert jogo.linhas[:4] == ["spec_show_xray", "demo_info", "spec_show_xray 0", "mirv_panorama panelStyle panelId=HudDemoController opacity=0"]
    assert any("record name" in l for l in jogo.linhas) and all(x.get("congelado_inicio_s") == 0.0 for x in reg["resultados"])
    assert "mirv_streams print" in jogo.linhas and jogo.linhas.count("mirv_cmd print") == 2 and reg["demo_carregou"] is True
    assert len(reg["console_rounds"]) == 2 and reg["console_rounds"][0]["round"] == 1 and "apos_pulo" in reg["console_rounds"][0]
    assert jogo.linhas.count("demo_resume") == 2 and jogo.linhas[-1] == "quit"
    assert [l for l in jogo.linhas if l.startswith("demo_goto")] == [f"demo_goto {int(20 * T) - 128} 0 1"] * 2 + [f"demo_goto {int(40 * T) - 128} 0 1"] * 2   # 2 pulos por round (câmera; logo antes de gravar)
    assert reg["guarda"]["ok"] is True and reg["netcon_enderecos"] == ["127.0.0.1"]
    salvo = json.loads((tmp / "saida" / "registro.json").read_text(encoding="utf-8"))
    assert "aviso_autoexec" in salvo and salvo["resultados"][0]["camera_candidato"] == 1


@pytest.mark.lento
def test_camera_errada_tenta_a_proxima_e_registra_qual_funcionou(ambiente):
    tmp, demo = ambiente
    rel = Relogio()
    jogo = JogoFalso(rel)
    dep = _dependencias(jogo, rel, verificacoes=[(False, "0 caixa(s) no kill feed para 3 abate(s)"), (True, "3 caixa(s)")])
    reg = gd.gravar(demo, _partida({1: 3}), _cfg(), tmp / "saida", trechos=gd.rounds_para_gravar(_partida({1: 3}), True, 3, 2.0, 3.0),
                    dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert [t["camera_candidato"] for t in reg["tentativas"]] == [1, 2] and reg["resultados"][0]["camera_candidato"] == 2
    assert "spec_player 10" in jogo.linhas and "spec_player 9" in jogo.linhas             # as duas primeiras candidatas foram testadas, nessa ordem
    assert jogo.linhas.index("spec_player 10") < jogo.linhas.index("spec_player 9")


@pytest.mark.lento
def test_camera_reprovada_e_depois_incerta_segue_com_aviso_e_registra_a_que_ficou_no_disco(ambiente):
    """R-022: câmera 1 = False e câmera 2 = None ("só parte bate") parava a gravação inteira e o registro descrevia a
    câmera 1, embora o round_01.mp4 no disco fosse da câmera 2. Agora fica a 2 com o alerta "confira".
    A-009 (Gemini): com 2 rounds, o round 2 já começa na câmera que ficou (a 2) e os dois entram nos resultados."""
    tmp, demo = ambiente
    rel = Relogio()
    jogo = JogoFalso(rel)
    dep = _dependencias(jogo, rel, verificacoes=[(False, "0 caixa(s) no kill feed para 3 abate(s)"),
                                                (None, "2 de 3 abates com caixa"), (True, "3 caixa(s)")])
    partida = _partida({1: 3, 2: 3})
    reg = gd.gravar(demo, partida, _cfg(), tmp / "saida", trechos=gd.rounds_para_gravar(partida, True, 3, 2.0, 3.0),
                    dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert [(t["round"], t["camera_candidato"], t["camera_ok"]) for t in reg["tentativas"]] == [(1, 1, False), (1, 2, None), (2, 2, True)]
    r1, r2 = reg["resultados"]
    assert (r1["round"], r1["camera_candidato"], r1["camera_ok"], r1["camera_detalhe"]) == (1, 2, None, "2 de 3 abates com caixa")
    assert (r2["round"], r2["camera_candidato"], r2["camera_ok"]) == (2, 2, True)
    # o arquivo do round 1 no disco é o da 2ª tentativa (take0001), e o registro aponta para esse mesmo take; o round 2 usou a mesma câmera
    assert r1["take"] == "take0001" and r2["take"] == "take0002" and Path(r1["arquivo"]).is_file() and Path(r2["arquivo"]).is_file()
    assert jogo.camera_por_take["take0001"] == jogo.camera_por_take["take0002"] != jogo.camera_por_take["take0000"]
    assert any("não deu para confirmar a câmera" in a for a in reg["alertas"])
    assert not reg.get("erro")


@pytest.mark.lento
def test_nenhuma_camera_funciona_para_e_guarda_os_videos_para_o_dono_ver(ambiente):
    tmp, demo = ambiente
    rel = Relogio()
    dep = _dependencias(JogoFalso(rel), rel, verificacoes=[(False, "0 caixa")] * 10)
    with pytest.raises(gd.ErroGravacao, match="Nenhuma das câmeras") as e:
        gd.gravar(demo, _partida({1: 3}), _cfg(), tmp / "saida", dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert e.value.etapa == "camera" and (tmp / "saida" / "rounds" / "round_01.mp4").is_file()


@pytest.mark.lento
def test_fim_agendado_que_nao_dispara_e_forcado_pelo_relogio_de_seguranca(ambiente):
    tmp, demo = ambiente
    rel = Relogio()
    jogo = JogoFalso(rel, fim_agendado_dispara=False)
    dep = _dependencias(jogo, rel)
    reg = gd.gravar(demo, _partida({1: 3}), _cfg(), tmp / "saida", dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert any("fim foi forçado" in a for a in reg["alertas"]) and "mirv_streams record end" in jogo.linhas
    assert (tmp / "saida" / "rounds" / "round_01.mp4").is_file()


@pytest.mark.lento
def test_sem_wav_o_arquivo_sai_com_duas_faixas_mudas_e_um_aviso(ambiente):
    tmp, demo = ambiente
    rel = Relogio()
    dep = _dependencias(JogoFalso(rel, wav=False), rel)
    reg = gd.gravar(demo, _partida({1: 3}), _cfg(), tmp / "saida", dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert any("sem áudio" in p for p in reg["resultados"][0]["problemas"])
    assert _audios(tmp / "saida" / "rounds" / "round_01.mp4") == 2


# ── CLI: esperar a troca de conta, checklist e o teste sem HLAE ───────────────────────────────────

def test_esperar_conta_espera_o_dono_trocar_e_desiste_no_prazo(ambiente):
    rel = Relogio()
    estados = iter([0, 0, ID_PRINCIPAL, ID_PRINCIPAL, ID_SEGUNDA])
    ultimo = {"v": 0}

    def logada():
        ultimo["v"] = next(estados, ID_SEGUNDA)
        return ultimo["v"]
    dep = _dependencias(JogoFalso(rel), rel, conta_logada=logada)
    msgs = []
    assert gd.esperar_conta(_cfg(), dep, imprimir=msgs.append, intervalo_s=3.0) == ID_SEGUNDA
    assert any("PRINCIPAL" in m or "fechado" in m for m in msgs) and rel.t >= 12.0                      # esperou as trocas
    # CONTROLE: se nunca trocar, desiste no prazo em vez de ficar para sempre
    rel2 = Relogio()
    dep2 = _dependencias(JogoFalso(rel2), rel2, conta_logada=lambda: ID_PRINCIPAL)
    with pytest.raises(gd.ErroGravacao, match="não aconteceu"):
        gd.esperar_conta(_cfg(), dep2, imprimir=lambda *a: None, tempo_maximo_s=20.0, intervalo_s=3.0)


def test_checklist_mostra_o_que_falta_sem_abrir_nada(ambiente, monkeypatch):
    tmp, demo = ambiente
    rel = Relogio()
    monkeypatch.setattr(terceiros, "rodando_como_admin", lambda: True)
    dep = _dependencias(JogoFalso(rel), rel, conta_logada=lambda: 0, steam_rodando=lambda: False, jogo_aberto=lambda: ["obs64.exe"])
    itens = {i: (ok, det) for i, ok, det in gd.checklist(demo, _cfg(), dep)}
    assert itens["sessão normal (não administrador)"][0] is False and itens["CS2 e OBS fechados"] == (False, "obs64.exe")
    assert itens["Steam aberto"][0] is False and itens["Steam na 2ª conta (conta do Bisturi)"][0] is False and itens["demo"][0] is True
    assert itens["Firewall bloqueia a porta 2121 para fora"] == (True, "ok")
    sem_fw = {i: (ok, det) for i, ok, det in gd.checklist(demo, _cfg(), _dependencias(JogoFalso(rel), rel, firewall=lambda p: (False, "sem regra")))}
    assert sem_fw["Firewall bloqueia a porta 2121 para fora"][0] is False and "New-NetFirewallRule" in sem_fw["Firewall bloqueia a porta 2121 para fora"][1]
    monkeypatch.setattr(terceiros, "rodando_como_admin", lambda: False)
    ok = {i: o for i, o, _ in gd.checklist(demo, _cfg(), _dependencias(JogoFalso(rel), rel))}
    assert all(ok.values())                                                                              # CONTROLE: tudo certo, nada falta


def test_teste_sem_hlae_abre_so_o_cs2_confere_porta_e_fecha(ambiente, monkeypatch):
    tmp, demo = ambiente
    monkeypatch.setattr(gd, "PASTA_PADRAO", tmp / "gravacao_demo")
    rel, jogo = Relogio(), JogoFalso(Relogio())
    dep = _dependencias(jogo, rel)
    res = gd.teste_sem_hlae(demo, _cfg(), dep, _roteiro(), imprimir=lambda *a: None)
    [cmd] = dep.lancados
    assert cmd[0].endswith("cs2.exe") and "-insecure" in cmd and "HLAE" not in " ".join(cmd) and "+connect" not in cmd
    assert res["abriu_console"] and res["porta_so_local"] is True and res["guarda_ok"] is True
    assert jogo.linhas == ["spec_show_xray", "demo_info", "demo_pause", "quit"] and (tmp / "gravacao_demo" / "teste_sem_hlae.json").is_file()
    assert res["console_responde"] is True and res["fechamento"] == {"quits_enviados": 1, "fechou_sozinho": True, "segundos": 1.0}
    assert [f["quando"] for f in res["tcp"]] == ["logo após conectar", "2 s depois", "depois da espera", "depois de fechar"] and res["firewall"] == "ok"
    # CONTROLE: console aberto para a rede sem proteção → para antes de qualquer comando
    rel2, jogo2 = Relogio(), JogoFalso(Relogio())
    dep2 = _dependencias(jogo2, rel2, escuta_so_em_local=lambda porta: (False, ["0.0.0.0"]))
    with pytest.raises(gd.ErroGravacao, match="protegido"):
        gd.teste_sem_hlae(demo, _cfg(), dep2, _roteiro(), imprimir=lambda *a: None)
    assert jogo2.linhas == ["quit"]
    # porta não encontrada na tabela (leitura instável deste Windows): NÃO para, registra o aviso e segue (a regra do Firewall já foi exigida)
    rel3, jogo3 = Relogio(), JogoFalso(Relogio())
    dep3 = _dependencias(jogo3, rel3, escuta_so_em_local=lambda porta: (False, []))
    res3 = gd.teste_sem_hlae(demo, _cfg(), dep3, _roteiro(), imprimir=lambda *a: None)
    assert res3["porta_so_local"] is False and any("tabela TCP" in a for a in res3["alertas"]) and jogo3.linhas[-1] == "quit" and "demo_info" in jogo3.linhas
    # CONTROLE: sem a regra do Firewall nada é aberto
    rel4, jogo4 = Relogio(), JogoFalso(Relogio())
    dep4 = _dependencias(jogo4, rel4, firewall=lambda porta: (False, "não achei regra"))
    with pytest.raises(gd.ErroGravacao, match="New-NetFirewallRule") as e4:
        gd.teste_sem_hlae(demo, _cfg(), dep4, _roteiro(), imprimir=lambda *a: None)
    assert dep4.lancados == [] and e4.value.etapa == "prechecagem"


def test_fechar_jogo_insiste_no_quit_reconectando_e_so_entao_encerra_pelo_pid(tmp_path):
    class Teimoso(JogoFalso):
        """Ignora o primeiro `quit` (o CS2 ainda carregando faz isso, visto em 08/11); fecha no segundo."""

        def __init__(self, relogio):
            super().__init__(relogio)
            self.quits = 0
            self.conexoes = 0

        def conectar(self, tempo_maximo=60.0, intervalo=0.5):
            self.conexoes += 1

        def enviar(self, linha):
            if linha == "quit":
                self.quits += 1
                self.linhas.append(linha)
                if self.quits >= 2:
                    self.fechado = True
                return
            super().enviar(linha)
    rel = Relogio()
    jogo = Teimoso(rel)
    dep = _dependencias(jogo, rel)
    reg = gd.Registro(tmp_path / "r.json")
    gd._fechar_jogo(None, dep, gd.Roteiro(espera_fechar_s=30.0), reg, lambda *a: None, porta=2121)
    assert jogo.quits == 2 and jogo.fechado and jogo.conexoes == 2                             # conexão NOVA na segunda tentativa
    assert reg.dados["fechamento"] == {"quits_enviados": 2, "fechou_sozinho": True, "segundos": 9.0} and "alertas" not in reg.dados
    # CONTROLE: um jogo que nunca fecha é encerrado pelo PID, com alerta, depois de espera_fechar_s
    rel2 = Relogio()
    nunca = Teimoso(rel2)
    nunca.enviar = lambda linha: nunca.linhas.append(linha)
    encerrados = []
    dep2 = _dependencias(nunca, rel2, encerrar_jogo=lambda: encerrados.append(1))
    reg2 = gd.Registro(tmp_path / "r2.json")
    gd._fechar_jogo(nunca, dep2, gd.Roteiro(espera_fechar_s=20.0), reg2, lambda *a: None, porta=2121)
    assert encerrados == [1] and reg2.dados["fechamento"]["fechou_sozinho"] is False and reg2.dados["fechamento"]["quits_enviados"] == 3
    assert reg2.dados["alertas"] == ["o CS2 não fechou sozinho; encerrado pelo PID"]


def test_gravar_segue_com_aviso_quando_a_tabela_tcp_nao_mostra_a_porta(ambiente):
    tmp, demo = ambiente
    rel, jogo = Relogio(), JogoFalso(Relogio())
    dep = _dependencias(jogo, rel, escuta_so_em_local=lambda porta: (False, []))
    jogo.enviar = lambda linha: (netcon.validar_linha(linha), jogo.linhas.append(linha))[1]
    with pytest.raises(gd.ErroGravacao) as e:                              # sem vídeo gravado o roteiro termina em erro, mas DEPOIS do preparo
        gd.gravar(demo, _partida({1: 3}), _cfg(), tmp / "saida", dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert e.value.etapa != "netcon" and any(linha.startswith("mirv_streams") for linha in jogo.linhas)
    reg = json.loads((tmp / "saida" / "registro.json").read_text(encoding="utf-8"))
    assert any("tabela TCP" in a for a in reg["alertas"]) and reg["netcon_ok"] is False and reg["console_responde"] is True
    assert jogo.linhas[0] == "spec_show_xray"                                                    # a sonda vai antes de qualquer preparo


@pytest.mark.lento
def test_quando_o_primeiro_record_start_nao_cria_take_o_gravador_manda_de_novo_e_registra(ambiente):
    class SemAgenda(JogoFalso):
        """Um HLAE que engole o primeiro `mirv_streams record start`; só grava no segundo (o reenvio do gravador)."""

        def __init__(self, relogio):
            super().__init__(relogio)
            self.starts = 0

        def enviar(self, linha):
            if linha == "mirv_streams record start":
                self.starts += 1
                self.linhas.append(linha)
                if self.starts >= 2 and self.fim is not None:
                    self.ini = self.tick_pausado
                    self._gravar()
                return
            super().enviar(linha)
    tmp, demo = ambiente
    rel = Relogio()
    jogo = SemAgenda(Relogio())
    reg = gd.gravar(demo, _partida({1: 3}), _cfg(), tmp / "saida", dep=_dependencias(jogo, rel), roteiro=_roteiro(), imprimir=lambda *a: None)
    assert [x["round"] for x in reg["resultados"]] == [1] and (tmp / "saida" / "rounds" / "round_01.mp4").is_file()
    assert any("`record start` de novo" in a for a in reg["alertas"]) and jogo.linhas.count("mirv_streams record start") == 2
    assert "apos_retomar_sem_take" in reg["console_rounds"][0]
    # CONTROLE: um HLAE que nem com início direto grava termina em erro claro, apontando o registro
    class Nunca(SemAgenda):
        def enviar(self, linha):
            if linha == "mirv_streams record start":
                self.linhas.append(linha)
                return
            super().enviar(linha)
    rel2 = Relogio()
    with pytest.raises(gd.ErroGravacao, match="duas vezes"):
        gd.gravar(demo, _partida({1: 3}), _cfg(), tmp / "saida2", dep=_dependencias(Nunca(Relogio()), rel2), roteiro=_roteiro(), imprimir=lambda *a: None)


@pytest.mark.lento
def test_congelado_no_inicio_mede_os_quadros_parados_do_comeco(tmp_path):
    parado = tmp_path / "parado.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=3", "-vf", "tpad=start_duration=1:start_mode=clone",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(parado)], check=True, capture_output=True)
    assert 0.8 <= gd._congelado_no_inicio(parado) <= 1.3
    vivo = tmp_path / "vivo.mp4"                                                                 # CONTROLE: sem congelamento = 0
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=2", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(vivo)], check=True, capture_output=True)
    assert gd._congelado_no_inicio(vivo) == 0.0


@pytest.mark.lento
def test_regravar_so_um_round_mantem_os_outros_no_registro(ambiente):
    tmp, demo = ambiente
    rel, jogo = Relogio(), JogoFalso(Relogio())
    todos = gd.rounds_para_gravar(_partida(), False, 0)
    gd.gravar(demo, _partida(), _cfg(), tmp / "saida", trechos=todos, dep=_dependencias(jogo, rel), roteiro=_roteiro(), imprimir=lambda *a: None)
    reg1 = json.loads((tmp / "saida" / "registro.json").read_text(encoding="utf-8"))
    assert [x["round"] for x in reg1["resultados"]] == [1, 2]
    rel2, jogo2 = Relogio(), JogoFalso(Relogio())
    jogo2.n_take = len(list((tmp / "saida" / "hlae").glob("take*")))                             # como o HLAE real: continua a numeração
    so_2 = [t for t in todos if t.round == 2]
    reg2 = gd.gravar(demo, _partida(), _cfg(), tmp / "saida", trechos=so_2, dep=_dependencias(jogo2, rel2), roteiro=_roteiro(), imprimir=lambda *a: None)
    assert sorted(x["round"] for x in reg2["resultados"]) == [1, 2] and sorted(t["round"] for t in reg2["trechos"]) == [1, 2]
    assert len([x for x in reg2["resultados"] if x["round"] == 2]) == 1                        # o round regravado substitui o antigo, sem duplicar
    assert (tmp / "saida" / "rounds" / "round_01.mp4").is_file() and (tmp / "saida" / "rounds" / "round_02.mp4").is_file()
    (tmp / "saida" / "rounds" / "round_01.mp4").unlink()                                       # CONTROLE: round antigo sem vídeo não é mantido
    rel3, jogo3 = Relogio(), JogoFalso(Relogio())
    jogo3.n_take = len(list((tmp / "saida" / "hlae").glob("take*")))
    reg3 = gd.gravar(demo, _partida(), _cfg(), tmp / "saida", trechos=so_2, dep=_dependencias(jogo3, rel3), roteiro=_roteiro(), imprimir=lambda *a: None)
    assert [x["round"] for x in reg3["resultados"]] == [2]



def test_avisos_de_conta_do_gravador_citam_o_apelido_so_quando_ha(ambiente):
    """C2 (14/11): o apelido entra na frase no lugar do nome fixo; sem apelido a frase é a genérica; a trava (quem passa) é a mesma."""
    tmp, demo = ambiente
    for com_apelido in (False, True):
        cfg = _cfg()
        if com_apelido:
            cfg.gravacao_demo.conta_apelido, cfg.gravacao_demo.conta_principal_apelido = "Smurf do Bisturi", "Conta de jogo"
        rel = Relogio()
        dep = _dependencias(JogoFalso(rel), rel, conta_logada=lambda: ID_PRINCIPAL, steam_rodando=lambda: False)
        msgs: list[str] = []
        with pytest.raises(gd.ErroGravacao, match="não aconteceu"):
            gd.esperar_conta(cfg, dep, imprimir=msgs.append, tempo_maximo_s=20.0, intervalo_s=3.0)
        assert "entre na conta do Bisturi" in msgs[0] and ("(Smurf do Bisturi)" in msgs[0]) is com_apelido        # Steam fechado: pede a conta do Bisturi
        msgs2: list[str] = []                                                                                           # Steam aberto, mas na conta principal
        with pytest.raises(gd.ErroGravacao, match="não aconteceu"):
            gd.esperar_conta(cfg, _dependencias(JogoFalso(rel), rel, conta_logada=lambda: ID_PRINCIPAL), imprimir=msgs2.append, tempo_maximo_s=20.0, intervalo_s=3.0)
        assert "PRINCIPAL" in msgs2[0] and ("(Conta de jogo)" in msgs2[0]) is com_apelido and ("(Smurf do Bisturi)" in msgs2[0]) is com_apelido
        itens = {i: (ok, det) for i, ok, det in gd.checklist(demo, cfg, _dependencias(JogoFalso(rel), rel, conta_logada=lambda: ID_PRINCIPAL))}
        ok, detalhe = itens["Steam na 2ª conta (conta do Bisturi)"]
        assert ok is False and "PRINCIPAL" in detalhe and ("(Conta de jogo)" in detalhe) is com_apelido and ("(Smurf do Bisturi)" in detalhe) is com_apelido
    rel = Relogio()
    certo = gd.esperar_conta(_cfg(), _dependencias(JogoFalso(rel), rel), imprimir=lambda *a: None)                    # CONTROLE: a conta certa passa
    assert certo == ID_SEGUNDA


def test_espera_o_jogo_sair_da_tela_de_carregamento_antes_do_primeiro_salto():
    # prova real de 17/11 (demo de campeonato): o salto saiu com a demo ainda na tela de carregamento e o CS2 caiu
    # ("CopyNewEntity: invalid class index (221), out of range 0"); nas gravações boas de 08/11 a demo já estava andando
    from types import SimpleNamespace as NS
    from cs2clip.gravador_demo import MARCA_EM_JOGO, esperar_em_jogo
    from cs2clip.netcon import SemConexao

    class Net:
        def __init__(self, respostas, viu=False):
            self.respostas, self.pedidos, self.viu_em_jogo = list(respostas), [], viu

        def esperar(self, texto, tempo_maximo=10.0):
            self.pedidos.append(texto)
            r = self.respostas.pop(0) if self.respostas else False
            if isinstance(r, Exception):
                raise r
            return r

    def dep(vivo=True):
        rel = Relogio()
        return NS(agora=rel.agora, dormir=rel.dormir, processo_do_jogo_vivo=lambda: vivo, rel=rel)

    d = dep()
    net = Net([False, False, True])
    assert esperar_em_jogo(net, d, 60.0) == (True, 4.0) and net.pedidos == [MARCA_EM_JOGO] * 3  # type: ignore[arg-type]
    d = dep()
    assert esperar_em_jogo(Net([]), d, 10.0)[0] is False and d.rel.t >= 10.0  # type: ignore[arg-type]  # nunca avisa: desiste no limite
    assert esperar_em_jogo(Net([SemConexao("caiu")]), dep(), 10.0)[0] is False  # type: ignore[arg-type]
    # revisão de 17/11: a marca já tinha passado (consumida por outra espera): entra na hora, sem perguntar de novo
    net = Net([], viu=True)
    assert esperar_em_jogo(net, dep(), 60.0) == (True, 0.0) and net.pedidos == []  # type: ignore[arg-type]
    # o jogo caiu no carregamento: sai logo, não espera os 60 s
    d = dep(vivo=False)
    assert esperar_em_jogo(Net([]), d, 60.0)[0] is False and d.rel.t < 10.0  # type: ignore[arg-type]


def test_cliente_do_console_lembra_que_o_jogo_entrou_no_mapa_mesmo_se_o_texto_for_consumido():
    from cs2clip.netcon import ClienteNetcon

    class Sock:
        def __init__(self, partes):
            self.partes = list(partes)

        def recv(self, n):
            return self.partes.pop(0) if self.partes else b""

    c = ClienteNetcon(2121)
    c._sock = Sock([b"ChangeGameUIState: CSGO_GAME_UI_STATE_LOADINGSCREEN -> CSGO_GAME_UI_STATE_INGAME\n", b"playback_ticks 1000\n"])  # type: ignore[assignment]
    assert c.viu_em_jogo is False
    assert c.esperar("playback_ticks", 1.0) is True          # esta espera consome a marca junto
    assert c.viu_em_jogo is True                              # mas a lembrança fica
    c2 = ClienteNetcon(2121)
    c2._sock = Sock([b"playback_ticks 1000\n"])  # type: ignore[assignment]
    assert c2.esperar("playback_ticks", 1.0) and c2.viu_em_jogo is False     # CONTROLE: sem a marca, não inventa


@pytest.mark.lento
def test_round_gravado_mudo_vira_problema_no_resultado(ambiente):
    tmp, demo = ambiente
    rel = Relogio()
    jogo = JogoFalso(rel, velocidade=5.0)
    dep = _dependencias(jogo, rel, som_mudo=lambda p: True)
    reg = gd.gravar(demo, _partida(), _cfg(), tmp / "saida", dep=dep, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert all(any("MUDO" in p for p in x["problemas"]) for x in reg["resultados"])
    dep2 = _dependencias(JogoFalso(Relogio(), velocidade=5.0), Relogio())          # CONTROLE: com som, nenhum problema
    reg2 = gd.gravar(demo, _partida(), _cfg(), tmp / "saida2", dep=dep2, roteiro=_roteiro(), imprimir=lambda *a: None)
    assert not any(x["problemas"] for x in reg2["resultados"])


def test_som_mudo_do_round_gravado(monkeypatch, tmp_path):
    # prova real de 17/11: com o Windows sem saída de som ligada, os rounds saíram mudos (silêncio digital: −91 dB no volumedetect)
    from types import SimpleNamespace as NS
    from cs2clip import gravador_demo as gd
    saida = {"t": ""}
    monkeypatch.setattr(gd.subprocess, "run", lambda *a, **k: NS(stderr=saida["t"], returncode=0))
    for texto, mudo in (("max_volume: -inf dB", True), ("max_volume: -91.0 dB", True), ("max_volume: -3.2 dB", False), ("lixo", False)):
        saida["t"] = texto
        assert gd._som_mudo(tmp_path / "x.mp4") is mudo, texto


def test_qualidade_mostra_som_mudo_em_vez_de_nao_consegui_medir(monkeypatch, tmp_path):
    from types import SimpleNamespace as NS
    from cs2clip import qc
    from cs2clip.qualidade import gate_audio
    resumo = "Summary:\n  Integrated loudness:\n    I:         -70.0 LUFS\n  True peak:\n    Peak:       -inf dBFS\n"
    monkeypatch.setattr(qc.subprocess, "run", lambda *a, **k: NS(stderr=resumo, returncode=0))
    medida = qc.medir_loudness(tmp_path / "m.mp4")
    assert medida == (-70.0, -150.0)
    g = gate_audio(medida, True, -14.0, rigido=False)
    assert g.status == "WARNING" and "-70.0 LUFS" in g.motivo                          # antes: NOT_RUN "não consegui medir o volume"


def test_som_continua_proibido_no_console():
    # 17/11: uma exceção para snd_mute_losefocus foi aberta e TIRADA na revisão (a causa provada do som mudo foi o Windows sem saída de som)
    for ruim in ("snd_mute_losefocus 0", "snd_mute_losefocus 1", "snd_volume 0", 'mirv_cmd addAtTick 5 "snd_mute_losefocus 0"'):
        with pytest.raises(netcon.ComandoBloqueado):
            netcon.validar_linha(ruim)
