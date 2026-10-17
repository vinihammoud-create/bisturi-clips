"""Checagem do ambiente (`python -m cs2clip --checar`) e trava de GPU.

Trava de GPU: com o CS2 ou o OBS abertos, a placa de vídeo é do jogo/da live. Nesse caso o programa só roda a
**prévia**: não transcreve (Whisper) nem renderiza; usa as falas do cache, se existirem. `--forcar-gpu` ignora a
trava (decisão do usuário). Isso protege os FPS do jogo e a live (regra do projeto)."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# processos que usam a GPU de verdade enquanto o usuário joga ou transmite
PROCESSOS_GPU = {"cs2.exe": "o CS2", "obs64.exe": "o OBS"}
DISCO_MIN_GB = 5.0
DISCO_AVISO_GB = 20.0
VRAM_LIVRE_MIN_MB = 2500


@dataclass
class Item:
    nome: str
    ok: bool
    detalhe: str
    grave: bool = True            # grave = o programa não consegue trabalhar; não grave = aviso/opcional
    como_resolver: str = ""


def processos_ativos() -> set[str]:
    """Nomes (minúsculos) dos programas abertos. Vazio se não for Windows ou der erro."""
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return set()
    nomes = set()
    for linha in r.stdout.splitlines():
        partes = linha.strip().strip('"').split('","')
        if partes and partes[0]:
            nomes.add(partes[0].lower())
    return nomes


def gpu_ocupada(ativos: set[str] | None = None) -> list[str]:
    """Quem está usando a GPU agora, em português ("o CS2", "o OBS"); vazio = livre."""
    ativos = processos_ativos() if ativos is None else {a.lower() for a in ativos}
    return [rotulo for exe, rotulo in PROCESSOS_GPU.items() if exe in ativos]


def aviso_trava(quem: list[str]) -> str:
    um = len(quem) == 1
    return (f"{' e '.join(quem)} {'está' if um else 'estão'} aberto: a placa de vídeo é de {'ele' if um else 'eles'}. "
            "Rodando só a PRÉVIA (sem transcrever e sem renderizar; usa as falas já guardadas, se houver). "
            "Feche o jogo/OBS para o processamento completo, ou use --forcar-gpu se tiver certeza.")


def _versao(exe: str) -> str:
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=15)
        return (r.stdout.splitlines() or [""])[0][:60]
    except (OSError, subprocess.SubprocessError):
        return ""


def _vram() -> tuple[str, int, int] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--query-gpu=name,memory.free,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        nome, livre, total = [x.strip() for x in r.stdout.splitlines()[0].split(",")]
        return nome, int(livre), int(total)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _tem_modulo(nome: str) -> bool:
    try:
        return importlib.util.find_spec(nome) is not None
    except (ImportError, ValueError):
        return False


def _cs_demo_manager() -> bool:
    """O CS Demo Manager está instalado? (só é preciso para GRAVAR jogadas de demo)"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False
    for raiz in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            chave = winreg.OpenKey(raiz, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(chave)[0]):
            try:
                sub = winreg.OpenKey(chave, winreg.EnumKey(chave, i))
                nome = winreg.QueryValueEx(sub, "DisplayName")[0]
            except OSError:
                continue
            if "demo manager" in str(nome).lower():
                return True
    return False


