"""P5, item 2 (27/11/2026): `python -m cs2clip.instalacao pos|checar`, o que o instalador .exe roda depois dos pacotes.

Nada aqui usa a internet: as músicas, os programas de terceiros e o modelo de voz vêm de funções falsas (`baixar_*`). A pasta do programa
e a de dados são temporárias, com espaço, acento e apóstrofo no caminho."""
import hashlib
import io
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from http.client import HTTPMessage
from pathlib import Path

import pytest

from cs2clip import caminhos, instalacao, terceiros
from cs2clip.checagem import Item

RAIZ = Path(__file__).resolve().parents[1]
URL = "https://raw.githubusercontent.com/SoundSafari/CC0-1.0-Music/main/freepd.com/"
EXEMPLO = '# exemplo de fábrica\n[transcricao]\nmodelo = "large-v3-turbo"\n'
FAIXAS = {"Faixa Um.mp3": b"ID3-um-" + b"a" * 3000, "Faixa Dois.mp3": b"ID3-dois-" + b"b" * 5000}
LINHA = re.compile(r"^(\[\d+/\d+\] |AVISO: )")


def _sha(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _zip(arquivos: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nome, dados in arquivos.items():
            z.writestr(nome, dados)
    return buf.getvalue()


PACOTES = {"twitchdl": _zip({"TwitchDownloaderCLI.exe": b"MZ-twitch"}), "hlae": _zip({"HLAE.exe": b"MZ-hlae"}),
           "boiler": _zip({"bin/boiler-writter.exe": b"MZ-boiler"})}


class Terceiros:
    """O `baixar(t, destino)` do `terceiros` sem rede: escreve um zip pequeno com o programa certo."""

    def __init__(self, falhar: set[str] | None = None):
        self.pedidos: list[str] = []
        self.falhar = falhar or set()

    def __call__(self, t, destino):
        self.pedidos.append(t.pasta)
        if t.pasta in self.falhar:
            raise terceiros.ErroTerceiros(f"{t.nome}: o SHA-256 do arquivo baixado NÃO bate com o fixado no código. Nada foi instalado.")
        Path(destino).write_bytes(PACOTES[t.pasta])
        return Path(destino)


class Rede:
    """O `baixar(faixa)` das músicas sem rede: devolve os bytes de cada faixa (ou outros, para simular adulteração)."""

    def __init__(self, conteudos: dict[str, bytes] | None = None):
        self.conteudos = dict(FAIXAS if conteudos is None else conteudos)
        self.pedidos: list[str] = []

    def __call__(self, faixa):
        self.pedidos.append(faixa.arquivo)
        return self.conteudos[faixa.arquivo]


class Modelo:
    def __init__(self, erro: BaseException | None = None):
        self.pedidos: list[tuple[str, Path]] = []
        self.erro = erro

    def __call__(self, nome, ao_progredir=None, cache_dir: Path | str = ""):
        self.pedidos.append((nome, Path(cache_dir)))
        if ao_progredir:
            ao_progredir(0, 100_000_000, 0.0, None)
        if self.erro is not None:
            raise self.erro
        if ao_progredir:
            ao_progredir(100_000_000, 100_000_000, 5e6, 0.0)
        return Path(cache_dir) / "snap"


def _checar_ok(raiz, dados):
    return [Item("Python", True, "3.12"), Item("Placa de vídeo", False, "sem NVIDIA", False, "Instale o driver da NVIDIA.")]


def _checar_grave(raiz, dados):
    return [Item("FFmpeg", False, "não abriu", True, "Abra o instalador de novo.")]


def _catalogo(raiz: Path, faixas: dict[str, bytes] = FAIXAS, extras: list | tuple = (), fora: list | tuple = ()) -> Path:
    lista = [{"arquivo": n, "titulo": Path(n).stem, "url": URL + urllib.parse.quote(n), "sha256": _sha(d), "tamanho": len(d)}
             for n, d in faixas.items()]
    arq = raiz / "instalador" / "componentes.json"
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps({"esquema": 1, "uv": {}, "ffmpeg": {}, "musicas": {"faixas": lista + list(extras), "fora": list(fora)}},
                              ensure_ascii=False), encoding="utf-8")
    return arq


@pytest.fixture
def prog(tmp_path, monkeypatch):
    """A pasta `app\\` de um programa instalado, com apóstrofo no caminho (como sob um usuário do Windows chamado "D'Ávila")."""
    raiz = tmp_path / "Usuário D'Ávila" / "Bisturi Clips" / "app"
    (raiz / "bin").mkdir(parents=True)
    (raiz / "bin" / "ffmpeg.exe").write_bytes(b"MZ-ffmpeg-falso")
    (raiz / "config.exemplo.toml").write_text(EXEMPLO, encoding="utf-8")
    monkeypatch.setattr(terceiros, "REGISTRO", raiz / "bin" / "TERCEIROS.json")
    return raiz


@pytest.fixture
def dados(tmp_path):
    return tmp_path / "Vídeos de João d'Arc" / "Bisturi Clips"


def _rodar(prog, dados, *extra, rede=None, terc=None, modelo=None, checar=_checar_ok):
    return instalacao.main(["pos", "--dados", str(dados), *extra], raiz_programa=prog, baixar_musica=rede or Rede(),
                           baixar_terceiro=terc or Terceiros(), baixar_modelo=modelo or Modelo(), checar=checar)


def _linhas(saida: str) -> list[str]:
    return [linha for linha in saida.splitlines() if linha.strip()]


def _foto(*pastas: Path) -> dict[str, tuple[bytes, int]]:
    return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for base in pastas if base.exists() for p in sorted(base.rglob("*")) if p.is_file()}


