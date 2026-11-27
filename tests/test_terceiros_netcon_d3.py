"""D3 (Plano D): instalador seguro dos terceiros e console remoto com lista branca. Sem rede e sem rodar nenhum programa de terceiros.

Cada teste tem CONTROLE (o caso bom passa; o ruim é recusado).
"""
import hashlib
import io
import os
import subprocess
import sys
import socket
import threading
import time
import zipfile
from pathlib import Path

import pytest

from cs2clip import netcon, terceiros
from cs2clip.netcon import ClienteNetcon, ComandoBloqueado, SemConexao, validar_linha, validar_valor
from cs2clip.terceiros import BOILER, HLAE, ErroTerceiros, Terceiro


def _zip_bytes(arquivos: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nome, dados in arquivos.items():
            z.writestr(nome, dados)
    return buf.getvalue()


class _Resposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _terceiro_falso(conteudo: bytes, pasta="falso") -> Terceiro:
    return Terceiro("Falso", "1.0", "https://github.com/x/y/releases/download/v1/falso.zip", hashlib.sha256(conteudo).hexdigest(),
                    len(conteudo), pasta, 10 * 1024 * 1024)


# ── constantes fixadas ────────────────────────────────────────────────────────────────────────────

def test_urls_e_hashes_fixados_sao_os_do_parecer_do_auditor():
    assert HLAE.url == "https://github.com/advancedfx/advancedfx/releases/download/v2.193.0/hlae_2_193_0.zip"
    assert HLAE.sha256 == "ac1f6740551cba7a72ba8437b84097d6f9a95d922ae05f333417bac6d1b746c2" and HLAE.tamanho == 9_136_528
    assert BOILER.url == "https://github.com/akiver/boiler-writter/releases/download/v1.7.0/boiler-writter-win-1.7.0.zip"
    assert BOILER.sha256 == "eda6ce361215c4c0427332e55811ffd621755b264ae7df0f5385ef5916331141" and BOILER.tamanho == 827_218
    assert "Setup" not in HLAE.url and HLAE.url.startswith("https://github.com/advancedfx/")


# ── download com hash fixo ────────────────────────────────────────────────────────────────────────

def test_download_confere_tamanho_e_hash_e_o_controle_com_hash_errado_e_recusado(tmp_path):
    dados = _zip_bytes({"a.txt": b"ola"})
    t = _terceiro_falso(dados)
    destino = terceiros.baixar_zip(t, tmp_path / "x.zip", abrir=lambda req, timeout=0: _Resposta(dados))
    assert destino.read_bytes() == dados
    adulterado = dados[:-1] + bytes([dados[-1] ^ 1])                      # mesmo tamanho, 1 bit diferente
    with pytest.raises(ErroTerceiros, match="SHA-256"):
        terceiros.baixar_zip(t, tmp_path / "y.zip", abrir=lambda req, timeout=0: _Resposta(adulterado))
    assert not (tmp_path / "y.zip").exists() and not list(tmp_path.glob("*.parcial"))
    with pytest.raises(ErroTerceiros, match="tamanho"):
        terceiros.baixar_zip(t, tmp_path / "z.zip", abrir=lambda req, timeout=0: _Resposta(dados + b"x"))
    gigante = _terceiro_falso(b"x")
    with pytest.raises(ErroTerceiros, match="grande demais"):
        terceiros.baixar_zip(gigante, tmp_path / "w.zip", abrir=lambda req, timeout=0: _Resposta(b"x" * (3 * 1024 * 1024)))
    assert not list(tmp_path.glob("*.parcial"))


def test_download_sem_rede_vira_erro_em_portugues(tmp_path):
    def sem_rede(req, timeout=0):
        raise OSError("sem rede")
    with pytest.raises(ErroTerceiros, match="não consegui baixar"):
        terceiros.baixar_zip(_terceiro_falso(b"x"), tmp_path / "x.zip", abrir=sem_rede)


# ── extração segura ───────────────────────────────────────────────────────────────────────────────

def test_extracao_fica_dentro_da_pasta_e_recusa_caminho_de_fuga_link_e_bomba(tmp_path):
    ok = tmp_path / "ok.zip"
    ok.write_bytes(_zip_bytes({"HLAE.exe": b"MZ", "x64/AfxHookSource2.dll": b"dll"}))
    escritos = terceiros.extrair_seguro(ok, tmp_path / "dest", 1024 * 1024)
    assert sorted(escritos) == ["HLAE.exe", "x64/AfxHookSource2.dll"] and (tmp_path / "dest" / "x64" / "AfxHookSource2.dll").is_file()
    fuga = tmp_path / "fuga.zip"
    fuga.write_bytes(_zip_bytes({"../fora.txt": b"mau"}))
    with pytest.raises(ErroTerceiros, match="fora da pasta"):
        terceiros.extrair_seguro(fuga, tmp_path / "dest2", 1024 * 1024)
    assert not (tmp_path / "fora.txt").exists() and not list((tmp_path / "dest2").glob("*"))        # nada foi escrito, nem dentro
    absoluto = tmp_path / "abs.zip"
    absoluto.write_bytes(_zip_bytes({"C:/Windows/mau.txt": b"x"}))
    with pytest.raises(ErroTerceiros):
        terceiros.extrair_seguro(absoluto, tmp_path / "dest3", 1024 * 1024)
    bomba = tmp_path / "bomba.zip"
    bomba.write_bytes(_zip_bytes({"zeros.bin": b"\0" * (5 * 1024 * 1024)}))
    with pytest.raises(ErroTerceiros, match="bomba"):
        terceiros.extrair_seguro(bomba, tmp_path / "dest4", 1024 * 1024)
    assert terceiros.extrair_seguro(bomba, tmp_path / "dest5", 10 * 1024 * 1024) == ["zeros.bin"]       # CONTROLE: com o limite certo extrai


def test_instalar_hlae_extrai_copia_o_ffmpeg_e_registra_sem_executar_nada(tmp_path, monkeypatch):
    monkeypatch.setattr(terceiros, "REGISTRO", tmp_path / "TERCEIROS.json")
    monkeypatch.setattr(terceiros, "rodando_como_admin", lambda: False)
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"FFMPEG")
    pacote = _zip_bytes({"HLAE.exe": b"MZ", "x64/AfxHookSource2.dll": b"dll"})

    def baixar_falso(t, destino):
        Path(destino).write_bytes(pacote)
        return Path(destino)
    pasta = terceiros.instalar_hlae(tmp_path / "bin", ffmpeg, baixar=baixar_falso)
    assert (pasta / "HLAE.exe").is_file() and (pasta / "ffmpeg" / "bin" / "ffmpeg.exe").read_bytes() == b"FFMPEG"
    assert terceiros.instalado("hlae", tmp_path / "bin") and not list((tmp_path / "bin").glob("_*.zip"))
    import json
    reg = json.loads((tmp_path / "TERCEIROS.json").read_text(encoding="utf-8"))
    assert reg["hlae"]["versao"] == "2.193.0" and reg["hlae"]["sha256"] == HLAE.sha256
    # instalar (só baixar e extrair) funciona mesmo numa sessão elevada; o que exige sessão normal é RODAR o HLAE
    monkeypatch.setattr(terceiros, "rodando_como_admin", lambda: True)
    assert terceiros.instalar_hlae(tmp_path / "bin2", ffmpeg, baixar=baixar_falso).is_dir()
    with pytest.raises(ErroTerceiros, match="administrador"):
        terceiros.exigir_sem_admin()
    monkeypatch.setattr(terceiros, "rodando_como_admin", lambda: False)
    terceiros.exigir_sem_admin()                                                  # CONTROLE: sessão normal passa


