"""A3 (13/11): todo kit de imagem limpa aparecia como "PARTIDA LIMPA" na Biblioteca (o nome vem do arquivo `PARTIDA_LIMPA.mp4`); com dois kits não dava para distinguir.
Agora o índice guarda um título ("Mirage 5x13 · 20/10 (imagem limpa)") e a tela o prefere."""
import datetime
import json
import os

import pytest

import cs2clip.pipeline as pipeline
from cs2clip import editar_demo as ed
from cs2clip import indice, mapas
from cs2clip.config import Config

from test_r1_ui import rodar_js
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers
from test_video_limpo_d7 import _cenario


def test_titulo_da_partida_com_mapa_placar_e_dia():
    d = datetime.datetime(2026, 10, 20, 15, 0)
    assert mapas.titulo_da_partida("de_mirage", (5, 13), d) == "Mirage 5x13 · 20/10"
    assert mapas.titulo_da_partida("de_mirage", None, d) == "Mirage · 20/10"
    assert mapas.titulo_da_partida("", (5, 13), d) == "Partida 5x13 · 20/10"
    assert mapas.titulo_da_partida("de_inferno", (4, 13), None) == "Inferno 4x13"
    assert mapas.titulo_da_partida("", None, None, sem_nada="x.dem") == "x.dem" and mapas.titulo_da_partida("", None, None) == ""


def test_data_da_demo_prefere_a_da_partida_e_cai_na_do_arquivo(tmp_path):
    demo = tmp_path / "match730_003849321203751714819_0599033075_201.dem"
    demo.write_bytes(b"x")
    ts = datetime.datetime(2026, 11, 8, 15, 0).timestamp()
    os.utime(demo, (ts, ts))
    assert mapas.data_da_demo(demo) == datetime.datetime(2026, 11, 8, 15, 0)
    demo.with_name(demo.name + ".info").write_text(json.dumps({"matchtime": int(datetime.datetime(2026, 10, 20, 21, 0).timestamp())}), encoding="utf-8")
    assert mapas.data_da_demo(demo) == datetime.datetime(2026, 10, 20, 21, 0)
    assert mapas.data_da_demo(tmp_path / "nao_existe.dem") is None


def test_o_indice_guarda_o_titulo_so_quando_ele_vem_e_definir_titulo_nao_mexe_no_resto(tmp_path):
    indice.atualizar_indice(tmp_path, "a_limpo", "PARTIDA_LIMPA.mp4", 120.0, [], [], "2026-11-10 00:41", "x", titulo="Mirage 5x13 · 20/10 (imagem limpa)")
    indice.atualizar_indice(tmp_path, "b", "live.mp4", 300.0, [], [], "2026-11-09 10:00", "y")
    lista = {x["id"]: x for x in indice.ler_indice(tmp_path)}
    assert lista["a_limpo"]["titulo"] == "Mirage 5x13 · 20/10 (imagem limpa)" and "titulo" not in lista["b"]
    assert indice.definir_titulo(tmp_path, "b", "Live de 09/11") is True and indice.definir_titulo(tmp_path, "nao_existe", "x") is False
    depois = {x["id"]: x for x in indice.ler_indice(tmp_path)}
    assert depois["b"]["titulo"] == "Live de 09/11" and depois["b"]["arquivo"] == "live.mp4" and depois["b"]["duracao"] == 300.0
    assert depois["a_limpo"] == lista["a_limpo"]                                                               # o outro vídeo ficou exatamente como estava
    assert (tmp_path / "indice.txt").is_file()


def test_nova_analise_do_mesmo_video_sem_titulo_nao_apaga_o_titulo_de_outro(tmp_path):
    indice.atualizar_indice(tmp_path, "a_limpo", "PARTIDA_LIMPA.mp4", 120.0, [], [], "2026-11-10 00:41", "x", titulo="T1")
    indice.atualizar_indice(tmp_path, "b", "live.mp4", 300.0, [], [], "2026-11-09 10:00", "y")
    assert {x["id"]: x.get("titulo") for x in indice.ler_indice(tmp_path)} == {"a_limpo": "T1", "b": None}


def test_a_biblioteca_mostra_o_titulo_quando_ha_e_o_nome_antigo_quando_nao_ha(tmp_path):
    r = rodar_js(tmp_path, """
      return [ui.nomeVideo({ id: "x_limpo", arquivo: "PARTIDA_LIMPA.mp4", titulo: "Mirage 5x13 · 20/10 (imagem limpa)" }),
              ui.nomeVideo({ id: "x_limpo", arquivo: "PARTIDA_LIMPA.mp4" }),
              ui.nomeVideo({ id: "2026-10-23-01-52-44", arquivo: "2026-10-23 01-52-44.mp4" })];
    """)
    assert r == ["Mirage 5x13 · 20/10 (imagem limpa)", "PARTIDA LIMPA", "2026-10-23 01-52-44"]


def test_a_rota_do_indice_entrega_o_titulo_para_a_tela(tmp_path):
    criar_projeto(tmp_path)
    (tmp_path / "saida" / "indice.json").write_text(json.dumps([{"id": "v1", "arquivo": "PARTIDA_LIMPA.mp4", "duracao": 120, "momentos": 1, "abates": 1,
                                                                 "titulo": "Mirage 5x13 · 20/10 (imagem limpa)"}], ensure_ascii=False), encoding="utf-8")
    with cliente(tmp_path) as c:
        assert c.get("/api/indice", headers=token_headers(c)).json()["itens"][0]["titulo"] == "Mirage 5x13 · 20/10 (imagem limpa)"


@pytest.mark.lento
def test_a_edicao_pela_demo_grava_o_titulo_no_indice(tmp_path, monkeypatch):
    partida, grav = _cenario(tmp_path)                                                                          # de_mirage, placar 5x13
    monkeypatch.setattr(ed, "ler_demo", lambda caminho, quem=None: partida)
    monkeypatch.setattr(pipeline, "render_all", lambda *a, **k: ([], None, None))
    demo = tmp_path / "match730_003849321203751714819_0599033075_201.dem"
    demo.write_bytes(b"PBDEMS2")
    ts = datetime.datetime(2026, 10, 20, 15, 0).timestamp()
    os.utime(demo, (ts, ts))
    cfg = Config()
    cfg.gravacao_demo.conta_principal_id = "76561198071376839"
    out = ed.editar(demo, cfg, pasta_gravacao=grav, saida_raiz=tmp_path / "saida", imprimir=lambda *a: None)
    item = next(x for x in indice.ler_indice(tmp_path / "saida") if x["id"] == out.name)
    assert item["titulo"] == "Mirage 5x13 · 20/10 (imagem limpa)" and item["arquivo"] == "PARTIDA_LIMPA.mp4"
