"""Revisão de 26/11/2026, lote D1: `--modelo-leve` só mexe na seção [transcricao] do config.toml (P-006)."""
import tomllib

import cs2clip.__main__ as main_mod


# ── P-006: --modelo-leve só mexe na seção [transcricao] ───────────────────────────────────────

def _rodar_leve(tmp_path, texto: str) -> str:
    cfg = tmp_path / "config.toml"
    cfg.write_text(texto, encoding="utf-8")
    assert main_mod.main(["--modelo-leve", "--config", str(cfg)]) == 0
    return cfg.read_text(encoding="utf-8")


def test_modelo_leve_nao_mexe_no_modelo_de_outra_secao_antes(tmp_path):
    saida = _rodar_leve(tmp_path, '[chat]\nmodelo = "do-chat"\n\n[transcricao]\nmodelo = "large-v3-turbo"\nidioma = "pt"\n')
    dados = tomllib.loads(saida)
    assert dados["chat"]["modelo"] == "do-chat" and dados["transcricao"]["modelo"] == "small" and dados["transcricao"]["idioma"] == "pt"


def test_modelo_leve_nao_mexe_no_modelo_de_outra_secao_depois(tmp_path):
    saida = _rodar_leve(tmp_path, '[transcricao]\nidioma = "pt"\n\n[chat]\nmodelo = "do-chat"\n')
    dados = tomllib.loads(saida)
    assert dados["chat"]["modelo"] == "do-chat" and dados["transcricao"] == {"idioma": "pt", "modelo": "small"}


def test_modelo_leve_sem_a_secao_cria_a_secao_e_deixa_o_modelo_dos_outros(tmp_path):
    saida = _rodar_leve(tmp_path, '[chat]\nmodelo = "do-chat"\n')
    dados = tomllib.loads(saida)
    assert dados["chat"]["modelo"] == "do-chat" and dados["transcricao"]["modelo"] == "small"


def test_modelo_leve_com_comentario_no_cabecalho_e_secao_no_fim(tmp_path):
    saida = _rodar_leve(tmp_path, '[chat]\nmodelo = "do-chat"\n\n[transcricao] # voz')
    dados = tomllib.loads(saida)
    assert dados["chat"]["modelo"] == "do-chat" and dados["transcricao"]["modelo"] == "small"