def test_instalar_boiler_exige_assinatura_da_valve_e_remove_se_nao_for(tmp_path, monkeypatch):
    monkeypatch.setattr(terceiros, "REGISTRO", tmp_path / "TERCEIROS.json")
    pacote = _zip_bytes({"boiler-writter.exe": b"MZ", "steam_api64.dll": b"dll"})

    def baixar_falso(t, destino):
        Path(destino).write_bytes(pacote)
        return Path(destino)
    pasta = terceiros.instalar_boiler(tmp_path / "bin", baixar=baixar_falso, conferir_assinatura=lambda dll: (True, "Valid; CN=Valve Corp"))
    assert (pasta / "boiler-writter.exe").is_file() and terceiros.instalado("boiler", tmp_path / "bin")
    assert terceiros.caminho_boiler(tmp_path / "bin") == pasta / "boiler-writter.exe" and terceiros.caminho_hlae(tmp_path / "bin") is None
    # CONTROLE: assinatura que não é da Valve → nada fica
    with pytest.raises(ErroTerceiros, match="assinatura válida da Valve"):
        terceiros.instalar_boiler(tmp_path / "bin2", baixar=baixar_falso, conferir_assinatura=lambda dll: (False, "NotSigned; "))
    assert not (tmp_path / "bin2" / "boiler").exists()
    sem_exe = _zip_bytes({"outra.txt": b"x"})
    with pytest.raises(ErroTerceiros, match="boiler-writter.exe"):
        terceiros.instalar_boiler(tmp_path / "bin3", baixar=lambda t, d: (Path(d).write_bytes(sem_exe), Path(d))[1],
                                  conferir_assinatura=lambda dll: (True, "ok"))


