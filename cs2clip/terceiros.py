"""D3 (Plano D): baixar e instalar, com segurança, os dois programas de terceiros da Parte D.

  * HLAE 2.193.0 (advancedfx, MIT): injeta o módulo de gravação no CS2 lançado por ele.
  * boiler-writter 1.7.0 (akiver, MIT): pergunta ao Game Coordinator da Valve, pela sessão do Steam já logada, as partidas e a URL das demos.
  * TwitchDownloaderCLI 1.56.5 (lay295, MIT): baixa o chat e as informações de um VOD da Twitch (opcional; o `instalar.bat` o instala por aqui).

Regras do parecer do auditor (`docs/audit/AUDITORIA_PARTE_D.md`): só a URL EXATA da release, SHA-256 FIXO NO CÓDIGO (nunca o da resposta da
internet), versão fixada (nunca atualiza sozinho), extração com proteção de caminho e de "bomba" de descompressão, nunca como administrador,
nunca o instalador `HLAE_Setup.exe`. Aqui NADA É EXECUTADO: só baixa, confere, extrai e registra em `bin\\TERCEIROS.json`.
"""
from __future__ import annotations

import ctypes
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import tools
from .tools import ROOT
from .tools import substituir_arquivo
from .tools import sem_janela

PASTA_BIN = ROOT / "bin"
REGISTRO = PASTA_BIN / "TERCEIROS.json"


@dataclass(frozen=True)
class Terceiro:
    nome: str
    versao: str
    url: str
    sha256: str
    tamanho: int
    pasta: str                 # dentro de bin\
    limite_descompactado: int  # bytes: acima disto a extração é recusada


HLAE = Terceiro(
    "HLAE", "2.193.0",
    "https://github.com/advancedfx/advancedfx/releases/download/v2.193.0/hlae_2_193_0.zip",
    "ac1f6740551cba7a72ba8437b84097d6f9a95d922ae05f333417bac6d1b746c2", 9_136_528, "hlae", 300 * 1024 * 1024)
BOILER = Terceiro(
    "boiler-writter", "1.7.0",
    "https://github.com/akiver/boiler-writter/releases/download/v1.7.0/boiler-writter-win-1.7.0.zip",
    "eda6ce361215c4c0427332e55811ffd621755b264ae7df0f5385ef5916331141", 827_218, "boiler", 50 * 1024 * 1024)
TWITCHDL_EXE = "TwitchDownloaderCLI.exe"      # o programa o procura direto em bin\ (cs2clip/chat.py, twitch_vod.py, checagem.py)
TWITCHDL = Terceiro(
    "TwitchDownloaderCLI", "1.56.5",
    "https://github.com/lay295/TwitchDownloader/releases/download/1.56.5/TwitchDownloaderCLI-1.56.5-Windows-x64.zip",
    "8b1b0695f2b1b6bf0d2535fab4b84032951cded8cf4078dfdf4d58e391c813a0", 52_226_305, "twitchdl", 200 * 1024 * 1024)
TERCEIROS = {"hlae": HLAE, "boiler": BOILER, "twitchdl": TWITCHDL}
URL_ULTIMA_HLAE = "https://api.github.com/repos/advancedfx/advancedfx/releases/latest"


class ErroTerceiros(Exception):
    """Falha de segurança ou de download (mensagem pronta, em português)."""