# ── o caminho feliz ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_pos_prepara_dados_config_pasta_dados_chat_e_musicas(prog, dados, capsys):
    _catalogo(prog)
    terc, rede = Terceiros(), Rede()
    assert _rodar(prog, dados, "--sem-nvidia", rede=rede, terc=terc) == 0
    saida = capsys.readouterr().out
    assert (dados / "config.toml").read_text(encoding="utf-8") == EXEMPLO                          # cópia do exemplo do programa
    bruto = (prog / "pasta_dados.txt").read_bytes()
    assert bruto.startswith(b"\xef\xbb\xbf") and bruto.decode("utf-8-sig") == str(dados)            # UTF-8 com BOM, como o instalador grava
    assert caminhos.resolver_pasta_dados(prog, {}) == dados.resolve()                               # o programa, ao abrir, acha a mesma pasta
    assert (prog / "bin" / "TwitchDownloaderCLI.exe").read_bytes() == b"MZ-twitch" and terc.pedidos == ["twitchdl"]
    for nome, conteudo in FAIXAS.items():
        assert (prog / "musicas" / nome).read_bytes() == conteudo
    assert sorted(rede.pedidos) == sorted(FAIXAS)
    assert "Músicas livres: baixei 2, já tinha 0, ficaram de fora 0." in saida
    linhas = _linhas(saida)
    assert all(LINHA.match(x) for x in linhas), linhas                                              # só `[n/N] ...` e `AVISO: ...`
    assert [int(m.group(1)) for x in linhas if (m := re.match(r"^\[(\d+)/4\]", x))] == sorted(int(m.group(1)) for x in linhas if (m := re.match(r"^\[(\d+)/4\]", x)))
    assert "[4/4] Checagem: tudo certo para trabalhar." in saida
    assert "AVISO: Placa de vídeo: sem NVIDIA" in saida                                             # aviso da checagem não reprova


def test_rodar_duas_vezes_da_o_mesmo_resultado(prog, dados, capsys):
    _catalogo(prog)
    terc, rede = Terceiros(), Rede()
    assert _rodar(prog, dados, "--sem-nvidia", "--com-gravacao-demo", rede=rede, terc=terc) == 0
    depois_1 = _foto(prog, dados)
    capsys.readouterr()
    assert _rodar(prog, dados, "--sem-nvidia", "--com-gravacao-demo", rede=rede, terc=terc) == 0
    saida = capsys.readouterr().out
    assert _foto(prog, dados) == depois_1                                                           # nenhum arquivo novo, mudado ou regravado
    assert terc.pedidos == ["twitchdl", "hlae", "boiler"]                                           # a 2ª vez não baixou nada de novo
    assert sorted(rede.pedidos) == sorted(FAIXAS)
    assert "baixei 0, já tinha 2, ficaram de fora 0" in saida and "já estava instalado" in saida
    assert "config.toml já existia, não mexi" in saida


