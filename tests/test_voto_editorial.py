"""B6 (Plano V7 integrado): voto editorial (motivo de rejeição e avaliação 0–3) e métricas de ranking contra o gabarito."""
import json

import pytest

from cs2clip.moments import Event, Moment
from cs2clip.momentos_io import gravar_momentos, ler_revisao
from test_ui_dados_seguranca import cliente, criar_projeto, token_headers


def _votar(c, **corpo):
    return c.post("/api/videos/v1/momento", headers=token_headers(c), json={"foco": 12.0, **corpo})


def _momento(tmp_path):
    return json.loads((tmp_path / "saida" / "v1" / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]


def test_motivo_de_rejeicao_so_aceita_os_da_lista_e_fica_gravado(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        assert _votar(c, acao="descartar").status_code == 200
        assert _votar(c, acao="motivo", motivo="repetido").status_code == 200
        assert _momento(tmp_path)["motivo_rejeicao"] == "repetido" and _momento(tmp_path)["excluido"] is True
        assert _votar(c, acao="motivo", motivo="<script>").status_code == 400
        assert _votar(c, acao="motivo", motivo="").status_code == 200            # limpa o motivo
        assert _momento(tmp_path)["motivo_rejeicao"] is None


def test_restaurar_apaga_o_motivo_de_rejeicao(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        _votar(c, acao="descartar")
        _votar(c, acao="motivo", motivo="sem_contexto")
        _votar(c, acao="restaurar")
        assert _momento(tmp_path)["motivo_rejeicao"] is None and _momento(tmp_path)["excluido"] is False


def test_avaliacao_0_a_3_valida_e_grava(tmp_path):
    criar_projeto(tmp_path)
    with cliente(tmp_path) as c:
        ok = _votar(c, acao="avaliar", avaliacao={"impacto": 3, "clareza": 2, "contexto": 1, "ritmo": 0, "audio": 2, "novidade": 3})
        assert ok.status_code == 200
        assert _momento(tmp_path)["avaliacao"] == {"impacto": 3, "clareza": 2, "contexto": 1, "ritmo": 0, "audio": 2, "novidade": 3}
        for ruim in ({"impacto": 4}, {"impacto": -1}, {"impacto": 1.5}, {"impacto": "alto"}, {"tamanho": 2}, "texto", {}):
            assert _votar(c, acao="avaliar", avaliacao=ruim).status_code == 400, ruim
        assert _momento(tmp_path)["avaliacao"]["impacto"] == 3                    # nada ruim foi gravado


def test_nova_analise_do_mesmo_video_preserva_motivo_e_avaliacao(tmp_path):
    m = Moment(10.0, 20.0, "abate", kills=[Event(12.0, "kill")], label="KILL", score=30.0, idx=1)
    gravar_momentos(tmp_path, [m], "v", 100.0, "7")
    d = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))
    d["momentos"][0].update({"excluido": True, "motivo_rejeicao": "audio_ruim", "avaliacao": {"impacto": 2}})
    (tmp_path / "momentos.json").write_text(json.dumps(d), encoding="utf-8")
    rev = ler_revisao(tmp_path / "momentos.json")
    assert list(rev.values())[0]["motivo_rejeicao"] == "audio_ruim" and list(rev.values())[0]["avaliacao"] == {"impacto": 2}
    gravar_momentos(tmp_path, [m], "v", 100.0, "7")                                # reanálise
    novo = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]
    assert novo["motivo_rejeicao"] == "audio_ruim" and novo["avaliacao"] == {"impacto": 2} and novo["excluido"] is True


def test_ui_card_descartado_tem_seletor_de_motivo_e_o_card_normal_tem_avaliacao(tmp_path):
    from test_r1_ui import rodar_js
    r = rodar_js(tmp_path, """
      ui.state.videoAtual = "v1";
      $("#filter-type").value = "todos"; $("#filter-emotion").value = "todas"; $("#filter-order").value = "nota";
      $("#filter-excluded").checked = true;
      ui.state.momentos = [
        { rotulo: "A", tipo: "abate", nota: 50, foco: 10, inicio: 5, fim: 15, parte: "P", excluido: true, motivo_rejeicao: "repetido", arquivos: {} },
        { rotulo: "B", tipo: "abate", nota: 40, foco: 50, inicio: 45, fim: 55, parte: "P", excluido: false, arquivos: {} }
      ];
      ui.renderizarMomentos();
      const cartoes = $("#moments-grid").children.filter(c => c.className && c.className.includes("moment-card"));
      return cartoes.map(c => textoDe(c));
    """)
    assert "Motivo da rejeição" in r[0] and "Avaliação" not in r[0]
    assert "Avaliar esta jogada" in r[1] and "Motivo da rejeição" not in r[1]


