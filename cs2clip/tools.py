"""Localiza ffmpeg/ffprobe (pasta do projeto, pasta pai, PATH ou a pasta de atalhos do winget)."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def sem_janela(flags_extras: int = 0) -> dict[str, Any]:
    """Argumentos para `subprocess.run/Popen` de um programa de console NÃO abrir janela preta (N3, 15/11/2026).

    O app abre com o `pythonw.exe` (sem console): sem esta flag, cada `ffmpeg`/`ffprobe` chamado abre uma janela de console por alguns décimos de segundo
    ("telas pretas piscando"; medido com `tools/vigia_janelas.py`: 124 janelas de ffmpeg/ffprobe do kit em ~4,5 min de "Refazer a edição"; 126 contando as de um terceiro processo).`flags_extras` soma as flags de quem chama
    (ex.: `subprocess.CREATE_NEW_PROCESS_GROUP`): a flag própria não se perde. Fora do Windows devolve `{}`. Uso: `subprocess.run(cmd, **sem_janela())`."""
    if sys.platform != "win32":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW | flags_extras}


def grupo_novo() -> int:
    """A opção "grupo de processos novo" (`CREATE_NEW_PROCESS_GROUP`, de que o cancelamento por CTRL+BREAK depende) para somar em `sem_janela(...)`.
    Só existe no Windows: fora dele vale 0, em vez de dar `AttributeError` antes de o trabalho começar (N7, revisão final de 16/11/2026)."""
    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NEW_PROCESS_GROUP


def encerrar_arvore(proc: Any, rodar: Callable[..., Any] | None = None) -> bool:
    """Mata o processo do trabalho e TODOS os descendentes (`taskkill /PID <pid> /T /F`): o `terminate()` mata só o filho e deixa o ffmpeg (neto)
    vivo, que pode terminar de escrever um arquivo depois de o trabalho aparecer como "Cancelada" (N3b). Só pelo PID do `Popen` que o app criou,
    nunca pelo nome do programa; só se o processo ainda está vivo (o PID de um que já saiu pode ser de outro programa); nunca o próprio app.
    Devolve True se o taskkill funcionou. Fora do Windows, sem PID válido ou com erro devolve False (quem chama cai no `terminate()`)."""
    rodar = subprocess.run if rodar is None else rodar
    pid = getattr(proc, "pid", None)
    if sys.platform != "win32" or type(pid) is not int or pid <= 0 or pid == os.getpid():
        return False
    try:
        if proc.poll() is not None:
            return False
        r = rodar(["taskkill", "/PID", str(pid), "/T", "/F"], shell=False, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  timeout=10, **sem_janela())
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def cancelar_processo(proc: Any, prazo_s: float = 60.0, rodar: Callable[..., Any] | None = None) -> None:
    """Cancelar um trabalho (`python -m cs2clip.*`): primeiro o pedido educado (CTRL+BREAK; o gravador fecha o jogo e confere os arquivos antes de sair).
    Sob o `pythonw` do app (sem console) o pedido falha na hora: aí a árvore cai de uma vez. Se o pedido funcionou mas o processo não sai em `prazo_s`,
    a árvore cai no fim do prazo. Se o taskkill não der, cai no `terminate()`."""
    if proc is None:
        return
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
    except (OSError, ValueError):
        _derrubar(proc, rodar)
        return
    timer = threading.Timer(prazo_s, lambda: proc.poll() is None and _derrubar(proc, rodar))
    timer.daemon = True
    timer.start()


def _derrubar(proc: Any, rodar: Callable[..., Any] | None) -> None:
    if encerrar_arvore(proc, rodar):
        return
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass


# o instalador põe o FFmpeg pelo winget; o PATH novo só vale em janelas abertas depois, então olha aqui também
WINGET_LINKS = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"


def _find(name: str) -> str:
    for cand in (ROOT / "bin" / f"{name}.exe", ROOT.parent / f"{name}.exe"):
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    if not found and (WINGET_LINKS / f"{name}.exe").exists():
        found = str(WINGET_LINKS / f"{name}.exe")
    if not found:
        raise SystemExit(f"{name} não encontrado. Coloque {name}.exe em {ROOT / 'bin'} ou no PATH.")
    return found


# FFMPEG/FFPROBE são procurados só quando alguém os usa (não no import): sem o ffmpeg, o `--checar` ainda roda e
# mostra a lista inteira (Python, GPU, disco, módulos). Quem importa FFMPEG sem ele instalado recebe o SystemExit.
def __getattr__(nome: str) -> str:
    if nome in ("FFMPEG", "FFPROBE"):
        achado = _find(nome.lower())
        globals()[nome] = achado   # guarda: a próxima leitura não procura de novo
        return achado
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")


TENTATIVAS_TROCA = 6      # vezes que a troca do .tmp tenta antes de desistir (espera 0,05; 0,10; 0,15... s)


def substituir_arquivo(tmp: Path, destino: Path) -> None:
    """`os.replace(tmp, destino)` que tolera a recusa MOMENTÂNEA do Windows (WinError 5: antivírus/indexador segurando o arquivo por alguns ms).
    Só `PermissionError` é tentado de novo; depois de `TENTATIVAS_TROCA` o erro sobe, e o `destino` antigo fica inteiro."""
    for n in range(1, TENTATIVAS_TROCA + 1):
        try:
            os.replace(tmp, destino)
            return
        except PermissionError:
            if n == TENTATIVAS_TROCA:
                raise
            time.sleep(0.05 * n)


def gravar_atomico(arq: Path, dados: str | bytes, fsync: bool = False) -> None:
    """Grava em `<nome>.tmp` e só troca pelo nome final no fim: se o programa for interrompido no meio (Cancelar da
    interface, `taskkill`, erro), o arquivo fica inteiro (o antigo) ou não existe, nunca pela metade.

    Queda de energia é outra coisa: com `fsync=False` (o padrão, barato, bom para cache que se refaz) o sistema de
    arquivos pode gravar a troca de nome antes do conteúdo, e o arquivo final ficar vazio ou incompleto. Com
    `fsync=True` o conteúdo vai ao disco (`flush` + `os.fsync`) ANTES da troca: use em segredo e em dado que o
    usuário escolheu e não dá para refazer (R-025/M-009)."""
    arq = Path(arq)
    tmp = arq.with_name(arq.name + ".tmp")
    if not fsync:                       # o caminho de sempre, sem custo extra
        if isinstance(dados, str):
            tmp.write_text(dados, encoding="utf-8")
        else:
            tmp.write_bytes(dados)
    else:
        texto = isinstance(dados, str)
        with open(tmp, "w" if texto else "wb", encoding="utf-8" if texto else None) as f:
            f.write(dados)
            _forcar_no_disco(f)
    substituir_arquivo(tmp, arq)


def _forcar_no_disco(f: Any) -> None:
    f.flush()
    os.fsync(f.fileno())


def fechar_leitor(proc: Any, com_erro: bool = False, prazo_s: float = 5.0) -> int:
    """Fecha o ffmpeg que alimenta um laço de leitura (`stdout=PIPE`) e devolve o código de saída.
    Saída normal (fim do vídeo): fecha o pipe e espera o processo acabar, como sempre. Saída por erro (o OCR ou o
    classificador levantou no meio): sem isso o ffmpeg ficava escrevendo num pipe que ninguém lê e pendurava (R-017);
    aqui ele recebe `terminate()` e, se não sair em `prazo_s`, `kill()` (A-004). Nunca levanta."""
    try:
        if proc.stdout is not None:
            proc.stdout.close()
    except OSError:
        pass
    if com_erro and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        return proc.wait(timeout=prazo_s if com_erro else None)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            return proc.wait(timeout=prazo_s)
        except (OSError, subprocess.TimeoutExpired):
            return -1


# O OpenCV no Windows não abre caminhos com acento (ex.: "edição"): lê/grava via memória.
def imread(path, flags: int = 1):
    import cv2
    import numpy as np
    try:
        data = np.fromfile(str(path), np.uint8)
    except OSError:   # arquivo que não existe (recorte apagado): None, como um PNG vazio
        return None
    return cv2.imdecode(data, flags) if data.size else None


def imwrite(path, img, params: list[int] | None = None) -> None:
    """Grava a imagem por `.tmp` + troca (recorte interrompido não fica pela metade). Se o OpenCV não conseguir
    codificar, levanta `OSError` em vez de seguir como se o arquivo existisse (R-028)."""
    import cv2
    ext = Path(path).suffix or ".png"
    ok, buf = cv2.imencode(ext, img, params or [])
    if not ok:
        raise OSError(f"não consegui gravar a imagem {Path(path).name} (o OpenCV não codificou o quadro)")
    gravar_atomico(Path(path), buf.tobytes())
