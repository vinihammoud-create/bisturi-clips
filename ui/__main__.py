"""Inicialização local: python -m ui [--port PORTA]."""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PORTA_PADRAO = 8765
SEM_JANELA_S = 20          # a janela foi fechada e não voltou: encerra depois disto (F5 volta em ~1 s)
OCIOSO_S = 180             # a página sumiu sem avisar (ex.: o Edge foi morto): encerra depois disto
LOG_MAX = 1_000_000        # bytes: acima disto o ui.log recomeça
SALTO_RELOGIO_S = 30       # volta do vigia demorou mais que isto: o PC dormiu, não é página sumida


# Aberto pelo atalho (pythonw.exe): não há janela preta e sys.stdout/sys.stderr são None. Medido AQUI, antes de
# preparar_log() trocar os dois por um arquivo (depois disso eles deixam de ser None).
_SEM_CONSOLE = sys.stdout is None or sys.stderr is None


def sem_console() -> bool:
    return _SEM_CONSOLE


def avisar(texto: str) -> None:
    """Sem janela preta, um erro de início precisa aparecer de outro jeito: caixa de aviso do Windows."""
    print(texto, file=sys.stderr)
    if sem_console() or os.environ.get("BISTURI_AVISO_JANELA"):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, texto, "Bisturi Clips", 0x10)   # type: ignore[attr-defined]
        except Exception:   # noqa: BLE001  (sem Windows/ctypes: o texto já foi para o log)
            pass


