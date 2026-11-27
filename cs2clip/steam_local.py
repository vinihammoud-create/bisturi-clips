"""D2 (Plano D): o Steam e o CS2 neste PC, e a GUARDA da configuração do jogo.

Tudo aqui é SÓ LEITURA: nunca escreve no Steam nem no CS2, nunca abre `config.vdf`/`loginusers.vdf` (segredos). A conta logada vem do
registro do Windows (`HKCU\\Software\\Valve\\Steam\\ActiveProcess\\ActiveUser`, o número de 32 bits da conta; 0 = ninguém/Steam fechado).

A guarda (parecer do auditor, `docs/audit/AUDITORIA_PARTE_D.md`): antes e depois de cada gravação o Bisturi tira uma FOTO dos arquivos que
o jogo usa (hash dos pequenos e críticos, tamanho e data dos grandes) e compara. Mudou algo crítico = alerta vermelho e parada.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .config import APELIDO_MAX
from .demos_cs2 import bibliotecas_steam
from .tools import sem_janela

STEAMID64_BASE = 76561197960265728
PASTA_CS2_RELATIVA = Path("steamapps") / "common" / "Counter-Strike Global Offensive"
CS2_EXE_RELATIVO = Path("game") / "bin" / "win64" / "cs2.exe"
CHAVE_ATIVA = r"Software\Valve\Steam\ActiveProcess"
CHAVE_STEAM = r"Software\Valve\Steam"

# O que o jogo escreve no uso normal e NÃO é alerta (ainda aparece na lista de "ignorados" do relatório)
IGNORADOS = ("console.log", "backup_round", "_iconcache/", "replays/", "logs/", "screenshots/", ".dem", ".dem.info", ".log")
# Arquivos pequenos e críticos: têm o hash conferido (os grandes só têm tamanho e data)
CRITICOS_CSGO = ("gameinfo.gi", "gameinfo_branchspecific.gi", "steam.inf", "cfg/")
AVISO_APENAS = ("socache.dt", "serverconfig.vdf")     # socache: cache do Steam; ServerConfig.vdf: bots, o CS2 reescreve ao abrir — só avisa


class ContaErrada(Exception):
    """A conta logada no Steam não é a do Bisturi (mensagem pronta, em português, para mostrar ao dono).
    `de_configuracao=True`: o erro está nas contas configuradas (esperar a troca no Steam não resolve)."""

    def __init__(self, mensagem: str, de_configuracao: bool = False):
        super().__init__(mensagem)
        self.de_configuracao = de_configuracao


# ── registro do Windows (isolado para os testes) ──────────────────────────────────────────────────

def _ler_registro(subchave: str, nome: str):
    """Valor do registro em HKEY_CURRENT_USER, ou None (Linux, chave ausente...)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subchave) as chave:
            return winreg.QueryValueEx(chave, nome)[0]
    except OSError:
        return None


def conta_logada() -> int:
    """accountid (32 bits) da conta logada no Steam; 0 = Steam fechado ou sem conta."""
    v = _ler_registro(CHAVE_ATIVA, "ActiveUser")
    return int(v) if isinstance(v, int) else 0


def steamid64_da_conta(accountid: int) -> int:
    return STEAMID64_BASE + int(accountid) if accountid else 0


def accountid_do_steamid64(steamid64: str | int) -> int:
    s = int(steamid64)
    return s - STEAMID64_BASE if s > STEAMID64_BASE else 0


def _pid_vivo(pid: int) -> bool:
    if pid <= 0 or sys.platform != "win32":
        return False
    r = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH", "/FO", "CSV"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **sem_janela())
    return f'"{int(pid)}"' in r.stdout


def steam_rodando() -> bool:
    """O processo do Steam está vivo (o registro guarda o pid do cliente ativo)."""
    v = _ler_registro(CHAVE_ATIVA, "pid")
    return _pid_vivo(int(v)) if isinstance(v, int) else False


