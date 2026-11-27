"""E1.1 (Plano E): a caixa única reconhece o que o dono cola ou arrasta. Sem rede; arquivos sintéticos."""
from pathlib import Path

import pytest

from cs2clip import entrada, sharecode
from cs2clip.entrada import reconhecer

CODIGO = sharecode.codificar(3230642215713767580, 3230647599455273103, 55788)
DEMO_NOME = "match730_003849321203751714819_0599033075_201.dem"


@pytest.fixture
def pasta(tmp_path):
    (tmp_path / "Meus Vídeos").mkdir()
    (tmp_path / "Meus Vídeos" / "2026-11-08 16-30-10.mkv").write_bytes(b"x" * 100)
    (tmp_path / "vazia").mkdir()
    (tmp_path / "com_videos").mkdir()
    (tmp_path / "com_videos" / "a.mp4").write_bytes(b"x")
    (tmp_path / "com_videos" / "b.txt").write_bytes(b"x")
    (tmp_path / DEMO_NOME).write_bytes(entrada.CABECALHO_CS2 + b"resto da demo")
    (tmp_path / "demo velha.dem").write_bytes(entrada.CABECALHO_CSGO_ANTIGO + b"x")
    (tmp_path / "lixo.dem").write_bytes(b"isto nao e uma demo")
    (tmp_path / "vazio.mp4").write_bytes(b"")
    (tmp_path / (DEMO_NOME + ".info")).write_bytes(b"{}")
    (tmp_path / "demo.dem.bz2").write_bytes(b"BZh9")
    (tmp_path / "nota.docx").write_bytes(b"x")
    return tmp_path


TWITCH = [
    ("https://www.twitch.tv/videos/1234567890", "1234567890"),
    ("twitch.tv/videos/1234567890", "1234567890"),
    ("https://m.twitch.tv/videos/1234567890?t=1h2m3s", "1234567890"),
    ("  https://twitch.tv/videos/1234567890/  ", "1234567890"),
]


@pytest.mark.parametrize("texto,vod", TWITCH)
def test_vod_da_twitch_em_varios_formatos(texto, vod):
    r = reconhecer(texto)
    assert r.tipo == "twitch_vod" and r.valor == vod and r.aceita and r.detalhes["link"] == f"https://www.twitch.tv/videos/{vod}"


YOUTUBE = [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"),
]


@pytest.mark.parametrize("texto,vid", YOUTUBE)
def test_video_do_youtube_em_varios_formatos(texto, vid):
    r = reconhecer(texto)
    assert r.tipo == "youtube" and r.valor == f"https://www.youtube.com/watch?v={vid}" and r.detalhes["id"] == vid and r.aceita


def test_youtube_com_playlist_usa_so_o_video_e_avisa():
    r = reconhecer("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123")
    assert r.tipo == "youtube" and "playlist" in r.mensagem


@pytest.mark.parametrize("texto,trecho", [
    ("https://www.youtube.com/playlist?list=PLabc123", "playlist"),
    ("https://www.youtube.com/@canaldeexemplo", "canal"),
    ("https://www.youtube.com/channel/UCabc", "canal"),
    ("https://www.youtube.com/watch?v=curto", "11 caracteres"),
    ("https://www.youtube.com/", "Não achei o vídeo"),
    ("https://clips.twitch.tv/AbcDefGhi", "CLIPE"),
    ("https://www.twitch.tv/canaldeexemplo/clip/AbcDefGhi", "CLIPE"),
    ("https://www.twitch.tv/canaldeexemplo", "canal"),
    ("https://www.twitch.tv/videos/123", "não parece certo"),
    ("https://exemplo.com.br/video", "site que o Bisturi não usa"),
    ("steam://run/730", "não traz o código"),
])
def test_o_que_nao_da_e_recusado_com_a_frase_do_porque(texto, trecho):
    r = reconhecer(texto)
    assert r.tipo == "recusado" and not r.aceita and trecho in r.mensagem


def test_codigo_de_partilha_cru_dentro_de_link_steam_e_com_lixo_em_volta():
    for texto in (CODIGO, f"  {CODIGO}\n", f"steam://rungame/730/76561202255233023/+csgo_download_match%20{CODIGO}",
                  f"olha a partida: {CODIGO} foi boa"):
        r = reconhecer(texto)
        assert r.tipo == "codigo_partilha" and r.valor == CODIGO and r.detalhes["token"] == 55788 and r.aceita
    ruim = CODIGO[:-1] + ("a" if CODIGO[-1] != "a" else "b")
    if sharecode.valido(ruim):                                   # o último caractere pode ainda decodificar: troca um do meio até quebrar
        ruim = "CSGO-AAAAA-BBBBB-CCCCC-DDDDD-0OIl0"
    assert reconhecer(ruim).tipo == "recusado" and "não está certo" in reconhecer(ruim).mensagem


