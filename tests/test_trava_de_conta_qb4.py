"""QB4 / trava de conta: a conta logada, o jogo aberto e o Steam rodando são lidos NA HORA da chamada.

O defeito de 13/11 (commit 8c0b568, em baixar_demo.py) era `def f(conta=steam_local.conta_logada)`: o valor fica preso na definição
da função, o teste que troca `steam_local.*` não troca nada e passa/falha conforme o Steam real da máquina.
"""
import ast
from pathlib import Path

from cs2clip import gravador_demo as gd
from cs2clip import steam_local

RAIZ = Path(__file__).resolve().parent.parent
PASTAS = ("cs2clip", "ui")


def test_dependencias_reais_usa_o_que_steam_local_tem_na_hora_da_chamada(monkeypatch):
    monkeypatch.setattr(steam_local, "conta_logada", lambda: 424242)
    monkeypatch.setattr(steam_local, "jogo_aberto", lambda: ["cs2.exe-falso"])
    monkeypatch.setattr(steam_local, "steam_rodando", lambda: False)
    dep = gd.dependencias_reais()
    assert dep.conta_logada() == 424242
    assert dep.jogo_aberto() == ["cs2.exe-falso"]
    assert dep.steam_rodando() is False


def test_dependencias_reais_controle_outra_troca_vale_na_chamada_seguinte(monkeypatch):
    """Controle: trocar de novo depois muda de novo (cada chamada de `dependencias_reais()` lê o `steam_local` do momento)."""
    monkeypatch.setattr(steam_local, "conta_logada", lambda: 1)
    assert gd.dependencias_reais().conta_logada() == 1
    monkeypatch.setattr(steam_local, "conta_logada", lambda: 2)
    assert gd.dependencias_reais().conta_logada() == 2


# ── o varredor: nenhuma função pode ter `steam_local.<algo>` como valor padrão de parâmetro ────────────────────────────

def _nomes_importados_de_steam_local(arvore: ast.AST) -> set[str]:
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and (no.module or "").split(".")[-1] == "steam_local":
            nomes.update(a.asname or a.name for a in no.names)
    return nomes


def _vem_de_steam_local(valor: ast.expr, importados: set[str]) -> bool:
    if isinstance(valor, ast.Attribute):
        base = valor
        while isinstance(base, ast.Attribute):
            base = base.value
        return isinstance(base, ast.Name) and base.id == "steam_local"
    return isinstance(valor, ast.Name) and valor.id in importados


def padroes_presos_em_steam_local(codigo: str) -> list[tuple[str, int]]:
    """[(função, linha)] das funções cujo valor padrão de parâmetro vem de `steam_local` (preso na definição)."""
    arvore = ast.parse(codigo)
    importados = _nomes_importados_de_steam_local(arvore)
    achados = []
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            padroes = list(no.args.defaults) + [d for d in no.args.kw_defaults if d is not None]
            for d in padroes:
                if _vem_de_steam_local(d, importados):
                    achados.append((getattr(no, "name", "<lambda>"), d.lineno))
    return achados


def test_varredor_enxerga_o_defeito_antigo_e_deixa_passar_o_jeito_certo():
    """Controle do próprio varredor, com o código do defeito de 13/11 e com o conserto."""
    defeito = "def listar(jogo_aberto=steam_local.jogo_aberto, conta=steam_local.conta_logada):\n    pass\n"
    assert [f for f, _ in padroes_presos_em_steam_local(defeito)] == ["listar", "listar"]
    assert padroes_presos_em_steam_local("def f(*, conta=steam_local.conta_logada): pass\n")
    assert padroes_presos_em_steam_local("from .steam_local import conta_logada\ndef f(conta=conta_logada): pass\n")
    assert padroes_presos_em_steam_local("g = lambda conta=steam_local.conta_logada: conta()\n")
    certo = "def listar(jogo_aberto=None, conta=None):\n    conta = conta or steam_local.conta_logada\n"
    assert padroes_presos_em_steam_local(certo) == []


def test_nenhuma_funcao_do_programa_prende_steam_local_no_valor_padrao():
    arquivos = [p for pasta in PASTAS for p in sorted((RAIZ / pasta).rglob("*.py"))]
    assert len(arquivos) > 30          # a varredura achou os arquivos de verdade
    presos = []
    for arq in arquivos:
        for funcao, linha in padroes_presos_em_steam_local(arq.read_text(encoding="utf-8")):
            presos.append(f"{arq.relative_to(RAIZ)}:{linha} ({funcao})")
    assert presos == [], "valor padrão preso na definição (leia `steam_local.*` dentro da função): " + "; ".join(presos)


# ── B2 (14/11): o varredor também pega quem chama o mundo de fora preso na definição ─────────────────────────────────
# `rodar=subprocess.run`, `abrir=urllib.request.urlopen` ou `conta_do_bisturi=conta_do_bisturi` (função do próprio módulo) ficam presos
# na definição: um teste que troca `subprocess.run`, `urlopen` ou a função do módulo não troca nada, e o programa de verdade roda
# (o boiler no Steam real, um download de verdade). Achado da revisão de 14/11 em `listar_ultimas`.

EXTERNOS = ("subprocess.", "urllib.")


def padroes_presos(codigo: str) -> list[tuple[str, int, str]]:
    """[(função, linha, valor)] dos valores padrão presos na definição: `steam_local.*`, `subprocess.*`, `urllib.*` ou uma função do próprio módulo."""
    arvore = ast.parse(codigo)
    importados = _nomes_importados_de_steam_local(arvore)
    do_modulo = {n.name for n in arvore.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    achados = []
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for d in list(no.args.defaults) + [d for d in no.args.kw_defaults if d is not None]:
                texto = ast.unparse(d)
                if (_vem_de_steam_local(d, importados) or texto.startswith(EXTERNOS)
                        or (isinstance(d, ast.Name) and d.id in do_modulo)):
                    achados.append((getattr(no, "name", "<lambda>"), d.lineno, texto))
    return achados


def test_varredor_ampliado_enxerga_os_padroes_novos_e_deixa_passar_o_jeito_certo():
    assert [v for _, _, v in padroes_presos("import subprocess\ndef f(rodar=subprocess.run): pass\n")] == ["subprocess.run"]
    assert [v for _, _, v in padroes_presos("import urllib.request\ndef f(*, abrir=urllib.request.urlopen): pass\n")] == ["urllib.request.urlopen"]
    assert [v for _, _, v in padroes_presos("def g(): return 1\ndef f(x=g): pass\n")] == ["g"]
    assert padroes_presos("def f(conta=steam_local.conta_logada): pass\n")                         # o padrão antigo continua pego
    certo = ("import subprocess, time\nLIMITE = 5\ndef g(): pass\n"
             "def f(rodar=None, dormir=time.sleep, limite=LIMITE, imprimir=print, x=None):\n    rodar = rodar or subprocess.run\n")
    assert padroes_presos(certo) == []                                                              # CONTROLE: time.sleep, constantes e print não são o problema


def test_nenhuma_funcao_do_programa_prende_o_mundo_de_fora_no_valor_padrao():
    arquivos = [p for pasta in PASTAS for p in sorted((RAIZ / pasta).rglob("*.py"))]
    assert len(arquivos) > 30
    presos = [f"{arq.relative_to(RAIZ)}:{linha} ({funcao} = {valor})"
              for arq in arquivos for funcao, linha, valor in padroes_presos(arq.read_text(encoding="utf-8"))]
    assert presos == [], "valor padrão preso na definição (use None e leia dentro da função): " + "; ".join(presos)
