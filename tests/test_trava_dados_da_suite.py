"""P2, item 10 (26/11/2026): a trava do `tests/conftest.py` que põe a pasta de dados da suíte numa pasta temporária.

Se o conftest deixar de definir `BISTURI_DADOS` antes de o pacote ser importado, a `PASTA_DADOS` volta a ser a raiz do projeto (a do dono)
e estes testes caem. O modo portátil de verdade (sem a variável) é provado em processo à parte em `tests/test_pasta_dados.py`.
"""
from __future__ import annotations

import os
from pathlib import Path

from cs2clip import baixar_demo, caminhos, demos_cs2, editar_demo, gravador_demo, steam_api
from cs2clip import musicas_catalogo as mc
import cs2clip.__main__ as principal

RAIZ = Path(__file__).resolve().parents[1]
PASTA_DA_SUITE = Path(os.environ.get("BISTURI_DADOS_DA_SUITE", "")).resolve() if os.environ.get("BISTURI_DADOS_DA_SUITE") else None


def test_a_suite_roda_com_a_pasta_de_dados_numa_pasta_temporaria():
    assert PASTA_DA_SUITE is not None, "o tests/conftest.py não definiu a pasta de dados da suíte"
    assert Path(os.environ["BISTURI_DADOS"]).resolve() == PASTA_DA_SUITE
    assert caminhos.PASTA_DADOS == PASTA_DA_SUITE and caminhos.PASTA_DADOS != RAIZ
    assert PASTA_DA_SUITE.is_relative_to(RAIZ / ".pytest_tmp")                   # dentro do projeto e fora do git (.gitignore)


def test_os_dados_padrao_do_nucleo_e_da_interface_ficam_fora_da_raiz():
    """Teste que esquecer `--saida`/`--config` ou chamar uma função com o caminho padrão grava na pasta da suíte, não na do dono."""
    import ui.__main__ as um
    padroes = {"baixar_demo.PASTA_DEMOS": baixar_demo.PASTA_DEMOS, "baixar_demo.CONFIG": baixar_demo.CONFIG,
               "demos_cs2.PASTA_DEMOS_PROJETO": demos_cs2.PASTA_DEMOS_PROJETO, "demos_cs2.PASTA_CACHE_VOD": demos_cs2.PASTA_CACHE_VOD,
               "steam_api.ARQUIVO": steam_api.ARQUIVO, "editar_demo.PASTA_GRAVACAO": editar_demo.PASTA_GRAVACAO,
               "editar_demo.PASTA_SAIDA": editar_demo.PASTA_SAIDA, "gravador_demo.PASTA_PADRAO": gravador_demo.PASTA_PADRAO,
               "__main__.PASTA_ENTRADA": principal.PASTA_ENTRADA, "__main__.PASTA_JOGADOR": principal.PASTA_JOGADOR,
               "musicas_catalogo.PASTA_PESSOA": mc.PASTA_PESSOA, "ui.__main__.PASTA_LOG": um.PASTA_LOG}
    fora = {k: str(v) for k, v in padroes.items() if not Path(v).resolve().is_relative_to(RAIZ / ".pytest_tmp")}
    assert fora == {}, fora


def test_o_modelo_de_voz_continua_no_lugar_de_sempre():
    """Com a variável, o `caminhos.py` poria o HF_HOME na pasta temporária: o conftest fixa o de sempre antes (nada é baixado de novo)."""
    hf_home = os.environ.get("HF_HOME")
    assert hf_home is not None and not Path(hf_home).resolve().is_relative_to(RAIZ / ".pytest_tmp")
