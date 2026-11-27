"""R-025 + M-009: `gravar_atomico(fsync=...)`; G-012 (resto): JSON persistente por gravar_atomico, segredo sem janela aberta."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cs2clip import sharecode, steam_api, tools


def _espiar(monkeypatch):
    eventos: list[str] = []
    real_fsync, real_subst = os.fsync, tools.substituir_arquivo
    monkeypatch.setattr(os, "fsync", lambda fd: (eventos.append("fsync"), real_fsync(fd))[1])

    def subst(tmp, destino):
        eventos.append(f"troca(tmp_tem_{tmp.stat().st_size}_bytes)")
        real_subst(tmp, destino)
    monkeypatch.setattr(tools, "substituir_arquivo", subst)
    return eventos


def test_r025_por_padrao_nao_da_fsync(tmp_path, monkeypatch):
    eventos = _espiar(monkeypatch)
    tools.gravar_atomico(tmp_path / "cache.json", "[1, 2]")
    assert "fsync" not in eventos
    assert (tmp_path / "cache.json").read_text(encoding="utf-8") == "[1, 2]"


def test_r025_com_fsync_o_conteudo_vai_ao_disco_antes_da_troca(tmp_path, monkeypatch):
    eventos = _espiar(monkeypatch)
    tools.gravar_atomico(tmp_path / "x.json", '{"a": 1}', fsync=True)
    assert eventos == ["fsync", "troca(tmp_tem_8_bytes)"]
    assert not list(tmp_path.glob("*.tmp"))


def test_r025_fsync_tambem_vale_para_bytes(tmp_path, monkeypatch):
    eventos = _espiar(monkeypatch)
    tools.gravar_atomico(tmp_path / "x.bin", b"\x00\x01\x02", fsync=True)
    assert eventos == ["fsync", "troca(tmp_tem_3_bytes)"]
    assert (tmp_path / "x.bin").read_bytes() == b"\x00\x01\x02"


def test_r025_a_docstring_nao_promete_o_que_nao_entrega():
    doc = tools.gravar_atomico.__doc__ or ""
    assert "fsync" in doc and "queda de energia" in doc.lower()


# ── G-012 (resto) e G-019 (6): segredo do Steam sem janela de exposição ──────────────────────────
# Tudo com dublês e arquivos falsos em tmp_path: o arquivo de segredos de verdade nunca é lido nem escrito.

CHAVE = "0123456789ABCDEF0123456789ABCDEF"
AUTH = "-".join(("ABCD", "12345", "WXYZ"))
STEAMID = "76561198071376839"
C0 = sharecode.codificar(3230642215713767580, 3230647599455273103, 55788)
C1 = sharecode.codificar(3230642215713767581, 3230647599455273104, 55789)


@pytest.fixture(autouse=True)
def _nunca_o_arquivo_de_verdade(tmp_path, monkeypatch):
    monkeypatch.setattr(steam_api, "ARQUIVO", tmp_path / "NAO_USAR" / "segredos_steam.json")


def _espiar_permissao(monkeypatch, final):
    """Troca o `_so_o_usuario` por um dublê que anota o que existia no disco no instante em que a permissão é aplicada."""
    quando = []

    def falso(arq):
        quando.append({"arquivo": arq.name, "tamanho": arq.stat().st_size, "final_existe": final.exists(),
                       "final_tamanho": final.stat().st_size if final.exists() else None})
    monkeypatch.setattr(steam_api, "_so_o_usuario", falso)
    return quando


def test_g012_segredo_so_recebe_o_conteudo_depois_da_permissao(tmp_path, monkeypatch):
    final = tmp_path / "dados" / "segredos_steam.json"
    quando = _espiar_permissao(monkeypatch, final)
    steam_api.gravar_segredos(CHAVE, STEAMID, AUTH, C0, arquivo=final)
    assert quando == [{"arquivo": "segredos_steam.json.tmp", "tamanho": 0, "final_existe": False, "final_tamanho": None}], \
        "a permissão tem de ser aplicada num .tmp ainda VAZIO e o arquivo final nem existir"
    assert json.loads(final.read_text(encoding="utf-8"))["api_key"] == CHAVE
    assert not list(final.parent.glob("*.tmp"))


def test_g012_ao_atualizar_o_ultimo_codigo_o_novo_conteudo_tambem_nasce_protegido(tmp_path, monkeypatch):
    final = tmp_path / "dados" / "segredos_steam.json"
    steam_api.gravar_segredos(CHAVE, STEAMID, AUTH, C0, arquivo=final)
    antigo = final.stat().st_size
    quando = _espiar_permissao(monkeypatch, final)

    class _Resp:
        status = 200

        def __init__(self, c):
            self.c = c

        def read(self):
            return json.dumps({"result": {"nextcode": self.c}}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    respostas = iter([_Resp(C1), _Resp("n/a")])
    steam_api.procurar(final, abrir=lambda req, timeout=30: next(respostas), imprimir=lambda *_: None)
    assert quando == [{"arquivo": "segredos_steam.json.tmp", "tamanho": 0, "final_existe": True, "final_tamanho": antigo}]
    assert steam_api.ler_segredos(final)["ultimo_codigo"] == C1


def test_g012_segredo_vai_ao_disco_com_fsync(tmp_path, monkeypatch):
    chamadas = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (chamadas.append(fd), real(fd))[1])
    steam_api.gravar_segredos(CHAVE, STEAMID, AUTH, C0, arquivo=tmp_path / "dados" / "s.json")
    assert chamadas


def test_g012_falha_na_troca_nao_deixa_segredo_solto_no_tmp(tmp_path, monkeypatch):
    def quebra(tmp, destino):
        raise PermissionError(5, "Acesso negado")
    monkeypatch.setattr(steam_api, "substituir_arquivo", quebra)
    with pytest.raises(PermissionError):
        steam_api.gravar_segredos(CHAVE, STEAMID, AUTH, C0, arquivo=tmp_path / "dados" / "s.json")
    assert list((tmp_path / "dados").iterdir()) == []


# ── os outros JSON persistentes passam por gravar_atomico ────────────────────────────────────────

def test_g012_capas_publicar_e_report_gravam_por_gravar_atomico(tmp_path, monkeypatch):
    import cs2clip.capa as capa
    import cs2clip.publicar as publicar
    import cs2clip.report as report
    gravados = []
    real = tools.gravar_atomico

    def espia(arq, dados, *a, **k):
        gravados.append(Path(arq).name)
        real(arq, dados, *a, **k)
    for modulo in (capa, publicar, report):
        monkeypatch.setattr(modulo, "gravar_atomico", espia)

    report.write_report(tmp_path / "r.html", [], {}, "x")
    publicar.gerar_publicar(tmp_path, "v.mp4", [], {}, "marca", musica={"titulo": "T", "autor": "A", "licenca": "CC0"})
    capa.gerar_capas_candidatas("x.mp4", [], tmp_path / "capas")       # sem momentos: só o capas.json vazio
    assert gravados == ["r.html", "publicar.json", "publicar.txt", "capas.json"]
    assert json.loads((tmp_path / "capas" / "capas.json").read_text(encoding="utf-8")) == []