def pasta_steam() -> Path | None:
    v = _ler_registro(CHAVE_STEAM, "SteamPath")
    if isinstance(v, str) and Path(v).is_dir():
        return Path(v)
    for p in (Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")):
        if p.is_dir():
            return p
    return None


def pasta_cs2(steam: Path | None = None) -> Path | None:
    """`...\\Counter-Strike Global Offensive` (a pasta do CS2, que ainda tem esse nome) na primeira biblioteca que a tem."""
    for biblioteca in bibliotecas_steam(steam or pasta_steam()):
        pasta = biblioteca / PASTA_CS2_RELATIVA
        if pasta.is_dir():
            return pasta
    return None


def cs2_exe(steam: Path | None = None) -> Path | None:
    pasta = pasta_cs2(steam)
    exe = pasta / CS2_EXE_RELATIVO if pasta else None
    return exe if exe and exe.is_file() else None


def versao_cs2(pasta: Path | None = None) -> dict[str, str]:
    """Campos do `game\\csgo\\steam.inf` (PatchVersion, VersionDate, ClientVersion...). Vazio se não achar."""
    pasta = pasta or pasta_cs2()
    if pasta is None:
        return {}
    try:
        texto = (pasta / "game" / "csgo" / "steam.inf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {k.strip(): v.strip() for k, _, v in (linha.partition("=") for linha in texto.splitlines()) if k.strip() and v.strip()}


def pasta_userdata(accountid: int, steam: Path | None = None) -> Path | None:
    s = steam or pasta_steam()
    p = s / "userdata" / str(int(accountid)) if s and accountid else None
    return p if p and p.is_dir() else None


def pasta_userdata_cs2(accountid: int, steam: Path | None = None) -> Path | None:
    """`userdata/<conta>/730`: só os dados do CS2 da conta (config de vídeo, teclas...). Nada de `userdata/<conta>/config` (do Steam)."""
    base = pasta_userdata(accountid, steam)
    p = base / "730" if base else None
    return p if p and p.is_dir() else None


# ── a guarda: foto dos arquivos antes e depois ────────────────────────────────────────────────────

@dataclass(frozen=True)
class ArquivoFoto:
    tamanho: int
    mtime_ns: int
    sha256: str | None            # só dos pequenos e críticos


@dataclass
class Foto:
    arquivos: dict[str, ArquivoFoto] = field(default_factory=dict)
    ausentes: list[str] = field(default_factory=list)      # pastas que não existiam (ex.: a conta principal sem userdata)

    def __len__(self) -> int:
        return len(self.arquivos)


@dataclass
class Diferencas:
    vermelho: list[str] = field(default_factory=list)     # alerta: parar e avisar o dono
    avisos: list[str] = field(default_factory=list)       # mudou, mas é o jogo trabalhando normalmente
    ignorados: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.vermelho

    def texto(self) -> str:
        if self.ok and not self.avisos:
            return "Nenhum arquivo do jogo ou da sua conta mudou."
        partes = [f"ALERTA: {x}" for x in self.vermelho] + [f"aviso: {x}" for x in self.avisos]
        return "\n".join(partes)


def _sha256(arq: Path) -> str:
    h = hashlib.sha256()
    with open(arq, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _eh_critico_csgo(rel: str) -> bool:
    return any(rel == c or (c.endswith("/") and rel.startswith(c)) for c in CRITICOS_CSGO)


def tirar_foto(csgo: Path | None, userdata_principal: Path | None) -> Foto:
    """Foto SÓ DE LEITURA de `game\\csgo` e da pasta do `userdata` da conta principal. Chaves: `csgo/<relativo>` e `userdata/<relativo>`."""
    foto = Foto()
    for prefixo, raiz, critico in (("csgo", csgo, _eh_critico_csgo), ("userdata", userdata_principal, lambda rel: True)):
        if raiz is None or not Path(raiz).is_dir():
            foto.ausentes.append(prefixo)
            continue
        raiz = Path(raiz)
        for dirpath, _dirs, nomes in os.walk(raiz):
            for nome in nomes:
                arq = Path(dirpath) / nome
                rel = arq.relative_to(raiz).as_posix()
                try:
                    st = arq.stat()
                    h = _sha256(arq) if critico(rel) and st.st_size <= 64 * 1024 * 1024 else None
                except OSError:
                    continue
                foto.arquivos[f"{prefixo}/{rel}"] = ArquivoFoto(st.st_size, st.st_mtime_ns, h)
    return foto


def _ignorado(rel: str) -> bool:
    baixo = rel.lower()
    return any((i.endswith("/") and f"/{i}" in f"/{baixo}") or (not i.endswith("/") and (baixo.endswith(i) or f"/{i}" in f"/{baixo}"))
               for i in IGNORADOS)


def comparar(antes: Foto, depois: Foto) -> Diferencas:
    """O que mudou entre as duas fotos. Qualquer mudança na pasta da conta principal ou em arquivo crítico do jogo é VERMELHA; arquivo novo
    ou alterado em `game\\csgo` também; só o que o jogo escreve no uso normal (`IGNORADOS`) ou o cache do Steam não é alerta."""
    d = Diferencas()
    chaves = sorted(set(antes.arquivos) | set(depois.arquivos))
    for chave in chaves:
        a, b = antes.arquivos.get(chave), depois.arquivos.get(chave)
        if a == b:
            continue
        origem, _, rel = chave.partition("/")
        if a is None:
            motivo = f"arquivo NOVO: {chave}"
        elif b is None:
            motivo = f"arquivo SUMIU: {chave}"
        elif a.sha256 is not None and b.sha256 is not None:
            if a.sha256 == b.sha256:
                continue                       # só a data mudou, o conteúdo é o mesmo
            motivo = f"conteúdo mudou: {chave}"
        else:
            motivo = f"tamanho ou data mudou: {chave}"
        if origem == "csgo" and _ignorado(rel):
            d.ignorados.append(motivo)
        elif rel.lower().endswith(AVISO_APENAS):
            d.avisos.append(motivo)
        else:
            d.vermelho.append(motivo)
    return d


# ── mensagens e bloqueio duro da conta ────────────────────────────────────────────────────────────

def apelido_limpo(apelido: object) -> str:
    """O apelido pronto para entrar numa frase: texto, sem quebra de linha nem controle, espaços aparados, no máximo 40 caracteres. Vazio se não for texto."""
    if not isinstance(apelido, str):
        return ""
    sem_controle = "".join(" " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c for c in apelido)
    return " ".join(sem_controle.split())[:APELIDO_MAX]


def rotulo_conta(qual: str, apelido: str = "", prep: str = "a", destaque: bool = False) -> str:
    """O nome de uma conta nas mensagens (função ÚNICA): "a conta do Bisturi" / "a sua conta principal", com o apelido entre parênteses quando o dono deu um.
    `prep` troca o artigo ("na", "da", "à", "A"...); `destaque` escreve PRINCIPAL em maiúsculas (aviso de segurança). É só texto: nenhuma trava olha o apelido."""
    if qual == "bisturi":
        base = "conta do Bisturi"
    elif qual == "principal":
        base = "sua conta PRINCIPAL" if destaque else "sua conta principal"
    else:
        raise ValueError(f"conta desconhecida: {qual!r}")
    nome = apelido_limpo(apelido)
    return f"{prep} {base}" + (f" ({nome})" if nome else "")


def exigir_conta_do_bisturi(conta_steamid: str, conta_principal_id: str, logada: int | None = None,
                            apelido_bisturi: str = "", apelido_principal: str = "") -> int:
    """Confere a conta logada no Steam antes de gravar. Devolve o accountid. Levanta `ContaErrada` (com o que o dono deve fazer) se:
    alguma das duas contas não está configurada (B2: sem a principal o bloqueio duro não teria contra o que comparar); o Steam está fechado;
    a conta logada é a PRINCIPAL (bloqueio duro, nunca grava com ela); ou não é a conta do Bisturi configurada."""
    ab, ap = apelido_bisturi, apelido_principal                  # só para as frases; a decisão abaixo usa apenas os accountids
    atual = conta_logada() if logada is None else int(logada)
    principal = accountid_do_steamid64(conta_principal_id) if conta_principal_id else 0
    segunda = accountid_do_steamid64(conta_steamid) if conta_steamid else 0
    if not segunda:
        raise ContaErrada(f"Falta dizer qual é {rotulo_conta('bisturi', ab)}. Diga em Configurar → Contas do Steam.", de_configuracao=True)
    if not principal:                                            # B2 (14/11): sem a principal o bloqueio duro sumia (e a do Bisturi podia ter o número dela)
        raise ContaErrada(f"Falta dizer qual é {rotulo_conta('principal', '')} (a que você joga): sem ela o Bisturi não tem como recusar gravar "
                          "com ela. Diga em Configurar → Contas do Steam.", de_configuracao=True)
    if principal == segunda:
        raise ContaErrada(f"{rotulo_conta('bisturi', ab, 'A')} configurada é igual {rotulo_conta('principal', ap, 'à')}. A gravação usa só {rotulo_conta('bisturi', ab, 'a')}.",
                          de_configuracao=True)
    if atual == 0:
        raise ContaErrada(f"O Steam está fechado ou sem conta. Abra o Steam, entre {rotulo_conta('bisturi', ab, 'na')} e clique em Continuar.")
    if atual == principal:
        raise ContaErrada(f"O Steam está {rotulo_conta('principal', ap, 'na', destaque=True)}. Por segurança o Bisturi nunca grava com ela. Saia dessa conta, "
                          f"entre {rotulo_conta('bisturi', ab, 'na')} e clique em Continuar.")
    if atual != segunda:
        raise ContaErrada(f"O Steam está numa conta que não é {rotulo_conta('bisturi', ab)}. Entre {rotulo_conta('bisturi', ab, 'na')} e clique em Continuar.")
    return atual


def jogo_aberto() -> list[str]:
    """Nomes dos processos `cs2.exe` e `obs64.exe` abertos (a gravação só roda com os dois fechados)."""
    if sys.platform != "win32":
        return []
    achados: list[str] = []
    for exe in ("cs2.exe", "obs64.exe"):
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH", "/FO", "CSV"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", **sem_janela())
        if re.search(re.escape(exe), r.stdout, re.IGNORECASE):
            achados.append(exe)
    return achados
