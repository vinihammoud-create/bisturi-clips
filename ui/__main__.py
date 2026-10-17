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

import uvicorn

from .servidor import app


PORTA_PADRAO = 8765


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


def abrir_navegador(server: uvicorn.Server, url: str) -> None:
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
            edge = caminho_edge()
            if edge:
                try:
                    subprocess.Popen([edge, f"--app={url}"], shell=False)  # noqa: S603
                    return
                except OSError:
                    pass
            webbrowser.open(url, new=1)
            return
        time.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bisturi Clips — interface local")
    parser.add_argument("--port", type=int, help="Porta local (8765–8775)")
    parser.add_argument("--sem-navegador", action="store_true", help="Só sobe o servidor, sem abrir a janela")
    args = parser.parse_args()
    try:
        porta = args.port if args.port is not None else porta_livre()
        if not 8765 <= porta <= 8775:
            raise ValueError("A porta precisa estar entre 8765 e 8775.")
        app.state.port = porta
        config = uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        url = f"http://127.0.0.1:{porta}"
        if not args.sem_navegador:
            threading.Thread(target=abrir_navegador, args=(server, url), daemon=True).start()
        print("Bisturi Clips — interface local")
        print(f"Endereço: {url}")
        if porta != PORTA_PADRAO:
            print(aviso_porta(porta))
        print("Esta janela mantém o servidor ativo. Pressione Ctrl+C aqui para encerrar.")
        server.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Não consegui iniciar a interface: {exc}", file=sys.stderr)
        print("Confira se as dependências foram instaladas com ui\\instalar_ui.bat.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