def checar(raiz: Path, saida: Path | None = None, ativos: set[str] | None = None) -> list[Item]:
    itens: list[Item] = []
    ok_py = sys.version_info >= (3, 11)
    itens.append(Item("Python", ok_py, sys.version.split()[0] if ok_py else f"{sys.version.split()[0]} (precisa 3.11+)",
                      True, "Rode instalar.bat (instala o Python 3.12)."))
    try:
        from .tools import FFMPEG, FFPROBE   # sem o ffmpeg isto levanta SystemExit: vira item reprovado abaixo
        from . import render
        v = _versao(FFMPEG)
        itens.append(Item("FFmpeg", bool(v), v or "não abriu", True, "Rode instalar.bat (instala o FFmpeg)."))
        vp = _versao(FFPROBE)
        itens.append(Item("FFprobe", bool(vp), vp or "não abriu", True, "Vem junto com o FFmpeg: rode instalar.bat."))
        nv = render._has_nvenc()
        itens.append(Item("Codificação na GPU (NVENC)", nv,
                          "funciona" if nv else "indisponível: os cortes sairão em x264 (CPU), mais lentos",
                          False, "Atualize o driver da NVIDIA."))
    except SystemExit as e:
        itens.append(Item("FFmpeg", False, str(e), True, "Rode instalar.bat (instala o FFmpeg)."))

    g = _vram()
    if g:
        nome, livre, total = g
        itens.append(Item("Placa de vídeo", livre >= VRAM_LIVRE_MIN_MB, f"{nome}: {livre} de {total} MB livres",
                          False, "Feche programas que usam a placa de vídeo (jogo, OBS, navegador com vídeo)."))
    else:
        itens.append(Item("Placa de vídeo", False, "não achei a NVIDIA (nvidia-smi): a transcrição usará a CPU (lenta)",
                          False, "Instale o driver da NVIDIA."))

    alvo = Path(saida or raiz / "saida")
    base = alvo if alvo.exists() else raiz
    livre_gb = shutil.disk_usage(base).free / 1e9
    pouco = " (pouco: cada vídeo gera vários GB)" if livre_gb < DISCO_AVISO_GB else ""
    itens.append(Item("Espaço em disco", livre_gb >= DISCO_MIN_GB, f"{livre_gb:.0f} GB livres{pouco}",
                      livre_gb < DISCO_MIN_GB, "Libere espaço no disco."))

    for mod, nome, obrigatorio in (("cv2", "OpenCV", True), ("numpy", "NumPy", True),
                                   ("faster_whisper", "Whisper (falas)", True),
                                   ("rapidocr_onnxruntime", "Leitor do banner de round", True),
                                   ("demoparser2", "Leitor de demos (.dem)", False)):
        ok = _tem_modulo(mod)
        itens.append(Item(nome, ok, "instalado" if ok else "falta instalar", obrigatorio,
                          "Rode instalar.bat." if obrigatorio else "Só é preciso para demos: pip install demoparser2==0.42.0"))
    twitch = (raiz / "bin" / "TwitchDownloaderCLI.exe").exists()
    itens.append(Item("Chat da Twitch (TwitchDownloaderCLI)", twitch,
                      "encontrado" if twitch else "não está em bin\\ (sem o chat, só voz e falas contam)", False,
                      "Opcional: coloque TwitchDownloaderCLI.exe na pasta bin."))

    quem = gpu_ocupada(ativos)
    itens.append(Item("CS2 e OBS fechados", not quem,
                      "fechados: GPU livre para o processamento completo" if not quem
                      else f"{' e '.join(quem)} aberto: só a prévia roda", False,
                      "Feche o jogo/OBS para o processamento completo (ou use --forcar-gpu)."))
    cdm = _cs_demo_manager()
    itens.append(Item("CS Demo Manager", cdm,
                      "instalado" if cdm else "não instalado (só é preciso para gravar jogadas de demo)",
                      False, "Opcional: instale quando formos gravar demos."))
    return itens


def texto(itens: list[Item]) -> str:
    linhas = ["Checagem do ambiente", ""]
    for i in itens:
        marca = "OK" if i.ok else ("ERRO" if i.grave else "AVISO")
        linhas.append(f"  [{marca:5}] {i.nome}: {i.detalhe}")
        if not i.ok and i.como_resolver:
            linhas.append(f"           → {i.como_resolver}")
    graves = [i for i in itens if not i.ok and i.grave]
    avisos = [i for i in itens if not i.ok and not i.grave]
    linhas.append("")
    linhas.append("Tudo certo para trabalhar." if not graves
                  else f"{len(graves)} problema(s) grave(s): resolva antes de processar.")
    if avisos and not graves:
        linhas.append(f"({len(avisos)} aviso(s): não impedem o trabalho.)")
    return "\n".join(linhas)


def tem_problema_grave(itens: list[Item]) -> bool:
    return any(not i.ok and i.grave for i in itens)
