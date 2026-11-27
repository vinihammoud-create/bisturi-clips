"""N7 (revisão final, 16/11/2026): os trabalhos da interface não podem depender de uma opção que só existe no Windows.

`subprocess.CREATE_NEW_PROCESS_GROUP` e `subprocess.CREATE_NO_WINDOW` não existem fora do Windows. Desde o N3, `ui/receitas.py::_rodar` e
`ui/gravacao.py::_iniciar` liam `subprocess.CREATE_NEW_PROCESS_GROUP` direto, sem proteção: no Linux (uma das máquinas do teste do GitHub) dava
`AttributeError` antes de o trabalho começar. Antes do N3 a leitura ficava dentro de `if sys.platform == "win32"`.
"""
import subprocess
import sys
import threading

import pytest

from cs2clip import tools


class _Filho:
    pid = 4321

    def __init__(self):
        self.stdout = iter(["linha\n"])

    def wait(self, *a, **k):
        return 0

    def poll(self):
        return 0


def _como_no_linux(monkeypatch):
    """Tira do `subprocess` o que só o Windows tem e diz ao programa que a plataforma é outra."""
    monkeypatch.delattr(subprocess, "CREATE_NEW_PROCESS_GROUP", raising=False)
    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")


def test_fora_do_windows_o_trabalho_da_receita_comeca_sem_a_opcao_do_windows(tmp_path, monkeypatch):
    from ui.receitas import GerenciadorReceitas
    chamadas = []
    g = GerenciadorReceitas(tmp_path / "raiz", python="PY", popen_factory=lambda cmd, **kw: (chamadas.append((cmd, kw)), _Filho())[1])
    g._cancel["x"] = threading.Event()
    _como_no_linux(monkeypatch)
    g._rodar({"id": "x"}, {}, ["cs2clip.editar_demo", "--demo", "d.dem"])            # antes: AttributeError aqui
    cmd, kw = chamadas[0]
    assert cmd[:2] == ["PY", "-m"] and "creationflags" not in kw


def test_fora_do_windows_a_gravacao_da_interface_comeca_sem_a_opcao_do_windows(tmp_path, monkeypatch):
    from ui.gravacao import GerenciadorGravacao
    chamadas = []
    g = GerenciadorGravacao(tmp_path / "raiz", popen_factory=lambda cmd, **kw: (chamadas.append((cmd, kw)), _Filho())[1])
    _como_no_linux(monkeypatch)
    g._iniciar("gravar", ["cs2clip.gravador_demo", "--x"], "teste")                  # antes: AttributeError aqui
    _, kw = chamadas[0]
    assert "creationflags" not in kw


def test_grupo_novo_vale_zero_onde_a_opcao_nao_existe(monkeypatch):
    _como_no_linux(monkeypatch)
    assert tools.grupo_novo() == 0 and tools.sem_janela(tools.grupo_novo()) == {}


@pytest.mark.skipif(sys.platform != "win32", reason="a opção só existe no Windows")
def test_no_windows_o_grupo_novo_continua_sendo_a_opcao_de_verdade_junto_da_sem_janela():
    assert tools.grupo_novo() == subprocess.CREATE_NEW_PROCESS_GROUP != 0          # o cancelamento por CTRL+BREAK depende dela
    assert tools.sem_janela(tools.grupo_novo()) == {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}


def test_nenhum_arquivo_do_programa_le_a_opcao_do_windows_sem_protecao():
    """Varredura: fora de `cs2clip/tools.py` (que protege com a plataforma), ninguém escreve `subprocess.CREATE_…` solto."""
    from pathlib import Path
    raiz = Path(__file__).resolve().parents[1]
    soltos = []
    for pasta in ("cs2clip", "ui"):
        for arq in sorted((raiz / pasta).glob("*.py")):
            if arq.name == "tools.py" and pasta == "cs2clip":
                continue
            for n, linha in enumerate(arq.read_text(encoding="utf-8").splitlines(), 1):
                codigo = linha.split("#", 1)[0]
                if "subprocess.CREATE_" in codigo and "getattr(" not in codigo and 'sys.platform == "win32"' not in codigo and "os.name" not in codigo:
                    soltos.append(f"{pasta}/{arq.name}:{n}")
    assert soltos == [], f"opção só do Windows lida sem proteção: {soltos}"