def test_retoma_depois_de_uma_queda_no_meio_das_musicas(prog, dados, capsys):
    """A rede caiu na 2ª faixa: a 1ª fica, a 2ª fica de fora sem sobrar nada; rodar de novo baixa só a que faltou."""
    _catalogo(prog)

    class Cai(Rede):
        def __call__(self, faixa):
            if faixa.arquivo == "Faixa Dois.mp3":
                raise OSError("a conexão caiu")
            return super().__call__(faixa)

    assert _rodar(prog, dados, "--sem-nvidia", rede=Cai()) == 0
    assert "a conexão caiu" in capsys.readouterr().out
    assert sorted(p.name for p in (prog / "musicas").iterdir()) == ["Faixa Um.mp3"]                 # nada pela metade, nenhum temporário
    rede = Rede()
    assert _rodar(prog, dados, "--sem-nvidia", rede=rede) == 0
    assert rede.pedidos == ["Faixa Dois.mp3"] and "baixei 1, já tinha 1, ficaram de fora 0" in capsys.readouterr().out


# ── músicas: hash, site, arquivo existente, faixa sem origem ───────────────────────────────────────────────────────────────────────
def test_hash_errado_e_recusado_e_nada_e_gravado(prog, dados, capsys):
    _catalogo(prog)
    adulterada = b"X" * len(FAIXAS["Faixa Dois.mp3"])                                              # mesmo tamanho, outro conteúdo
    assert _rodar(prog, dados, "--sem-nvidia", rede=Rede({**FAIXAS, "Faixa Dois.mp3": adulterada})) == 0
    saida = capsys.readouterr().out
    assert sorted(p.name for p in (prog / "musicas").iterdir()) == ["Faixa Um.mp3"]                 # nem a faixa, nem um .baixando
    assert "baixei 1, já tinha 0, ficaram de fora 1" in saida
    assert "AVISO: a música Faixa Dois.mp3 ficou de fora: o arquivo baixado NÃO bate com o SHA-256" in saida


def test_sha256_em_maiusculas_no_catalogo_vale_igual(tmp_path):
    """O montar.py aceita o SHA-256 em maiúsculas; aqui também (normalizado para minúsculas antes de comparar)."""
    arq = _catalogo(tmp_path)
    dados = json.loads(arq.read_text(encoding="utf-8"))
    for f in dados["musicas"]["faixas"]:
        f["sha256"] = f["sha256"].upper()
    arq.write_text(json.dumps(dados), encoding="utf-8")
    faixas, fora = instalacao.ler_musicas(arq)
    assert fora == [] and all(f.sha256 == f.sha256.lower() for f in faixas)
    res = instalacao.instalar_musicas(arq, tmp_path / "m", Rede())
    assert sorted(res.baixadas) == sorted(FAIXAS) and res.fora == []
    assert sorted(instalacao.instalar_musicas(arq, tmp_path / "m", Rede()).ja_tinha) == sorted(FAIXAS)


def test_hash_conferido_antes_de_gravar_mesmo_com_tamanho_diferente(tmp_path):
    arq = _catalogo(tmp_path)
    res = instalacao.instalar_musicas(arq, tmp_path / "m", Rede({**FAIXAS, "Faixa Um.mp3": b"curta"}))
    assert [f.arquivo for f in res.fora] == ["Faixa Um.mp3"] and not (tmp_path / "m" / "Faixa Um.mp3").exists()


@pytest.mark.parametrize("url", [
    "https://exemplo-malicioso.test/Faixa.mp3",
    "http://raw.githubusercontent.com/SoundSafari/CC0-1.0-Music/main/x.mp3",                       # sem https
    "https://raw.githubusercontent.com@exemplo-malicioso.test/x.mp3",                               # o site de verdade é o depois do @
    "https://raw.githubusercontent.com.exemplo-malicioso.test/x.mp3",
    "https://raw.githubusercontent.com:8443/x.mp3",
    "file:///C:/Windows/win.ini",
])
def test_site_fora_da_lista_e_recusado_sem_nem_pedir(prog, dados, capsys, url):
    conteudo = b"ID3-mau" * 100
    _catalogo(prog, faixas={}, extras=[{"arquivo": "Faixa.mp3", "url": url, "sha256": _sha(conteudo), "tamanho": len(conteudo)}])
    rede = Rede({"Faixa.mp3": conteudo})
    assert _rodar(prog, dados, "--sem-nvidia", rede=rede) == 0
    assert rede.pedidos == [] and not (prog / "musicas" / "Faixa.mp3").exists()
    assert "AVISO: a música Faixa.mp3 ficou de fora: o endereço não é https de um site permitido" in capsys.readouterr().out
    assert not instalacao.url_permitida(url)