# ── métricas de ranking contra os votos ───────────────────────────────────────────────────────────

def _m(nota, aprovado=None, excluido=False, rotulo="KILL", rnd=None, motivo=None, av=None):
    return {"nota": nota, "aprovado": aprovado, "excluido": excluido, "rotulo": rotulo, "round": rnd,
            "motivo_rejeicao": motivo, "avaliacao": av}


def test_relevancia_e_que_voto_vale():
    from cs2clip.ranking_medida import relevancia
    assert relevancia(_m(5)) is None                                                # sem voto: fora da medida
    assert relevancia(_m(5, excluido=True)) == 0 and relevancia(_m(5, aprovado=False)) == 0
    assert relevancia(_m(5, aprovado=True)) == 2
    assert relevancia(_m(5, aprovado=True, av={"impacto": 3, "clareza": 3})) == 3
    assert relevancia(_m(5, aprovado=True, av={"impacto": 1, "clareza": 1})) == 2


def test_precisao_e_ndcg_perfeitos_quando_a_ordem_da_nota_bate_com_os_votos():
    from cs2clip.ranking_medida import medir
    ms = [_m(90, True), _m(80, True), _m(70, True), _m(20, excluido=True, motivo="comum"), _m(10, excluido=True, motivo="repetido")]
    r = medir(ms)
    assert r["precisao_em_k"]["3"] == 1.0 and r["ndcg_em_k"]["3"] == 1.0 and r["taxa_de_aprovacao"] == 0.6
    assert r["motivos_de_rejeicao"] == {"comum": 1, "repetido": 1} and r["poucos_votos"] is True


def test_ranking_invertido_tem_precisao_e_ndcg_baixos():
    from cs2clip.ranking_medida import medir
    ms = [_m(90, excluido=True), _m(80, excluido=True), _m(70, excluido=True), _m(20, True), _m(10, True), _m(5, True)]
    r = medir(ms)
    assert r["precisao_em_k"]["3"] == 0.0 and r["ndcg_em_k"]["3"] == 0.0


def test_sem_votos_nao_inventa_numero_e_k_maior_que_os_revisados_nao_aparece():
    from cs2clip.ranking_medida import medir, texto
    r = medir([_m(50), _m(40)])
    assert r["revisados"] == 0 and r["taxa_de_aprovacao"] is None and "Ainda não há votos" in texto(r)
    r2 = medir([_m(50, True), _m(40, True)])
    assert r2["precisao_em_k"] == {} and "pelo menos 3" in texto(r2)


def test_diversidade_e_cobertura_de_rounds():
    from cs2clip.ranking_medida import medir
    ms = [_m(90, True, rotulo="ACE", rnd=1), _m(80, True, rotulo="ACE", rnd=1), _m(70, excluido=True, rotulo="KILL", rnd=2)]
    r = medir(ms)
    assert r["diversidade_de_rotulos_no_topo"] == pytest.approx(2 / 3, abs=0.01) and r["cobertura_de_rounds"] == 0.5


def test_carregar_junta_todos_os_videos_da_pasta(tmp_path):
    from cs2clip.ranking_medida import carregar
    for nome in ("a", "b"):
        (tmp_path / nome).mkdir()
        (tmp_path / nome / "momentos.json").write_text(json.dumps({"momentos": [{"nota": 1}, {"nota": 2}]}), encoding="utf-8")
    (tmp_path / "ruim").mkdir()
    (tmp_path / "ruim" / "momentos.json").write_text("{lixo", encoding="utf-8")
    assert len(carregar(tmp_path)) == 4 and len(carregar(tmp_path / "a")) == 2
