"""Revisão de 26/11/2026, lote D1 (A-012): sem `musicas/catalogo.json`, nenhuma música entra na escolha automática (música só com licença registrada)."""
import json

from cs2clip import montagem
from cs2clip.config import Config


def _pasta_com_mp3_soltos(tmp_path):
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    for nome in ("solta1.mp3", "solta2.mp3"):
        (pasta / nome).write_bytes(b"x" * 60_000)
    return pasta


def test_sem_catalogo_nenhuma_musica_e_liberada_e_avisa(tmp_path, capsys):
    pasta = _pasta_com_mp3_soltos(tmp_path)
    assert montagem.musicas_liberadas(pasta) == []
    saida = capsys.readouterr().out
    assert "catalogo.json não existe" in saida and "licença registrada" in saida


def test_sem_catalogo_a_escolha_automatica_devolve_nada(tmp_path, capsys):
    pasta = _pasta_com_mp3_soltos(tmp_path)
    assert montagem.escolher_musica_por_ritmo(12.0, pasta, "MONTAGEM_x") == (None, 0.0)
    cfg = Config()
    cfg.montagem.musica = "auto"
    assert montagem.escolher_musica(cfg, "MONTAGEM_x", pasta) is None
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_x", 12.0, pasta, 60.0) is None
    assert "catalogo.json não existe" in capsys.readouterr().out


def test_controle_com_catalogo_so_entra_quem_tem_licenca(tmp_path):
    pasta = _pasta_com_mp3_soltos(tmp_path)
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": [
        {"arquivo": "solta1.mp3", "licenca": "CC0 1.0"}, {"arquivo": "solta2.mp3", "licenca": ""}]}), encoding="utf-8")
    assert [p.name for p in montagem.musicas_liberadas(pasta)] == ["solta1.mp3"]


def test_escolha_explicita_pelo_nome_continua_funcionando_sem_catalogo(tmp_path):
    """Quem escreve o nome do arquivo no config.toml escolheu aquela música: não é sorteio."""
    pasta = _pasta_com_mp3_soltos(tmp_path)
    cfg = Config()
    cfg.montagem.musica = "solta2.mp3"
    assert montagem.escolher_musica(cfg, "MONTAGEM_x", pasta) == pasta / "solta2.mp3"