class _Resposta(io.BytesIO):
    def __init__(self, dados: bytes, final: str):
        super().__init__(dados)
        self.final = final

    def geturl(self):
        return self.final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


def _faixa(conteudo: bytes, url: str = URL + "x.mp3"):
    return instalacao.Faixa("x.mp3", "x", url, _sha(conteudo), len(conteudo))


def test_redirecionamento_para_site_fora_da_lista_e_recusado():
    conteudo = b"ID3" * 50
    for final in ("https://exemplo-malicioso.test/x.mp3", "http://media.githubusercontent.com/x.mp3"):
        with pytest.raises(instalacao.ErroDownload, match="fora da lista"):
            instalacao.baixar_faixa(_faixa(conteudo), abrir=lambda req, timeout, f=final: _Resposta(conteudo, f))
    ok = "https://media.githubusercontent.com/media/SoundSafari/x.mp3"                                # o GitHub manda o arquivo grande para outro servidor dele
    assert instalacao.baixar_faixa(_faixa(conteudo), abrir=lambda req, timeout: _Resposta(conteudo, ok)) == conteudo
    pedido = urllib.request.Request(URL + "x.mp3")
    with pytest.raises(instalacao.ErroDownload, match="fora da lista"):                            # o handler do urllib recusa antes de seguir
        instalacao._RedirecionaSoParaPermitido().redirect_request(pedido, io.BytesIO(), 302, "Found", HTTPMessage(),
                                                                  "https://exemplo-malicioso.test/x")


def test_arquivo_maior_que_o_catalogo_para_de_ler_na_hora():
    lidos = []

    class Sem_fim(_Resposta):
        def read(self, size: int | None = -1, /) -> bytes:
            lidos.append(size)
            return b"A" * (1 << 20)

    with pytest.raises(instalacao.ErroDownload, match="maior"):
        instalacao.baixar_faixa(_faixa(b"ID3" * 10), abrir=lambda req, timeout: Sem_fim(b"", URL + "x.mp3"))
    assert len(lidos) == 1


def test_arquivo_existente_nao_e_tocado(prog, dados, capsys):
    _catalogo(prog)
    (prog / "musicas").mkdir()
    minha = prog / "musicas" / "Faixa Um.mp3"
    minha.write_bytes(b"a minha versao, editada")                                                  # mesmo nome, outro conteúdo
    certa = prog / "musicas" / "Faixa Dois.mp3"
    certa.write_bytes(FAIXAS["Faixa Dois.mp3"])                                                     # já baixada antes, inteira
    dados.mkdir(parents=True)
    (dados / "config.toml").write_text('[transcricao]\nmodelo = "medium"  # meu\n', encoding="utf-8")
    antes = _foto(prog / "musicas", dados)
    rede = Rede()
    assert _rodar(prog, dados, "--sem-nvidia", rede=rede) == 0
    saida = capsys.readouterr().out
    assert _foto(prog / "musicas", dados) == antes                                                  # bytes e data iguais: nada regravado
    assert rede.pedidos == []
    assert "baixei 0, já tinha 1, ficaram de fora 1" in saida and "outro conteúdo; não mexi nele" in saida
    assert "config.toml já existia, não mexi" in saida


def test_criar_sem_sobrescrever_nunca_troca_o_que_existe(tmp_path):
    arq = tmp_path / "x.txt"
    assert instalacao.criar_sem_sobrescrever(arq, b"primeiro") is True
    assert instalacao.criar_sem_sobrescrever(arq, b"segundo") is False
    assert arq.read_bytes() == b"primeiro" and sorted(p.name for p in tmp_path.iterdir()) == ["x.txt"]


