"""P6 (publicação): testes do `instalador/notas_da_versao.py` (extrai do CHANGELOG.md a seção da tag). Usa só CHANGELOG de exemplo em `tmp_path`."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


def _carregar(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


notas = _carregar("notas_da_versao_p6", RAIZ / "instalador" / "notas_da_versao.py")
ErroNotas = notas.ErroNotas

CHANGELOG = """# Mudanças

Todas as mudanças do projeto.

## [Unreleased]

- Coisa que ainda não saiu.

## [1.1.0] - 2026-11-28

### Novo
- Câmera em outro jogador.
- Instalador `.exe`.

### Corrigido
- Acentos no caminho da pasta.

## [1.0.0] - 2026-10-20

### Novo
- Primeira versão.

[Unreleased]: https://github.com/exemplo/bisturi-clips/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/exemplo/bisturi-clips/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/exemplo/bisturi-clips/releases/tag/v1.0.0
"""


def test_extrai_a_secao_do_meio_sem_vazar_a_vizinha():
    texto = notas.extrair_notas(CHANGELOG, "v1.1.0")
    assert "Câmera em outro jogador." in texto
    assert "Acentos no caminho" in texto
    assert "### Novo" in texto
    assert "Primeira versão" not in texto
    assert "Coisa que ainda não saiu" not in texto
    assert not texto.startswith("\n") and texto.endswith("\n") and not texto.endswith("\n\n")


def test_a_ultima_secao_perde_as_linhas_de_link_do_rodape():
    texto = notas.extrair_notas(CHANGELOG, "1.0.0")
    assert "Primeira versão." in texto
    assert "https://" not in texto


def test_aceita_a_tag_com_ou_sem_v_e_o_titulo_sem_colchetes():
    simples = "# X\n\n## 1.2.3 (2026-12-01)\n\n- Algo.\n"
    assert notas.extrair_notas(simples, "v1.2.3") == notas.extrair_notas(simples, "1.2.3") == "- Algo.\n"
    com_v = "## [v2.0.0] - 2027-01-01\n- Outra.\n"
    assert notas.extrair_notas(com_v, "v2.0.0") == "- Outra.\n"


def test_versao_que_nao_existe_da_erro_claro_e_lista_as_que_existem():
    with pytest.raises(ErroNotas) as e:
        notas.extrair_notas(CHANGELOG, "v9.9.9")
    msg = str(e.value)
    assert "9.9.9" in msg and "CHANGELOG" in msg
    assert "1.1.0" in msg and "1.0.0" in msg


def test_nao_confunde_1_1_0_com_1_1_01_nem_com_11_1_0():
    texto = "## [11.1.0] - x\n- a\n\n## [1.1.01] - y\n- b\n"
    with pytest.raises(ErroNotas):
        notas.extrair_notas(texto, "1.1.0")


def test_secao_vazia_e_erro():
    with pytest.raises(ErroNotas, match="vazia"):
        notas.extrair_notas("## [1.1.0] - 2026-11-28\n\n## [1.0.0]\n- x\n", "v1.1.0")


def test_secao_repetida_e_erro():
    with pytest.raises(ErroNotas, match="mais de uma"):
        notas.extrair_notas("## [1.1.0]\n- a\n\n## [1.1.0]\n- b\n", "1.1.0")


def test_titulo_dentro_de_bloco_de_codigo_nao_encerra_a_secao():
    texto = "## [1.1.0]\n- Exemplo:\n\n```\n## isto é código\n```\n- fim\n\n## [1.0.0]\n- x\n"
    saida = notas.extrair_notas(texto, "1.1.0")
    assert "## isto é código" in saida and "- fim" in saida and "- x" not in saida


@pytest.mark.parametrize("tag", ["", "v1.1", "1.1.0.0", "v1.1.0-rc1", "../1.1.0", "v1.1.x", "v1.1.0\n## [1.0.0]"])
def test_tag_malformada_e_recusada(tag):
    with pytest.raises(ErroNotas, match="tag"):
        notas.extrair_notas(CHANGELOG, tag)


def test_crlf_e_bom_do_arquivo_sao_tratados(tmp_path):
    arq = tmp_path / "CHANGELOG.md"
    arq.write_bytes(b"\xef\xbb\xbf" + CHANGELOG.replace("\n", "\r\n").encode("utf-8"))
    saida = tmp_path / "dist" / "notas.md"
    assert notas.main(["--changelog", "CHANGELOG.md", "--tag", "v1.1.0", "--saida", "dist/notas.md"], raiz=tmp_path) == 0
    bruto = saida.read_bytes()
    assert b"\r" not in bruto and not bruto.startswith(b"\xef\xbb\xbf")
    assert "Câmera em outro jogador." in bruto.decode("utf-8")


def test_main_sem_secao_sai_com_1_e_mensagem_em_portugues(tmp_path, capsys):
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    assert notas.main(["--changelog", "CHANGELOG.md", "--tag", "v3.0.0", "--saida", "n.md"], raiz=tmp_path) == 1
    assert "ERRO" in capsys.readouterr().err
    assert not (tmp_path / "n.md").exists()


def test_main_sem_changelog_sai_com_1(tmp_path, capsys):
    assert notas.main(["--changelog", "CHANGELOG.md", "--tag", "v1.1.0", "--saida", "n.md"], raiz=tmp_path) == 1
    assert "CHANGELOG" in capsys.readouterr().err


@pytest.mark.parametrize("campo,valor", [("--changelog", "../fora.md"), ("--saida", "../fora.md"), ("--saida", "C:/Windows/x.md"), ("--changelog", "/etc/passwd")])
def test_caminho_fora_do_projeto_e_recusado(tmp_path, capsys, campo, valor):
    projeto = tmp_path / "projeto"
    projeto.mkdir()
    (projeto / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    (tmp_path / "fora.md").write_text(CHANGELOG, encoding="utf-8")
    args = {"--changelog": "CHANGELOG.md", "--tag": "v1.1.0", "--saida": "n.md", campo: valor}
    assert notas.main([x for par in args.items() for x in par], raiz=projeto) == 1
    assert "dentro do projeto" in capsys.readouterr().err
    assert not (tmp_path / "x.md").exists()


def test_sem_tag_o_argparse_recusa(tmp_path):
    with pytest.raises(SystemExit) as e:
        notas.main(["--changelog", "CHANGELOG.md", "--saida", "n.md"], raiz=tmp_path)
    assert e.value.code == 2


# ── revisão final da 1.1.0, item 6: título "A PUBLICAR" (sem data) é recusado ─────────────────────────

SEM_DATA = CHANGELOG.replace("## [1.1.0] - 2026-11-28", "## [1.1.0] - A PUBLICAR")


def test_titulo_a_publicar_e_recusado_na_funcao_e_aceito_so_como_rascunho():
    with pytest.raises(ErroNotas, match="A PUBLICAR"):
        notas.extrair_notas(SEM_DATA, "v1.1.0")
    with pytest.raises(ErroNotas, match="A PUBLICAR"):
        notas.extrair_notas(SEM_DATA.replace("A PUBLICAR", "a  publicar"), "v1.1.0")               # tanto faz maiúscula ou espaço extra
    assert "Câmera em outro jogador." in notas.extrair_notas(SEM_DATA, "v1.1.0", rascunho=True)
    assert "Câmera em outro jogador." in notas.extrair_notas(CHANGELOG, "v1.1.0")                   # CONTROLE: com data passa sem opção


def test_main_a_publicar_sai_com_1_sem_gravar_e_com_rascunho_sai_com_0(tmp_path, capsys):
    (tmp_path / "CHANGELOG.md").write_text(SEM_DATA, encoding="utf-8")
    args = ["--changelog", "CHANGELOG.md", "--tag", "v1.1.0", "--saida", "dist/notas.md"]
    assert notas.main(args, raiz=tmp_path) == 1
    assert "A PUBLICAR" in capsys.readouterr().err and not (tmp_path / "dist" / "notas.md").exists()
    assert notas.main([*args, "--rascunho"], raiz=tmp_path) == 0
    assert (tmp_path / "dist" / "notas.md").is_file()


def test_o_workflow_oficial_nao_usa_rascunho():
    assert "--rascunho" not in (RAIZ / "instalador" / "ci" / "release.yml").read_text(encoding="utf-8")
