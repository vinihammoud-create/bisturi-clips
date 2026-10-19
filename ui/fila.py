"""Fila serial de processamento; nunca inicia dois processos ao mesmo tempo."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .seguranca import EXTENSOES_ENTRADA


VIDEO_EXTS = EXTENSOES_ENTRADA - {".dem"}


def _pid_vivo_windows(pid: int) -> bool:
    """No Windows `os.kill(pid, 0)` NÃO testa o processo: o sinal 0 é CTRL_C_EVENT. Medido: dava "vivo" para um
    processo que já tinha terminado. Aqui se pergunta ao Windows pelo código de saída (259 = ainda rodando)."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    manipulador = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not manipulador:
        return ctypes.get_last_error() == 5                  # 5 = acesso negado: o processo existe
    try:
        codigo = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(manipulador, ctypes.byref(codigo)):
            return False
        return codigo.value == 259                           # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(manipulador)


def pid_vivo(pid: Any) -> bool:
    try:
        pid_int = int(pid)
        if pid_int <= 0:
            return False
        if os.name == "nt":
            return _pid_vivo_windows(pid_int)
        os.kill(pid_int, 0)
        return True
    except (OSError, TypeError, ValueError, OverflowError):
        return False


def _jogadores_da_saida(texto: str) -> str:
    """Lista 'Jogadores: ...' que o núcleo imprime quando não acha o nome (para mostrar abaixo do campo)."""
    linhas = [ln for ln in texto.splitlines() if "Jogadores:" in ln]
    if not linhas:
        return ""
    return linhas[-1].split("Jogadores:", 1)[1].strip()[:400]


def construir_argumentos(
    raiz: Path,
    arquivos: list[Path],
    opcoes: dict[str, Any],
    python_exe: Path | None = None,
) -> list[str]:
    if not arquivos:
        raise ValueError("Escolha pelo menos um arquivo.")
    tipos = {"demo" if p.suffix.lower() == ".dem" else "video" for p in arquivos}
    if len(tipos) != 1:
        raise ValueError("Não misture vídeos e demos na mesma execução. Coloque-os na fila separadamente.")
    for arquivo in arquivos:
        if arquivo.suffix.lower() not in EXTENSOES_ENTRADA:
            raise ValueError(f"Tipo de arquivo não permitido: {arquivo.name}")
        if not arquivo.is_file():
            raise FileNotFoundError(f"Não encontrei o arquivo {arquivo.name}. Escolha-o novamente.")

    raiz = raiz.resolve()
    # O mesmo Python que já rodou a interface (e a checagem): serve para .venv, Python global ou outra pasta.
    python = python_exe or Path(sys.executable)
    argumentos = [str(python), "-m", "cs2clip"]
    argumentos.extend(str(arquivo.resolve()) for arquivo in arquivos)
    argumentos.extend(["--saida", str((raiz / "saida").resolve())])
    if not bool(opcoes.get("gerar_cortes", True)):
        argumentos.append("--so-analise")
    else:
        if not bool(opcoes.get("gerar_shorts", True)):
            argumentos.append("--sem-shorts")
        if not bool(opcoes.get("gerar_montagem", True)):
            argumentos.append("--sem-montagem")
    if bool(opcoes.get("forcar_gpu", False)) and tipos == {"video"}:
        argumentos.append("--forcar-gpu")
    jogador = opcoes.get("jogador")
    if tipos == {"demo"} and isinstance(jogador, str) and jogador.strip():
        nome = jogador.strip()
        if len(nome) > 60 or any(ord(c) < 32 for c in nome):
            raise ValueError("O nome do jogador precisa ter até 60 caracteres.")
        argumentos.extend(["--jogador", nome])
    return argumentos


