"""B5 (14/11/2026): miúdos da revisão de fora. Cada teste tem CONTROLE."""
import subprocess
from pathlib import Path

import pytest

from ui.seguranca import nome_arquivo_seguro, nome_reservado_windows

RAIZ = Path(__file__).resolve().parents[1]


# ── 1. nomes reservados: a lista oficial da Microsoft ("Naming files, paths, and namespaces") inclui COM0 e LPT0 ────────

@pytest.mark.parametrize("nome", ["COM0", "LPT0", "com0.mp4", "lpt0 .dem", "COM0 .mp4", "LPT0..mkv", "COM¹.mp4", "lpt³ .mp4"])
def test_com0_e_lpt0_tambem_sao_reservados(nome):
    assert nome_reservado_windows(nome)
    assert not nome_reservado_windows(nome_arquivo_seguro(nome))           # o nome que vai para o disco nunca é um dispositivo


@pytest.mark.parametrize("nome", ["COM10.mp4", "LPT00.mp4", "COM.mp4", "compra0.mp4", "LPT.dem", "com0x.mp4", "partida COM0.mp4"])
def test_controle_nomes_parecidos_nao_sao_reservados(nome):
    assert not nome_reservado_windows(nome)


# ── 2. .gitignore: o temporário da tela Configurar (ui/configuracao.py grava em config-*.tmp antes de trocar) ─────────

def test_o_temporario_da_tela_configurar_esta_no_gitignore():
    ignorado = subprocess.run(["git", "-C", str(RAIZ), "check-ignore", "-q", "config-abc123.tmp"]).returncode
    assert ignorado == 0
    assert subprocess.run(["git", "-C", str(RAIZ), "check-ignore", "-q", "config.exemplo.toml"]).returncode == 1   # CONTROLE: o exemplo entra
    assert 'prefix="config-", suffix=".tmp"' in (RAIZ / "ui" / "configuracao.py").read_text(encoding="utf-8")       # o nome que a tela usa de fato
