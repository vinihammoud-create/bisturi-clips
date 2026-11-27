"""P2.4a do plano de publicação (item 6 da P2, 26/11/2026): as rotas da tela Músicas com DUAS bases.

- a FÁBRICA é `<programa>\\musicas` (o `catalogo.json` e os mp3 que vêm com o programa): somente leitura;
- a PESSOA é `<dados>\\musicas` (o `catalogo_usuario.json`, os mp3 dela e o `historico.json`).
Adicionar, ligar/desligar, tocar e "trocar a música" do vídeo acham o arquivo na pessoa e depois na fábrica, e nada é escrito na pasta do
programa. No modo portátil (só `root_dir`, ou `dados_dir` igual a ele) as duas são a mesma `musicas/` de sempre.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cs2clip import musicas_catalogo as mc
from test_interface_duas_bases import _cliente, _h
from test_pasta_dados import _diferencas, _foto
from test_ui_dados_seguranca import criar_projeto

GRANDE = b"F" * 70_000          # o mp3 "de fábrica" (conteúdo diferente do da pessoa, para saber qual foi servido)
DA_PESSOA = b"P" * 90_000


def _faixa(arquivo: str, **extra) -> dict:
    return {"arquivo": arquivo, "titulo": arquivo, "autor": "Autor", "licenca": "CC0 1.0", "bpm": 120.0, "duracao_s": 150.0, "drop_s": 30.0,
            "emocao": 0.6, **extra}


def _medir(_arq) -> dict:
    return {"duracao_s": 150.0, "bpm": 128.0, "batidas_detectadas": 300, "energia": 0.8, "drop_s": 40.0, "impacto_db": 9.0, "emocao": 0.7}


@pytest.fixture
def instalado(tmp_path, monkeypatch):
    """O programa com o catálogo de fábrica (uma faixa) e os dados da pessoa em outra pasta (sem `musicas\\` ainda: a primeira gravação a cria)."""
    programa = tmp_path / "Programs" / "Bisturi Clips" / "app"
    dados = tmp_path / "Vídeos" / "Bisturi Clips"
    (programa / "musicas").mkdir(parents=True)
    (programa / "musicas" / "catalogo.json").write_text(json.dumps({"faixas": [_faixa("fabrica.mp3")]}), encoding="utf-8")
    (programa / "musicas" / "fabrica.mp3").write_bytes(GRANDE)
    criar_projeto(dados)
    origem = tmp_path / "Downloads" / "Minha Faixa.mp3"
    origem.parent.mkdir()
    origem.write_bytes(DA_PESSOA)
    monkeypatch.setattr(mc, "medir_faixa", _medir)
    return programa, dados, origem


def _escolhida(origem: Path):
    return lambda: [{"caminho": str(origem), "nome": origem.name, "tamanho": origem.stat().st_size}]


def _adicionar(c, origem: Path):
    h = _h(c)
    assert c.post("/api/musicas/escolher", headers=h).status_code == 200                # autoriza o arquivo, como o botão da tela
    return c.post("/api/musicas/adicionar", json={"caminho": str(origem), "licenca": "cc0", "autor": "Eu", "titulo": "Minha"}, headers=h)


def _pessoa(dados: Path) -> dict:
    return json.loads((dados / "musicas" / mc.ARQUIVO_PESSOA).read_text(encoding="utf-8"))


def test_adicionar_faixa_grava_o_mp3_e_o_catalogo_da_pessoa_na_pasta_de_dados(instalado):
    programa, dados, origem = instalado
    antes = _foto(programa)
    with _cliente(programa, dados, music_chooser=_escolhida(origem)) as c:
        r = _adicionar(c, origem)
        assert r.status_code == 200, r.text
        assert (dados / "musicas" / "Minha Faixa.mp3").read_bytes() == DA_PESSOA
        assert [f["arquivo"] for f in _pessoa(dados)["faixas"]] == ["Minha Faixa.mp3"]
        lista = c.get("/api/musicas", headers=_h(c)).json()
        assert [f["arquivo"] for f in lista["faixas"]] == ["fabrica.mp3", "Minha Faixa.mp3"] and all(f["existe"] for f in lista["faixas"])
        assert _adicionar(c, origem).status_code == 400                                  # repetida (o nome já está no catálogo da pessoa)
    assert not (programa / "musicas" / "Minha Faixa.mp3").exists() and not (programa / "musicas" / mc.ARQUIVO_PESSOA).exists()
    depois = _foto(programa)
    assert depois == antes, "a pasta do programa mudou: " + _diferencas(antes, depois)


def test_ligar_e_desligar_faixa_de_fabrica_grava_so_nos_ajustes_da_pessoa(instalado):
    programa, dados, _ = instalado
    antes = _foto(programa)
    with _cliente(programa, dados) as c:
        h = _h(c)
        r = c.post("/api/musicas/ativar", json={"arquivo": "fabrica.mp3", "ativa": False}, headers=h)
        assert r.status_code == 200, r.text
        assert _pessoa(dados) == {"faixas": [], "ajustes": {"fabrica.mp3": {"desligada": True}}}
        assert c.get("/api/musicas", headers=h).json()["resumo"]["ativas"] == 0
        assert c.post("/api/musicas/ativar", json={"arquivo": "fabrica.mp3", "ativa": True}, headers=h).status_code == 200
        assert _pessoa(dados)["ajustes"] == {} and c.get("/api/musicas", headers=h).json()["resumo"]["ativas"] == 1
        assert c.post("/api/musicas/ativar", json={"arquivo": "nao_tem.mp3", "ativa": False}, headers=h).status_code == 404
    depois = _foto(programa)
    assert depois == antes, "a pasta do programa mudou: " + _diferencas(antes, depois)


def test_audio_serve_a_faixa_da_pessoa_e_a_de_fabrica(instalado):
    programa, dados, origem = instalado
    with _cliente(programa, dados, music_chooser=_escolhida(origem)) as c:
        assert _adicionar(c, origem).status_code == 200
        assert c.get("/api/musicas/audio/Minha Faixa.mp3").content == DA_PESSOA         # mora só na pasta de dados
        assert c.get("/api/musicas/audio/fabrica.mp3").content == GRANDE                 # mora só na pasta do programa
        (dados / "musicas" / "solto.mp3").write_bytes(DA_PESSOA)                         # na pasta da pessoa, mas fora do catálogo
        assert c.get("/api/musicas/audio/solto.mp3").status_code == 404
        assert c.get("/api/musicas/audio/..%2F..%2Fconfig.toml").status_code in (400, 404)


def test_faixa_da_pessoa_com_o_nome_de_uma_de_fabrica_toca_a_da_pessoa(instalado):
    programa, dados, _ = instalado
    (dados / "musicas").mkdir()
    (dados / "musicas" / "fabrica.mp3").write_bytes(DA_PESSOA)
    (dados / "musicas" / mc.ARQUIVO_PESSOA).write_text(json.dumps({"faixas": [_faixa("fabrica.mp3", autor="Pessoa")]}), encoding="utf-8")
    with _cliente(programa, dados) as c:
        assert c.get("/api/musicas/audio/fabrica.mp3").content == DA_PESSOA


def test_trocar_a_musica_do_video_grava_o_historico_na_pasta_de_dados(instalado, monkeypatch):
    programa, dados, _ = instalado
    monkeypatch.delenv("BISTURI_HISTORICO_MUSICA", raising=False)                       # o lugar de verdade do rodízio (a trava do conftest o desvia)
    (dados / "saida" / "v1" / "benchmark.json").write_text(json.dumps({"montagem_musica": "fabrica.mp3"}), encoding="utf-8")
    antes = _foto(programa)
    with _cliente(programa, dados) as c:
        r = c.post("/api/videos/v1/trocar-musica", headers=_h(c))
        assert r.status_code == 200 and r.json()["trocada"] is True, r.text
    historico = json.loads((dados / "musicas" / "historico.json").read_text(encoding="utf-8"))
    assert historico["evitar"] == {"MONTAGEM_v1": ["fabrica.mp3"]}
    depois = _foto(programa)
    assert depois == antes, "a pasta do programa mudou: " + _diferencas(antes, depois)


@pytest.mark.parametrize("com_dados_igual", [False, True])
def test_portatil_a_pessoa_continua_na_mesma_pasta_musicas(tmp_path, monkeypatch, com_dados_igual):
    """Só `root_dir` (ou `dados_dir` = `root_dir`): catálogo da pessoa, mp3 e histórico na mesma `musicas/` de sempre."""
    criar_projeto(tmp_path)
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": [_faixa("fabrica.mp3")]}), encoding="utf-8")
    (pasta / "fabrica.mp3").write_bytes(GRANDE)
    origem = tmp_path / "Downloads" / "Minha Faixa.mp3"
    origem.parent.mkdir()
    origem.write_bytes(DA_PESSOA)
    monkeypatch.setattr(mc, "medir_faixa", _medir)
    monkeypatch.delenv("BISTURI_HISTORICO_MUSICA", raising=False)
    (tmp_path / "saida" / "v1" / "benchmark.json").write_text(json.dumps({"montagem_musica": "fabrica.mp3"}), encoding="utf-8")
    catalogo_antes = (pasta / "catalogo.json").read_bytes()
    with _cliente(tmp_path, tmp_path if com_dados_igual else None, music_chooser=_escolhida(origem)) as c:
        assert _adicionar(c, origem).status_code == 200
        assert c.post("/api/musicas/ativar", json={"arquivo": "fabrica.mp3", "ativa": False}, headers=_h(c)).status_code == 200
        assert c.post("/api/videos/v1/trocar-musica", headers=_h(c)).json()["trocada"] is True
        assert c.get("/api/musicas/audio/Minha Faixa.mp3").content == DA_PESSOA
    assert (pasta / "Minha Faixa.mp3").is_file() and (pasta / "historico.json").is_file()
    cat = json.loads((pasta / mc.ARQUIVO_PESSOA).read_text(encoding="utf-8"))
    assert [f["arquivo"] for f in cat["faixas"]] == ["Minha Faixa.mp3"] and cat["ajustes"] == {"fabrica.mp3": {"desligada": True}}
    assert (pasta / "catalogo.json").read_bytes() == catalogo_antes