def test_assinatura_valve_le_o_resultado_do_powershell(monkeypatch, tmp_path):
    monkeypatch.setattr(terceiros.sys, "platform", "win32")

    class R:
        def __init__(self, saida):
            self.stdout = saida
    ok, texto = terceiros.assinatura_valve(tmp_path / "steam_api64.dll", rodar=lambda *a, **k: R("Valid|CN=Valve Corp., O=Valve Corp."))
    assert ok and "Valid" in texto
    assert not terceiros.assinatura_valve(tmp_path / "x.dll", rodar=lambda *a, **k: R("Valid|CN=Outra Empresa"))[0]
    assert not terceiros.assinatura_valve(tmp_path / "x.dll", rodar=lambda *a, **k: R("NotSigned|"))[0]


def test_assinatura_valve_nunca_poe_o_caminho_no_texto_do_comando(monkeypatch, tmp_path):
    """P5.0: no modo instalado o caminho fica sob o nome do usuario; um apostrofo (ou `$(...)`) nele quebrava ou virava comando."""
    monkeypatch.setattr(terceiros.sys, "platform", "win32")
    dll = tmp_path / "O'Neil $(calc)" / "steam_api64.dll"
    visto = {}

    class R:
        stdout = "Valid|CN=Valve Corp."

    def rodar(cmd, **k):
        visto["cmd"], visto["env"] = cmd, k.get("env")
        return R()
    assert terceiros.assinatura_valve(dll, rodar=rodar)[0]
    assert str(dll) not in " ".join(visto["cmd"]) and "O'Neil" not in " ".join(visto["cmd"])
    assert visto["env"] is not None and visto["env"]["BISTURI_DLL_ASSINADA"] == str(dll)


def _primeiro_do_comando(monkeypatch, tmp_path, system_root: str | None) -> str:
    monkeypatch.setattr(terceiros.sys, "platform", "win32")
    if system_root is None:
        monkeypatch.delenv("SystemRoot", raising=False)
    else:
        monkeypatch.setenv("SystemRoot", system_root)
    visto = {}

    class R:
        stdout = "Valid|CN=Valve Corp."

    def rodar(cmd, **k):
        visto["cmd"] = cmd
        return R()
    assert terceiros.assinatura_valve(tmp_path / "steam_api64.dll", rodar=rodar)[0]
    return visto["cmd"][0]


def test_assinatura_valve_chama_o_powershell_pelo_caminho_completo(monkeypatch, tmp_path):
    """P9, F3: com um PATH sem `WindowsPowerShell\\v1.0` o "powershell" solto dava [WinError 2] e "gravar pela demo" falhava (código 4)."""
    windows = tmp_path / "Win dows"
    exe = windows / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ falso")
    assert _primeiro_do_comando(monkeypatch, tmp_path, str(windows)) == str(exe)
    # CONTROLE: sem o arquivo (ou sem SystemRoot), volta ao "powershell" do PATH, como antes
    assert _primeiro_do_comando(monkeypatch, tmp_path, str(tmp_path / "nao_existe")) == "powershell"
    assert _primeiro_do_comando(monkeypatch, tmp_path, None) == "powershell"


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell do Windows")
def test_assinatura_valve_acha_o_powershell_deste_windows(monkeypatch, tmp_path):
    primeiro = _primeiro_do_comando(monkeypatch, tmp_path, os.environ["SystemRoot"])
    assert Path(primeiro).is_absolute() and Path(primeiro).is_file() and Path(primeiro).name.lower() == "powershell.exe"


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell do Windows")
def test_assinatura_valve_com_apostrofo_no_caminho_funciona_de_verdade(tmp_path):
    pasta = tmp_path / "Joao O'Neil"
    pasta.mkdir()
    dll = pasta / "steam_api64.dll"
    dll.write_bytes(b"MZ nao assinado")
    ok, texto = terceiros.assinatura_valve(dll)
    assert not ok
    assert "NotSigned" in texto or "UnknownError" in texto or "NotSupported" in texto, texto   # leu o arquivo (nao quebrou o comando)


def test_assinatura_valve_sem_resposta_do_powershell_diz_o_motivo(monkeypatch, tmp_path):
    """O PowerShell pode ficar mudo (visto no runner do GitHub): a mensagem não pode ser vazia."""
    monkeypatch.setattr(terceiros.sys, "platform", "win32")

    class R:
        stdout = ""
        stderr = "o cmdlet falhou" + chr(10) + "outra linha"
        returncode = 3
    ok, texto = terceiros.assinatura_valve(tmp_path / "x.dll", rodar=lambda *a, **k: R())
    assert not ok and "sem resposta do PowerShell" in texto and "código 3" in texto and "o cmdlet falhou" in texto