def rodando_como_admin() -> bool:
    """O HLAE nunca roda como administrador (parecer do auditor). Fora do Windows devolve False."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def exigir_sem_admin() -> None:
    if rodando_como_admin():
        raise ErroTerceiros("O Bisturi está rodando como administrador. O HLAE nunca deve rodar assim: feche e abra o programa normalmente.")


def sha256_do_arquivo(arq: Path) -> str:
    h = hashlib.sha256()
    with open(arq, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def baixar_zip(t: Terceiro, destino: Path, abrir=None) -> Path:
    """Baixa `t.url` (e só ela) para `destino`, confere tamanho e SHA-256 fixos e devolve o arquivo. Hash diferente = apaga e recusa."""
    abrir = urllib.request.urlopen if abrir is None else abrir   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    try:
        with abrir(urllib.request.Request(t.url, headers={"User-Agent": "BisturiClips"}), timeout=60) as r, open(parcial, "wb") as f:
            total = 0
            while True:
                bloco = r.read(1 << 20)
                if not bloco:
                    break
                total += len(bloco)
                if total > t.tamanho * 2 + (1 << 20):            # muito maior que o esperado: algo está errado
                    raise ErroTerceiros(f"{t.nome}: o arquivo baixado é grande demais; nada foi mantido.")
                f.write(bloco)
        if parcial.stat().st_size != t.tamanho:
            raise ErroTerceiros(f"{t.nome}: o tamanho do arquivo baixado ({parcial.stat().st_size}) não é o esperado ({t.tamanho}); nada foi mantido.")
        if sha256_do_arquivo(parcial) != t.sha256:
            raise ErroTerceiros(f"{t.nome}: o SHA-256 do arquivo baixado NÃO bate com o fixado no código. Nada foi instalado.")
        substituir_arquivo(parcial, destino)
        return destino
    except OSError as e:
        raise ErroTerceiros(f"{t.nome}: não consegui baixar ({e}).") from e
    finally:
        parcial.unlink(missing_ok=True)


def extrair_seguro(zip_path: Path, pasta: Path, limite_descompactado: int) -> list[str]:
    """Extrai só para dentro de `pasta`: recusa caminho que sai dela (`..`, absoluto), link simbólico e bomba de descompressão."""
    pasta = Path(pasta).resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    escritos: list[str] = []
    with zipfile.ZipFile(zip_path) as z:
        itens = z.infolist()
        if sum(i.file_size for i in itens) > limite_descompactado:
            raise ErroTerceiros("O arquivo descompactado seria grande demais (possível bomba de descompressão); nada foi extraído.")
        for i in itens:
            alvo = (pasta / i.filename).resolve()
            try:
                alvo.relative_to(pasta)
            except ValueError as e:
                raise ErroTerceiros(f"O zip tenta escrever fora da pasta ({i.filename}); nada foi extraído.") from e
            if (i.external_attr >> 28) == 0xA:              # link simbólico
                raise ErroTerceiros(f"O zip tem um link simbólico ({i.filename}); nada foi extraído.")
        for i in itens:
            alvo = (pasta / i.filename).resolve()
            if i.is_dir():
                alvo.mkdir(parents=True, exist_ok=True)
                continue
            alvo.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as origem, open(alvo, "wb") as saida:
                shutil.copyfileobj(origem, saida)
            escritos.append(i.filename)
    return escritos


def _powershell() -> str:
    """O `powershell.exe` do Windows pelo caminho completo; senão o do PATH. Com um PATH sem `WindowsPowerShell\\v1.0` o nome solto
    dava [WinError 2] e "gravar pela demo" falhava no instalador (P9, F3)."""
    raiz = os.environ.get("SystemRoot")
    if raiz:
        exe = Path(raiz) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if exe.is_file():
            return str(exe)
    return "powershell"


def assinatura_valve(dll: Path, rodar=None) -> tuple[bool, str]:
    """Confere (só leitura, `Get-AuthenticodeSignature`) que a `steam_api64.dll` do boiler é assinada pela Valve. (ok, texto)."""
    rodar = subprocess.run if rodar is None else rodar   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    if sys.platform != "win32":
        return False, "só dá para conferir a assinatura no Windows"
    # O caminho vai por variável de ambiente, NUNCA dentro do texto do comando: no modo instalado ele fica sob o nome do usuário, e um
    # apóstrofo (ou `$(...)`) nele quebraria a conferência ou viraria comando (P5.0).
    cmd = [_powershell(), "-NoProfile", "-NonInteractive", "-Command",
           "try { $s = Get-AuthenticodeSignature -LiteralPath $env:BISTURI_DLL_ASSINADA -ErrorAction Stop; "
           "Write-Output ($s.Status.ToString() + '|' + $s.SignerCertificate.Subject) } "
           "catch { Write-Output ('Erro|' + $_.Exception.Message) }"]
    # Sem PSModulePath herdado: quem abre o programa de dentro do PowerShell 7 (o runner do GitHub, por exemplo) passa os módulos do 7,
    # e o Windows PowerShell 5.1 não consegue carregar o `Microsoft.PowerShell.Security` ("module could not be loaded"). Ele recalcula o padrão.
    ambiente = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    ambiente.update({"BISTURI_DLL_ASSINADA": str(Path(dll)), "PYTHONIOENCODING": "utf-8"})
    r = rodar(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=ambiente, **sem_janela())
    saida = (r.stdout or "").strip()
    if not saida:   # o PowerShell não respondeu nada: diga o que ele reclamou, em vez de devolver texto vazio
        erro = ((getattr(r, "stderr", "") or "").strip().splitlines() or [""])[0][:160]
        return False, f"sem resposta do PowerShell (código {getattr(r, 'returncode', '?')}) {erro}".strip()
    estado, _, assunto = saida.partition("|")
    ok = estado == "Valid" and "valve" in assunto.lower()
    return ok, f"{estado}; {assunto}".strip("; ")


def _registrar(t: Terceiro, extra: dict) -> None:
    dados = {}
    if REGISTRO.is_file():
        try:
            dados = json.loads(REGISTRO.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            dados = {}
    dados[t.pasta] = {"nome": t.nome, "versao": t.versao, "url": t.url, "sha256": t.sha256,
                      "instalado_em": datetime.datetime.now().isoformat(timespec="seconds"), **extra}
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    REGISTRO.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


def dica_instalar(nome: str) -> str:
    """O que a pessoa faz quando falta o HLAE ou o boiler. No programa instalado pelo .exe não existe `python` no PATH: o caminho é o instalador."""
    from . import caminhos
    if caminhos.modo_instalado():
        return "abra o instalador do Bisturi Clips de novo e marque \"Gravar pela demo\" (ele instala o HLAE e o boiler)"
    return f"python -m cs2clip.terceiros instalar {nome}"


def caminho_hlae(bin_dir: Path | None = None) -> Path | None:
    exe = (Path(bin_dir) if bin_dir else PASTA_BIN) / HLAE.pasta / "HLAE.exe"
    return exe if exe.is_file() else None


def caminho_boiler(bin_dir: Path | None = None) -> Path | None:
    """O `boiler-writter.exe` (o zip da release o põe numa subpasta `bin`)."""
    pasta = (Path(bin_dir) if bin_dir else PASTA_BIN) / BOILER.pasta
    return next(pasta.rglob("boiler-writter.exe"), None) if pasta.is_dir() else None


def caminho_twitchdl(bin_dir: Path | None = None) -> Path | None:
    exe = (Path(bin_dir) if bin_dir else PASTA_BIN) / TWITCHDL_EXE
    return exe if exe.is_file() else None


def instalado(chave: str, bin_dir: Path | None = None) -> bool:
    achar = {"hlae": caminho_hlae, "boiler": caminho_boiler, "twitchdl": caminho_twitchdl}[chave]
    return achar(bin_dir) is not None


def _ffmpeg_do_programa() -> Path:
    """O `ffmpeg.exe` que o HLAE recebe, procurado só na hora de instalar o HLAE, nunca ao importar este módulo: o `montar.py --validar`
    lê as versões e os hashes daqui numa máquina sem FFmpeg (a do GitHub) e o import dava SystemExit (revisão final da 1.1.0, item 1)."""
    try:
        return Path(tools.FFMPEG)
    except SystemExit as e:
        raise ErroTerceiros(f"não achei o FFmpeg do programa ({e}); o HLAE precisa dele para gravar. Nada foi baixado.") from None


def instalar_hlae(bin_dir: Path | None = None, ffmpeg: str | Path | None = None, baixar=None) -> Path:
    """Baixa o HLAE fixado, confere o SHA-256, extrai em `bin\\hlae\\` e põe o `ffmpeg.exe` do projeto em `bin\\hlae\\ffmpeg\\bin\\`
    (o zip do HLAE não traz ffmpeg). Devolve a pasta. Nada é executado (por isso instalar não exige estar fora do administrador;
    RODAR o HLAE exige: `exigir_sem_admin()` é chamado pelo lançador). Sem FFmpeg recusa antes de baixar."""
    baixar = baixar_zip if baixar is None else baixar   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    ff = Path(ffmpeg) if ffmpeg else _ffmpeg_do_programa()
    bin_dir = Path(bin_dir) if bin_dir else PASTA_BIN
    bin_dir.mkdir(parents=True, exist_ok=True)
    pasta = bin_dir / HLAE.pasta
    zip_local = bin_dir / f"_{HLAE.pasta}_{HLAE.versao}.zip"
    try:
        baixar(HLAE, zip_local)
        extrair_seguro(zip_local, pasta, HLAE.limite_descompactado)
    finally:
        zip_local.unlink(missing_ok=True)
    if not (pasta / "HLAE.exe").is_file():
        raise ErroTerceiros("O HLAE foi extraído mas não achei o HLAE.exe na pasta; confira a versão.")
    destino = pasta / "ffmpeg" / "bin" / "ffmpeg.exe"
    if ff.is_file():
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ff, destino)
    _registrar(HLAE, {"ffmpeg_copiado": destino.is_file()})
    return pasta


def instalar_boiler(bin_dir: Path | None = None, baixar=None, conferir_assinatura=None) -> Path:
    """Baixa o boiler-writter fixado, confere o SHA-256, extrai em `bin\\boiler\\` e confere (só leitura) que a `steam_api64.dll` é da Valve.
    Assinatura que não é da Valve = remove a pasta e recusa."""
    baixar = baixar_zip if baixar is None else baixar   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    conferir_assinatura = assinatura_valve if conferir_assinatura is None else conferir_assinatura
    bin_dir = Path(bin_dir) if bin_dir else PASTA_BIN
    bin_dir.mkdir(parents=True, exist_ok=True)
    pasta = bin_dir / BOILER.pasta
    zip_local = bin_dir / f"_{BOILER.pasta}_{BOILER.versao}.zip"
    try:
        baixar(BOILER, zip_local)
        extrair_seguro(zip_local, pasta, BOILER.limite_descompactado)
    finally:
        zip_local.unlink(missing_ok=True)
    exe = next(pasta.rglob("boiler-writter.exe"), None)
    if exe is None:
        shutil.rmtree(pasta, ignore_errors=True)
        raise ErroTerceiros("O boiler-writter foi extraído mas não achei o boiler-writter.exe; nada foi mantido.")
    dll = next(pasta.rglob("steam_api64.dll"), None)
    if dll is not None:
        ok, texto = conferir_assinatura(dll)
        if not ok:
            shutil.rmtree(pasta, ignore_errors=True)
            raise ErroTerceiros(f"A steam_api64.dll do boiler-writter não tem assinatura válida da Valve ({texto}); nada foi mantido.")
        extra = {"steam_api64_assinatura": texto}
    else:
        extra = {"steam_api64_assinatura": "dll não encontrada"}
    _registrar(BOILER, extra)
    return exe.parent


def instalar_twitchdl(bin_dir: Path | None = None, baixar=None) -> Path:
    """Baixa o TwitchDownloaderCLI fixado (URL exata, tamanho e SHA-256 no código), extrai numa pasta temporária e põe só o
    `TwitchDownloaderCLI.exe` em `bin\\` (os avisos de licença já estão no git). Devolve o caminho do exe. Nada é executado.
    Substitui o `releases/latest` sem conferência que o `instalar.bat` usava (R-019)."""
    baixar = baixar_zip if baixar is None else baixar   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    bin_dir = Path(bin_dir) if bin_dir else PASTA_BIN
    bin_dir.mkdir(parents=True, exist_ok=True)
    zip_local = bin_dir / f"_{TWITCHDL.pasta}_{TWITCHDL.versao}.zip"
    temporaria = bin_dir / f"_{TWITCHDL.pasta}_extraido"
    shutil.rmtree(temporaria, ignore_errors=True)
    destino = bin_dir / TWITCHDL_EXE
    novo = destino.with_name(destino.name + ".novo")
    try:
        baixar(TWITCHDL, zip_local)
        extrair_seguro(zip_local, temporaria, TWITCHDL.limite_descompactado)
        exe = next(temporaria.rglob(TWITCHDL_EXE), None)
        if exe is None:
            raise ErroTerceiros(f"O {TWITCHDL.nome} foi extraído mas não achei o {TWITCHDL_EXE}; nada foi mantido.")
        shutil.copy2(exe, novo)
        substituir_arquivo(novo, destino)
    finally:
        zip_local.unlink(missing_ok=True)
        novo.unlink(missing_ok=True)
        shutil.rmtree(temporaria, ignore_errors=True)
    _registrar(TWITCHDL, {})
    return destino


def versao_nova_do_hlae(abrir=None) -> str | None:
    """Só INFORMA: a tag da última release do HLAE no GitHub (ex.: 'v2.194.0') se for diferente da fixada; None = igual ou sem rede.
    Nunca baixa nem instala."""
    abrir = urllib.request.urlopen if abrir is None else abrir   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    try:
        with abrir(urllib.request.Request(URL_ULTIMA_HLAE, headers={"User-Agent": "BisturiClips"}), timeout=15) as r:
            tag = str(json.loads(r.read().decode("utf-8")).get("tag_name", ""))
    except (OSError, ValueError):
        return None
    return tag if tag and tag.lstrip("v") != HLAE.versao else None


def main(argv: list[str] | None = None) -> int:
    """`python -m cs2clip.terceiros instalar hlae|boiler|twitchdl|tudo` | `estado` | `versao-nova`. Só baixa, confere, extrai e registra; nada é executado.
    `tudo` = HLAE e boiler-writter (gravar pela demo); o TwitchDownloaderCLI (chat da Twitch, opcional) é pedido por nome e o `instalar.bat` o pede."""
    import argparse
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="cs2clip.terceiros", description=main.__doc__)
    ap.add_argument("acao", choices=["instalar", "estado", "versao-nova"])
    ap.add_argument("alvo", nargs="?", choices=["hlae", "boiler", "twitchdl", "tudo"], default="tudo")
    a = ap.parse_args(argv)
    try:
        if a.acao == "instalar":
            if a.alvo in ("hlae", "tudo"):
                print("HLAE:", instalar_hlae())
            if a.alvo in ("boiler", "tudo"):
                print("boiler-writter:", instalar_boiler())
            if a.alvo == "twitchdl":
                print("TwitchDownloaderCLI:", instalar_twitchdl())
        elif a.acao == "estado":
            for chave, t in TERCEIROS.items():
                print(f"{t.nome} {t.versao}: {'instalado' if instalado(chave) else 'NÃO instalado'}")
        else:
            nova = versao_nova_do_hlae()
            print(f"Há versão nova do HLAE no GitHub: {nova} (a fixada é v{HLAE.versao}; nada foi baixado)." if nova else
                  f"O HLAE fixado (v{HLAE.versao}) é a última versão conhecida (ou sem rede).")
    except ErroTerceiros as e:
        print(f"[erro] {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
