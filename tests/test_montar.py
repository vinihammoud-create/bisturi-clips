"""P5 (instalador): testes do `instalador/montar.py`. Nenhum teste usa a rede: o download é sempre um falso, e o ISCC também."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

RAIZ = Path(__file__).resolve().parents[1]


def _carregar(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


montar = _carregar("montar_p5", RAIZ / "instalador" / "montar.py")
ErroMontagem = montar.ErroMontagem


# ── falsos ────────────────────────────────────────────────────────────────────────────────────────
def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Resposta:
    def __init__(self, dados: bytes, url: str = "https://github.com/x/y"):
        self._d, self._url, self._i = dados, url, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n: int = -1) -> bytes:
        bloco = self._d[self._i:self._i + (n if n > 0 else len(self._d))]
        self._i += len(bloco)
        return bloco

    def geturl(self) -> str:
        return self._url


class Rede:
    """`abrir` falso: devolve `dados` e conta as chamadas."""

    def __init__(self, dados: bytes = b"", final: str = "https://release-assets.githubusercontent.com/z"):
        self.dados, self.final, self.chamadas = dados, final, 0

    def __call__(self, req, timeout=0):
        self.chamadas += 1
        return Resposta(self.dados, self.final)


def sem_rede(req, timeout=0):
    raise AssertionError("não devia usar a rede")


URL_OK = "https://github.com/astral-sh/uv/releases/download/0.13.10/uv-x86_64-pc-windows-msvc.zip"


# ── 1. dados: o componentes.json real e o esquema ─────────────────────────────────────────────────
def test_validar_passa_no_componentes_real():
    comp = montar.validar_tudo(RAIZ)
    assert comp.uv.versao and comp.ffmpeg.versao
    assert len(comp.faixas) == 31
    assert "black-box-legendary-9509.mp3" in comp.fora


def test_cli_validar_passa_no_repositorio(capsys):
    assert montar.main(["--validar"]) == 0
    assert "ok: uv" in capsys.readouterr().out


def test_ffmpeg_real_tem_o_tamanho_descompactado_da_prova_d():
    """O `ExternalSize` do FFmpeg no .iss vem do `tamanho_extraido` (345.175.907 B medidos na P1, ACHADOS_POC_DEF.md D.6)."""
    comp = montar.validar_tudo(RAIZ)
    assert comp.ffmpeg.tamanho == 36671021 and comp.ffmpeg.tamanho_extraido == 345175907
    assert comp.ffmpeg.url.startswith("https://github.com/") and comp.ffmpeg.sha256 == comp.ffmpeg.sha256.lower()


def test_componentes_real_so_https_e_dominios_permitidos_e_hash_de_64_hex():
    dados = json.loads((RAIZ / "instalador" / "componentes.json").read_bytes().decode("utf-8"))
    assert not (RAIZ / "instalador" / "componentes.json").read_bytes().startswith(b"\xef\xbb\xbf")
    itens = [dados["uv"], dados["ffmpeg"], *dados["musicas"]["faixas"]]
    for i in itens:
        assert montar.url_permitida(i["url"]) is None
        assert len(i["sha256"]) == 64 and int(i["sha256"], 16) >= 0
    assert len({f["arquivo"] for f in dados["musicas"]["faixas"]}) == 31


def _dados_ok() -> dict:
    h = "a" * 64
    return {"esquema": 1,
            "uv": {"versao": "0.13.10", "url": URL_OK, "sha256": h, "tamanho": 10},
            "ffmpeg": {"versao": "9.1", "url": "https://github.com/GyanD/codexffmpeg/releases/download/9.1/ffmpeg-9.1-essentials_build.7z",
                       "sha256": h, "tamanho": 10, "tamanho_extraido": 100},
            "musicas": {"faixas": [{"arquivo": "a.mp3", "titulo": "A", "sha256": h, "tamanho": 10,
                                    "url": "https://raw.githubusercontent.com/SoundSafari/CC0-1.0-Music/abc/freepd.com/a.mp3"}],
                        "fora": [{"arquivo": "b.mp3", "motivo": "Pixabay"}]}}


def test_dados_de_exemplo_servem():
    assert montar.problemas_dos_dados(_dados_ok()) == []


def _mut(f):
    d = copy.deepcopy(_dados_ok())
    f(d)
    return d


@pytest.mark.parametrize("mutacao", [
    lambda d: d.update(esquema=2),
    lambda d: d.update(esquema=True),
    lambda d: d["uv"].update(sha256="abc"),
    lambda d: d["uv"].update(sha256="z" * 64),
    lambda d: d["uv"].update(url=URL_OK.replace("https", "http")),
    lambda d: d["uv"].update(url="https://evil.example.com/uv.zip"),
    lambda d: d["uv"].update(url="https://github.com.evil.com/astral-sh/uv/releases/download/0.13.10/uv.zip"),
    lambda d: d["uv"].update(url="https://" + "user:pw" + "@github.com/astral-sh/uv/releases/download/0.13.10/uv.zip"),
    lambda d: d["uv"].update(url="https://github.com:8443/astral-sh/uv/releases/download/0.13.10/uv.zip"),
    lambda d: d["uv"].update(url=URL_OK.replace("0.13.10", "0.13.9")),             # a url não é da versão declarada
    lambda d: d["uv"].update(tamanho=0),
    lambda d: d["uv"].update(tamanho="10"),
    lambda d: d["ffmpeg"].pop("versao"),
    lambda d: d["musicas"]["faixas"][0].update(arquivo="../a.mp3"),
    lambda d: d["musicas"]["faixas"][0].update(arquivo="pasta/a.mp3"),
    lambda d: d["musicas"]["faixas"][0].update(arquivo="a.exe"),
    lambda d: d["musicas"]["faixas"][0].update(sha256=""),
    lambda d: d["musicas"]["faixas"].append(dict(d["musicas"]["faixas"][0])),            # repetida
    lambda d: d["musicas"]["fora"].append({"arquivo": "a.mp3", "motivo": "x"}),         # em faixas e em fora
    lambda d: d["musicas"]["fora"].append({"arquivo": "c.mp3", "motivo": ""}),
    lambda d: d["musicas"].pop("fora"),
    lambda d: d.pop("musicas"),
    lambda d: d.pop("uv"),
    lambda d: d["ffmpeg"].pop("tamanho_extraido"),                                     # o .iss precisa do ExternalSize
    lambda d: d["ffmpeg"].update(tamanho_extraido=0),
    lambda d: d["ffmpeg"].update(tamanho_extraido=True),
    lambda d: d["ffmpeg"].update(tamanho_extraido=10),                                 # descompactado não é menor que o .7z
    lambda d: d["ffmpeg"].update(url="https://raw.githubusercontent.com/GyanD/codexffmpeg/9.1/ffmpeg-9.1-essentials_build.7z"),
])
def test_esquema_invalido_e_recusado(mutacao):
    d = _mut(mutacao)
    assert montar.problemas_dos_dados(d)
    with pytest.raises(ErroMontagem):
        montar.validar_dados(d)


def test_arquivo_que_nao_e_json_objeto_ou_tem_bom_e_recusado(tmp_path):
    for conteudo in (b"[]", b"nao e json", b"\xef\xbb\xbf" + json.dumps(_dados_ok()).encode()):
        arq = tmp_path / "c.json"
        arq.write_bytes(conteudo)
        with pytest.raises(ErroMontagem):
            montar.ler_componentes(arq)


# ── 2. faixa sem origem fica listada em `fora` ────────────────────────────────────────────────────
def _raiz_com_catalogo(tmp_path: Path, nomes: list[str]) -> Path:
    (tmp_path / "musicas").mkdir()
    (tmp_path / "musicas" / "catalogo.json").write_text(json.dumps({"faixas": [{"arquivo": n} for n in nomes]}), encoding="utf-8")
    return tmp_path


def test_faixa_do_catalogo_sem_origem_nem_fora_e_apontada(tmp_path):
    comp = montar.validar_dados(_dados_ok())
    raiz = _raiz_com_catalogo(tmp_path, ["a.mp3", "b.mp3", "sem_origem.mp3"])
    p = montar.conferir_catalogo(comp, raiz)
    assert len(p) == 1 and "sem_origem.mp3" in p[0]


def test_faixa_listada_em_fora_ou_em_faixas_esta_certa_e_sobra_e_apontada(tmp_path):
    comp = montar.validar_dados(_dados_ok())
    assert montar.conferir_catalogo(comp, _raiz_com_catalogo(tmp_path, ["a.mp3", "b.mp3"])) == []
    outra = tmp_path / "o"
    outra.mkdir()
    assert any("a.mp3" in p for p in montar.conferir_catalogo(comp, _raiz_com_catalogo(outra, ["b.mp3"])))


def test_validar_tudo_recusa_catalogo_com_faixa_nao_listada(tmp_path):
    raiz = _raiz_com_catalogo(tmp_path, ["a.mp3", "b.mp3", "nova.mp3"])
    (raiz / "instalador").mkdir()
    (raiz / "instalador" / "componentes.json").write_text(json.dumps(_dados_ok()), encoding="utf-8")
    with pytest.raises(ErroMontagem, match="nova.mp3"):
        montar.validar_tudo(raiz)


def test_terceiros_vem_do_terceiros_py_e_nao_e_duplicado_aqui():
    from cs2clip import terceiros
    fonte = (RAIZ / "instalador" / "montar.py").read_text(encoding="utf-8")
    comp = (RAIZ / "instalador" / "componentes.json").read_text(encoding="utf-8")
    for t in terceiros.TERCEIROS.values():
        assert t.sha256 not in fonte and t.sha256 not in comp
    assert montar.conferir_terceiros() == []


def test_terceiro_com_dominio_ruim_e_apontado(monkeypatch):
    from cs2clip import terceiros
    ruim = terceiros.Terceiro("X", "1", "https://evil.example.com/x.zip", "a" * 64, 5, "x", 10)
    monkeypatch.setattr(terceiros, "TERCEIROS", {"x": ruim})
    assert any("terceiros.x" in p for p in montar.conferir_terceiros())


# ── 3. download com hash ──────────────────────────────────────────────────────────────────────────
def test_hash_errado_e_recusado_e_nada_e_gravado(tmp_path):
    bom = b"conteudo certo"
    rede = Rede(b"CONTEUDO CERTO")                                  # mesmo tamanho, hash diferente
    destino = tmp_path / "cache" / "uv.zip"
    with pytest.raises(ErroMontagem, match="SHA-256"):
        montar.baixar_conferido(URL_OK, sha(bom), len(bom), destino, rede)
    assert rede.chamadas == 1
    assert list((tmp_path / "cache").iterdir()) == []                  # nem o destino nem o .parcial


def test_tamanho_errado_e_recusado_e_nada_e_gravado(tmp_path):
    bom = b"0123456789"
    for dados in (bom[:5], bom + b"extra"):
        with pytest.raises(ErroMontagem, match="tamanho|maior"):
            montar.baixar_conferido(URL_OK, sha(bom), len(bom), tmp_path / "c" / "f.zip", Rede(dados))
        assert list((tmp_path / "c").iterdir()) == []


def test_hash_certo_grava_atomico_e_segunda_vez_nao_usa_a_rede(tmp_path):
    bom = b"zip de verdade"
    destino = tmp_path / "c" / "uv.zip"
    r1, novo1 = montar.baixar_conferido(URL_OK, sha(bom), len(bom), destino, Rede(bom))
    assert novo1 and r1.read_bytes() == bom
    assert [p.name for p in destino.parent.iterdir()] == ["uv.zip"]
    _, novo2 = montar.baixar_conferido(URL_OK, sha(bom), len(bom), destino, sem_rede)
    assert not novo2


def test_arquivo_existente_com_hash_certo_nao_e_tocado(tmp_path):
    bom = b"ja estava aqui"
    destino = tmp_path / "uv.zip"
    destino.write_bytes(bom)
    antes = destino.stat().st_mtime_ns
    montar.baixar_conferido(URL_OK, sha(bom), len(bom), destino, sem_rede)
    assert destino.read_bytes() == bom and destino.stat().st_mtime_ns == antes


def test_arquivo_existente_nao_e_tocado_quando_o_novo_download_e_ruim(tmp_path):
    esperado = b"o certo (12)"
    destino = tmp_path / "uv.zip"
    destino.write_bytes(b"estragado")
    with pytest.raises(ErroMontagem):
        montar.baixar_conferido(URL_OK, sha(esperado), len(esperado), destino, Rede(b"outra coisa!"))
    assert destino.read_bytes() == b"estragado"                       # o que existia continua como estava
    assert [p.name for p in tmp_path.iterdir()] == ["uv.zip"]


def test_arquivo_existente_estragado_e_trocado_so_depois_do_hash_certo(tmp_path):
    bom = b"conteudo bom"
    destino = tmp_path / "uv.zip"
    destino.write_bytes(b"estragado")
    montar.baixar_conferido(URL_OK, sha(bom), len(bom), destino, Rede(bom))
    assert destino.read_bytes() == bom


@pytest.mark.parametrize("url", [
    "https://evil.example.com/uv.zip",
    "http://github.com/astral-sh/uv.zip",
    "https://github.com.evil.com/uv.zip",
    "ftp://github.com/uv.zip",
    "https://objects.githubusercontent.com/uv.zip",                    # só vale como destino de redirecionamento, nunca como url inicial
    "",
])
def test_dominio_fora_da_lista_e_recusado_sem_usar_a_rede(tmp_path, url):
    with pytest.raises(ErroMontagem, match="recusado"):
        montar.baixar_conferido(url, "a" * 64, 5, tmp_path / "f.zip", sem_rede)
    assert list(tmp_path.iterdir()) == []


def test_download_sem_hash_ou_tamanho_validos_e_recusado(tmp_path):
    for sha_, tam in (("", 5), ("abc", 5), ("a" * 64, 0)):
        with pytest.raises(ErroMontagem):
            montar.baixar_conferido(URL_OK, sha_, tam, tmp_path / "f.zip", sem_rede)


def test_download_que_termina_em_endereco_nao_permitido_e_recusado(tmp_path):
    bom = b"abc"
    with pytest.raises(ErroMontagem, match="endereço"):
        montar.baixar_conferido(URL_OK, sha(bom), 3, tmp_path / "f.zip", Rede(bom, final="https://evil.example.com/x"))
    assert list(tmp_path.iterdir()) == []


def test_redirecionamento_para_fora_da_lista_e_recusado():
    h = montar._RedirecionamentoSeguro()
    req = SimpleNamespace(full_url=URL_OK)
    with pytest.raises(ErroMontagem, match="redirecionamento"):
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/uv.zip")
    with pytest.raises(ErroMontagem, match="redirecionamento"):
        h.redirect_request(req, None, 302, "Found", {}, "http://objects.githubusercontent.com/uv.zip")


def test_baixar_componentes_traz_uv_e_ffmpeg_para_o_cache(tmp_path):
    uv, ff = b"zip do uv", b"7z do ffmpeg"
    d = _dados_ok()
    d["uv"].update(sha256=sha(uv), tamanho=len(uv))
    d["ffmpeg"].update(sha256=sha(ff), tamanho=len(ff))
    comp = montar.validar_dados(d)

    def abrir(req, timeout=0):
        return Resposta(uv if "uv-x86" in req.full_url else ff)
    r = montar.baixar_componentes(comp, tmp_path / "cache", abrir)
    assert r["uv"].read_bytes() == uv and r["ffmpeg"].read_bytes() == ff
    montar.baixar_componentes(comp, tmp_path / "cache", sem_rede)       # idempotente: tudo já está lá


# ── 4. caminhos seguros ───────────────────────────────────────────────────────────────────────────
def test_pasta_do_projeto_aceita_so_subpasta_fora_do_codigo(tmp_path):
    assert montar.pasta_do_projeto(".tmp_agentes/pacote", tmp_path) == (tmp_path / ".tmp_agentes" / "pacote").resolve()
    for ruim in ("..", "../fora", str(tmp_path.parent / "fora"), ".", "cs2clip/pacote", "ui", "docs/x", ".git/x", "instalador/p", "", 'a"b'):
        with pytest.raises(ErroMontagem):
            montar.pasta_do_projeto(ruim, tmp_path)
    with pytest.raises(ErroMontagem):
        montar.pasta_do_projeto(str(tmp_path), tmp_path)


# ── 5. o pacote ───────────────────────────────────────────────────────────────────────────────────
def _zip_uv(pasta: Path) -> tuple[Path, str]:
    z = pasta / "uv.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("uv.exe", b"MZ-uv-falso")
        f.writestr("uvx.exe", b"MZ-uvx")
    return z, sha(z.read_bytes())


def _sfx_falso(pasta: Path) -> dict[str, Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    nomes = ("whoosh_transicao", "impacto_hs", "riser_clutch", "bass_drop")
    for n in nomes:
        (pasta / f"{n}.wav").write_bytes(b"RIFF")
    return {n: pasta / f"{n}.wav" for n in nomes}


def _escrever(raiz: Path, rel: str, conteudo: bytes | str = "x") -> None:
    p = raiz / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(conteudo if isinstance(conteudo, bytes) else conteudo.encode("utf-8"))


PROIBIDOS_PLANTADOS = [
    "cs2clip/__pycache__/m.cpython-312.pyc", "cs2clip/video.mp4", "cs2clip/partida.dem", "cs2clip/musica.mp3", "cs2clip/som.wav",
    "cs2clip/saida2/relatorio.txt", "cs2clip/dados/segredos_steam.json", "cs2clip/config.toml", "cs2clip/config.toml.bak",
    "ui/tests/t.py", "ui/.venv/lib.py", "ui/.git/HEAD", "ui/.tmp_agentes/x.txt", "ui/docs/interno.md", "ui/PEDIDOS_AO_NUCLEO.md",
    "ui/static/token_x.json", "ui/ui.log", "ui/static/.env", "cs2clip/entrada/v.mkv", "cs2clip/historico.json", "cs2clip/pasta_dados.txt",
    "cs2clip/bin_x.exe", "cs2clip/x.dll",
]


def _raiz_falsa(tmp_path: Path) -> Path:
    raiz = tmp_path / "proj"
    for rel, c in {"cs2clip/__init__.py": '__version__ = "9.9.9"\n', "cs2clip/mod.py": "x = 1\n", "cs2clip/assets/icons/a.png": b"PNG",
                   "cs2clip/assets/fontes/f.ttf": b"TTF", "ui/__init__.py": "", "ui/static/index.html": "<html>", "config.exemplo.toml": "[a]\n",
                   "componentes.json": "{}", "overrides.txt": "x==1\n", "LICENSE": "MIT", "CREDITOS.md": "c", "README.md": "r",
                   "requirements-runtime.lock": "a==1 --hash=sha256:" + "a" * 64, "requirements-gpu.lock": "b==1",
                   "musicas/catalogo.json": '{"faixas": []}', "musicas/licencas.txt": "l", "musicas/COMO_ADICIONAR_MUSICAS.md": "m",
                   "bin/COPYRIGHT.txt": "c", "bin/TwitchDownloaderCLI.exe": b"MZ",
                   "instalador/componentes.json": json.dumps(_dados_ok())}.items():
        _escrever(raiz, rel, c)
    for rel in PROIBIDOS_PLANTADOS:
        _escrever(raiz, rel, "proibido")
    # lixo na raiz e pastas que não são do programa
    for rel in ("config.toml", "dados/jogador/a.png", "saida/x.txt", "saida2/y.txt", "entrada/v.mp4", "tests/test_a.py", "docs/STATUS.md",
                "CLAUDE.md", ".git/HEAD", ".venv/Scripts/python.exe", ".tmp_agentes/x", "demos/a.dem", "musicas/Faixa.mp3",
                "musicas/historico.json", "musicas/catalogo_usuario.json", "tools/exportar_publico.py", "gravacao_demo/x/y.mp4"):
        _escrever(raiz, rel, "proibido")
    return raiz


def _montar_falso(tmp_path: Path):
    raiz = _raiz_falsa(tmp_path)
    z, h = _zip_uv(tmp_path)
    destino = raiz / ".tmp_agentes" / "pacote"
    lista = montar.montar_pacote(raiz, destino, z, h, _sfx_falso)
    return raiz, destino, lista


def _arquivos(pasta: Path) -> set[str]:
    return {p.relative_to(pasta).as_posix() for p in pasta.rglob("*") if p.is_file()}


def test_pacote_tem_o_que_o_instalador_precisa(tmp_path):
    _, destino, lista = _montar_falso(tmp_path)
    arqs = _arquivos(destino)
    for esperado in ("uv.exe", "app/cs2clip/__init__.py", "app/cs2clip/mod.py", "app/cs2clip/assets/icons/a.png", "app/ui/static/index.html",
                     "app/componentes.json", "app/instalador/componentes.json", "app/config.exemplo.toml", "app/overrides.txt",
                     "app/requirements-runtime.lock", "app/requirements-gpu.lock", "app/LICENSE", "app/CREDITOS.md",
                     "app/musicas/catalogo.json", "app/musicas/licencas.txt", "app/bin/COPYRIGHT.txt",
                     "app/cs2clip/assets/sfx/whoosh_transicao.wav", "app/cs2clip/assets/sfx/bass_drop.wav"):
        assert esperado in arqs, esperado
    assert (destino / "uv.exe").read_bytes() == b"MZ-uv-falso"
    assert set(lista) == arqs


def test_pacote_nao_leva_nada_proibido(tmp_path):
    _, destino, _ = _montar_falso(tmp_path)
    arqs = _arquivos(destino)
    # só os 4 wav gerados (em assets/sfx) e o uv.exe da raiz são exceções
    esperados_wav = {f"app/cs2clip/assets/sfx/{n}.wav" for n in ("whoosh_transicao", "impacto_hs", "riser_clutch", "bass_drop")}
    for a in arqs - esperados_wav - {"uv.exe"}:
        assert montar.motivo_proibido(a) is None, a
    for a in arqs:
        assert not a.startswith(("app/tests/", "app/docs/", "app/dados/", "app/saida", "app/entrada/", "app/.git", "app/.venv", "app/.tmp_agentes"))
        assert not a.endswith((".mp4", ".dem", ".mp3", ".pyc", ".mkv"))
    assert "app/config.toml" not in arqs and "app/CLAUDE.md" not in arqs
    assert "app/bin/TwitchDownloaderCLI.exe" not in arqs               # o exe vem do terceiros.py na instalação, nunca do pacote
    assert "app/ui/PEDIDOS_AO_NUCLEO.md" not in arqs
    assert "app/musicas/historico.json" not in arqs and "app/musicas/catalogo_usuario.json" not in arqs
    assert [a for a in montar.verificar_pacote(destino) if "sfx" not in a and a != "uv.exe"] == []


def test_verificador_pega_arquivo_proibido_plantado_no_pacote(tmp_path):
    _, destino, _ = _montar_falso(tmp_path)
    for rel in ("app/dados/a.json", "app/saida3/x.txt", "app/cs2clip/v.mp4", "app/config.toml", "app/x/.git/HEAD", "app/tests/t.py"):
        _escrever(destino, rel, "x")
    achados = "\n".join(montar.verificar_pacote(destino))
    for rel in ("app/dados", "app/saida3", "app/cs2clip/v.mp4", "app/config.toml", "app/x/.git", "app/tests"):
        assert rel in achados, rel


def test_pacote_sem_arquivo_obrigatorio_e_recusado(tmp_path):
    raiz = _raiz_falsa(tmp_path)
    (raiz / "overrides.txt").unlink()
    z, h = _zip_uv(tmp_path)
    with pytest.raises(ErroMontagem, match="overrides.txt"):
        montar.montar_pacote(raiz, raiz / ".tmp_agentes" / "p", z, h, _sfx_falso)


def test_pacote_e_idempotente_e_refaz_sem_sobrar_arquivo_velho(tmp_path):
    raiz, destino, _ = _montar_falso(tmp_path)
    antes = _arquivos(destino)
    _escrever(destino, "app/sobra_antiga.py", "velho")
    z, h = _zip_uv(tmp_path)
    montar.montar_pacote(raiz, destino, z, h, _sfx_falso)
    assert _arquivos(destino) == antes


def test_pasta_de_pacote_com_outras_coisas_nao_e_apagada(tmp_path):
    raiz = _raiz_falsa(tmp_path)
    z, h = _zip_uv(tmp_path)
    destino = raiz / ".tmp_agentes" / "meus_arquivos"
    _escrever(destino, "importante.docx", "do dono")
    with pytest.raises(ErroMontagem, match="importante.docx"):
        montar.montar_pacote(raiz, destino, z, h, _sfx_falso)
    assert (destino / "importante.docx").read_text() == "do dono"


def test_uv_com_hash_errado_ou_sem_zip_e_recusado(tmp_path):
    raiz = _raiz_falsa(tmp_path)
    z, _ = _zip_uv(tmp_path)
    with pytest.raises(ErroMontagem, match="hash"):
        montar.montar_pacote(raiz, raiz / ".tmp_agentes" / "p", z, "0" * 64, _sfx_falso)
    with pytest.raises(ErroMontagem, match="--baixar"):
        montar.montar_pacote(raiz, raiz / ".tmp_agentes" / "p", tmp_path / "nao_existe.zip", "0" * 64, _sfx_falso)


def test_extrair_uv_recusa_zip_sem_uv_exe(tmp_path):
    z = tmp_path / "z.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("pasta/uv.exe", b"x")
    with pytest.raises(ErroMontagem, match="não tem o uv.exe"):
        montar.extrair_uv(z, sha(z.read_bytes()), tmp_path / "o" / "uv.exe")
    assert not (tmp_path / "o" / "uv.exe").exists()


def test_gerador_de_sfx_real_gera_os_4_wav(tmp_path):
    r = montar.gerar_sfx_padrao(tmp_path / "sfx")
    assert len(r) == 4 and all(p.is_file() and p.stat().st_size > 1000 for p in r.values())


def test_sfx_incompleto_derruba_a_montagem(tmp_path):
    raiz = _raiz_falsa(tmp_path)
    z, h = _zip_uv(tmp_path)
    with pytest.raises(ErroMontagem, match="efeitos sonoros"):
        montar.montar_pacote(raiz, raiz / ".tmp_agentes" / "p", z, h, lambda pasta: {})


@pytest.mark.skipif(not (RAIZ / "tools" / "exportar_publico.py").is_file(), reason="o exportador é só da oficina: esta cópia é a pública")
def test_pacote_do_repositorio_real_so_tem_arquivo_do_git_e_publico(tmp_path):
    """O pacote real (sem baixar nada, sem gerar sfx de verdade) só leva arquivos que o git conhece e que a exportação pública também leva."""
    if not (RAIZ / ".git").exists() or shutil.which("git") is None:
        pytest.skip("sem repositório git")
    expor = _carregar("exportar_publico_p5", RAIZ / "tools" / "exportar_publico.py")
    rastreados = montar.arquivos_rastreados(RAIZ)
    assert rastreados
    z, h = _zip_uv(tmp_path)
    destino = tmp_path / "pacote"          # aqui só se chama montar_pacote: a regra "dentro do projeto" é da linha de comando
    montar.montar_pacote(RAIZ, destino, z, h, _sfx_falso)
    arqs = _arquivos(destino)
    assert "uv.exe" in arqs and "app/cs2clip/__init__.py" in arqs and "app/instalador/componentes.json" in arqs
    gerados = {a for a in arqs if a.startswith("app/cs2clip/assets/sfx/")} | {"uv.exe"}
    for a in arqs - gerados:
        rel = a.removeprefix("app/")
        assert rel in rastreados, f"{rel} não está no git"
        assert expor.publico(rel), f"{rel} não vai ao repositório público"
        assert not rel.startswith(("tests/", "docs/", ".claude/", ".git", "tools/")) and rel != "CLAUDE.md"
    assert montar.motivo_proibido("app/tests/x.py") and montar.motivo_proibido("app/docs/x.md")


# ── 6. o compilador ───────────────────────────────────────────────────────────────────────────────
FFMPEG = montar.validar_dados(_dados_ok()).ffmpeg


def _defines(args: list[str]) -> dict[str, str]:
    return dict(a[2:].split("=", 1) for a in args if a.startswith("/D"))


def _raiz_para_compilar(tmp_path: Path) -> tuple[Path, Path, Path]:
    raiz = tmp_path / "r"
    _escrever(raiz, "instalador/BisturiClips.iss", "; falso")
    pacote = raiz / ".tmp_agentes" / "pacote"
    _escrever(pacote, "app/x.py", "x")
    _escrever(pacote, "uv.exe", "MZ")
    iscc = tmp_path / "Inno" / "ISCC.exe"
    _escrever(iscc.parent, "ISCC.exe", "MZ")
    return raiz, pacote, iscc


def test_compilar_chama_o_iscc_com_lista_de_argumentos(tmp_path):
    raiz, pacote, iscc = _raiz_para_compilar(tmp_path)
    chamadas: list = []

    def executar(args, **kw):
        chamadas.append((args, kw))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    saida = raiz / ".tmp_agentes" / "saida"
    montar.compilar(raiz, pacote, "1.1.0", saida, FFMPEG, "6b1d2a44-3c7e-4f59-9a10-7e2c5d8b0f13", str(iscc), executar)
    args, kw = chamadas[0]
    assert isinstance(args, list) and not kw.get("shell")
    assert args[0] == str(iscc)
    assert "/DAppVersion=1.1.0" in args
    assert f"/DPacote={pacote}" in args
    assert "/DAppIdGuid={{6B1D2A44-3C7E-4F59-9A10-7E2C5D8B0F13}" in args
    assert f"/O{saida}" in args and args[-1] == str(raiz / "instalador" / "BisturiClips.iss")
    d = _defines(args)
    assert d["FFmpegUrl"] == FFMPEG.url and d["FFmpegSha256"] == "a" * 64 and d["FFmpegTamanhoExtraido"] == "100"


def test_todo_define_obrigatorio_do_iss_e_passado_pelo_montar(tmp_path):
    """Cruza as duas listas: cada `#ifndef X` + `#error` do .iss real tem o seu /DX= nos argumentos que o montar passa ao ISCC."""
    iss = (RAIZ / "instalador" / "BisturiClips.iss").read_text(encoding="utf-8-sig")
    obrigatorios = set(re.findall(r"^#ifndef (\w+)\s*\n\s*#error ", iss, re.M))
    assert {"Pacote", "FFmpegUrl", "FFmpegSha256", "FFmpegTamanhoExtraido"} <= obrigatorios
    raiz, pacote, iscc = _raiz_para_compilar(tmp_path)
    vistos: list = []
    montar.compilar(raiz, pacote, "1.1.0", raiz / "s", FFMPEG, None, str(iscc),
                    lambda args, **kw: vistos.append(args) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert obrigatorios <= set(_defines(vistos[0])), obrigatorios - set(_defines(vistos[0]))


def test_ffmpeg_sem_tamanho_extraido_nao_compila(tmp_path):
    raiz, pacote, iscc = _raiz_para_compilar(tmp_path)
    for ruim in (montar.Baixavel(FFMPEG.versao, FFMPEG.url, FFMPEG.sha256, FFMPEG.tamanho),
                 montar.Baixavel(FFMPEG.versao, "https://raw.githubusercontent.com/x/9.1/f.7z", FFMPEG.sha256, 10, 100),
                 montar.Baixavel(FFMPEG.versao, FFMPEG.url, "abc", 10, 100)):
        with pytest.raises(ErroMontagem, match="FFmpeg"):
            montar.compilar(raiz, pacote, "1.1.0", raiz / "s", ruim, None, str(iscc), lambda *a, **k: pytest.fail("não devia chamar"))


def test_cli_compilar_passa_o_ffmpeg_do_componentes_ao_iscc(tmp_path, monkeypatch):
    """A linha de comando inteira (`--baixar --pacote --compilar`), com rede e ISCC falsos: os /D do FFmpeg saem do componentes.json."""
    raiz, abrir = _raiz_da_cli(tmp_path)
    _escrever(raiz, "instalador/BisturiClips.iss", "; falso")
    monkeypatch.setattr(montar, "gerar_sfx_padrao", _sfx_falso)
    _, _, iscc = _raiz_para_compilar(tmp_path)
    vistos: list = []
    assert montar.main(["--baixar", "--pacote", ".tmp_agentes/pacote", "--compilar", "--iscc", str(iscc)], raiz=raiz, abrir=abrir,
                       executar=lambda args, **kw: vistos.append(args) or SimpleNamespace(returncode=0, stdout="", stderr="")) == 0
    d = _defines(vistos[0])
    comp = json.loads((raiz / "instalador" / "componentes.json").read_text(encoding="utf-8"))["ffmpeg"]
    assert d["FFmpegUrl"] == comp["url"] and d["FFmpegSha256"] == comp["sha256"].lower()
    assert d["FFmpegTamanhoExtraido"] == str(comp["tamanho_extraido"])


def test_compilar_sem_guid_nao_passa_appidguid_e_caminho_com_espaco_acento_e_apostrofo_vai_inteiro(tmp_path):
    raiz = tmp_path / "Pasta d'Avila é"
    _escrever(raiz, "instalador/BisturiClips.iss", "; falso")
    pacote = raiz / "meu pacote"
    _escrever(pacote, "app/x.py", "x")
    _escrever(pacote, "uv.exe", "MZ")
    _, _, iscc = _raiz_para_compilar(tmp_path)
    visto: list = []

    def executar(args, **kw):
        visto.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    montar.compilar(raiz, pacote, "1.1.0", raiz / "saida x", FFMPEG, None, str(iscc), executar)
    assert not any(a.startswith("/DAppIdGuid") for a in visto[0])
    assert f"/DPacote={pacote}" in visto[0]                             # um único argumento: nada é dividido nem interpretado por shell


@pytest.mark.parametrize("versao,guid", [("1.1", None), ("1.1.0; calc", None), ("v1.1.0", None), ("1.1.0", "nao-e-guid"),
                                         ("1.1.0", "{6B1D2A44-3C7E-4F59-9A10-7E2C5D8B0F13}"), ("1.1.0", "6B1D2A44-3C7E-4F59-9A10-7E2C5D8B0F13 /DX=1")])
def test_versao_ou_guid_invalidos_sao_recusados(tmp_path, versao, guid):
    raiz, pacote, iscc = _raiz_para_compilar(tmp_path)
    with pytest.raises(ErroMontagem):
        montar.compilar(raiz, pacote, versao, raiz / "s", FFMPEG, guid, str(iscc), lambda *a, **k: pytest.fail("não devia chamar"))


def test_compilar_erro_do_iscc_e_relatado_e_sem_pacote_ou_iss_e_recusado(tmp_path):
    raiz, pacote, iscc = _raiz_para_compilar(tmp_path)
    with pytest.raises(ErroMontagem, match="ISCC falhou"):
        montar.compilar(raiz, pacote, "1.1.0", raiz / "s", FFMPEG, None, str(iscc),
                        lambda *a, **k: SimpleNamespace(returncode=2, stdout="Error on line 3", stderr=""))
    (pacote / "uv.exe").unlink()
    with pytest.raises(ErroMontagem, match="pacote"):
        montar.compilar(raiz, pacote, "1.1.0", raiz / "s", FFMPEG, None, str(iscc), lambda *a, **k: pytest.fail("não devia chamar"))
    (raiz / "instalador" / "BisturiClips.iss").unlink()
    with pytest.raises(ErroMontagem, match="BisturiClips.iss"):
        montar.compilar(raiz, pacote, "1.1.0", raiz / "s", FFMPEG, None, str(iscc), lambda *a, **k: pytest.fail("não devia chamar"))


def test_achar_iscc_usa_explicito_variavel_ou_pastas_do_inno_e_falha_sem_nada(tmp_path):
    _, _, iscc = _raiz_para_compilar(tmp_path)
    nada = lambda _: None  # noqa: E731
    assert montar.achar_iscc(str(iscc), {}, nada) == iscc
    assert montar.achar_iscc(None, {"ISCC": str(iscc)}, nada) == iscc
    local = tmp_path / "L"
    _escrever(local / "Programs" / "Inno Setup 6", "ISCC.exe", "MZ")
    assert montar.achar_iscc(None, {"LOCALAPPDATA": str(local)}, nada).name == "ISCC.exe"
    with pytest.raises(ErroMontagem, match="ISCC"):
        montar.achar_iscc(None, {}, nada)
    _escrever(tmp_path, "outro.exe", "MZ")
    with pytest.raises(ErroMontagem):                                   # só aceita um arquivo chamado ISCC.exe
        montar.achar_iscc(str(tmp_path / "outro.exe"), {}, nada)


# ── 7. a linha de comando inteira, numa raiz falsa ────────────────────────────────────────────────
def _raiz_da_cli(tmp_path: Path):
    raiz = _raiz_falsa(tmp_path)
    uv = (tmp_path / "uv.zip")
    _zip_uv(tmp_path)
    ff = b"7z-falso"
    d = _dados_ok()
    d["uv"].update(sha256=sha(uv.read_bytes()), tamanho=uv.stat().st_size)
    d["ffmpeg"].update(sha256=sha(ff), tamanho=len(ff))
    (raiz / "instalador" / "componentes.json").write_text(json.dumps(d), encoding="utf-8")
    (raiz / "musicas" / "catalogo.json").write_text(json.dumps({"faixas": [{"arquivo": "a.mp3"}, {"arquivo": "b.mp3"}]}), encoding="utf-8")

    def abrir(req, timeout=0):
        return Resposta(uv.read_bytes() if "uv-x86" in req.full_url else ff)
    return raiz, abrir


def test_cli_baixar_e_pacote_de_ponta_a_ponta_com_rede_falsa(tmp_path, capsys, monkeypatch):
    raiz, abrir = _raiz_da_cli(tmp_path)
    monkeypatch.setattr(montar, "gerar_sfx_padrao", _sfx_falso)
    assert montar.main(["--baixar", "--pacote", ".tmp_agentes/pacote"], raiz=raiz, abrir=abrir) == 0
    assert (raiz / ".tmp_agentes" / "pacote" / "uv.exe").read_bytes() == b"MZ-uv-falso"
    assert (raiz / ".tmp_agentes" / "cache_instalador" / "uv-x86_64-pc-windows-msvc.zip").is_file()
    # segunda vez: idempotente e sem rede
    assert montar.main(["--baixar", "--pacote", ".tmp_agentes/pacote"], raiz=raiz, abrir=sem_rede) == 0
    assert "já estava no cache" in capsys.readouterr().out


def test_cli_pacote_sem_baixar_antes_falha_com_mensagem(tmp_path, capsys):
    raiz, _ = _raiz_da_cli(tmp_path)
    assert montar.main(["--pacote", ".tmp_agentes/pacote"], raiz=raiz) == 1
    assert "--baixar" in capsys.readouterr().err


def test_cli_hash_errado_no_download_sai_com_erro_e_deixa_o_cache_vazio(tmp_path, capsys):
    raiz, _ = _raiz_da_cli(tmp_path)
    assert montar.main(["--baixar"], raiz=raiz, abrir=Rede(b"x" * 8)) == 1
    assert "ERRO" in capsys.readouterr().err
    cache = raiz / ".tmp_agentes" / "cache_instalador"
    assert not cache.exists() or list(cache.iterdir()) == []


def test_cli_pacote_fora_do_projeto_e_recusado_e_argumentos_ruins_saem_com_2(tmp_path, capsys):
    raiz, _ = _raiz_da_cli(tmp_path)
    assert montar.main(["--pacote", str(tmp_path.parent / "fora")], raiz=raiz) == 1
    for argv in ([], ["--compilar"]):
        with pytest.raises(SystemExit) as e:
            montar.main(argv, raiz=raiz)
        assert e.value.code == 2


def test_cli_roda_como_script_de_verdade():
    """`python instalador/montar.py --validar` num processo à parte (o jeito do instalador e do CI)."""
    r = subprocess.run([sys.executable, str(RAIZ / "instalador" / "montar.py"), "--validar"], capture_output=True, text=True, encoding="utf-8",
                       cwd=RAIZ, timeout=120, check=False)
    assert r.returncode == 0, r.stderr
    assert "31 músicas" in r.stdout
