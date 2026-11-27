"""N3 / telas pretas: nenhum programa de console pode abrir janela quando o app roda sem console (`pythonw.exe`).

Causa provada em 15/11/2026 com o vigia de janelas (`tools/vigia_janelas.py`): o "Refazer a edição" rodava `python -m cs2clip.editar_demo` com
`sys.executable`, que no app do dono é o `pythonw.exe` (sem console). Esse filho, sem console, abria uma janela preta a cada `ffmpeg`/`ffprobe`
(126 janelas em 4,5 min). O conserto: `tools.sem_janela()` em TODA chamada de `subprocess` + o filho do trabalho roda com o `python.exe` (console escondido).
"""
from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

from cs2clip import tools

RAIZ = Path(__file__).resolve().parent.parent
PASTAS = ("cs2clip", "ui")
CHAMADAS = {"run", "Popen", "check_output", "check_call", "call"}
MARCA_JANELA_PROPRIA = "janela-propria"       # comentário na linha da chamada: programa que PRECISA da própria janela (HLAE/CS2, navegador, Explorer)


# ── o auxiliar ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="creationflags só existe no Windows")
def test_sem_janela_no_windows_pede_create_no_window():
    assert tools.sem_janela() == {"creationflags": subprocess.CREATE_NO_WINDOW}


@pytest.mark.skipif(sys.platform != "win32", reason="creationflags só existe no Windows")
def test_sem_janela_combina_com_as_flags_de_quem_chama():
    esperado = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    assert tools.sem_janela(subprocess.CREATE_NEW_PROCESS_GROUP) == {"creationflags": esperado}


def test_sem_janela_fora_do_windows_nao_devolve_nada(monkeypatch):
    monkeypatch.setattr(tools.sys, "platform", "linux")
    assert tools.sem_janela() == {}
    assert tools.sem_janela(512) == {}


def test_sem_janela_funciona_de_verdade_num_subprocess():
    r = subprocess.run([sys.executable, "-c", "print('oi')"], capture_output=True, text=True, **tools.sem_janela())
    assert r.returncode == 0 and r.stdout.strip() == "oi"


# ── o varredor: nenhuma chamada de subprocess sem o auxiliar ────────────────────────────────────────────────
# N3b: o varredor entende apelidos (`import subprocess as sp`, `from subprocess import run`), `getattr`, `os.system`/`os.popen`, funções passadas
# como valor, `asyncio.create_subprocess_*`, a marca só como COMENTÁRIO na linha da chamada e o `sem_janela` só se for o de `cs2clip.tools`.

