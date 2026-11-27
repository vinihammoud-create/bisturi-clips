"""F3: toda troca "arquivo temporário vira o arquivo final" passa por `tools.substituir_arquivo`, que tolera a recusa MOMENTÂNEA do Windows
(antivírus/indexador segurando o arquivo por milissegundos; já derrubou teste sob carga e derrubaria uma gravação de verdade).

Um varredor com `ast` falha se aparecer `os.replace`, `os.rename`, `shutil.move` ou `Path.replace(destino)` fora da lista de exceções (cada uma com o motivo).
"""
import ast
import json
import os
from pathlib import Path

import pytest

from cs2clip import tools
from test_ui_dados_seguranca import criar_projeto

RAIZ = Path(__file__).resolve().parents[1]

# (arquivo, função) → por que a troca fica de fora de `substituir_arquivo`
EXCECOES = {
    ("cs2clip/tools.py", "substituir_arquivo"): "é a própria função que faz a troca com nova tentativa",
    ("cs2clip/efeitos.py", "_separar_com_ia"): "MOVE o resultado do Demucs de uma pasta temporária para o cache (pode ser outro volume: shutil.move copia se preciso); recurso opcional, fora do uso normal",
    ("ui/dados.py", "apagar_kit"): "renomeia a PASTA do kit antes de apagar: a recusa do Windows (arquivo aberto) é o sinal de parar sem apagar nada pela metade; nova tentativa aqui seria errado",
}


def _trocas_de_arquivo() -> set[tuple[str, str, int]]:
    achadas: set[tuple[str, str, int]] = set()
    for pasta in ("cs2clip", "ui", "tools"):
        for arq in sorted((RAIZ / pasta).rglob("*.py")):
            arvore = ast.parse(arq.read_text(encoding="utf-8"))
            rel = arq.relative_to(RAIZ).as_posix()

            class V(ast.NodeVisitor):
                def __init__(self):
                    self.pilha: list[str] = []

                def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
                    self.pilha.append(node.name)
                    self.generic_visit(node)
                    self.pilha.pop()

                def visit_AsyncFunctionDef(self, node):
                    self.visit_FunctionDef(node)

                def visit_Call(self, node):
                    f = node.func
                    if isinstance(f, ast.Attribute):
                        dono = f.value.id if isinstance(f.value, ast.Name) else ""
                        troca = ((dono == "os" and f.attr in {"replace", "rename"}) or (dono == "shutil" and f.attr == "move")
                                 or (f.attr in {"replace", "rename"} and len(node.args) == 1 and not node.keywords))      # Path.replace/rename(destino); str.replace tem 2 argumentos
                        if troca:
                            achadas.add((rel, self.pilha[-1] if self.pilha else "<módulo>", node.lineno))
                    self.generic_visit(node)

            V().visit(arvore)
    return achadas


def test_nenhuma_troca_de_arquivo_escapou_de_substituir_arquivo():
    fora = sorted((a, f, n) for a, f, n in _trocas_de_arquivo() if (a, f) not in EXCECOES)
    assert fora == [], f"troque por tools.substituir_arquivo (ou justifique em EXCECOES): {fora}"


def test_as_excecoes_existem_de_verdade_e_nao_ficaram_velhas():
    achadas = {(a, f) for a, f, _ in _trocas_de_arquivo()}
    assert set(EXCECOES) <= achadas, set(EXCECOES) - achadas


# ── a recusa momentânea do Windows não derruba (3 pontos de tipos diferentes: núcleo, interface e cópia de arquivo grande) ──────────────

@pytest.fixture
def recusa_antes(monkeypatch):
    real = os.replace
    estado = {"chamadas": 0, "recusar": 2}

    def falso(origem, destino):
        estado["chamadas"] += 1
        if estado["chamadas"] <= estado["recusar"]:
            raise PermissionError(5, "Acesso negado")
        return real(origem, destino)

    monkeypatch.setattr(os, "replace", falso)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return estado


def test_nucleo_progresso_grava_mesmo_com_uma_recusa_do_windows(tmp_path, recusa_antes):
    from cs2clip import progresso
    progresso.iniciar(tmp_path, 1)
    dados = json.loads((tmp_path / "progresso.json").read_text(encoding="utf-8"))
    assert dados["estado"] == "rodando" and recusa_antes["chamadas"] == 3


def test_interface_marcar_momento_grava_mesmo_com_uma_recusa_do_windows(tmp_path, recusa_antes):
    from ui.dados import DadosProjeto
    raiz = criar_projeto(tmp_path)
    DadosProjeto(raiz).marcar_momento("v1", 12.0, "aprovar", lambda: True)
    doc = json.loads((raiz / "saida" / "v1" / "momentos.json").read_text(encoding="utf-8"))
    assert doc["momentos"][0]["aprovado"] is True and recusa_antes["chamadas"] >= 3
    assert not list((raiz / "saida" / "v1").glob("momentos-*.tmp"))


def test_copia_de_demo_para_o_projeto_termina_mesmo_com_uma_recusa_do_windows(tmp_path, recusa_antes):
    from cs2clip import demos_cs2
    origem = tmp_path / "partida.dem"
    origem.write_bytes(b"DEMO" * 1000)
    copia = demos_cs2.copiar_para_projeto(origem, tmp_path / "demos")
    assert copia.read_bytes() == b"DEMO" * 1000 and not list((tmp_path / "demos").glob("*.parcial"))
    assert recusa_antes["chamadas"] == 3


def test_controle_substituir_arquivo_continua_a_mesma_funcao_da_qb4():
    assert tools.TENTATIVAS_TROCA >= 3
