"""B7 (Plano V7 integrado): cada parte do vídeo diz o que é, com que confiança e por quê; o incerto pede confirmação
e nada competitivo é descartado em silêncio."""
import json


from cs2clip.moments import Moment
from cs2clip.partes import carregar_partes, dividir_partes, gravar_partes
from cs2clip.rounds import RoundTimelineEntry


def _rounds(n, inicio=100.0, passo=100.0):
    return [RoundTimelineEntry(t=inicio + i * passo, resultado="vitoria" if i % 2 else "derrota", confianca=0.9) for i in range(n)]


def _partes(n_rounds, abates=(), duracao=3000.0, exatos=None):
    return dividir_partes(_rounds(n_rounds), list(abates), duracao, exatos=exatos)


def test_partida_longa_lida_pelos_banners_tem_confianca_alta_e_nao_pede_confirmacao():
    p = next(x for x in _partes(12) if x.tipo == "partida")
    assert p.classificacao == "COMPETITIVE" and p.confianca_tipo >= 0.85 and not p.precisa_confirmar
    assert any("12 rounds" in e for e in p.evidencias)


def test_partida_curta_de_poucos_banners_pede_confirmacao():
    p = next(x for x in _partes(3) if x.tipo == "partida")
    assert p.classificacao == "COMPETITIVE" and p.confianca_tipo < 0.7 and p.precisa_confirmar


def test_partida_da_demo_tem_confianca_maxima():
    p = next(x for x in _partes(6, exatos=[(50.0, 800.0)]) if x.tipo == "partida")
    assert p.exata and p.confianca_tipo >= 0.95 and not p.precisa_confirmar and any("demo" in e for e in p.evidencias)


def test_deathmatch_inferido_por_ausencia_de_round_sempre_pede_confirmacao():
    partes = _partes(8, abates=[2000.0 + i for i in range(10)])           # abates depois da partida, sem rounds
    dm = next(x for x in partes if x.tipo == "deathmatch")
    assert dm.classificacao == "DEATHMATCH" and dm.precisa_confirmar and 0.4 < dm.confianca_tipo < 0.8
    assert any("sem round" in e or "fora de qualquer round" in e for e in dm.evidencias)


def test_video_sem_nenhum_banner_e_desconhecido_e_pede_confirmacao_se_tem_abates():
    partes = dividir_partes([], [100.0 + i for i in range(5)], 600.0)
    assert [p.tipo for p in partes] == ["fora"]
    assert partes[0].classificacao == "UNKNOWN" and partes[0].precisa_confirmar
    vazio = dividir_partes([], [], 600.0)[0]
    assert vazio.classificacao == "UNKNOWN" and not vazio.precisa_confirmar        # nada de relevante a confirmar


def test_nada_do_que_ja_era_elegivel_deixa_de_ser():
    from cs2clip.moments import elegiveis
    ms = [Moment(10, 20, "abate", parte_tipo="fora"), Moment(30, 40, "abate", parte_tipo="fora")]
    assert elegiveis(ms) == ms                                                      # sem partida detectada: tudo continua valendo


def test_partes_json_guarda_e_recarrega_a_classificacao(tmp_path):
    partes = _partes(8, abates=[2000.0 + i for i in range(10)])
    gravar_partes(tmp_path, partes, "v", 3000.0)
    dados = json.loads((tmp_path / "partes.json").read_text(encoding="utf-8"))
    assert {"classificacao", "confianca_tipo", "evidencias", "precisa_confirmar"} <= set(dados[0])
    volta = carregar_partes(tmp_path)
    assert [(p.classificacao, p.precisa_confirmar) for p in volta] == [(p.classificacao, p.precisa_confirmar) for p in partes]


def test_json_antigo_sem_os_campos_novos_ainda_carrega(tmp_path):
    (tmp_path / "partes.json").write_text(json.dumps([{"tipo": "partida", "inicio": 0.0, "fim": 100.0, "n": 1, "vitorias": 1,
                                                       "derrotas": 0, "rounds_faltando": 0, "abates": 0, "rounds": []}]), encoding="utf-8")
    p = carregar_partes(tmp_path)[0]
    assert p.classificacao == "" and p.precisa_confirmar is False


def test_momento_herda_a_classificacao_da_parte_e_o_json_dos_momentos_leva(tmp_path):
    from cs2clip.emotion import anotar
    from cs2clip.momentos_io import gravar_momentos
    partes = _partes(8, abates=[2000.0 + i for i in range(10)])
    m = Moment(2000.0, 2010.0, "abate", kills=[], idx=1)
    m.kills = []
    anotar([m], partes, None, [], [])
    assert m.parte_classe == "DEATHMATCH" and m.parte_confirmar
    gravar_momentos(tmp_path, [m], "v", 3000.0, "7")
    d = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]
    assert d["parte_classe"] == "DEATHMATCH" and d["parte_confirmar"] is True and d["parte_evidencias"]


def test_ui_mostra_confirmar_parte_so_nos_momentos_incertos(tmp_path):
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      ui.state.videoAtual = "v1";
      $("#filter-type").value = "todos"; $("#filter-emotion").value = "todas"; $("#filter-order").value = "nota";
      ui.state.momentos = [
        { rotulo: "KILL", tipo: "abate", nota: 10, foco: 10, inicio: 5, fim: 15, parte: "Deathmatch/aquecimento", parte_confirmar: true, parte_evidencias: ["10 abates seus fora de qualquer round lido"], arquivos: {} },
        { rotulo: "ACE", tipo: "abate", nota: 90, foco: 50, inicio: 45, fim: 55, parte: "Partida 1 (9x3)", parte_confirmar: false, arquivos: {} }
      ];
      ui.renderizarMomentos();
      const cartoes = $("#moments-grid").children.filter(c => c.className && c.className.includes("moment-card"));
      return cartoes.map(c => textoDe(c));
    """)
    assert sum("CONFIRMAR PARTE" in t for t in r) == 1 and "CONFIRMAR PARTE" in r[1] and "CONFIRMAR PARTE" not in r[0]  # ACE (nota 90) vem primeiro