AUXILIAR = "cs2clip.tools.sem_janela"
ARQUIVO_DO_AUXILIAR = "cs2clip/tools.py"          # aí o `sem_janela` é definido (não importado)
ACEITAM_FLAG = {"subprocess.run", "subprocess.Popen", "subprocess.check_output", "subprocess.check_call", "subprocess.call",
                "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}
NAO_ACEITAM_FLAG = {"subprocess.getoutput", "subprocess.getstatusoutput", "os.system", "os.popen"}
MODULOS_DE_PROCESSO = {"subprocess", "os", "asyncio"}
EXECUTORES = {"submit", "map", "starmap", "apply", "apply_async", "run_in_executor", "to_thread", "partial", "Thread", "Timer", "Process"}


def _pontilhado(no: ast.AST) -> str | None:
    partes: list[str] = []
    while isinstance(no, ast.Attribute):
        partes.append(no.attr)
        no = no.value
    if isinstance(no, ast.Name):
        partes.append(no.id)
        return ".".join(reversed(partes))
    return None


def _apelidos(arvore: ast.AST, arquivo: str) -> dict[str, str]:
    """nome local → nome completo (`sp` → `subprocess`, `run` → `subprocess.run`, `tools` → `cs2clip.tools`). Nome redefinido no arquivo (def, classe,
    atribuição, parâmetro) perde o apelido: um `sem_janela()` próprio não é o auxiliar."""
    pacote = arquivo.split("/")[0]
    ap: dict[str, str] = {}
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for a in no.names:
                if a.asname:
                    ap[a.asname] = a.name
                else:
                    ap[a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(no, ast.ImportFrom):
            base = no.module or ""
            if no.level:                                                       # `.tools` dentro de cs2clip/ é cs2clip.tools; em ui/ seria ui.tools
                base = pacote + ("." + base if base else "")
            for a in no.names:
                ap[a.asname or a.name] = f"{base}.{a.name}" if base else a.name
    locais: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            locais.add(no.name)
        elif isinstance(no, ast.Name) and isinstance(no.ctx, ast.Store):
            locais.add(no.id)
        elif isinstance(no, ast.arg):
            locais.add(no.arg)
    for nome in locais:
        ap.pop(nome, None)
    if arquivo == ARQUIVO_DO_AUXILIAR:
        ap["sem_janela"] = AUXILIAR
    return ap


def _resolve(no: ast.AST, ap: dict[str, str]) -> str | None:
    d = _pontilhado(no)
    if d is None:
        return None
    cabeca, _, resto = d.partition(".")
    return ap.get(cabeca, cabeca) + ("." + resto if resto else "")


def _perigosa(nome: str | None) -> str | None:
    """'pode' = aceita `creationflags` (exige `**sem_janela()`); 'nunca' = não aceita flags (sempre reprovado, só com a marca)."""
    if nome is None:
        return None
    if nome in ACEITAM_FLAG:
        return "pode"
    if nome in NAO_ACEITAM_FLAG or re.fullmatch(r"os\.(spawn\w*|exec\w*|posix_spawn\w*)", nome):
        return "nunca"
    return None


def _usa_sem_janela(chamada: ast.Call, ap: dict[str, str]) -> bool:
    for kw in chamada.keywords:
        if kw.arg is None and isinstance(kw.value, ast.Call) and _resolve(kw.value.func, ap) == AUXILIAR:     # **sem_janela(...) importado de cs2clip.tools
            return True
    return False


def _linhas_com_a_marca(codigo: str) -> set[int]:
    """Linhas que têm a marca COMO COMENTÁRIO (a palavra dentro de uma string ou de um argumento não vale)."""
    achadas: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(codigo).readline):
        if tok.type == tokenize.COMMENT and MARCA_JANELA_PROPRIA in tok.string:
            achadas.add(tok.start[0])
    return achadas


def _dentro_do_valor_de_atribuicao(no: ast.AST, pais: dict[ast.AST, ast.AST]) -> list[ast.AST] | None:
    """Os alvos da atribuição (`rodar = subprocess.run if …`, `self._popen = f or subprocess.Popen`) se `no` está no valor dela."""
    atual = no
    while atual in pais:
        pai = pais[atual]
        if isinstance(pai, (ast.Assign, ast.AnnAssign)) and atual is pai.value:
            return list(pai.targets) if isinstance(pai, ast.Assign) else [pai.target]
        if isinstance(pai, ast.stmt):
            return None
        atual = pai
    return None


def _nome_do_alvo(alvo: ast.AST) -> str | None:
    return alvo.id if isinstance(alvo, ast.Name) else alvo.attr if isinstance(alvo, ast.Attribute) else None


def chamadas_sem_o_auxiliar(codigo: str, arquivo: str = "cs2clip/x.py") -> list[tuple[int, str]]:
    """[(linha, texto)] de tudo que pode abrir janela de console: chamada de `subprocess` (ou de apelido/`from subprocess import`/nome que a guarda)
    sem `**sem_janela()` de `cs2clip.tools`, `os.system`/`os.popen`/`os.spawn*`, `getattr(subprocess, …)`, `asyncio.create_subprocess_*` sem o auxiliar, e
    `subprocess.run` passado como valor. Só a marca `janela-propria` como COMENTÁRIO na(s) linha(s) da chamada isenta."""
    arvore = ast.parse(codigo)
    linhas = codigo.splitlines()
    ap = _apelidos(arvore, arquivo)
    marcadas = _linhas_com_a_marca(codigo)
    pais = {filho: no for no in ast.walk(arvore) for filho in ast.iter_child_nodes(no)}
    achados: dict[int, str] = {}

    def reprova(no: ast.AST) -> None:
        inicio, fim = no.lineno, (getattr(no, "end_lineno", None) or no.lineno)      # type: ignore[attr-defined]
        if not any(n in marcadas for n in range(inicio, fim + 1)):
            achados.setdefault(inicio, linhas[inicio - 1].strip())

    guardados: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and any(a.name == "*" for a in no.names) and (no.module or "").split(".")[0] in MODULOS_DE_PROCESSO:
            reprova(no)                                                                  # `from subprocess import *`: some o rastro do nome
        if isinstance(no, (ast.Attribute, ast.Name)) and isinstance(no.ctx, ast.Load) and not isinstance(pais.get(no), ast.Attribute):
            tipo = _perigosa(_resolve(no, ap))
            if tipo is None:
                continue
            pai = pais.get(no)
            if isinstance(pai, ast.Call) and pai.func is no:                           # a chamada em si
                if tipo == "nunca" or not _usa_sem_janela(pai, ap):
                    reprova(pai)
                continue
            alvos = _dentro_do_valor_de_atribuicao(no, pais)
            if alvos is not None:                                                       # `rodar = subprocess.run if …`: quem CHAMA `rodar` é conferido abaixo
                guardados.update(n for n in map(_nome_do_alvo, alvos) if n)
            else:
                reprova(no)                                                             # passada como valor (submit, map, argumento, retorno, valor padrão…)
        if isinstance(no, ast.Call):
            f = no.func
            if isinstance(f, ast.Name) and f.id == "getattr" and no.args and (_resolve(no.args[0], ap) or "").split(".")[0] in MODULOS_DE_PROCESSO:
                reprova(no)                                                             # getattr(subprocess, "run")(…): o nome some do olho do varredor
            if _resolve(f, ap) in ("__import__", "importlib.import_module"):
                primeiro = no.args[0] if no.args else None
                if not (isinstance(primeiro, ast.Constant) and isinstance(primeiro.value, str) and primeiro.value.split(".")[0] not in MODULOS_DE_PROCESSO):
                    reprova(no)
    for no in ast.walk(arvore):                                                         # quem chama (ou repassa) um nome que guarda o subprocess
        if isinstance(no, ast.Call) and _nome_do_alvo(no.func) in guardados and not _usa_sem_janela(no, ap):
            reprova(no)
        elif isinstance(no, (ast.Name, ast.Attribute)) and isinstance(no.ctx, ast.Load) and _nome_do_alvo(no) in guardados:
            pai = pais.get(no)
            if isinstance(pai, ast.Call) and pai.func is no:
                continue                                                                # é a chamada em si (conferida acima)
            if isinstance(pai, ast.keyword):
                pai = pais.get(pai)                                                     # `Thread(target=rodar)`; já `rodar=rodar` (repasse a uma função nossa) é normal
                if not (isinstance(pai, ast.Call) and _nome_do_alvo(pai.func) in EXECUTORES):
                    continue
            if isinstance(pai, ast.Call) and pai.func is not no and _nome_do_alvo(pai.func) not in EXECUTORES:
                continue                                                                # `bloqueada(porta, rodar)`: a função nossa aplica o auxiliar na chamada dela
            if isinstance(pai, (ast.Call, ast.Return, ast.Lambda, ast.Tuple, ast.List, ast.Dict)):
                reprova(no)                                                             # `ex.submit(rodar, …)`, `return rodar`
    return sorted(achados.items())


def _linhas_reprovadas(codigo: str, arquivo: str = "cs2clip/x.py") -> list[int]:
    return [n for n, _ in chamadas_sem_o_auxiliar(codigo, arquivo)]


IMPORTA = "import subprocess\nfrom cs2clip.tools import sem_janela\n"           # as duas linhas que o código de verdade tem no topo


def test_varredor_pega_chamada_sem_o_auxiliar_e_aceita_a_certa():
    ruim = "import subprocess\nsubprocess.run(['x'])\nsubprocess.Popen(['y'], creationflags=0)\nsubprocess.check_output(['z'])\n"
    assert _linhas_reprovadas(ruim) == [2, 3, 4]
    bom = ("import subprocess\nfrom cs2clip import tools\nfrom cs2clip.tools import sem_janela\nsubprocess.run(['x'], **sem_janela())\n"
           "subprocess.Popen(['y'], **tools.sem_janela(subprocess.CREATE_NEW_PROCESS_GROUP))\n")
    assert chamadas_sem_o_auxiliar(bom) == []
    propria = "import subprocess\nsubprocess.Popen(['hlae'])   # janela-propria: o HLAE precisa da janela\n"
    assert chamadas_sem_o_auxiliar(propria) == []


def test_varredor_pega_o_nome_que_guarda_subprocess_run():
    ruim = IMPORTA + "def f(rodar=None):\n    rodar = subprocess.run if rodar is None else rodar\n    return rodar(['x'], capture_output=True)\n"
    assert _linhas_reprovadas(ruim) == [5]
    bom = IMPORTA + "def f(rodar=None):\n    rodar = subprocess.run if rodar is None else rodar\n    return rodar(['x'], **sem_janela())\n"
    assert chamadas_sem_o_auxiliar(bom) == []


def test_varredor_pega_a_fabrica_de_processos_guardada_no_objeto():
    ruim = (IMPORTA + "class G:\n    def __init__(self, popen_factory=None):\n        self._popen = popen_factory or subprocess.Popen\n"
            "    def r(self):\n        return self._popen(['x'], cwd='.')\n")
    assert _linhas_reprovadas(ruim) == [7]
    bom = ruim.replace("cwd='.')", "cwd='.', **sem_janela(subprocess.CREATE_NEW_PROCESS_GROUP))")
    assert chamadas_sem_o_auxiliar(bom) == []


def test_varredor_pega_chamada_em_varias_linhas():
    ruim = IMPORTA + "subprocess.run(\n    ['x'],\n    capture_output=True,\n)\n"
    assert _linhas_reprovadas(ruim) == [3]
    bom = IMPORTA + "subprocess.run(\n    ['x'],\n    **sem_janela(),\n)\n"
    assert chamadas_sem_o_auxiliar(bom) == []


# N3b: as variações que o QA do N3 achou furadas. Cada uma é reprovada, e a versão certa de cada uma passa.

def test_varredor_pega_from_subprocess_import():
    assert _linhas_reprovadas("from subprocess import run\nrun(['x'])\n") == [2]
    assert _linhas_reprovadas("from subprocess import Popen as P\nP(['x'])\n") == [2]
    assert _linhas_reprovadas("from subprocess import check_output, call\ncall(['x'])\ncheck_output(['y'])\n") == [2, 3]
    assert _linhas_reprovadas("from subprocess import *\nrun(['x'])\n") == [1]
    assert chamadas_sem_o_auxiliar("from subprocess import run\nfrom cs2clip.tools import sem_janela\nrun(['x'], **sem_janela())\n") == []


def test_varredor_pega_import_subprocess_como_apelido():
    assert _linhas_reprovadas("import subprocess as sp\nsp.run(['x'])\n") == [2]
    assert _linhas_reprovadas("import subprocess as sp\nx = sp.Popen\nx(['y'])\n") == [3]
    assert chamadas_sem_o_auxiliar("import subprocess as sp\nfrom cs2clip.tools import sem_janela\nsp.run(['x'], **sem_janela())\n") == []


def test_varredor_pega_getattr_no_modulo():
    assert _linhas_reprovadas("import subprocess\ngetattr(subprocess, 'run')(['x'])\n") == [2]
    assert _linhas_reprovadas("import subprocess\nnome = 'run'\ngetattr(subprocess, nome)(['x'])\n") == [3]
    assert _linhas_reprovadas("import subprocess as sp\nf = getattr(sp, 'Popen')\n") == [2]
    assert _linhas_reprovadas("import os\ngetattr(os, 'system')('dir')\n") == [2]
    assert chamadas_sem_o_auxiliar("x = getattr(objeto, 'run')\n") == []                          # getattr em outra coisa é normal


def test_varredor_pega_os_system_e_os_popen():
    assert _linhas_reprovadas("import os\nos.system('dir')\n") == [2]
    assert _linhas_reprovadas("import os\nos.popen('dir')\n") == [2]
    assert _linhas_reprovadas("from os import system\nsystem('dir')\n") == [2]
    assert _linhas_reprovadas("import os as o\no.popen('dir')\n") == [2]
    assert _linhas_reprovadas("import os\nos.spawnl(os.P_WAIT, 'x', 'x')\n") == [2]
    assert _linhas_reprovadas("import subprocess\nsubprocess.getoutput('dir')\n") == [2]
    assert chamadas_sem_o_auxiliar("import os\nos.system('dir')   # janela-propria: o atalho abre a própria janela\n") == []
    assert chamadas_sem_o_auxiliar("import os\nos.startfile('x')\nos.getpid()\nos.path.join('a')\n") == []


def test_varredor_pega_funcao_passada_como_valor():
    assert _linhas_reprovadas("import subprocess\nex.submit(subprocess.run, ['x'])\n") == [2]
    assert _linhas_reprovadas("import subprocess\nlist(map(subprocess.check_output, [['x']]))\n") == [2]
    assert _linhas_reprovadas("import subprocess\ndef f(rodar=subprocess.run):\n    return rodar(['x'])\n") == [2]
    assert _linhas_reprovadas("import subprocess\ndef f():\n    return subprocess.Popen\n") == [3]
    assert _linhas_reprovadas("import subprocess\nG(popen_factory=subprocess.Popen)\n") == [2]
    assert _linhas_reprovadas("from subprocess import run\nex.submit(run, ['x'])\n") == [2]
    ruim = IMPORTA + "rodar = subprocess.run\nex.submit(rodar, ['x'])\n"
    assert _linhas_reprovadas(ruim) == [4]
    assert chamadas_sem_o_auxiliar("import subprocess\nx = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.PIPE\nsubprocess.TimeoutExpired\n") == []   # constantes e erros são normais


def test_varredor_pega_asyncio_create_subprocess():
    assert _linhas_reprovadas("import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('x')\n") == [3]
    assert _linhas_reprovadas("import asyncio\nasync def f():\n    await asyncio.create_subprocess_shell('x')\n") == [3]
    assert _linhas_reprovadas("from asyncio import create_subprocess_exec\nasync def f():\n    await create_subprocess_exec('x')\n") == [3]
    certo = "import asyncio\nfrom cs2clip.tools import sem_janela\nasync def f():\n    await asyncio.create_subprocess_exec('x', **sem_janela())\n"
    assert chamadas_sem_o_auxiliar(certo) == []
    assert chamadas_sem_o_auxiliar("import asyncio\nasync def f():\n    await asyncio.to_thread(len, [])\n    await asyncio.sleep(1)\n") == []


def test_varredor_pega_import_dinamico():
    assert _linhas_reprovadas("m = __import__('subprocess')\n") == [1]
    assert _linhas_reprovadas("import importlib\nm = importlib.import_module('os')\n") == [2]
    assert _linhas_reprovadas("import importlib\nm = importlib.import_module(nome)\n") == [2]
    assert chamadas_sem_o_auxiliar("import importlib\nm = importlib.import_module('numpy')\n") == []


def test_marca_janela_propria_so_vale_como_comentario_na_linha_da_chamada():
    assert chamadas_sem_o_auxiliar("import subprocess\nsubprocess.Popen(['hlae'])  # janela-propria: o HLAE tem janela\n") == []
    assert chamadas_sem_o_auxiliar("import subprocess\nsubprocess.Popen(\n    ['hlae'],\n)  # janela-propria: o HLAE tem janela\n") == []
    assert _linhas_reprovadas("import subprocess\nsubprocess.Popen(['janela-propria'])\n") == [2]                         # dentro de uma string
    assert _linhas_reprovadas("import subprocess\nsubprocess.Popen(['x'], cwd='janela-propria')\n") == [2]                # em outro argumento
    assert _linhas_reprovadas("import subprocess\nsubprocess.Popen(['x', 'y # janela-propria'])\n") == [2]                # '#' dentro da string não é comentário
    assert _linhas_reprovadas("import subprocess\n# janela-propria\nsubprocess.Popen(['x'])\n") == [3]                    # comentário de OUTRA linha
    assert _linhas_reprovadas("import subprocess\nsubprocess.Popen(['x'])\nx = 1  # janela-propria\n") == [2]
    assert _linhas_reprovadas("import subprocess\njanela_propria = 'janela-propria'\nsubprocess.Popen([janela_propria])\n") == [3]
    assert _linhas_reprovadas('import subprocess\n"""janela-propria"""\nsubprocess.Popen([\'x\'])\n') == [3]


def test_sem_janela_so_vale_o_importado_de_cs2clip_tools():
    chamada = "subprocess.run(['x'], **sem_janela())\n"
    assert _linhas_reprovadas("import subprocess\n" + chamada) == [2]                                                    # nem importado nem definido
    assert _linhas_reprovadas("import subprocess\ndef sem_janela():\n    return {}\n" + chamada) == [4]                  # um falso local
    assert _linhas_reprovadas("import subprocess\nsem_janela = lambda: {}\n" + chamada) == [3]
    assert _linhas_reprovadas("import subprocess\nfrom cs2clip.tools import sem_janela\ndef sem_janela():\n    return {}\n" + chamada) == [5]   # redefinido depois
    assert _linhas_reprovadas("import subprocess\nfrom outro import sem_janela\n" + chamada) == [3]                     # importado de outro lugar
    assert _linhas_reprovadas("import subprocess\nfrom cs2clip.outra import sem_janela\n" + chamada) == [3]
    assert _linhas_reprovadas("import subprocess\nimport tools\nsubprocess.run(['x'], **tools.sem_janela())\n") == [3]   # `tools` qualquer
    assert _linhas_reprovadas("import subprocess\nsubprocess.run(['x'], **outra.sem_janela())\n") == [2]
    assert _linhas_reprovadas("import subprocess\nfrom cs2clip.tools import sem_janela\ndef f(sem_janela):\n    subprocess.run(['x'], **sem_janela())\n") == [4]
    for certo in ("from cs2clip.tools import sem_janela\n" + chamada,
                  "from cs2clip.tools import sem_janela as sj\nsubprocess.run(['x'], **sj())\n",
                  "from cs2clip import tools\nsubprocess.run(['x'], **tools.sem_janela())\n",
                  "import cs2clip.tools\nsubprocess.run(['x'], **cs2clip.tools.sem_janela())\n",
                  "import cs2clip.tools as t\nsubprocess.run(['x'], **t.sem_janela())\n",
                  "from .tools import sem_janela\n" + chamada):
        assert chamadas_sem_o_auxiliar("import subprocess\n" + certo) == [], certo
    assert _linhas_reprovadas("import subprocess\nfrom .tools import sem_janela\n" + chamada, "ui/x.py") == [3]            # em ui/ o `.tools` seria outro módulo
    assert chamadas_sem_o_auxiliar("import subprocess\nfrom cs2clip.tools import sem_janela\n" + chamada, "ui/x.py") == []


def test_so_o_proprio_cs2clip_tools_define_o_sem_janela():
    definicao = "import subprocess\ndef sem_janela(extra=0):\n    return {}\nsubprocess.run(['x'], **sem_janela())\n"
    assert chamadas_sem_o_auxiliar(definicao, "cs2clip/tools.py") == []
    assert _linhas_reprovadas(definicao, "cs2clip/outro.py") == [4]
    assert _linhas_reprovadas(definicao, "ui/tools.py") == [4]


def test_varredor_pega_a_chamada_nova_do_taskkill():
    """O taskkill do cancelamento (N3b) também passa pelo varredor: sem o auxiliar abriria uma janela preta."""
    assert _linhas_reprovadas("import subprocess\nsubprocess.run(['taskkill', '/PID', '1', '/T', '/F'])\n") == [2]


def test_nenhuma_chamada_de_subprocess_do_programa_abre_janela():
    arquivos = [p for pasta in PASTAS for p in sorted((RAIZ / pasta).rglob("*.py"))]
    assert len(arquivos) > 30
    ruins = []
    for p in arquivos:
        relativo = p.relative_to(RAIZ).as_posix()
        for n, texto in chamadas_sem_o_auxiliar(p.read_text(encoding="utf-8"), relativo):
            ruins.append(f"{relativo}:{n}: {texto}")
    assert ruins == [], "chamada de subprocess sem `**sem_janela()` (abre janela preta quando o app roda com pythonw):\n" + "\n".join(ruins)


# ── o filho dos trabalhos: python.exe (console escondido) e flags combinadas ────────────────────────────────

class _Filho:
    def __init__(self):
        self.stdout = iter(["linha\n"])

    def wait(self, *a, **k):
        return 0

    def poll(self):
        return 0


def _python_do_app_sem_console(tmp_path, monkeypatch) -> Path:
    """Imita o app aberto pelo atalho: `sys.executable` é um pythonw.exe que tem um python.exe do lado."""
    (tmp_path / "pythonw.exe").write_bytes(b"")
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    return tmp_path / "python.exe"


def test_trabalho_da_receita_roda_com_python_de_console_e_sem_janela(tmp_path, monkeypatch):
    import threading

    from ui.receitas import GerenciadorReceitas
    console = _python_do_app_sem_console(tmp_path, monkeypatch)
    chamadas = []
    g = GerenciadorReceitas(tmp_path / "raiz", popen_factory=lambda cmd, **kw: (chamadas.append((cmd, kw)), _Filho())[1])
    assert g._python == str(console)                       # pythonw como filho = uma janela preta por ffmpeg (provado em 15/11)
    g._cancel["x"] = threading.Event()
    g._rodar({"id": "x"}, {}, ["cs2clip.editar_demo", "--demo", "d.dem"])
    cmd, kw = chamadas[0]
    assert cmd[0] == str(console)
    if sys.platform == "win32":
        assert kw["creationflags"] == subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


def test_python_passado_de_fora_continua_valendo(tmp_path, monkeypatch):
    from ui.receitas import GerenciadorReceitas
    _python_do_app_sem_console(tmp_path, monkeypatch)
    assert GerenciadorReceitas(tmp_path / "raiz", python="PY")._python == "PY"


def test_gravacao_da_interface_roda_com_python_de_console_e_sem_janela(tmp_path, monkeypatch):
    from ui.gravacao import GerenciadorGravacao
    console = _python_do_app_sem_console(tmp_path, monkeypatch)
    chamadas = []
    g = GerenciadorGravacao(tmp_path / "raiz", popen_factory=lambda cmd, **kw: (chamadas.append((cmd, kw)), _Filho())[1])
    assert g._python == str(console)
    g._iniciar("gravar", ["cs2clip.gravador_demo", "--x"], "teste")
    cmd, kw = chamadas[0]
    assert cmd[0] == str(console)
    if sys.platform == "win32":
        assert kw["creationflags"] == subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


def test_varredor_nao_aceita_outro_auxiliar_nem_dicionario_qualquer():
    assert [n for n, _ in chamadas_sem_o_auxiliar("subprocess.run(['x'], **outra_coisa())\n")] == [1]
    assert [n for n, _ in chamadas_sem_o_auxiliar("subprocess.run(['x'], **kwargs)\n")] == [1]
