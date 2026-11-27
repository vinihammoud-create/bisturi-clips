"""C10 (Plano V7 integrado): ajuste fino de início/fim de um corte pela tela de revisão (±0,5 s), refazendo só aquele corte."""
import json

import pytest

from cs2clip.moments import Event, Moment
from cs2clip.momentos_io import ajustes_dos_momentos, gravar_momentos, ler_revisao
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers


def _votar(c, **corpo):
    return c.post("/api/videos/v1/momento", headers=token_headers(c), json={"foco": 12.0, **corpo})


def _momento(tmp_path):
    return json.loads((tmp_path / "saida" / "v1" / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]


def test_ajuste_valido_e_gravado_e_arredondado(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert _votar(c, acao="ajustar", ajuste={"inicio": -1.5, "fim": 2.0}).status_code == 200
        assert _momento(tmp_path)["ajuste"] == {"inicio": -1.5, "fim": 2.0}
        assert _votar(c, acao="ajustar", ajuste={"inicio": 0.04, "fim": 0.0}).status_code == 200
        assert _momento(tmp_path)["ajuste"] is None                           # tudo zero = sem ajuste (volta ao padrão)


@pytest.mark.parametrize("ruim", [{"inicio": 7.0, "fim": 0}, {"inicio": -7.0, "fim": 0}, {"inicio": "x", "fim": 0}, {"inicio": 0},
                                  {"inicio": 0, "fim": 0, "extra": 1}, "texto", None, {"inicio": True, "fim": 0}])
def test_ajuste_invalido_e_recusado_e_nada_e_gravado(tmp_path, ruim):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert _votar(c, acao="ajustar", ajuste=ruim).status_code == 400
        assert "ajuste" not in _momento(tmp_path) or _momento(tmp_path)["ajuste"] is None


def test_validador_recusa_nan_e_infinito():
    from ui.dados import _ajuste_valido
    for ruim in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            _ajuste_valido({"inicio": ruim, "fim": 0})


def test_ajuste_sobrevive_a_nova_analise_e_e_lido_pelo_pipeline(tmp_path):
    m = Moment(10.0, 20.0, "abate", kills=[Event(12.0, "kill")], label="KILL", score=30.0, idx=1)
    gravar_momentos(tmp_path, [m], "v", 100.0, "7")
    d = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))
    d["momentos"][0]["ajuste"] = {"inicio": -1.0, "fim": 1.5}
    (tmp_path / "momentos.json").write_text(json.dumps(d), encoding="utf-8")
    assert list(ler_revisao(tmp_path / "momentos.json").values())[0]["ajuste"] == {"inicio": -1.0, "fim": 1.5}
    gravar_momentos(tmp_path, [m], "v", 100.0, "7")
    assert json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]["ajuste"] == {"inicio": -1.0, "fim": 1.5}
    assert ajustes_dos_momentos(tmp_path / "momentos.json") == {"12.0": (-1.0, 1.5)}


def test_aplicar_ajuste_muda_inicio_fim_e_segmentos_respeitando_os_limites():
    from cs2clip.pipeline import aplicar_ajustes
    m = Moment(10.0, 20.0, "abate", kills=[Event(12.0, "kill")], idx=1)
    aplicar_ajustes([m], {"12.0": (-1.0, 1.5)}, duracao=100.0, minimo=3.0)
    assert (m.start, m.end) == (9.0, 21.5)
    seq = Moment(10.0, 40.0, "abate", kills=[Event(12.0, "kill"), Event(38.0, "kill")], idx=2, segmentos=[(10.0, 15.0), (30.0, 40.0)])
    aplicar_ajustes([seq], {"38.0": (0.5, -0.5)}, duracao=100.0, minimo=3.0)
    assert seq.segmentos == [(10.5, 15.0), (30.0, 39.5)] and (seq.start, seq.end) == (10.5, 39.5)


def test_ajuste_nunca_sai_do_video_nem_deixa_o_corte_menor_que_o_minimo():
    from cs2clip.pipeline import aplicar_ajustes
    a = Moment(1.0, 6.0, "abate", kills=[Event(3.0, "kill")], idx=1)
    aplicar_ajustes([a], {"3.0": (-5.0, 0.0)}, duracao=100.0, minimo=3.0)
    assert a.start == 0.0                                                      # não passa do começo do vídeo
    b = Moment(10.0, 14.0, "abate", kills=[Event(12.0, "kill")], idx=2)
    aplicar_ajustes([b], {"12.0": (3.0, 0.0)}, duracao=100.0, minimo=3.0)
    assert b.end - b.start >= 3.0 - 1e-9                                       # encurtar demais é limitado
    c = Moment(95.0, 99.0, "abate", kills=[Event(97.0, "kill")], idx=3)
    aplicar_ajustes([c], {"97.0": (0.0, 5.0)}, duracao=100.0, minimo=3.0)
    assert c.end == 100.0


def test_momento_sem_ajuste_fica_intacto():
    from cs2clip.pipeline import aplicar_ajustes
    m = Moment(10.0, 20.0, "abate", kills=[Event(12.0, "kill")], idx=1)
    aplicar_ajustes([m], {}, duracao=100.0, minimo=3.0)
    assert (m.start, m.end) == (10.0, 20.0)


def test_ui_cartao_tem_botoes_de_ajuste_e_marca_pendente_para_aplicar(tmp_path):
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      ui.state.videoAtual = "v1";
      $("#filter-type").value = "todos"; $("#filter-emotion").value = "todas"; $("#filter-order").value = "nota";
      ui.state.momentos = [{ rotulo: "A", tipo: "abate", nota: 50, foco: 10, inicio: 5, fim: 15, parte: "P", excluido: false, ajuste: { inicio: -0.5, fim: 0 }, arquivos: {} }];
      ui.renderizarMomentos();
      const cartao = $("#moments-grid").children.filter(c => c.className && c.className.includes("moment-card"))[0];
      return { texto: textoDe(cartao), novo: ui.novoAjuste({ inicio: -0.5, fim: 0 }, "inicio", +0.5), limite: ui.novoAjuste({ inicio: 6, fim: 0 }, "inicio", +0.5) };
    """)
    assert "Ajustar corte" in r["texto"] and "início -0,5" in r["texto"]
    assert r["novo"] == {"inicio": 0, "fim": 0} and r["limite"] == {"inicio": 6, "fim": 0}      # nunca passa de ±6


def test_ajuste_grande_numa_sequencia_nao_inverte_nem_engole_o_primeiro_trecho():
    from cs2clip.pipeline import aplicar_ajustes
    seq = Moment(10.0, 40.0, "abate", kills=[Event(12.0, "kill"), Event(38.0, "kill")], idx=1, segmentos=[(10.0, 14.0), (30.0, 40.0)])
    aplicar_ajustes([seq], {"38.0": (6.0, 0.0)}, duracao=100.0, minimo=3.0)
    assert seq.segmentos[0][0] < seq.segmentos[0][1] and seq.segmentos[0][1] - seq.segmentos[0][0] >= 1.0   # 1º trecho continua válido
    assert seq.start == seq.segmentos[0][0] and seq.end == 40.0
    seq2 = Moment(10.0, 40.0, "abate", kills=[Event(12.0, "kill"), Event(38.0, "kill")], idx=2, segmentos=[(10.0, 14.0), (30.0, 40.0)])
    aplicar_ajustes([seq2], {"38.0": (0.0, -6.0)}, duracao=100.0, minimo=3.0)        # fim -6 s não pode comer o último trecho
    assert seq2.segmentos[-1][1] - seq2.segmentos[-1][0] >= 1.0 and seq2.end > seq2.segmentos[-1][0]
