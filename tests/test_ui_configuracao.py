import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from ui.configuracao import CAMPOS, ConfiguracaoProjeto, ErroConfig


DEFAULTS = {
    "cortes": {"antes": 3, "depois": 5, "juntar_intervalo": 8, "duracao_minima": 3,
               "estender_reacao": True, "reacoes_sem_abate": 4, "so_partidas": True,
               "limiar_reacao": 0.7, "nota_montagem": 30, "nota_short": 40, "juntar_no_round": 120, "pular_parado": 6},
    "shorts": {"gerar": True, "quantidade": 8, "duracao_maxima": 60, "legendas": True, "selo_jogada": True, "zoom_no_abate": False,
               "marca": "@drd0llar"},
    "montagem": {"gerar": True, "ordem": "melhores"},
    "transcricao": {"modelo": "small", "idioma": "pt"},
    "chat": {"usar": False},
    "qualidade": {"encoder": "auto", "cq": 21, "renders_paralelos": 1},
    "demo": {"jogador": ""},
}


class FakeConfig:
    def __init__(self):
        for secao, itens in DEFAULTS.items():
            setattr(self, secao, SimpleNamespace(**itens))


def load_config(path: Path):
    texto = path.read_text(encoding="utf-8") if path.exists() else ""
    dados = tomllib.loads(texto) if texto.strip() else {}
    if dados.get("cortes", {}).get("antes") == 14:
        raise ValueError("configuração recusada pelo teste")
    return dados


def test_edicao_pontual_preserva_comentarios_outras_linhas_e_crlf(tmp_path):
    original = (
        "# comentário inicial\r\n"
        "[cortes]\r\n"
        "antes = 4 # comentário da chave\r\n"
        "depois = 6\r\n"
        "# comentário da seção\r\n"
        "[shorts]\r\n"
        "gerar = true\r\n"
        "[nao_expor]\r\n"
        "secreto = \"continua aqui\"\r\n"
    )
    (tmp_path / "config.toml").write_bytes(original.encode("utf-8"))
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    config.salvar({"cortes.antes": 8, "cortes.duracao_minima": 10, "shorts.gerar": False})
    bytes_depois = (tmp_path / "config.toml").read_bytes()
    texto = bytes_depois.decode("utf-8")
    assert "# comentário inicial" in texto
    assert "antes = 8 # comentário da chave" in texto
    assert "# comentário da seção" in texto
    assert b'[nao_expor]\r\nsecreto = "continua aqui"\r\n' in bytes_depois
    dados = tomllib.loads(texto)
    assert dados["cortes"]["antes"] == 8
    assert dados["cortes"]["duracao_minima"] == 10
    assert dados["shorts"]["gerar"] is False
    assert dados["nao_expor"]["secreto"] == "continua aqui"
    assert b"\r\n" in bytes_depois
    assert (tmp_path / "config.toml.bak").exists()


def test_limites_e_chaves_fora_da_lista_sao_rejeitados(tmp_path):
    (tmp_path / "config.toml").write_text("[cortes]\nantes = 4\n", encoding="utf-8")
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    with pytest.raises(ErroConfig):
        config.salvar({"cortes.antes": 16})
    with pytest.raises(ErroConfig):
        config.salvar({"deteccao.ativar": True})
    with pytest.raises(ErroConfig):
        config.salvar({"shorts.camera_x": 123})
    assert "antes = 4" in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_restaura_backup_quando_load_config_falha(tmp_path):
    original = "# manter\n[cortes]\nantes = 4\ndepois = 3\n"
    caminho = tmp_path / "config.toml"
    caminho.write_text(original, encoding="utf-8")
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    with pytest.raises(ErroConfig):
        config.salvar({"cortes.antes": 14})
    assert caminho.read_text(encoding="utf-8") == original
    assert (tmp_path / "config.toml.bak").read_text(encoding="utf-8") == original


def test_voltar_aos_padroes_remove_a_linha_sem_apagar_comentarios(tmp_path):
    original = "# principal\n[cortes]\n# nota preservada\nantes = 4 # valor atual\ndepois = 5\n[shorts]\ngerar = true\n"
    caminho = tmp_path / "config.toml"
    caminho.write_text(original, encoding="utf-8")
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    config.voltar_padrao(["cortes.antes"])
    texto = caminho.read_text(encoding="utf-8")
    assert "antes = 4" not in texto
    assert "# principal" in texto
    assert "# nota preservada" in texto
    assert "depois = 5" in texto
    assert tomllib.loads(texto)["shorts"]["gerar"] is True


def test_padrao_vem_de_config_e_nao_de_valor_inventado(tmp_path):
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    dados = config.ler()
    por_nome = {f"{c['secao']}.{c['chave']}": c for c in dados["campos"]}
    assert len(por_nome) == len(CAMPOS)
    assert por_nome["cortes.antes"]["valor"] == DEFAULTS["cortes"]["antes"]
    assert por_nome["demo.jogador"]["valor"] == ""
    assert por_nome["qualidade.encoder"]["valor"] == "auto"
