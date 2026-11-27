"""D3 (Plano D): cliente do console remoto do CS2 (`-netconport`), com LISTA BRANCA de comandos.

O CS2 aberto com `-netconport 2121` aceita linhas de comando por TCP, em texto puro e SEM senha. Por isso (parecer do auditor):
  * só `127.0.0.1`; ANTES de abrir o jogo, o Firewall do Windows precisa ter uma regra de ENTRADA que bloqueie a porta (o CS2 abre o
    `-netconport` em TODAS as placas, 0.0.0.0, medido em 08/11/2026); depois de conectar, a tabela TCP é lida direto da API do Windows
    (`escuta_so_em_local`), porque o `Get-NetTCPConnection -State Listen` deste Windows falha em sockets vivos;
  * só os comandos da LISTA BRANCA passam (`spec_*`, `mirv_streams`, `mirv_cmd`, `demo_*`, `demoui` sozinho, `mirv_script_load` de um arquivo fixo, `quit`);
  * `connect`, `map`, `exec`, `bind`, `rcon`, `sv_*`, `playdemo`... NUNCA saem daqui (o `playdemo` só vai na linha de comando do lançamento);
  * dentro de `mirv_cmd ... "cmd1; cmd2"` cada comando interno passa pela mesma lista; `;` solto, quebra de linha e NUL são recusados;
  * linhas longas (> 400 caracteres; o jogo corta em ~512 sem avisar) são recusadas.
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from typing import NamedTuple
from collections.abc import Callable
from .tools import sem_janela

HOST = "127.0.0.1"
LIMITE_LINHA = 400
PERMITIDOS = ("spec_", "mirv_streams", "mirv_cmd", "demo_", "demoui", "mirv_panorama", "mirv_script_load", "mirv_script_spec_lock", "quit")
PANORAMA_PERMITIDO = re.compile(r"^panelStyle panelId=[A-Za-z0-9_]{1,60} opacity=(0|1|0\.\d{1,3})$")      # só esconder/mostrar um painel pelo id
SCRIPTS_PERMITIDOS = ("mirv_script_spec_lock.js",)
SO_ADD_AT_TICK = re.compile(r'^addattick\s+\d{1,9}\s+"([^"]*)"$', re.IGNORECASE)      # a única forma agendada que o projeto gera (hlae.comandos_do_trecho)
SO_ENABLED = re.compile(r"^enabled(\s+[01])?$", re.IGNORECASE)

# Nunca saem daqui, nem como "interno" de um mirv_cmd (a lista branca já os recusa; a lista preta existe para a mensagem clara)
PROIBIDOS = ("connect", "map", "map_workshop", "host_workshop_map", "exec", "bind", "rcon", "playdemo", "disconnect", "+connect",
             "sv_", "alias", "cl_", "net_", "sensitivity", "fps_max", "mat_", "r_", "snd_")


class ComandoBloqueado(Exception):
    """A linha não está na lista branca (ou tenta passar algo escondido): não foi enviada ao jogo."""


class SemConexao(Exception):
    """Não consegui falar com o console remoto do CS2."""


def _separar(linha: str) -> list[str]:
    """Quebra a linha em comandos no `;` que está FORA de aspas."""
    partes, atual, aspas = [], [], False
    for c in linha:
        if c == '"':
            aspas = not aspas
        if c == ";" and not aspas:
            partes.append("".join(atual))
            atual = []
        else:
            atual.append(c)
    if aspas:
        raise ComandoBloqueado("aspas sem fechar")
    partes.append("".join(atual))
    return [p.strip() for p in partes if p.strip()]


def _cabeca(comando: str) -> str:
    return comando.strip().split(None, 1)[0].lower() if comando.strip() else ""


def _validar_mirv_cmd(resto: str, profundidade: int) -> None:
    """G-005 (26/11): só as formas que o projeto gera: `print`, `clear`, `enabled 0|1` e `addAtTick <tick inteiro> "<comandos>"`, sem NADA
    fora disso (texto solto, segundo argumento, aspas extras). Os comandos entre aspas passam, um a um, pela mesma lista branca.
    `add` e `addAtTime` o projeto nunca emite: ficam bloqueados (`save` e `load` gravam arquivos)."""
    if resto.lower() in ("print", "clear") or SO_ENABLED.match(resto):
        return
    m = SO_ADD_AT_TICK.match(resto)
    if not m:
        raise ComandoBloqueado('mirv_cmd só aceita print, clear, enabled 0|1 e addAtTick <tick> "<comandos>" (nada solto fora das aspas)')
    internos = _separar(m.group(1))
    if not internos:
        raise ComandoBloqueado("mirv_cmd addAtTick sem comando")
    for sub in internos:
        _validar_comando(sub, profundidade + 1)


def _validar_comando(comando: str, profundidade: int = 0) -> None:
    cab = _cabeca(comando)
    if not cab:
        raise ComandoBloqueado("comando vazio")
    for proibido in PROIBIDOS:
        if cab == proibido or (proibido.endswith("_") and cab.startswith(proibido)):
            raise ComandoBloqueado(f"'{cab}' é proibido")
    if not any(cab == p or (p.endswith("_") and cab.startswith(p)) for p in PERMITIDOS):
        raise ComandoBloqueado(f"'{cab}' não está na lista branca")
    resto = comando.strip()[len(cab):].strip() if comando.strip().lower().startswith(cab) else ""
    if cab in ("demoui", "quit"):
        if resto:
            raise ComandoBloqueado(f"'{cab}' não aceita argumento")
        return
    if cab == "mirv_panorama":
        if not PANORAMA_PERMITIDO.match(resto):
            raise ComandoBloqueado("mirv_panorama só aceita 'panelStyle panelId=<id> opacity=<0..1>'")
        return
    if cab == "mirv_script_load":
        alvo = resto.strip().strip('"')
        if alvo not in SCRIPTS_PERMITIDOS:
            raise ComandoBloqueado("mirv_script_load só aceita os scripts fixos do HLAE: " + ", ".join(SCRIPTS_PERMITIDOS))
        return
    if cab == "mirv_cmd":
        if profundidade >= 1:
            raise ComandoBloqueado("mirv_cmd dentro de mirv_cmd")
        _validar_mirv_cmd(resto, profundidade)
        return
    internos = re.findall(r'"([^"]*)"', resto)
    for texto in internos:                       # dado entre aspas (caminho, nome): nada de `;` escondido
        if ";" in texto:
            raise ComandoBloqueado("';' dentro de aspas")


def validar_linha(linha: str) -> list[str]:
    """Os comandos da linha, se TODOS passam na lista branca; senão levanta `ComandoBloqueado`."""
    if not isinstance(linha, str) or not linha.strip():
        raise ComandoBloqueado("linha vazia")
    if len(linha) > LIMITE_LINHA:
        raise ComandoBloqueado(f"linha com {len(linha)} caracteres (máximo {LIMITE_LINHA}: o jogo corta em silêncio)")
    if any(c in linha for c in ("\n", "\r", "\x00")):
        raise ComandoBloqueado("quebra de linha ou caractere nulo")
    comandos = _separar(linha)
    for c in comandos:
        _validar_comando(c)
    return comandos


def validar_valor(texto: str) -> str:
    """Nome de demo ou pasta que vai entrar numa linha: recusa `"`, `;`, quebra de linha e NUL (injeção no console)."""
    if not isinstance(texto, str) or not texto or any(c in texto for c in ('"', ";", "\n", "\r", "\x00")):
        raise ComandoBloqueado("valor com caractere proibido (aspas, ponto e vírgula, quebra de linha)")
    return texto


class ClienteNetcon:
    def __init__(self, porta: int = 2121, host: str = HOST):
        if host != HOST:
            raise SemConexao("o console remoto só é usado em 127.0.0.1")
        self.porta, self.host = int(porta), host
        self._sock: socket.socket | None = None
        self._buffer = ""
        self.viu_em_jogo = False

    def conectar(self, tempo_maximo: float = 60.0, intervalo: float = 0.5) -> None:
        """Tenta conectar até `tempo_maximo` s (o jogo leva de 0,6 a ~7 s para abrir a porta)."""
        fim = time.monotonic() + tempo_maximo
        ultimo: Exception | None = None
        while time.monotonic() < fim:
            try:
                self._sock = socket.create_connection((self.host, self.porta), timeout=3.0)
                self._sock.settimeout(0.5)
                return
            except OSError as e:
                ultimo = e
                time.sleep(intervalo)
        raise SemConexao(f"o CS2 não abriu o console remoto na porta {self.porta} em {tempo_maximo:.0f} s ({ultimo})")

    def enviar(self, linha: str) -> None:
        """Valida (lista branca) e envia UMA linha. Nada fora da lista sai daqui."""
        validar_linha(linha)
        if self._sock is None:
            raise SemConexao("não conectado")
        try:
            self._sock.sendall((linha.strip() + "\n").encode("utf-8"))
        except OSError as e:
            raise SemConexao(f"a conexão com o console do CS2 caiu ({e})") from e

    def _ler(self) -> None:
        if self._sock is None:
            return
        try:
            dados = self._sock.recv(65536)
            if dados:
                texto = dados.decode("utf-8", errors="replace")
                self._buffer += texto
                if "CSGO_GAME_UI_STATE_INGAME" in self._buffer[-(len(texto) + 40):]:
                    self.viu_em_jogo = True      # o gravador espera o jogo sair da tela de carregamento; a marca pode ser consumida por outra espera
        except TimeoutError:
            pass
        except OSError:
            pass

    def esperar(self, texto: str, tempo_maximo: float = 10.0) -> bool:
        """Espera `texto` aparecer no que o jogo escreve de volta. True se apareceu; consome o que foi lido até ali."""
        fim = time.monotonic() + tempo_maximo
        while time.monotonic() < fim:
            self._ler()
            i = self._buffer.find(texto)
            if i >= 0:
                self._buffer = self._buffer[i + len(texto):]
                return True
        return False

    def lido(self) -> str:
        """Tudo o que o jogo escreveu desde a última vez (para o registro da gravação)."""
        self._ler()
        saida, self._buffer = self._buffer, ""
        return saida

    def fechar(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


# ── a tabela TCP do Windows, lida direto da API (sem PowerShell) ─────────────────────────────────
# Medido em 08/11/2026 neste Windows 11 (26H2, build 26300) com sockets nossos: a tabela devolve ESTADO 0 para sockets em escuta, o `netstat`
# não os lista e `Get-NetTCPConnection -State Listen -LocalPort <porta>` veio vazio 5 vezes em 5 para uma porta realmente aberta. Por isso um
# ouvinte é reconhecido pela ESTRUTURA da linha (lado remoto zerado), nunca pelo estado, e a leitura é feita pela API, sem processo filho.

LOOPBACK = ("127.0.0.1", "::1")
TCP_TABLE_OWNER_PID_ALL = 5
ESTADOS_TCP = {1: "CLOSED", 2: "LISTEN", 3: "SYN_SENT", 4: "SYN_RCVD", 5: "ESTABLISHED", 6: "FIN_WAIT1", 7: "FIN_WAIT2", 8: "CLOSE_WAIT",
               9: "CLOSING", 10: "LAST_ACK", 11: "TIME_WAIT", 12: "DELETE_TCB"}
FECHANDO = {"CLOSED", "FIN_WAIT1", "FIN_WAIT2", "CLOSE_WAIT", "CLOSING", "LAST_ACK", "TIME_WAIT", "DELETE_TCB"}


class Conexao(NamedTuple):
    local: str
    porta_local: int
    remoto: str
    porta_remota: int
    estado: str          # nome do estado, ou "?N" quando o Windows devolve um valor fora da tabela (acontece: 0 em sockets vivos)
    pid: int

    @property
    def ouvinte(self) -> bool:
        """Socket em escuta: lado remoto zerado. O estado não é confiável neste Windows."""
        return self.porta_remota == 0 and self.remoto in ("0.0.0.0", "::")

    @property
    def local_e_loopback(self) -> bool:
        return self.local in LOOPBACK


def tabela_tcp() -> list[Conexao]:
    """Todas as conexões TCP (v4 e v6) da máquina, lidas de `GetExtendedTcpTable` (iphlpapi; sessão normal basta). Fora do Windows: []."""
    if sys.platform != "win32":
        return []
    import ctypes
    import struct
    from ctypes import wintypes
    iphlpapi = ctypes.WinDLL("iphlpapi")

    class Linha4(ctypes.Structure):
        _fields_ = [("estado", wintypes.DWORD), ("local", wintypes.DWORD), ("porta_local", wintypes.DWORD),
                    ("remoto", wintypes.DWORD), ("porta_remota", wintypes.DWORD), ("pid", wintypes.DWORD)]

    class Linha6(ctypes.Structure):
        _fields_ = [("local", ctypes.c_ubyte * 16), ("escopo_local", wintypes.DWORD), ("porta_local", wintypes.DWORD),
                    ("remoto", ctypes.c_ubyte * 16), ("escopo_remoto", wintypes.DWORD), ("porta_remota", wintypes.DWORD),
                    ("estado", wintypes.DWORD), ("pid", wintypes.DWORD)]
    saida: list[Conexao] = []
    for familia, Linha in ((int(socket.AF_INET), Linha4), (int(socket.AF_INET6), Linha6)):
        codigo = -1
        for _ in range(3):                                   # a tabela pode crescer entre medir e ler (ERROR_INSUFFICIENT_BUFFER)
            tam = wintypes.DWORD(0)
            iphlpapi.GetExtendedTcpTable(None, ctypes.byref(tam), False, familia, TCP_TABLE_OWNER_PID_ALL, 0)
            buf = ctypes.create_string_buffer(int(tam.value) + 4096)
            tam = wintypes.DWORD(len(buf))
            codigo = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(tam), False, familia, TCP_TABLE_OWNER_PID_ALL, 0)
            if codigo == 0:
                break
        else:
            raise OSError(f"GetExtendedTcpTable falhou (código {codigo}, família {familia})")
        n = struct.unpack_from("I", buf, 0)[0]
        for linha in (Linha * n).from_buffer_copy(buf, 4):
            if familia == int(socket.AF_INET):
                local = socket.inet_ntoa(struct.pack("I", linha.local))
                remoto = socket.inet_ntoa(struct.pack("I", linha.remoto))
            else:
                local = socket.inet_ntop(socket.AF_INET6, bytes(linha.local))
                remoto = socket.inet_ntop(socket.AF_INET6, bytes(linha.remoto))
            saida.append(Conexao(local, socket.ntohs(linha.porta_local & 0xFFFF), remoto, socket.ntohs(linha.porta_remota & 0xFFFF),
                                 ESTADOS_TCP.get(int(linha.estado), f"?{int(linha.estado)}"), int(linha.pid)))
    return saida


def linhas_da_porta(porta: int, ler: Callable[[], list[Conexao]] | None = None) -> list[Conexao]:
    """As linhas cuja porta LOCAL é `porta`: o(s) ouvinte(s) e, do lado do servidor, as conexões que ele aceitou."""
    ler = tabela_tcp if ler is None else ler   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    return [c for c in ler() if c.porta_local == int(porta)]


def escuta_so_em_local(porta: int, rodar=None, ler: Callable[[], list[Conexao]] | None = None, pids_do_jogo=None,
                       tentativas: int = 3, pausa_s: float = 0.5, dormir=time.sleep) -> tuple[bool, list[str]]:
    """Depois de conectar no console: a porta está protegida? Devolve (ok, detalhes).

    Aceito: (a) ouvinte só em 127.0.0.1/::1; (b) ouvinte em 0.0.0.0/:: COM regra de ENTRADA de bloqueio no Firewall do Windows ligado (o CS2
    abre o `-netconport` em todas as placas; o bloqueio vence o "Permitir" do cs2.exe e não afeta o próprio PC); (c) ouvinte já fechado e só
    a NOSSA conexão (vinda de 127.0.0.1) aceita: ninguém mais entra. Reprovado, com detalhes: ouvinte na rede sem bloqueio; conexão aceita de
    outro endereço; ouvinte de OUTRO processo na porta. `(False, [])` = nada encontrado em `tentativas` leituras (o jogo fechou ou a tabela
    falhou): quem chama decide, porque a tabela deste Windows é instável."""
    rodar = subprocess.run if rodar is None else rodar   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    ler = ler or tabela_tcp
    linhas: list[Conexao] = []
    for i in range(max(1, int(tentativas))):
        try:
            linhas = [c for c in linhas_da_porta(porta, ler) if c.ouvinte or c.estado not in FECHANDO]   # o estado do ouvinte não vale nada aqui
        except OSError:
            linhas = []
        if pids_do_jogo:
            pids = {int(p) for p in pids_do_jogo}
            intrusos = [c for c in linhas if c.ouvinte and c.pid not in pids]
            if intrusos:
                return False, [f"porta {int(porta)} em uso por outro processo (pid {c.pid}, {c.local})" for c in intrusos]
            linhas = [c for c in linhas if c.pid in pids]
        if linhas:
            break
        if i + 1 < tentativas:
            dormir(pausa_s)
    aceitas = [c for c in linhas if not c.ouvinte]
    de_fora = [c for c in aceitas if c.remoto not in LOOPBACK]
    if de_fora:
        return False, [f"conexão aceita de fora: {c.remoto}:{c.porta_remota}" for c in de_fora]
    ouvintes = sorted({c.local for c in linhas if c.ouvinte})
    if ouvintes:
        if all(e in LOOPBACK for e in ouvintes):
            return True, ouvintes
        if bloqueada_no_firewall(porta, rodar):
            return True, [f"{e} (porta bloqueada para fora no Firewall)" for e in ouvintes]
        return False, ouvintes
    if aceitas:
        return True, [f"ouvinte fechado; só a nossa conexão ({c.remoto}:{c.porta_remota}, pid {c.pid})" for c in aceitas]
    return False, []


def estado_do_firewall(porta: int, rodar=None) -> tuple[bool, str]:
    """Só leitura, pelo objeto COM `HNetCfg.FwPolicy2` (o mesmo que o `netsh` usa): funciona SEM administrador, ao contrário de
    `Get-NetFirewallPortFilter`, que responde "Acesso negado" numa sessão normal (medido em 08/11/2026 na prova real 3).
    Exige: Firewall do Windows ligado nos perfis Particular e Público (e no perfil ativo) E uma regra ligada, de ENTRADA, de BLOQUEIO, TCP,
    para essa porta, vinda de qualquer endereço, valendo nos perfis Particular e Público. Devolve (ok, explicação)."""
    rodar = subprocess.run if rodar is None else rodar   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    if sys.platform != "win32":
        return False, "só no Windows"
    porta = int(porta)
    script = (
        "$pol = New-Object -ComObject HNetCfg.FwPolicy2; "
        "$nomes = @{1='Dominio'; 2='Particular'; 4='Publico'}; "
        "$exigidos = @(2, 4); foreach ($p in 1, 2, 4) { if ((($pol.CurrentProfileTypes -band $p) -ne 0) -and ($exigidos -notcontains $p)) { $exigidos += $p } }; "
        "$desligados = @($exigidos | Where-Object { -not $pol.FirewallEnabled($_) } | ForEach-Object { $nomes[$_] }); "
        "if ($desligados.Count -gt 0) { 'PERFIL_DESLIGADO ' + ($desligados -join ',') }; "
        "$ok = @($pol.Rules | Where-Object { $_.Enabled -and ($_.Direction -eq 1) -and ($_.Action -eq 0) -and ($_.Protocol -eq 6) "
        "-and ($_.RemoteAddresses -eq '*') -and (($_.Profiles -band 6) -eq 6) -and ((\"$($_.LocalPorts)\" -split ',') -contains '" + str(porta) + "') }); "
        "if ($ok.Count -gt 0) { 'BLOQUEADO ' + $ok[0].Name } else { 'SEM_REGRA' }"
    )
    try:
        r = rodar(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, encoding="utf-8",
                  errors="replace", timeout=90, **sem_janela())
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"não consegui consultar o Firewall do Windows ({type(e).__name__})"
    linhas = [x.strip() for x in (getattr(r, "stdout", "") or "").splitlines() if x.strip()]
    desligados = [x for x in linhas if x.startswith("PERFIL_DESLIGADO")]
    if desligados:
        return False, "o Firewall do Windows está desligado no(s) perfil(is): " + desligados[0].split(" ", 1)[-1]
    bloqueado = [x for x in linhas if x.startswith("BLOQUEADO")]
    if bloqueado:
        nome = bloqueado[0].split(" ", 1)[-1].strip() or "?"
        return True, f"regra '{nome}' ativa e Firewall ligado"
    return False, f"não achei regra de entrada que bloqueie a porta {porta} (TCP) para fora, nos perfis Particular e Público"


def bloqueada_no_firewall(porta: int, rodar=None) -> bool:
    rodar = subprocess.run if rodar is None else rodar   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    return estado_do_firewall(porta, rodar)[0]