def test_faixa_sem_origem_e_as_de_fora_sao_listadas_com_o_motivo(prog, dados, capsys):
    conteudo = b"ID3-sem" * 100
    _catalogo(prog, extras=[{"arquivo": "Sem Origem.mp3", "sha256": _sha(conteudo), "tamanho": len(conteudo)},
                            {"arquivo": "Sem Hash.mp3", "url": URL + "Sem%20Hash.mp3", "tamanho": 10},
                            {"arquivo": "..\\fora.mp3", "url": URL + "f.mp3", "sha256": _sha(b"f"), "tamanho": 1}],
              fora=[{"arquivo": "black-box-legendary-9509.mp3", "motivo": "é do Pixabay (licença própria), não CC0"}])
    rede = Rede()
    assert _rodar(prog, dados, "--sem-nvidia", rede=rede) == 0
    saida = capsys.readouterr().out
    assert "baixei 2, já tinha 0, ficaram de fora 4: o porquê vem abaixo." in saida
    assert "[3/4] a música black-box-legendary-9509.mp3 ficou de fora: é do Pixabay (licença própria), não CC0" in saida
    assert "AVISO: a música Sem Origem.mp3 ficou de fora: sem origem (endereço de download) no catálogo" in saida
    assert "AVISO: a música Sem Hash.mp3 ficou de fora: sem SHA-256 no catálogo" in saida
    assert "ficou de fora: o nome do arquivo no catálogo não é válido" in saida
    assert sorted(rede.pedidos) == sorted(FAIXAS) and not (prog / "fora.mp3").exists()


def test_sem_componentes_json_pula_as_musicas_com_aviso(prog, dados, capsys):
    rede = Rede()
    assert _rodar(prog, dados, "--sem-nvidia", rede=rede) == 0
    saida = capsys.readouterr().out
    assert "AVISO: não achei a lista de músicas" in saida and rede.pedidos == [] and not (prog / "musicas").exists()
    assert "[4/4] Checagem: tudo certo" in saida


def test_sem_musicas_nao_baixa_nada(prog, dados, capsys):
    _catalogo(prog)
    rede = Rede()
    assert _rodar(prog, dados, "--sem-nvidia", "--sem-musicas", rede=rede) == 0
    assert rede.pedidos == [] and "Músicas livres: não pedidas" in capsys.readouterr().out


@pytest.mark.parametrize("texto", ["{nao e json", json.dumps({"esquema": 2, "musicas": {"faixas": []}}), json.dumps({"esquema": 1})])
def test_componentes_ilegivel_ou_de_outro_esquema_pula_com_aviso(prog, dados, capsys, texto):
    (prog / "instalador").mkdir()
    (prog / "instalador" / "componentes.json").write_text(texto, encoding="utf-8")
    assert _rodar(prog, dados, "--sem-nvidia") == 0
    assert "AVISO: a lista de músicas" in capsys.readouterr().out