def test_versao_nova_do_hlae_so_informa(monkeypatch):
    abrir = lambda tag: (lambda req, timeout=0: _Resposta(('{"tag_name": "%s"}' % tag).encode()))  # noqa: E731
    assert terceiros.versao_nova_do_hlae(abrir("v2.193.0")) is None                  # igual à fixada
    assert terceiros.versao_nova_do_hlae(abrir("v2.194.0")) == "v2.194.0"            # há nova: só avisa
    assert terceiros.versao_nova_do_hlae(lambda req, timeout=0: (_ for _ in ()).throw(OSError("sem rede"))) is None


# ── lista branca do console remoto ────────────────────────────────────────────────────────────────

BOAS = [
    "spec_show_xray 0", "spec_player 10", "spec_mode 1", "spec_lock_to_accountid 222222222",
    "mirv_streams add normal bisturi", 'mirv_streams record name "C:\\Users\\jogador\\Projetos\\bisturi\\gravacao_demo\\r12"',
    "mirv_streams record fps 60", "mirv_streams record start", "mirv_streams record end",
    'mirv_streams settings add ffmpeg bisturi "-c:v h264_nvenc -preset p5 -cq 18 -pix_fmt yuv420p {AFX_OUTPUT_FILE}"',
    'mirv_cmd addAtTick 69481 "mirv_streams record start"', 'mirv_cmd addAtTick 76400 "mirv_streams record end; demo_pause"',
    "mirv_cmd print", "mirv_cmd clear", "demo_gototick 69481", "demo_goto 69481 0 1", "demo_pause", "demo_resume", "demo_timescale 1",
    'mirv_script_load "mirv_script_spec_lock.js"', "mirv_script_spec_lock 10", "quit", "SPEC_PLAYER 3",
    "spec_show_xray 0; mirv_streams record start",
]
RUINS = [
    "connect 1.2.3.4", "map de_dust2", "map_workshop 123", "host_workshop_map 1", 'exec "autoexec.cfg"', 'bind "x" "quit"', "rcon_password x",
    "playdemo x.dem", "disconnect", "+connect 1.2.3.4", "sv_cheats 1", "alias a quit", "fps_max 30", "cl_forcepreload 1", "echo oi",
    "spec_show_xray 0; connect 1.2.3.4",                                         # comando escondido depois do ';'
    'mirv_cmd addAtTick 1 "connect 1.2.3.4"',                                    # comando proibido dentro do agendamento
    'mirv_cmd addAtTick 1 "mirv_streams record start; map de_dust2"',
    'mirv_cmd save "arquivo.xml"', 'mirv_cmd load "arquivo.xml"',                # gravam/leem arquivos
    'mirv_script_load "outro.js"', 'mirv_script_load "C:\\x\\mau.js"',
    'mirv_streams record name "C:\\a;b"',                                        # ';' dentro de aspas
    "mirv_streams record start\nconnect 1.2.3.4", "spec_player 1\r", "spec_player\x00 1", "", "   ", 'mirv_streams record name "sem fechar',
    "spec_player " + "9" * 500,
]


def test_lista_branca_aceita_os_comandos_do_gravador_e_recusa_o_resto():
    for linha in BOAS:
        assert validar_linha(linha), linha                                       # CONTROLE: os bons passam
    for linha in RUINS:
        with pytest.raises(ComandoBloqueado):
            validar_linha(linha)


def test_valores_com_aspas_ponto_e_virgula_ou_quebra_de_linha_sao_recusados():
    assert validar_valor("match730_003846488745834446879_2134712485_201.dem") and validar_valor("C:\\Users\\jogador\\gravacao_demo\\r12")
    for ruim in ('a"b', "a;b", "a\nb", "a\rb", "a\x00b", ""):
        with pytest.raises(ComandoBloqueado):
            validar_valor(ruim)


# ── cliente TCP ───────────────────────────────────────────────────────────────────────────────────

class _ServidorFalso:
    """Faz o papel do console do CS2 em 127.0.0.1 (só loopback): aceita uma conexão, guarda as linhas e responde com eco.
    Já nasce ouvindo (o `listen` guarda a conexão na fila), então nenhum teste precisa dormir para esperar a porta."""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.porta = self.srv.getsockname()[1]
        self.recebido: list[str] = []
        self._t = threading.Thread(target=self._rodar, daemon=True)
        self._t.start()

    def _rodar(self):
        self.srv.settimeout(10)
        try:
            c, _ = self.srv.accept()
            c.settimeout(5)
            while True:
                dados = c.recv(4096)
                if not dados:
                    break
                for linha in dados.decode().splitlines():
                    self.recebido.append(linha)
                    c.sendall(f"[eco] {linha}\n".encode())
        except OSError:
            pass
        finally:
            self.srv.close()