def test_demo_do_cs2_por_caminho_com_aspas_por_nome_e_por_url_de_arquivo(pasta):
    caminho = pasta / DEMO_NOME
    for texto in (str(caminho), f'"{caminho}"', f"'{caminho}'", caminho.as_uri()):
        r = reconhecer(texto)
        assert r.tipo == "demo" and Path(r.valor) == caminho.resolve() and r.detalhes["padrao_cs2"] is True, texto
    por_nome = reconhecer(DEMO_NOME, procurar_em=[pasta])                      # só o nome, achado numa pasta conhecida
    assert por_nome.tipo == "demo" and Path(por_nome.valor) == caminho.resolve()
    assert reconhecer(DEMO_NOME, procurar_em=[pasta / "vazia"]).tipo == "recusado"       # CONTROLE: fora das pastas conhecidas não acha


def test_demo_que_nao_serve(pasta):
    assert "CS:GO antigo" in reconhecer(str(pasta / "demo velha.dem")).mensagem
    assert "não parece uma demo do CS2" in reconhecer(str(pasta / "lixo.dem")).mensagem
    assert "arquivo de dados" in reconhecer(str(pasta / (DEMO_NOME + ".info"))).mensagem
    assert "compactada" in reconhecer(str(pasta / "demo.dem.bz2")).mensagem
    assert "vazio" in reconhecer(str(pasta / "vazio.mp4")).mensagem


def test_video_pasta_e_arquivos_estranhos(pasta):
    v = reconhecer(str(pasta / "Meus Vídeos" / "2026-11-08 16-30-10.mkv"))                # espaço e acento no caminho
    assert v.tipo == "video" and v.detalhes["nome"] == "2026-11-08 16-30-10.mkv" and v.aceita
    p = reconhecer(str(pasta / "com_videos"))
    assert p.tipo == "pasta" and p.detalhes["videos"] == 1
    assert "nenhum vídeo" in reconhecer(str(pasta / "vazia")).mensagem
    assert "Não sei usar arquivos .docx" in reconhecer(str(pasta / "nota.docx")).mensagem
    assert "Não achei esse arquivo" in reconhecer(str(pasta / "nao_existe.mp4")).mensagem
    assert "Não achei esse arquivo" in reconhecer("C:\\Pasta\\que\\nao\\existe\\jogo.mp4").mensagem


def test_vazio_e_texto_solto_sao_desconhecidos_com_a_ajuda():
    for texto in ("", "   ", "oi tudo bem", None):
        r = reconhecer(texto)
        assert r.tipo == "desconhecido" and not r.aceita and "Aceito:" in r.mensagem


def test_varias_linhas_coladas_usa_a_primeira_com_conteudo():
    assert reconhecer("\n\n  https://www.twitch.tv/videos/1234567890  \nhttps://youtu.be/dQw4w9WgXcQ").tipo == "twitch_vod"


def test_lista_de_extensoes_de_video_e_a_mesma_do_pipeline():
    import cs2clip.__main__ as principal
    assert entrada.VIDEO_EXT == principal.VIDEO_EXT


def test_reconhecer_nunca_levanta_excecao(pasta):
    for texto in ("\x00", "C:\\" + "a" * 400, "http://", "https://", "twitch.tv/videos/", "CSGO-", "////", "..", ".", "~", "%%%", str(pasta / "x" / "..")):
        assert reconhecer(texto).tipo in ("recusado", "desconhecido", "pasta", "demo", "video", "twitch_vod", "youtube", "codigo_partilha")


@pytest.mark.parametrize("texto", ["\\\\servidor\\pasta\\partida.dem", "//servidor/pasta/video.mp4", '"\\\\192.168.0.5\\share\\a.mkv"'])
def test_caminho_de_rede_e_recusado_sem_tocar_no_disco(texto, monkeypatch):
    def nao_pode(*a, **k):
        raise AssertionError("tocou no caminho de rede")
    monkeypatch.setattr(Path, "exists", nao_pode)
    monkeypatch.setattr(Path, "stat", nao_pode)
    r = reconhecer(texto)
    assert r.tipo == "recusado" and "Caminhos de rede" in r.mensagem

