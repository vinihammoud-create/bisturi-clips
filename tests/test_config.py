"""Testes do carregador de config (cs2clip/config.py). Puro stdlib, sem GPU."""
from pathlib import Path

from cs2clip.config import load_config


def test_sem_arquivo_usa_padroes():
    cfg = load_config(None)
    assert cfg.cortes.antes == 4.0
    assert cfg.cortes.depois == 2.5
    assert cfg.qualidade.lufs == -14.0


def test_arquivo_inexistente_usa_padroes(tmp_path: Path):
    cfg = load_config(tmp_path / "nao-existe.toml")
    assert cfg.cortes.duracao_minima == 7.0


def test_sobrescreve_so_o_que_o_arquivo_tem(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        "[cortes]\nduracao_minima = 99.0\n\n[qualidade]\ncq = 19\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.cortes.duracao_minima == 99.0
    assert cfg.qualidade.cq == 19
    # o que não foi sobrescrito continua no padrão
    assert cfg.cortes.antes == 4.0
    assert cfg.transcricao.modelo == "large-v3-turbo"


def test_opcao_desconhecida_e_ignorada_sem_quebrar(tmp_path: Path, capsys):
    p = tmp_path / "config.toml"
    p.write_text("[cortes]\ncampo_que_nao_existe = 123\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.cortes.antes == 4.0  # não quebrou, só ignorou
    assert "aviso" in capsys.readouterr().out