class _RelogioFalso:
    """Faz o papel do módulo `time` dentro do netcon: `sleep` só avança o relógio de mentira (o teste não espera de verdade)."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, segundos):
        self.t += segundos


class _SocketFalso:
    """Console do CS2 de mentira: guarda o que recebe e responde com eco; sem dado novo, 'estoura o tempo' como o socket de verdade."""

    def __init__(self, relogio):
        self.relogio, self.enviado, self._fila, self.fechado = relogio, [], [], False

    def settimeout(self, _):
        pass

    def sendall(self, dados):
        for linha in dados.decode().splitlines():
            self.enviado.append(linha)
            self._fila.append(f"[eco] {linha}\n".encode())

    def recv(self, _):
        if self._fila:
            return self._fila.pop(0)
        self.relogio.t += 0.5                               # o recv de verdade esperaria 0,5 s e estouraria
        raise TimeoutError()

    def close(self):
        self.fechado = True


def _usar_relogio_falso(monkeypatch):
    relogio = _RelogioFalso()
    monkeypatch.setattr(netcon, "time", relogio)
    return relogio


def test_cliente_fala_com_um_servidor_de_verdade_em_loopback_e_so_envia_o_permitido():
    srv = _ServidorFalso()
    c = ClienteNetcon(srv.porta)
    c.conectar(tempo_maximo=10.0, intervalo=0.2)
    c.enviar("spec_show_xray 0")
    assert c.esperar("[eco] spec_show_xray 0", 5.0)
    with pytest.raises(ComandoBloqueado):
        c.enviar("connect 1.2.3.4")
    c.enviar("mirv_streams record start")
    assert c.esperar("record start", 5.0) and "[eco] mirv_streams record start" not in c.lido()
    c.fechar()
    assert srv.recebido == ["spec_show_xray 0", "mirv_streams record start"]        # o comando proibido NUNCA chegou ao jogo


def test_cliente_conecta_com_repeticao_ate_o_jogo_abrir_a_porta(monkeypatch):
    relogio = _usar_relogio_falso(monkeypatch)
    sock = _SocketFalso(relogio)
    tentativas = []

    def conexao(endereco, timeout=0):
        tentativas.append((endereco, relogio.t))
        if relogio.t < 1.0:                                  # o jogo demora 1 s (de mentira) para abrir a porta
            raise ConnectionRefusedError("recusado")
        return sock
    monkeypatch.setattr(netcon.socket, "create_connection", conexao)
    c = ClienteNetcon(2121)
    c.conectar(tempo_maximo=10.0, intervalo=0.2)
    assert len(tentativas) > 1 and relogio.t >= 1.0          # tentou de novo até a porta abrir
    assert {t[0] for t in tentativas} == {("127.0.0.1", 2121)}
    c.enviar("spec_show_xray 0")
    assert c.esperar("[eco] spec_show_xray 0", 5.0)
    with pytest.raises(ComandoBloqueado):
        c.enviar("connect 1.2.3.4")
    c.enviar("mirv_streams record start")
    assert c.esperar("record start", 5.0) and "[eco] mirv_streams record start" not in c.lido()
    c.fechar()
    assert sock.fechado and sock.enviado == ["spec_show_xray 0", "mirv_streams record start"]    # o comando proibido NUNCA chegou ao jogo


def test_sem_servidor_desiste_no_prazo_e_so_aceita_127_0_0_1(monkeypatch):
    relogio = _usar_relogio_falso(monkeypatch)

    def recusa(endereco, timeout=0):
        raise ConnectionRefusedError("recusado")
    monkeypatch.setattr(netcon.socket, "create_connection", recusa)
    with pytest.raises(SemConexao, match="não abriu"):
        ClienteNetcon(2121).conectar(tempo_maximo=1.0, intervalo=0.2)
    assert 0.9 <= relogio.t < 5.0                            # desistiu no prazo (segundos do relógio de mentira)
    with pytest.raises(SemConexao, match="127.0.0.1"):
        ClienteNetcon(2121, host="0.0.0.0")
    with pytest.raises(SemConexao, match="não conectado"):
        ClienteNetcon(2121).enviar("quit")


def _c(local, porta, remoto="0.0.0.0", porta_remota=0, estado="?0", pid=4242):
    return netcon.Conexao(local, porta, remoto, porta_remota, estado, pid)


class _R:
    def __init__(self, s):
        self.stdout = s


def _firewall(resposta):
    """`rodar` falso para a consulta do Firewall: 'BLOQUEADO <regra>', 'SEM_REGRA' ou 'PERFIL_DESLIGADO <perfis>'."""
    return lambda cmd, **k: _R(resposta + "\n")


def _explode(*a, **k):
    raise AssertionError("o Firewall não deveria ser consultado aqui")


def test_ouvinte_e_reconhecido_pela_estrutura_da_linha_nao_pelo_estado():
    # Neste Windows (11 26H2, build 26300) a tabela devolve estado 0 para sockets em escuta: o estado não serve para achar o ouvinte
    assert _c("0.0.0.0", 2121, estado="?0").ouvinte and _c("127.0.0.1", 2121, estado="LISTEN").ouvinte and _c("::", 2121).ouvinte
    assert not _c("127.0.0.1", 2121, "127.0.0.1", 50000, "ESTABLISHED").ouvinte                        # CONTROLE: conexão aceita
    assert not _c("127.0.0.1", 2121, "127.0.0.1", 50000, "?0").ouvinte
    assert _c("127.0.0.1", 1).local_e_loopback and _c("::1", 1).local_e_loopback and not _c("0.0.0.0", 1).local_e_loopback


def test_porta_so_local_e_aceita_sem_consultar_o_firewall():
    ler = lambda: [_c("127.0.0.1", 2121), _c("::1", 2121), _c("10.0.0.5", 80)]  # noqa: E731
    assert netcon.escuta_so_em_local(2121, rodar=_explode, ler=ler) == (True, ["127.0.0.1", "::1"])


def test_porta_na_rede_so_vale_com_regra_de_bloqueio_e_firewall_ligado():
    ler = lambda: [_c("0.0.0.0", 2121, estado="LISTEN")]  # noqa: E731
    ok, det = netcon.escuta_so_em_local(2121, rodar=_firewall("BLOQUEADO Bisturi bloqueia 2121"), ler=ler)
    assert ok is True and det == ["0.0.0.0 (porta bloqueada para fora no Firewall)"]
    assert netcon.escuta_so_em_local(2121, rodar=_firewall("SEM_REGRA"), ler=ler) == (False, ["0.0.0.0"])               # CONTROLE: sem regra, para
    assert netcon.escuta_so_em_local(2121, rodar=_firewall("PERFIL_DESLIGADO Public"), ler=ler)[0] is False                # firewall desligado = sem proteção
    assert netcon.escuta_so_em_local(2121, rodar=_firewall("BLOQUEADO x"), ler=lambda: [_c("::", 2121)])[0] is True
    assert netcon.estado_do_firewall(2121, rodar=_firewall("BLOQUEADO Bisturi bloqueia 2121")) == (True, "regra 'Bisturi bloqueia 2121' ativa e Firewall ligado")
    assert netcon.estado_do_firewall(2121, rodar=_firewall("PERFIL_DESLIGADO Public,Private"))[1].startswith("o Firewall do Windows está desligado")
    assert netcon.estado_do_firewall(2121, rodar=_firewall("SEM_REGRA"))[1].startswith("não achei regra")

    def cai(*a, **k):
        raise subprocess.TimeoutExpired("powershell", 90)
    assert netcon.estado_do_firewall(2121, rodar=cai) == (False, "não consegui consultar o Firewall do Windows (TimeoutExpired)")
    chamadas = []                                                                                                               # a consulta é só leitura
    netcon.estado_do_firewall(2121, rodar=lambda cmd, **k: chamadas.append(cmd) or _R("SEM_REGRA"))
    assert "HNetCfg.FwPolicy2" in chamadas[0][-1] and "'2121'" in chamadas[0][-1]                 # COM: lê sem administrador (prova real 3)
    assert "Get-NetFirewallPortFilter" not in chamadas[0][-1]                                      # esse dá "Acesso negado" fora da elevação
    assert not any(verbo in chamadas[0][-1] for verbo in ("New-NetFirewallRule", "Set-NetFirewall", "Remove-", ".Add(", ".Remove(", "Enable-", "Disable-"))


@pytest.mark.lento       # consulta o Firewall de verdade (PowerShell): fica fora da suíte rápida
@pytest.mark.skipif(sys.platform != "win32", reason="consulta o Firewall do Windows (só leitura)")
def test_consulta_real_do_firewall_responde_sem_regra_para_uma_porta_qualquer():
    ok, det = netcon.estado_do_firewall(2999)
    assert ok is False and (det.startswith("não achei regra") or det.startswith("o Firewall do Windows está desligado")), det


def test_ouvinte_fechado_com_so_a_nossa_conexao_e_aceito_e_conexao_de_fora_reprova():
    nossa = _c("127.0.0.1", 2121, "127.0.0.1", 50001, "ESTABLISHED", pid=77)
    ok, det = netcon.escuta_so_em_local(2121, rodar=_explode, ler=lambda: [nossa])
    assert ok is True and det == ["ouvinte fechado; só a nossa conexão (127.0.0.1:50001, pid 77)"]
    assert netcon.escuta_so_em_local(2121, rodar=_explode, ler=lambda: [_c("127.0.0.1", 2121, "127.0.0.1", 50001, "?0", pid=77)])[0] is True
    de_fora = _c("192.168.0.10", 2121, "192.168.0.7", 40000, "ESTABLISHED")
    assert netcon.escuta_so_em_local(2121, rodar=_firewall("BLOQUEADO x"), ler=lambda: [_c("0.0.0.0", 2121), de_fora]) == \
        (False, ["conexão aceita de fora: 192.168.0.7:40000"])


def test_nada_na_porta_le_a_tabela_3_vezes_e_devolve_vazio_para_quem_chama_decidir():
    leituras, pausas = [], []
    assert netcon.escuta_so_em_local(2121, rodar=_explode, ler=lambda: leituras.append(1) or [], dormir=pausas.append) == (False, [])
    assert len(leituras) == 3 and pausas == [0.5, 0.5]
    velha = _c("127.0.0.1", 2121, "127.0.0.1", 49000, "TIME_WAIT", pid=1)                     # sobra de uma sessão anterior: conta como nada
    assert netcon.escuta_so_em_local(2121, rodar=_explode, ler=lambda: [velha], dormir=lambda s: None) == (False, [])
    # prova real 4 (08/11): o ouvinte do CS2 veio com estado "CLOSED" e depois "?0" com a porta viva: um ouvinte NUNCA é descartado pelo estado
    fantasma = _c("0.0.0.0", 2121, estado="CLOSED", pid=8900)
    nossa = _c("127.0.0.1", 2121, "127.0.0.1", 52831, "ESTABLISHED", pid=8900)
    assert netcon.escuta_so_em_local(2121, rodar=_firewall("SEM_REGRA"), ler=lambda: [fantasma, nossa]) == (False, ["0.0.0.0"])
    assert netcon.escuta_so_em_local(2121, rodar=_firewall("BLOQUEADO x"), ler=lambda: [fantasma, nossa])[1] == ["0.0.0.0 (porta bloqueada para fora no Firewall)"]

    def falha():
        raise OSError("tabela")
    assert netcon.escuta_so_em_local(2121, rodar=_explode, ler=falha, dormir=lambda s: None) == (False, [])


def test_ouvinte_de_outro_processo_na_porta_e_reprovado_e_linhas_velhas_de_outro_pid_sao_ignoradas():
    intruso = _c("0.0.0.0", 2121, estado="LISTEN", pid=99)
    assert netcon.escuta_so_em_local(2121, rodar=_firewall("BLOQUEADO x"), ler=lambda: [intruso], pids_do_jogo=[10]) == \
        (False, ["porta 2121 em uso por outro processo (pid 99, 0.0.0.0)"])
    velha = _c("127.0.0.1", 2121, "127.0.0.1", 49000, "?0", pid=99)                             # sobra de outro processo, sem ouvinte: ignorada
    nossa = _c("127.0.0.1", 2121, estado="?0", pid=10)
    assert netcon.escuta_so_em_local(2121, rodar=_explode, ler=lambda: [velha, nossa], pids_do_jogo=[10]) == (True, ["127.0.0.1"])
    assert netcon.escuta_so_em_local(2121, rodar=_firewall("BLOQUEADO x"), ler=lambda: [intruso])[0] is True          # CONTROLE: sem a lista de pids, nada é filtrado


def _ate(condicao, tentativas=80, pausa_s=0.1):
    """A tabela TCP deste Windows (build 26300) às vezes demora, ou nem mostra, um socket vivo (medido em 08/11/2026: em 1 rodada de 6 um
    ouvinte novo ficou invisível por mais de 3 s). Espera a condição valer, como o código real faz."""
    for _ in range(tentativas):
        valor = condicao()
        if valor:
            return valor
        time.sleep(pausa_s)
    return condicao()


def _ou_pula(valor, porque):
    """A lógica da porta está coberta pelos testes com linhas falsas; este teste só confere a leitura REAL. Se o Windows esconder o socket,
    o teste é PULADO com o motivo registrado (não é falha nossa, e não pode mascarar o resto da suíte)."""
    if not valor:
        pytest.skip("a tabela TCP deste Windows não mostrou o socket em 8 s: " + porque)
    return valor


@pytest.mark.lento       # abre socket em 0.0.0.0 (pode acionar o alerta do Windows) e espera a tabela TCP de verdade
@pytest.mark.skipif(sys.platform != "win32", reason="lê a tabela TCP do Windows")
def test_tabela_tcp_real_mostra_os_nossos_sockets_e_o_ouvinte_fechado():
    meu = os.getpid()
    a = socket.socket()
    a.bind(("127.0.0.1", 0))
    a.listen(5)
    porta_a = a.getsockname()[1]
    _ou_pula(_ate(lambda: [c for c in netcon.linhas_da_porta(porta_a) if c.pid == meu and c.ouvinte and c.local == "127.0.0.1"]), "ouvinte em 127.0.0.1")
    assert netcon.escuta_so_em_local(porta_a, rodar=_explode, pids_do_jogo=[meu]) == (True, ["127.0.0.1"])
    b = socket.socket()
    b.bind(("0.0.0.0", 0))
    b.listen(5)
    porta_b = b.getsockname()[1]
    _ou_pula(_ate(lambda: [c for c in netcon.linhas_da_porta(porta_b) if c.pid == meu and c.ouvinte]), "ouvinte em 0.0.0.0")
    assert netcon.escuta_so_em_local(porta_b, rodar=_firewall("SEM_REGRA"), pids_do_jogo=[meu]) == (False, ["0.0.0.0"])      # CONTROLE
    assert netcon.escuta_so_em_local(porta_b, rodar=_firewall("BLOQUEADO x"), pids_do_jogo=[meu])[0] is True
    cliente = socket.create_connection(("127.0.0.1", porta_b))
    servidor, _ = b.accept()
    b.close()                                                                                   # ouvinte fechado: só a nossa conexão fica
    _ou_pula(_ate(lambda: not any(c.ouvinte for c in netcon.linhas_da_porta(porta_b))), "o ouvinte fechado ainda aparece")
    _ou_pula(_ate(lambda: [c for c in netcon.linhas_da_porta(porta_b) if c.pid == meu and not c.ouvinte]), "a conexão aceita")
    ok, det = netcon.escuta_so_em_local(porta_b, rodar=_explode, pids_do_jogo=[meu])
    assert ok is True and det and det[0].startswith("ouvinte fechado; só a nossa conexão (127.0.0.1:"), det
    for s in (a, cliente, servidor):
        s.close()


def test_serverconfig_do_cs2_e_so_aviso_nao_alerta_vermelho():
    from cs2clip import steam_local
    from cs2clip.steam_local import ArquivoFoto, Foto
    antes, depois = Foto(), Foto()
    antes.arquivos["csgo/ServerConfig.vdf"] = ArquivoFoto(489, 1, None)
    depois.arquivos["csgo/ServerConfig.vdf"] = ArquivoFoto(489, 2, None)
    d = steam_local.comparar(antes, depois)
    assert not d.vermelho and d.avisos
    outro = Foto(); outro2 = Foto()                                                                 # CONTROLE: outro arquivo de csgo continua vermelho
    outro.arquivos["csgo/cfg/autoexec.cfg"] = ArquivoFoto(10, 1, "a")
    outro2.arquivos["csgo/cfg/autoexec.cfg"] = ArquivoFoto(10, 2, "b")
    assert steam_local.comparar(outro, outro2).vermelho


def test_demoui_so_passa_sozinho_e_quit_tambem():
    assert validar_linha("demoui") == ["demoui"] and validar_linha("quit") == ["quit"]
    for ruim in ("demoui 1", "demoui; connect 1.2.3.4", "quit now", "demouix"):
        with pytest.raises(ComandoBloqueado):
            validar_linha(ruim)


def test_mirv_panorama_so_passa_na_forma_exata_de_esconder_um_painel():
    assert validar_linha("mirv_panorama panelStyle panelId=HudDemoController opacity=0") == ["mirv_panorama panelStyle panelId=HudDemoController opacity=0"]
    assert validar_linha("mirv_panorama panelStyle panelId=Scoreboard opacity=0.5")
    for ruim in ("mirv_panorama", "mirv_panorama foo", "mirv_panorama panelStyle panelId=X opacity=2", "mirv_panorama panelStyle panelId=X opacity=0 extra",
                 'mirv_panorama panelStyle panelId="X" opacity=0', "mirv_panorama panelStyle panelClassName=CSGOHudHintText opacity=0"):
        with pytest.raises(ComandoBloqueado):
            validar_linha(ruim)