def preparar_log() -> Path | None:
    """Sem console, tudo o que o programa escreve vai para `saida/ui.log` (e o uvicorn não quebra por falta de stderr)."""
    if not sem_console():
        return None
    pasta = RAIZ / "saida"
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        arq = pasta / "ui.log"
        modo = "w" if arq.exists() and arq.stat().st_size > LOG_MAX else "a"
        fluxo = open(arq, modo, encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        fluxo = open(os.devnull, "w", encoding="utf-8")
        arq = None
    if sys.stdout is None:
        sys.stdout = fluxo
    if sys.stderr is None:
        sys.stderr = fluxo
    print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    return arq


try:
    import uvicorn

    from .servidor import app
except ImportError as _erro:   # dependências da interface não instaladas
    avisar(f"Não consegui abrir o Bisturi Clips: falta instalar algo ({_erro}).\n\n"
           "Dê dois cliques em instalar.bat na pasta do programa.")
    raise SystemExit(1)


def aviso_porta(porta: int) -> str:
    # As respostas da página Revisão ficam guardadas no navegador junto com a porta.
    return (f"Atenção: a porta {PORTA_PADRAO} estava ocupada e a interface abriu na {porta}. As respostas da página "
            f"Revisão que ainda não viraram CSV ficaram na porta {PORTA_PADRAO}: feche o que a ocupa e abra de novo para vê-las.")


def porta_livre() -> int:
    for porta in range(8765, 8776):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", porta))
            except OSError:
                continue
            return porta
    raise RuntimeError("Não encontrei uma porta livre entre 8765 e 8775.")


def portas_ocupadas() -> list[int]:
    """Portas 8765-8775 onde já há algo escutando. Só estas valem a pena testar: no Windows, bater numa porta
    fechada demora ~1 s (medido), e testar as 11 seguidas travaria a abertura por mais de 10 s."""
    ocupadas = []
    for porta in range(8765, 8776):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", porta))
            except OSError:
                ocupadas.append(porta)
    return ocupadas


def instancia_ativa() -> str | None:
    """Endereço de um servidor do Bisturi Clips que já está rodando (a janela do app foi fechada, a preta não), ou None."""
    for porta in portas_ocupadas():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{porta}/health", timeout=1) as resposta:
                if resposta.status == 200 and b'"ok"' in resposta.read(200):
                    return f"http://127.0.0.1:{porta}"
        except Exception:
            continue
    return None


def caminho_edge() -> str | None:
    """O msedge.exe quase nunca está no PATH (o Windows o acha pelo 'App Paths', que o Popen não consulta)."""
    achado = shutil.which("msedge")
    if achado:
        return achado
    for variavel in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(variavel)
        if base:
            candidato = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidato.is_file():
                return str(candidato)
    return None


def abrir_janela(url: str) -> None:
    """Abre a interface no Edge em modo aplicativo; sem Edge, no navegador padrão."""
    edge = caminho_edge()
    if edge:
        try:
            subprocess.Popen([edge, f"--app={url}"], shell=False)  # noqa: S603
            return
        except OSError:
            pass
    webbrowser.open(url, new=1)


def abrir_navegador(server: "uvicorn.Server", url: str) -> None:
    prazo = time.monotonic() + 30
    while time.monotonic() < prazo and not server.should_exit:
        if server.started:
            try:
                with urllib.request.urlopen(f"{url}/health", timeout=2) as resposta:
                    if resposta.status != 200:
                        time.sleep(0.2)
                        continue
            except Exception:
                time.sleep(0.2)
                continue
            abrir_janela(url)
            return
        time.sleep(0.2)


def vigia_encerramento(server: "uvicorn.Server", estado, agora=time.monotonic, intervalo: float = 2.0) -> None:
    """Sem a janela preta ninguém aperta Ctrl+C: o programa encerra sozinho quando a janela do app é fechada.
    Nunca encerra enquanto há processamento (a fila termina primeiro) nem durante os primeiros segundos."""
    fila = estado.fila
    anterior = agora()
    while not server.should_exit:
        time.sleep(intervalo)
        t = agora()
        if t - anterior > SALTO_RELOGIO_S:   # o PC dormiu/travou: a página não teve tempo de falar; recomeça a contagem
            estado.ultima_atividade = t
            if estado.janela_fechada_em is not None:
                estado.janela_fechada_em = t
        anterior = t
        if estado.pedir_saida:
            server.should_exit = True
            return
        if fila.esta_processando():
            estado.ultima_atividade = t           # o processamento segura o programa; o relógio recomeça no fim dele
            continue
        fechada = estado.janela_fechada_em
        # relatório/revisão abertos à parte ainda tocando vídeo seguram o programa: a contagem recomeça no último trecho
        parado_desde = max(fechada, getattr(estado, "ultima_midia", 0.0)) if fechada is not None else None
        if (parado_desde is not None and t - parado_desde >= SEM_JANELA_S) or t - estado.ultima_atividade >= OCIOSO_S:
            server.should_exit = True
            return


def main() -> int:
    preparar_log()
    parser = argparse.ArgumentParser(description="Bisturi Clips — interface local")
    parser.add_argument("--port", type=int, help="Porta local (8765–8775)")
    parser.add_argument("--sem-navegador", action="store_true", help="Só sobe o servidor, sem abrir a janela")
    parser.add_argument("--manter", action="store_true", help="Não encerra sozinho quando a janela do app é fechada")
    args = parser.parse_args()
    try:
        # Já há um servidor do programa rodando (a pessoa fechou só a janela do app): abre a janela nele e sai,
        # em vez de subir um 2º servidor com outra fila numa porta diferente.
        if args.port is None:
            ativa = instancia_ativa()
            if ativa:
                if not args.sem_navegador:
                    abrir_janela(ativa)
                print(f"O Bisturi Clips já está rodando em {ativa}. Abri a janela de novo.")
                return 0
        porta = args.port if args.port is not None else porta_livre()
        if not 8765 <= porta <= 8775:
            raise ValueError("A porta precisa estar entre 8765 e 8775.")
        app.state.port = porta
        config = uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        url = f"http://127.0.0.1:{porta}"
        if not args.sem_navegador:
            threading.Thread(target=abrir_navegador, args=(server, url), daemon=True).start()
            if not args.manter:
                threading.Thread(target=vigia_encerramento, args=(server, app.state), daemon=True).start()
        print("Bisturi Clips — interface local")
        print(f"Endereço: {url}")
        if porta != PORTA_PADRAO:
            print(aviso_porta(porta))
        if not sem_console():
            print("Fechar a janela do app encerra o programa. Ctrl+C aqui também encerra.")
        server.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        avisar(f"Não consegui iniciar a interface: {exc}\n\nConfira se o programa foi instalado (instalar.bat na pasta "
               "do programa). Detalhes em saida\\ui.log.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