# ── modelo de voz: sempre por último; falhar não invalida a instalação ─────────────────────────────────────────────────────────────
def test_modelo_sem_nvidia_e_o_small_no_cache_da_pasta_de_dados_e_e_o_ultimo(prog, dados, capsys, monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    modelo = Modelo()
    assert _rodar(prog, dados, "--sem-nvidia", "--com-gravacao-demo", "--com-modelo", modelo=modelo) == 0
    assert modelo.pedidos == [("small", dados.resolve() / "modelos" / "hub")]
    linhas = _linhas(capsys.readouterr().out)
    passos = [int(m.group(1)) for x in linhas if (m := re.match(r"^\[(\d+)/6\]", x))]
    assert passos == sorted(passos) and passos[-1] == 6
    assert linhas[-1] == "[6/6] Modelo de voz small: pronto."
    assert any(x.startswith("[6/6] Modelo de voz small: 0 de 100 MB") for x in linhas)              # o progresso aparece no andamento


def test_modelo_com_nvidia_e_o_do_config(prog, dados, capsys, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(dados.parent / "hf da pessoa"))
    modelo = Modelo()
    assert _rodar(prog, dados, "--nvidia", "--com-modelo", modelo=modelo) == 0
    assert modelo.pedidos == [("large-v3-turbo", dados.parent / "hf da pessoa" / "hub")]           # o HF_HOME que a pessoa já tem vale


def test_sem_dizer_a_placa_o_pos_procura_a_nvidia(prog, dados):
    for tem, esperado in ((False, "small"), (True, "large-v3-turbo")):
        modelo = Modelo()
        assert instalacao.main(["pos", "--dados", str(dados), "--com-modelo"], raiz_programa=prog, baixar_musica=Rede(),
                               baixar_terceiro=Terceiros(), baixar_modelo=modelo, checar=_checar_ok, tem_nvidia=lambda t=tem: t) == 0
        assert modelo.pedidos[0][0] == esperado


@pytest.mark.parametrize("erro", [RuntimeError("sem internet"), KeyboardInterrupt()])
def test_falha_ou_cancelamento_do_modelo_sai_com_0_e_aviso(prog, dados, capsys, erro):
    assert _rodar(prog, dados, "--sem-nvidia", "--com-modelo", modelo=Modelo(erro)) == 0
    linhas = _linhas(capsys.readouterr().out)
    assert linhas[-1].startswith("AVISO: modelo de voz não baixado") and "cartão da tela Início" in linhas[-1]
    assert (dados / "config.toml").is_file() and (prog / "pasta_dados.txt").is_file()               # a instalação continua válida


# ── falhas essenciais: código 1, e o que vem depois não roda ───────────────────────────────────────────────────────────────────────
def test_sem_o_config_exemplo_e_falha_essencial_no_passo_1(prog, dados, capsys):
    (prog / "config.exemplo.toml").unlink()
    terc, modelo = Terceiros(), Modelo()
    assert _rodar(prog, dados, "--sem-nvidia", "--com-modelo", terc=terc, modelo=modelo) == 1
    cap = capsys.readouterr()
    assert "[1/5] ERRO: Falta o config.exemplo.toml" in cap.out and "passo 1 de 5" in cap.err
    assert terc.pedidos == [] and modelo.pedidos == [] and not (prog / "pasta_dados.txt").exists()


def test_checagem_com_problema_grave_sai_com_1_e_nao_baixa_o_modelo(prog, dados, capsys):
    modelo = Modelo()
    assert _rodar(prog, dados, "--sem-nvidia", "--com-modelo", modelo=modelo, checar=_checar_grave) == 1
    assert "[4/5] ERRO: a checagem achou 1 problema(s) grave(s): FFmpeg: não abriu" in capsys.readouterr().out
    assert modelo.pedidos == []


def test_gravacao_pela_demo_pedida_que_falha_e_essencial(prog, dados, capsys):
    assert _rodar(prog, dados, "--sem-nvidia", "--com-gravacao-demo", terc=Terceiros(falhar={"hlae"})) == 1
    saida = capsys.readouterr().out
    assert "[4/5] ERRO: não consegui instalar o HLAE e o boiler-writter" in saida and "SHA-256" in saida
    assert not (prog / "bin" / "hlae").exists()


def test_chat_da_twitch_que_falha_e_so_aviso(prog, dados, capsys):
    assert _rodar(prog, dados, "--sem-nvidia", terc=Terceiros(falhar={"twitchdl"})) == 0
    saida = capsys.readouterr().out
    assert "AVISO: o TwitchDownloaderCLI (chat da Twitch, opcional) não foi instalado" in saida
    assert not (prog / "bin" / "TwitchDownloaderCLI.exe").exists()


def test_pasta_dados_txt_que_aponta_para_outra_pasta_nao_e_trocado(prog, dados, tmp_path, capsys):
    outra = tmp_path / "Dados antigos"
    outra.mkdir()
    (prog / "pasta_dados.txt").write_text(str(outra), encoding="utf-8-sig")
    antes = (prog / "pasta_dados.txt").read_bytes()
    assert _rodar(prog, dados, "--sem-nvidia") == 1
    assert "[1/4] ERRO: Este programa já usa outra pasta de dados" in capsys.readouterr().out
    assert (prog / "pasta_dados.txt").read_bytes() == antes and not dados.exists()


def test_pasta_dados_txt_que_ja_aponta_para_a_mesma_pasta_segue(prog, dados):
    (prog / "pasta_dados.txt").write_text(str(dados), encoding="utf-8-sig")
    antes = (prog / "pasta_dados.txt").stat().st_mtime_ns
    assert _rodar(prog, dados, "--sem-nvidia") == 0 and (prog / "pasta_dados.txt").stat().st_mtime_ns == antes


def test_pos_marca_a_pasta_de_dados_que_ele_criou(prog, dados, capsys):
    """O desinstalador só apaga algo da pasta de dados se ela tem o marcador `.bisturi-dados` (gravado aqui, nunca por cima)."""
    import cs2clip
    assert _rodar(prog, dados, "--sem-nvidia") == 0
    marcador = dados / instalacao.MARCADOR_DADOS
    assert instalacao.MARCADOR_DADOS == ".bisturi-dados"
    assert json.loads(marcador.read_text(encoding="utf-8")) == {"esquema": 1, "programa": "Bisturi Clips", "versao": cs2clip.__version__}
    assert "não pus o marcador" not in capsys.readouterr().out
    antes = marcador.stat().st_mtime_ns
    assert _rodar(prog, dados, "--sem-nvidia") == 0 and marcador.stat().st_mtime_ns == antes


@pytest.mark.parametrize("item", ["saida", "entrada", "config.toml", "modelos"])
def test_pasta_que_ja_tinha_coisas_com_os_nomes_do_programa_fica_sem_marcador(prog, dados, capsys, item):
    """Pasta escolhida que já tinha `saida`, `entrada`... da pessoa: sem marcador (o desinstalador nunca apaga nada dela) e um AVISO."""
    dados.mkdir(parents=True)
    (dados / item).mkdir() if "." not in item else (dados / item).write_text('[transcricao]\nmodelo = "small"\n', encoding="utf-8")
    assert _rodar(prog, dados, "--sem-nvidia") == 0
    saida = capsys.readouterr().out
    assert not (dados / instalacao.MARCADOR_DADOS).exists()
    assert re.search(rf"^AVISO: .*{re.escape(item)}.*desinstalador não vai apagar nada", saida, re.M), saida
    assert _rodar(prog, dados, "--sem-nvidia") == 0 and not (dados / instalacao.MARCADOR_DADOS).exists()   # nem na 2ª vez


def test_marcador_que_ja_existe_nao_e_tocado_mesmo_com_os_dados_da_pessoa(prog, dados, capsys):
    """Pasta de uma instalação anterior (os dados ficaram ao desinstalar): o marcador continua e vale."""
    (dados / "saida").mkdir(parents=True)
    (dados / instalacao.MARCADOR_DADOS).write_bytes(b'{"esquema": 1}')
    assert _rodar(prog, dados, "--sem-nvidia") == 0
    assert (dados / instalacao.MARCADOR_DADOS).read_bytes() == b'{"esquema": 1}' and "não pus o marcador" not in capsys.readouterr().out


def test_na_copia_de_desenvolvimento_nao_mexe_em_nada(prog, dados, capsys):
    (prog / ".git").write_text("gitdir: x", encoding="utf-8")                                       # como uma worktree
    terc = Terceiros()
    assert _rodar(prog, dados, "--sem-nvidia", terc=terc) == 1
    assert "cópia de desenvolvimento" in capsys.readouterr().out
    assert terc.pedidos == [] and not dados.exists() and not (prog / "pasta_dados.txt").exists()


# ── argumentos e caminhos ──────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("valor", ["dados_relativo", "", "\\\\servidor\\pasta"])
def test_dados_invalido_sai_com_2_sem_mexer_em_nada(prog, valor, capsys):
    terc = Terceiros()
    assert instalacao.main(["pos", "--dados", valor], raiz_programa=prog, baixar_terceiro=terc, checar=_checar_ok) == 2
    assert terc.pedidos == [] and not (prog / "pasta_dados.txt").exists() and "ERRO:" in capsys.readouterr().out


def test_dados_dentro_da_pasta_do_programa_e_recusado(prog, capsys):
    for dentro in (prog / "dados", prog.parent / "dados", prog.parent.parent):
        assert instalacao.main(["pos", "--dados", str(dentro)], raiz_programa=prog, checar=_checar_ok) == 2
        assert "a desinstalação apaga a pasta do programa inteira" in capsys.readouterr().out
    assert not (prog / "pasta_dados.txt").exists()


def test_raiz_do_disco_e_recusada_mesmo_com_o_programa_em_outro_disco(tmp_path, capsys):
    """Programa em outro disco (aqui um Z: de mentira): `C:\\`, `C:\\.` ou `C:\\x\\..` não passam pela regra "contém o programa"; a raiz
    do disco é recusada por ela mesma (o desinstalador apagaria pastas soltas na raiz)."""
    disco = Path(tmp_path.anchor)
    prog = Path("Z:/Bisturi Clips/app")
    for valor in (str(disco), str(disco) + ".", str(disco / "x") + "\\..", str(disco / "x" / ".." / ".")):
        with pytest.raises(ValueError, match="raiz do disco"):
            instalacao._validar_dados(valor, prog)
        terc = Terceiros()
        assert instalacao.main(["pos", "--dados", valor], raiz_programa=tmp_path / "app", baixar_terceiro=terc, checar=_checar_ok) == 2
        assert "raiz do disco" in capsys.readouterr().out and terc.pedidos == []
    assert instalacao._validar_dados(str(disco / "Bisturi Clips"), prog) == (disco / "Bisturi Clips").resolve()


def test_argumento_desconhecido_ou_faltando_sai_com_2():
    assert instalacao.main(["pos"]) == 2
    assert instalacao.main(["pos", "--dados", "C:\\x", "--nvidia", "--sem-nvidia"]) == 2
    assert instalacao.main(["instalar-tudo"]) == 2
    r = subprocess.run([sys.executable, "-m", "cs2clip.instalacao", "pos", "--dados", "relativo"], cwd=RAIZ, capture_output=True,
                       text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 2 and "não é um caminho completo" in r.stdout


def test_dados_com_acento_e_apostrofo_valem_para_o_programa(prog, tmp_path):
    dados = tmp_path / "Ação & Ünïcode d'Água" / "Bisturi Clips"
    assert _rodar(prog, dados, "--sem-nvidia", "--com-gravacao-demo") == 0
    assert (dados / "config.toml").is_file()
    assert caminhos.resolver_pasta_dados(prog, {}) == dados.resolve()
    assert (prog / "bin" / "hlae" / "HLAE.exe").is_file() and (prog / "bin" / "hlae" / "ffmpeg" / "bin" / "ffmpeg.exe").read_bytes() == b"MZ-ffmpeg-falso"
    assert terceiros.caminho_boiler(prog / "bin") is not None


def test_cache_do_modelo_segue_o_hugging_face_da_pessoa():
    d = Path("D:/dados")
    assert instalacao.cache_do_modelo(d, {}) == d / "modelos" / "hub"
    assert instalacao.cache_do_modelo(d, {"HF_HOME": "E:/hf"}) == Path("E:/hf") / "hub"
    assert instalacao.cache_do_modelo(d, {"HF_HOME": "E:/hf", "HF_HUB_CACHE": "F:/c"}) == Path("F:/c")


# ── checar ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_checar_usa_a_pasta_de_dados_do_programa(prog, dados, capsys, monkeypatch):
    monkeypatch.delenv(caminhos.VARIAVEL, raising=False)
    dados.mkdir(parents=True)
    (prog / "pasta_dados.txt").write_text(str(dados), encoding="utf-8-sig")
    vistos = []
    assert instalacao.main(["checar"], raiz_programa=prog, checar=lambda r, d: vistos.append(d) or _checar_ok(r, d)) == 0
    assert vistos == [dados.resolve()] and "Tudo certo para trabalhar." in capsys.readouterr().out
    assert instalacao.main(["checar"], raiz_programa=prog, checar=_checar_grave) == 1


def test_checar_de_verdade_inclui_a_pasta_de_dados(tmp_path, monkeypatch):
    from cs2clip import checagem
    monkeypatch.setattr(checagem, "checar", lambda raiz, saida=None, ativos=None: [])
    itens = instalacao.checar_ambiente(tmp_path, tmp_path)
    assert [(i.nome, i.ok) for i in itens] == [("Pasta de dados", True)]
    itens = instalacao.checar_ambiente(tmp_path, tmp_path / "nao existe")
    assert itens[0].nome == "Pasta de dados" and not itens[0].ok and itens[0].grave


# ── a dica do leitor de demos não manda usar o pip (o programa instalado não tem pip) ──────────────────────────────────────────────
def test_dica_do_leitor_de_demos_sem_pip(monkeypatch):
    from cs2clip import checagem, demo, render
    monkeypatch.setattr(checagem, "_versao", lambda exe: "ffmpeg version falso")
    monkeypatch.setattr(checagem, "_vram", lambda: None)
    monkeypatch.setattr(render, "_has_nvenc", lambda: False)
    monkeypatch.setattr(checagem, "_tem_modulo", lambda nome: nome != "demoparser2")
    leitor = next(i for i in checagem.checar(RAIZ, RAIZ / "saida_teste_nao_existe") if i.nome == "Leitor de demos (.dem)")
    assert not leitor.ok and "pip" not in leitor.como_resolver and "instalador" in leitor.como_resolver and "0.42.2" in leitor.como_resolver
    monkeypatch.setitem(sys.modules, "demoparser2", None)                                           # o import falha como sem o pacote
    with pytest.raises(demo.DemoInvalida) as e:
        demo._abrir(Path("x.dem"))
    assert "pip" not in str(e.value) and "instalador" in str(e.value)
