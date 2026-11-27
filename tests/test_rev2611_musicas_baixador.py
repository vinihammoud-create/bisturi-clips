"""Revisão de 26/11/2026, lote D1 (P-002): o baixador de músicas nunca destrói nem encolhe o catálogo e nunca rotula a licença errada."""
import importlib.util
import json
from pathlib import Path

import pytest

from cs2clip.musicas_catalogo import ErroCatalogo

RAIZ = Path(__file__).resolve().parents[1]
GRANDE = b"x" * 60_000
BLOB = "https://github.com/SoundSafari/CC0-1.0-Music/blob/main/"


def _baixador():
    spec = importlib.util.spec_from_file_location("baixar_musicas_cc0_rev2611", RAIZ / "tools" / "baixar_musicas_cc0.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _medir(arq):
    return {"duracao_s": 100.0, "bpm": 120.0, "drop_s": 10.0, "batidas_detectadas": 200, "intensidade": "media"}


class _Rede:
    """Troca o download: grava um arquivo qualquer e lembra o que foi pedido."""

    def __init__(self, falha=()):
        self.pedidos = []
        self.falha = set(falha)

    def __call__(self, caminho_repo, destino):
        self.pedidos.append(caminho_repo)
        if Path(caminho_repo).name in self.falha:
            raise OSError("sem rede")
        Path(destino).write_bytes(GRANDE)
        return Path(destino)


def _gravar_catalogo(pasta: Path, faixas: list[dict], **topo) -> dict:
    pasta.mkdir(parents=True, exist_ok=True)
    cat = {"fonte": "f", "licenca": "varia", "total_faixas": len(faixas), "faixas": faixas, **topo}
    (pasta / "catalogo.json").write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cat


def _lido(pasta: Path) -> dict:
    return json.loads((pasta / "catalogo.json").read_text(encoding="utf-8"))


def test_catalogo_existente_nao_encolhe_e_a_licenca_de_cada_faixa_fica(tmp_path):
    b = _baixador()
    pasta = tmp_path / "musicas"
    com_arquivo = {"arquivo": "Minha.mp3", "autor": "Eu", "licenca": "NCS (NoCopyrightSounds) — Usage Policy", "licenca_chave": "ncs", "exige_credito": True,
                   "credito": "Música: Eu — Minha", "emocao": 0.9, "drop_s": 12.0}
    sem_arquivo_cc0 = {"arquivo": "Komiku - 01 - Action Introduction.mp3", "autor": "Komiku", "licenca": "CC0 1.0 (domínio público)",
                       "fonte": BLOB + "chosic.com/Komiku_-_01_-_Action_Introduction%28chosic.com%29.mp3", "estilo": "x", "emocao": 0.67}
    sem_arquivo_pixabay = {"arquivo": "black-box-legendary-9509.mp3", "autor": "Pixabay", "licenca": "Pixabay Content License — https://pixabay.com/service/license-summary/",
                           "fonte": BLOB + "pixbay.com/black-box-legendary-9509.mp3", "aviso": "Content ID"}
    solta_do_usuario = {"arquivo": "Sem origem.mp3", "autor": "Eu", "licenca": "CC BY — https://exemplo.com/x"}
    antes = _gravar_catalogo(pasta, [com_arquivo, sem_arquivo_cc0, sem_arquivo_pixabay, solta_do_usuario])
    (pasta / "Minha.mp3").write_bytes(GRANDE)
    rede = _Rede()
    arquivos = b.baixar_musicas(pasta, baixar=rede, medir=_medir)
    depois = _lido(pasta)
    assert depois["faixas"][:4] == antes["faixas"]                                  # as 4 faixas continuam, iguais, na mesma ordem
    assert {f["arquivo"] for f in depois["faixas"]} >= {f["arquivo"] for f in antes["faixas"]}
    # as 14 do pacote inicial que o catálogo não lista entram (com a licença da coleção); nenhuma saiu
    assert len(depois["faixas"]) == 4 + 14 and depois["total_faixas"] == len(depois["faixas"])
    # baixou: a do catálogo (caminho decodificado), a do Pixabay, e o pacote; nunca a que não tem origem
    assert "chosic.com/Komiku_-_01_-_Action_Introduction(chosic.com).mp3" in rede.pedidos
    assert "pixbay.com/black-box-legendary-9509.mp3" in rede.pedidos and not any("Sem origem" in p for p in rede.pedidos)
    assert not (pasta / "Sem origem.mp3").exists() and (pasta / "Minha.mp3").exists() and len(arquivos) == len(rede.pedidos)
    # a licença de cada uma fica como o catálogo diz
    texto = (pasta / "licencas.txt").read_text(encoding="utf-8")
    assert "- black-box-legendary-9509.mp3 | Autor: Pixabay | Licença: Pixabay Content License" in texto
    assert "- Minha.mp3 | Autor: Eu | Licença: NCS" in texto and "Crédito: Música: Eu — Minha" in texto
    assert "- Sem origem.mp3 | Autor: Eu | Licença: CC BY" in texto


def test_pasta_sem_catalogo_cria_so_com_a_licenca_certa_e_a_do_pixabay_nao_e_cc0(tmp_path):
    b = _baixador()
    pasta = tmp_path / "musicas"
    rede = _Rede()
    b.baixar_musicas(pasta, baixar=rede, medir=_medir)
    cat = _lido(pasta)
    por_nome = {f["arquivo"]: f for f in cat["faixas"]}
    assert len(por_nome) == 15 == len(b.FAIXAS_CC0)
    pix = por_nome["black-box-legendary-9509.mp3"]
    assert pix["licenca"].startswith("Pixabay Content License") and pix["licenca_chave"] == "pixabay" and "Content ID" in pix["aviso"]
    assert all(f["licenca"].startswith("CC0 1.0") and f["licenca_chave"] == "cc0" for n, f in por_nome.items() if n != pix["arquivo"])
    texto = (pasta / "licencas.txt").read_text(encoding="utf-8")
    linha_pix = next(linha for linha in texto.splitlines() if linha.startswith("- black-box"))
    licenca_pix = linha_pix.split("| Licença: ")[1].split(" | Estilo:")[0]
    assert licenca_pix.startswith("Pixabay Content License") and "CC0" not in licenca_pix


def test_arquivo_solto_sem_origem_nao_entra_no_catalogo_nem_vira_cc0(tmp_path, capsys):
    b = _baixador()
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    (pasta / "meu_funk.mp3").write_bytes(GRANDE)
    b.baixar_musicas(pasta, baixar=_Rede(), medir=_medir)
    nomes = {f["arquivo"] for f in _lido(pasta)["faixas"]}
    assert "meu_funk.mp3" not in nomes
    assert "meu_funk.mp3" in capsys.readouterr().out                                 # avisa que ficou de fora


def test_faixa_que_ja_esta_no_catalogo_nao_e_refeita(tmp_path):
    b = _baixador()
    pasta = tmp_path / "musicas"
    faixa = {"arquivo": "Spec Ops.mp3", "autor": "Outro autor", "licenca": "licença revisada à mão", "emocao": 0.123, "bpm": 99.0}
    _gravar_catalogo(pasta, [faixa])
    (pasta / "Spec Ops.mp3").write_bytes(GRANDE)
    medidas = []
    b.baixar_musicas(pasta, baixar=_Rede(), medir=lambda arq: medidas.append(arq.name) or _medir(arq))
    assert _lido(pasta)["faixas"][0] == faixa and "Spec Ops.mp3" not in medidas


def test_catalogo_ilegivel_para_sem_baixar_nem_escrever_nada(tmp_path):
    b = _baixador()
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    (pasta / "catalogo.json").write_bytes(b"{ilegivel")
    rede = _Rede()
    with pytest.raises(ErroCatalogo):
        b.baixar_musicas(pasta, baixar=rede, medir=_medir)
    assert (pasta / "catalogo.json").read_bytes() == b"{ilegivel" and rede.pedidos == [] and sorted(p.name for p in pasta.iterdir()) == ["catalogo.json"]


def test_falha_num_download_nao_derruba_as_outras_nem_mexe_no_catalogo(tmp_path, capsys):
    b = _baixador()
    pasta = tmp_path / "musicas"
    antes = _gravar_catalogo(pasta, [{"arquivo": "Spec Ops.mp3", "autor": "a", "licenca": "CC0 1.0 (Domínio Público)"}])
    rede = _Rede(falha={"Spec Ops.mp3"})
    arquivos = b.baixar_musicas(pasta, baixar=rede, medir=_medir)
    assert (pasta / "Spec Ops.mp3").exists() is False and len(arquivos) == 14           # as outras 14 do pacote vieram
    assert _lido(pasta)["faixas"][0] == antes["faixas"][0] and "não consegui baixar Spec Ops.mp3" in capsys.readouterr().out


def test_nome_ou_caminho_perigoso_do_catalogo_nao_escreve_fora_da_pasta(tmp_path):
    b = _baixador()
    pasta = tmp_path / "musicas"
    _gravar_catalogo(pasta, [
        {"arquivo": "../fora.mp3", "autor": "a", "licenca": "x", "fonte": BLOB + "freepd.com/a.mp3"},
        {"arquivo": "bom.mp3", "autor": "a", "licenca": "x", "fonte": BLOB + "freepd.com/../../outro/a.mp3"},
        {"arquivo": "sub/dir.mp3", "autor": "a", "licenca": "x", "fonte": BLOB + "freepd.com/b.mp3"},
    ])
    rede = _Rede()
    b.baixar_musicas(pasta, baixar=rede, medir=_medir)
    assert not (tmp_path / "fora.mp3").exists() and not (pasta / "bom.mp3").exists() and not (pasta / "sub").exists()
    assert not any(".." in p for p in rede.pedidos) and "freepd.com/a.mp3" not in rede.pedidos and "freepd.com/b.mp3" not in rede.pedidos


def test_o_catalogo_do_projeto_tem_origem_para_as_32_faixas_e_todas_as_licencas_ficam():
    b = _baixador()
    cat = json.loads((RAIZ / "musicas" / "catalogo.json").read_text(encoding="utf-8"))
    pedidos = b.faixas_a_baixar(cat)
    no_catalogo = [p for p in pedidos if p[0] in {f["arquivo"] for f in cat["faixas"]}]
    assert len(no_catalogo) == len(cat["faixas"]) == 32                                  # nenhuma ficou sem origem
    assert all(b.caminho_do_repo_valido(cam) and b.nome_local_valido(nome) for nome, cam, _a, _e in pedidos)
    assert len(pedidos) == 32                                                            # o pacote inicial já está todo no catálogo


def test_licenca_da_colecao_pixabay_nunca_e_cc0():
    b = _baixador()
    assert b.licenca_da_colecao("pixbay.com/x.mp3")["licenca"].startswith("Pixabay Content License")
    for cam in ("freepd.com/x.mp3", "chosic.com/Komiku.mp3"):
        assert b.licenca_da_colecao(cam)["licenca"].startswith("CC0 1.0")


def test_readme_e_o_guia_ensinam_como_ter_musicas_num_clone_novo():
    for arq in ("README.md", "musicas/COMO_ADICIONAR_MUSICAS.md"):
        texto = (RAIZ / arq).read_text(encoding="utf-8")
        assert "baixar_musicas_cc0.py" in texto and "licença" in texto.lower(), arq
    assert "tela Músicas" in (RAIZ / "README.md").read_text(encoding="utf-8")
