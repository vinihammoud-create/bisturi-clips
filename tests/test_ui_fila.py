import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import ui.fila as fila_mod
from ui.fila import GerenciadorFila, construir_argumentos


@pytest.fixture(autouse=True)
def taskkill_falso(monkeypatch):
    """Os PIDs dos processos falsos podem existir de verdade no Windows (OBS, Steam, CS2...): o taskkill
    real nunca pode rodar nos testes. Aqui ele só é anotado e "falha", e o cancelamento cai no terminate()."""
    chamadas = []

    def run_falso(args, *a, **k):
        chamadas.append(list(args))
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(fila_mod.subprocess, "run", run_falso)
    return chamadas


class FakeProcess:
    _next_pid = 50000

    def __init__(self, code=0, delay=0, on_start=None, on_finish=None, saida="linha falsa do programa\nsegunda linha\n"):
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.return_code = code
        self.delay = delay
        self.started = time.monotonic()
        self.terminated = False
        self.stdout = io.StringIO(saida)
        self._on_finish = on_finish
        self._finished = False
        if on_start:
            on_start()

    def poll(self):
        if self.terminated:
            self._finish()
            return -15
        if time.monotonic() - self.started >= self.delay:
            self._finish()
            return self.return_code
        return None

    def wait(self, timeout=None):
        limite = time.monotonic() + (timeout if timeout is not None else 5)
        while time.monotonic() < limite:
            resultado = self.poll()
            if resultado is not None:
                return resultado
            time.sleep(0.01)
        raise TimeoutError()

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def _finish(self):
        if not self._finished:
            self._finished = True
            if self._on_finish:
                self._on_finish()


def aguardar(manager, predicate, segundos=4):
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_argumentos_exatos_com_espacos_acentos_opcoes_e_sem_shell(tmp_path):
    (tmp_path / "saida").mkdir()
    arquivo = tmp_path / "vídeo com espaço-á.mp4"
    arquivo.write_bytes(b"video")
    args = construir_argumentos(tmp_path, [arquivo], {"gerar_cortes": True, "gerar_shorts": False, "gerar_montagem": False})
    assert args == [
        sys.executable, "-m", "cs2clip",
        str(arquivo.resolve()), "--saida", str((tmp_path / "saida").resolve()), "--sem-shorts", "--sem-montagem"
    ]
    demo = tmp_path / "ranked demo.dem"
    demo.write_bytes(b"demo")
    args_demo = construir_argumentos(tmp_path, [demo], {"gerar_cortes": True, "jogador": "Dollar The D0G"})
    assert args_demo[-2:] == ["--jogador", "Dollar The D0G"]
    args_analise = construir_argumentos(tmp_path, [arquivo], {"gerar_cortes": False, "gerar_shorts": True, "gerar_montagem": True})
    assert args_analise[-1] == "--so-analise"
    assert "--sem-shorts" not in args_analise
    assert "--sem-montagem" not in args_analise
    with pytest.raises(ValueError):
        construir_argumentos(tmp_path, [arquivo, demo], {})


def test_fila_um_por_vez_e_codigos_0_3_1(tmp_path):
    (tmp_path / "saida").mkdir()
    (tmp_path / "entrada").mkdir()
    arquivos = [tmp_path / "um.mp4", tmp_path / "dois.mkv", tmp_path / "demo.dem"]
    for arquivo in arquivos:
        arquivo.write_bytes(b"x")
    resultados = iter([0, 3, 1])
    lock = threading.Lock()
    ativo = 0
    max_ativo = 0

    def on_start():
        nonlocal ativo, max_ativo
        with lock:
            ativo += 1
            max_ativo = max(max_ativo, ativo)

    def on_finish():
        nonlocal ativo
        with lock:
            ativo -= 1

    chamadas = []

    def factory(args, **kwargs):
        assert isinstance(args, list)
        assert kwargs["shell"] is False
        assert kwargs["cwd"] == str(tmp_path.resolve())
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        chamadas.append(args)
        saida = "  [erro] demo.dem: Diga quem é você na demo. Use --jogador \"nome\". Jogadores: Dollar (20 abates)\n" \
            if args[3].endswith(".dem") else "linha falsa do programa\n"
        return FakeProcess(next(resultados), delay=0.08, on_start=on_start, on_finish=on_finish, saida=saida)

    def builder(root, paths, options):
        return ["python-falso", "-m", "cs2clip", str(paths[0]), "--saida", str(root / "saida")]

    manager = GerenciadorFila(tmp_path, popen_factory=factory, command_builder=builder)
    try:
        jobs = manager.adicionar_varios([str(p) for p in arquivos], {"gerar_cortes": True})
        assert aguardar(manager, lambda: all(j["estado"] in {"pronto", "com_problemas", "erro", "precisa_jogador"} for j in manager.listar()["itens"]))
        estados = {j["nome"]: j["estado"] for j in manager.listar()["itens"]}
        assert estados["um.mp4"] == "pronto"
        assert estados["dois.mkv"] == "com_problemas"
        assert estados["demo.dem"] == "precisa_jogador"
        assert len(chamadas) == 3
        assert max_ativo == 1
    finally:
        manager.encerrar()


def test_cancelar_encerra_processo_e_define_mensagem(tmp_path, taskkill_falso):
    (tmp_path / "saida").mkdir()
    arquivo = tmp_path / "longo.mp4"
    arquivo.write_bytes(b"x")
    processos = []

    def factory(args, **kwargs):
        proc = FakeProcess(code=0, delay=30)
        processos.append(proc)
        return proc

    manager = GerenciadorFila(tmp_path, popen_factory=factory, command_builder=lambda *args: ["fake", "--saida"])
    try:
        job = manager.adicionar(arquivo, {})
        assert aguardar(manager, lambda: manager.listar()["ativo"] == job["id"] and len(processos) == 1)
        item = manager.cancelar(job["id"])
        assert item["estado"] == "cancelado"   # cancelar não é erro: sem etiqueta vermelha
        assert processos[0].terminated is True
        if os.name == "nt":   # foi o taskkill falso, nunca o de verdade
            assert ["taskkill", "/PID", str(processos[0].pid), "/T", "/F"] in taskkill_falso
        assert aguardar(manager, lambda: manager.listar()["ativo"] is None)
        final = next(j for j in manager.listar()["itens"] if j["id"] == job["id"])
        assert final["mensagem"] == "Cancelado."
        assert final["estado"] == "cancelado"
    finally:
        manager.encerrar()


def test_processamento_externo_ativo_bloqueia_nova_fila(tmp_path):
    (tmp_path / "saida").mkdir()
    (tmp_path / "saida" / "progresso.json").write_text(json.dumps({"estado": "rodando", "pid": os.getpid()}), encoding="utf-8")
    arquivo = tmp_path / "video.mp4"
    arquivo.write_bytes(b"x")
    manager = GerenciadorFila(tmp_path, popen_factory=lambda *a, **k: pytest.fail("não deveria iniciar"), command_builder=lambda *a: [])
    try:
        with pytest.raises(RuntimeError):
            manager.adicionar(arquivo)
    finally:
        manager.encerrar()