class GerenciadorFila:
    def __init__(self, raiz: Path, popen_factory: Callable[..., Any] | None = None, command_builder: Callable[..., list[str]] | None = None):
        self.raiz = raiz.resolve()
        self.saida = self.raiz / "saida"
        self.saida.mkdir(parents=True, exist_ok=True)
        self.popen_factory = popen_factory or subprocess.Popen
        self.command_builder = command_builder or construir_argumentos
        self._jobs: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._active_id: str | None = None
        self._active_process: Any = None
        self._external_block = self._detectar_processo_externo()
        self._closing = False
        self._session = threading.Thread(target=self._worker, name="cs2clip-ui-fila", daemon=True)
        self._session.start()

    def _ler_progresso(self) -> dict[str, Any]:
        try:
            caminho = self.saida / "progresso.json"
            resolvido = caminho.resolve(strict=False)
            resolvido.relative_to(self.saida.resolve())
            with resolvido.open("r", encoding="utf-8-sig") as arquivo:
                dados = json.load(arquivo)
            return dados if isinstance(dados, dict) else {}
        except (OSError, json.JSONDecodeError, UnicodeError):
            return {}

    def _progresso_do_job(self, job: dict[str, Any]) -> dict[str, Any]:
        """O progresso.json só vale para o job se foi gravado por uma execução iniciada depois dele começar.
        Até o núcleo escrever o seu (imports, config...), o arquivo ainda é o da execução anterior: nome do
        vídeo velho, barra cheia. Compara iniciado_em (o pid do venv no Windows não é o do Popen)."""
        progresso = self._ler_progresso()
        inicio = job.get("iniciado_em")
        if not progresso or not inicio:
            return {}
        try:
            gravado = datetime.fromisoformat(str(progresso.get("iniciado_em"))).timestamp()
        except (ValueError, TypeError, OverflowError, OSError):
            return {}
        return progresso if gravado >= inicio - 1 else {}   # -1 s: o núcleo grava o horário sem fração

    def _detectar_processo_externo(self) -> dict[str, Any] | None:
        progresso = self._ler_progresso()
        if progresso.get("estado") == "rodando" and pid_vivo(progresso.get("pid")):
            return {"externo": True, "pid": progresso.get("pid"), "progresso": progresso, "desde": time.time()}
        return None

    def _externo_vivo(self) -> bool:
        with self._lock:
            atual = self._external_block
            ativo = self._active_id
        if not atual:
            if ativo is not None:
                return False
            detectado = self._detectar_processo_externo()
            if detectado is None:
                return False
            with self._lock:
                self._external_block = detectado
                atual = detectado
        progresso = self._ler_progresso()
        if progresso.get("estado") == "rodando" and pid_vivo(progresso.get("pid")):
            with self._lock:
                if self._external_block is not None:
                    self._external_block["progresso"] = progresso
            return True
        with self._lock:
            self._external_block = None
        return False

    def adicionar(self, arquivo: str | Path, opcoes: dict[str, Any] | None = None) -> dict[str, Any]:
        caminho = Path(arquivo).expanduser()
        if not caminho.is_file():
            raise FileNotFoundError("Não encontrei esse arquivo. Escolha-o novamente.")
        if caminho.suffix.lower() not in EXTENSOES_ENTRADA:
            raise ValueError("Esse tipo de arquivo não é aceito.")
        job = {
            "id": uuid.uuid4().hex[:12],
            "arquivo": str(caminho.resolve()),
            "nome": caminho.name,
            "tipo": "demo" if caminho.suffix.lower() == ".dem" else "video",
            "opcoes": dict(opcoes or {}),
            "estado": "na_fila",
            "mensagem": "Aguardando sua vez.",
            "criado_em": time.time(),
            "iniciado_em": None,
            "terminado_em": None,
            "codigo_saida": None,
            "detalhes": [],
            "progresso": {},
            "precisa_jogador": False,
        }
        with self._condition:
            # _externo_vivo relê o progresso.json: libera a fila assim que o processamento de fora terminar.
            if self._externo_vivo():
                raise RuntimeError("Já existe um processamento iniciado fora da interface. Espere terminar.")
            self._jobs.append(job)
            self._condition.notify_all()
        return self._public_job(job)

    def adicionar_varios(self, arquivos: list[str | Path], opcoes: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not isinstance(arquivos, list) or not arquivos:
            raise ValueError("Escolha pelo menos um arquivo.")
        caminhos = [Path(p).expanduser() for p in arquivos]
        for caminho in caminhos:
            if not caminho.is_file():
                raise FileNotFoundError(f"Não encontrei {caminho.name}. Escolha-o novamente.")
            if caminho.suffix.lower() not in EXTENSOES_ENTRADA:
                raise ValueError(f"Esse tipo de arquivo não é aceito: {caminho.name}")
        # Um job por arquivo evita misturar demos e vídeos e mantém cancelamento individual.
        return [self.adicionar(caminho, opcoes) for caminho in caminhos]

    @staticmethod
    def _public_job(job: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in job.items() if k not in {"opcoes", "criado_em"}}

    def listar(self) -> dict[str, Any]:
        if self._external_block:
            self._externo_vivo()   # o processamento de fora pode ter terminado: não mostrar o bloqueio velho
        with self._lock:
            jobs = [self._public_job(j) for j in self._jobs]
            ativo = self._active_id
            externo = self._external_block.copy() if self._external_block else None
            if ativo:
                para = next((j for j in self._jobs if j["id"] == ativo), None)
            else:
                para = None
        progresso = self._ler_progresso()
        if para is not None:
            para_publico = next((j for j in jobs if j["id"] == ativo), None)
            if para_publico is not None:
                para_publico["progresso"] = self._progresso_do_job(para)
                para_publico["detalhes"] = list(para["detalhes"])
        return {"itens": jobs, "ativo": ativo, "progresso": progresso, "externo": externo}

    def esta_processando(self, video_id: str | None = None) -> bool:
        # O contrato proíbe editar momentos enquanto qualquer processamento da fila roda.
        with self._lock:
            if self._active_id is not None:
                return True
        return self._externo_vivo()

    def _tem_job_aguardando(self) -> bool:
        return any(j["estado"] == "na_fila" for j in self._jobs)

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._closing and (not self._tem_job_aguardando() or self._externo_vivo()):
                    self._condition.wait(timeout=0.5)
                if self._closing:
                    return
                job = next((j for j in self._jobs if j["estado"] == "na_fila"), None)
                if job is None:
                    continue
                job["estado"] = "rodando"
                job["mensagem"] = "Processando…"
                job["iniciado_em"] = time.time()
                self._active_id = job["id"]
            try:
                self._executar_job(job)
            finally:
                with self._condition:
                    if self._active_id == job["id"]:
                        self._active_id = None
                        self._active_process = None
                    self._condition.notify_all()

    def _executar_job(self, job: dict[str, Any]) -> None:
        arquivo = Path(job["arquivo"])
        try:
            args = self.command_builder(self.raiz, [arquivo], job["opcoes"])
        except Exception as exc:
            with self._lock:
                job["estado"] = "erro"
                job["mensagem"] = "Não consegui preparar o processamento. Confira o arquivo e as opções."
                job["detalhes"] = [str(exc)]
                job["terminado_em"] = time.time()
            return
        ambiente = os.environ.copy()
        ambiente["PYTHONIOENCODING"] = "utf-8"
        kwargs: dict[str, Any] = {
            "cwd": str(self.raiz), "env": ambiente, "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT, "text": True, "encoding": "utf-8",
            "errors": "replace", "bufsize": 1, "shell": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            processo = self.popen_factory(args, **kwargs)
        except Exception as exc:
            with self._lock:
                job["estado"] = "erro"
                job["mensagem"] = "Não consegui iniciar o processamento. Confira se a interface foi instalada."
                job["detalhes"] = [str(exc)]
                job["terminado_em"] = time.time()
            return
        with self._lock:
            self._active_process = processo
            job["pid"] = getattr(processo, "pid", None)

        buffer: deque[str] = deque(maxlen=200)
        leitura_pronta = threading.Event()

        def ler_saida() -> None:
            try:
                stream = getattr(processo, "stdout", None)
                if stream is not None:
                    for linha in iter(stream.readline, ""):
                        buffer.append(linha.rstrip("\r\n"))
            except Exception as exc:
                buffer.append(f"Não consegui ler os detalhes: {exc}")
            finally:
                leitura_pronta.set()

        leitor = threading.Thread(target=ler_saida, name=f"cs2clip-stdout-{job['id']}", daemon=True)
        leitor.start()
        codigo: int | None = None
        cancelado = False
        while codigo is None:
            with self._lock:
                cancelado = job.get("cancelando", False)
                job["detalhes"] = list(buffer)
                job["progresso"] = self._progresso_do_job(job)
                pid = getattr(processo, "pid", None)
            if cancelado:
                self._encerrar_processo(processo, pid)
                try:
                    codigo = processo.wait(timeout=5)
                except Exception:
                    codigo = -1
                break
            try:
                codigo = processo.poll()
            except Exception:
                codigo = -1
                break
            if codigo is None:
                time.sleep(0.25)
        try:
            leitor.join(timeout=1)
        except RuntimeError:
            pass
        with self._lock:
            job["detalhes"] = list(buffer)
            job["codigo_saida"] = codigo
            job["terminado_em"] = time.time()
            job.pop("cancelando", None)
            if cancelado:
                job["estado"] = "cancelado"
                job["mensagem"] = "Cancelado."
                progresso_path = self.saida / "progresso.json"
                try:
                    if progresso_path.is_symlink():
                        raise OSError("link simbólico não permitido")
                    resolvido = progresso_path.resolve(strict=False)
                    resolvido.relative_to(self.saida.resolve())
                    progresso = self._ler_progresso()
                    progresso.update({"formato": progresso.get("formato", 1), "estado": "erro", "mensagem": "Cancelado"})
                    resolvido.write_text(json.dumps(progresso, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                except (OSError, ValueError):
                    pass
            elif codigo == 0:
                job["estado"] = "pronto"
                job["mensagem"] = "Pronto!"
            elif codigo == 3:
                job["estado"] = "com_problemas"
                job["mensagem"] = "Terminou com problemas. Confira o registro de problemas."
            elif codigo == 1:
                # Só pede o nome quando o programa disse que faltou ou que o nome não bate (vale também
                # para nome digitado errado); demo corrompida cai no erro comum.
                texto = "\n".join(job["detalhes"])
                if job["tipo"] == "demo" and ("Diga quem é você" in texto or "Não achei o jogador" in texto):
                    job["estado"] = "precisa_jogador"
                    job["precisa_jogador"] = True
                    job["jogadores"] = _jogadores_da_saida(texto)
                    if "Não achei o jogador" in texto:
                        job["mensagem"] = "Não achei esse nome na demo. Confira como ele aparece no jogo e tente de novo."
                    else:
                        job["mensagem"] = "Não consegui identificar seu nome na demo. Digite seu nome no jogo e tente de novo."
                else:
                    job["estado"] = "erro"
                    job["mensagem"] = "Não deu certo. Confira a configuração e o registro de problemas."
            else:
                job["estado"] = "erro"
                job["mensagem"] = f"O processamento terminou com um código inesperado ({codigo})."

    @staticmethod
    def _encerrar_processo(processo: Any, pid: Any) -> None:
        if os.name == "nt" and pid:
            try:
                resultado = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], shell=False, check=False,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                if resultado.returncode == 0:
                    return
                # o taskkill não conseguiu (processo já saiu, acesso negado...): tenta o jeito do Python
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            processo.terminate()
            processo.wait(timeout=3)
        except Exception:
            try:
                processo.kill()
            except Exception:
                pass

    def cancelar(self, job_id: str) -> dict[str, Any]:
        with self._condition:
            job = next((j for j in self._jobs if j["id"] == job_id), None)
            if job is None:
                raise FileNotFoundError("Esse item não está na fila.")
            if job["estado"] == "na_fila":
                job["estado"] = "cancelado"
                job["mensagem"] = "Cancelado antes de começar."
                job["terminado_em"] = time.time()
            elif job["estado"] == "rodando":
                job["cancelando"] = True
                processo = self._active_process
                if processo is not None:
                    self._encerrar_processo(processo, getattr(processo, "pid", None))
                job["estado"] = "cancelado"
                job["mensagem"] = "Cancelado."
            else:
                raise ValueError("Esse item já terminou.")
            self._condition.notify_all()
            return self._public_job(job)

    def cancelar_tudo(self) -> dict[str, Any]:
        with self._condition:
            ids = [j["id"] for j in self._jobs if j["estado"] == "na_fila"]
            for job in self._jobs:
                if job["estado"] == "na_fila":
                    job["estado"] = "cancelado"
                    job["mensagem"] = "Cancelado."
                    job["terminado_em"] = time.time()
                elif job["estado"] == "rodando":
                    job["cancelando"] = True
            processo = self._active_process
            pid = getattr(processo, "pid", None) if processo is not None else None
            self._condition.notify_all()
        if processo is not None:
            self._encerrar_processo(processo, pid)
        return {"sucesso": True, "cancelados": len(ids) + (1 if processo is not None else 0)}

    def informar_jogador(self, job_id: str, jogador: str) -> dict[str, Any]:
        if not isinstance(jogador, str) or not jogador.strip() or len(jogador.strip()) > 60 or any(ord(c) < 32 for c in jogador):
            raise ValueError("Digite seu nome no jogo (até 60 caracteres).")
        with self._condition:
            job = next((j for j in self._jobs if j["id"] == job_id), None)
            if job is None or job["estado"] != "precisa_jogador":
                raise ValueError("Esse item não está aguardando o nome do jogador.")
            job["opcoes"]["jogador"] = jogador.strip()
            job["precisa_jogador"] = False
            job.pop("jogadores", None)
            job["estado"] = "na_fila"
            job["mensagem"] = "Nome informado. Aguardando nova tentativa."
            job["codigo_saida"] = None
            job["detalhes"] = []
            job.pop("pid", None)
            self._condition.notify_all()
            return self._public_job(job)

    def esperar_ocioso(self, timeout: float = 3.0) -> bool:
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            with self._lock:
                pendente = any(j["estado"] in {"na_fila", "rodando"} for j in self._jobs)
            if not pendente:
                return True
            time.sleep(0.02)
        return False

    def encerrar(self) -> None:
        with self._condition:
            self._closing = True
            processo = self._active_process
            pid = getattr(processo, "pid", None) if processo is not None else None
            if processo is not None:
                for job in self._jobs:
                    if job["estado"] == "rodando":
                        job["cancelando"] = True
            self._condition.notify_all()
        if processo is not None:
            self._encerrar_processo(processo, pid)
        self._session.join(timeout=3)
