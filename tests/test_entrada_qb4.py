"""QB4: dois bugs antigos de `cs2clip/entrada.py` (achados pelo QA).

1. Caminho de arquivo que EXISTE e tem colchete no nome (o yt-dlp gera `Título [id].mp4`) caía em "Não entendi o que é isso":
   o `urlparse` do link da Twitch/YouTube estourava com "Invalid IPv6 URL" antes de o arquivo ser olhado.
2. `reconhecer(". /")` levantava FileNotFoundError (virava erro 500 na rota): o Windows trata ". " como a pasta atual.
Cada teste tem controle: o que já era reconhecido continua igual.
"""
from pathlib import Path

import pytest

from cs2clip.entrada import reconhecer


def _video(pasta: Path, nome: str) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / nome
    arq.write_bytes(b"\x00" * 64)
    return arq


@pytest.mark.parametrize("nome", ["partida [1].mp4", "Título [dQw4w9WgXcQ].mp4", "[x].mkv", "a[b.mp4"])
def test_arquivo_que_existe_com_colchete_no_nome_e_reconhecido_como_video(tmp_path, nome):
    arq = _video(tmp_path, nome)
    r = reconhecer(str(arq))
    assert r.tipo == "video" and r.aceita, r
    assert Path(r.valor) == arq.resolve() and r.detalhes["nome"] == nome


def test_arquivo_dentro_de_pasta_com_colchete_e_reconhecido(tmp_path):
    arq = _video(tmp_path / "[x]", "v.mp4")
    r = reconhecer(str(arq))
    assert r.tipo == "video" and Path(r.valor) == arq.resolve()
    assert reconhecer(f'"{arq}"').tipo == "video"                      # com as aspas do arrastar-e-soltar


def test_pasta_com_colchete_no_nome_e_reconhecida_como_pasta(tmp_path):
    _video(tmp_path / "[lote 1]", "a.mp4")
    r = reconhecer(str(tmp_path / "[lote 1]"))
    assert r.tipo == "pasta" and r.detalhes["videos"] == 1


def test_caminho_relativo_com_colchete_que_existe_e_reconhecido(tmp_path, monkeypatch):
    _video(tmp_path / "[x]", "v.mp4")
    monkeypatch.chdir(tmp_path)
    assert reconhecer("[x]\\v.mp4").tipo == "video"


def test_controle_colchete_que_nao_e_arquivo_continua_nao_reconhecido(tmp_path):
    """O que já era "não entendi" continua: texto solto com colchete, link quebrado e arquivo com colchete que NÃO existe."""
    for texto in ("a[b", "http://[abc", str(tmp_path / "nao existe [1].mp4")):
        r = reconhecer(texto)
        assert r.tipo == "desconhecido" and "Não entendi" in r.mensagem, texto


def test_controle_links_e_arquivos_de_sempre_nao_mudaram(tmp_path):
    assert reconhecer("https://www.twitch.tv/videos/2345678901").tipo == "twitch_vod"
    assert reconhecer("https://youtu.be/dQw4w9WgXcQ").tipo == "youtube"
    assert reconhecer(str(_video(tmp_path, "normal.mp4"))).tipo == "video"
    assert reconhecer(str(tmp_path)).tipo == "pasta"
    assert reconhecer(r"\\servidor\pasta\a.mp4").tipo == "recusado"       # rede continua recusada sem tocar no disco


@pytest.mark.parametrize("texto", [". /", ". \\", "... /", " . /x"])
def test_texto_estranho_que_o_windows_le_como_pasta_nao_levanta_erro(texto):
    r = reconhecer(texto)                                   # antes: FileNotFoundError ([WinError 3]) para ". /"
    assert r.tipo in ("desconhecido", "recusado") and r.mensagem
    assert not r.aceita
