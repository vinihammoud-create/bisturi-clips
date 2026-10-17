"""E3: momentos.json com versão, arquivos gerados e revisão do usuário (aprovado/excluido)."""
import json

from cs2clip.momentos_io import FORMATO, gravar_momentos, ler_revisao
from cs2clip.moments import Event, Moment


def _abate(idx, t_ultimo, inicio=None):
    ks = [Event(t_ultimo - 2, "kill"), Event(t_ultimo, "kill")]
    return Moment(inicio if inicio is not None else t_ultimo - 6, t_ultimo + 3, "abate", kills=ks,
                  label="DOUBLE KILL", score=28.0, idx=idx)


def _ler(out):
    return json.loads((out / "momentos.json").read_text(encoding="utf-8"))


def test_grava_versao_video_e_momentos(tmp_path):
    gravar_momentos(tmp_path, [_abate(1, 100.0)], "2894824156", 3069.43, "3")
    d = _ler(tmp_path)
    assert d["formato"] == FORMATO and d["detector"] == "3" and d["video"] == "2894824156"
    assert d["duracao"] == 3069.43
    m = d["momentos"][0]
    assert m["n"] == 1 and m["rotulo"] == "DOUBLE KILL" and m["foco"] == 100.0
    assert m["arquivos"] is None and m["aprovado"] is None and m["excluido"] is False


def test_arquivos_gerados_entram_no_json_depois_do_render(tmp_path):
    arq = {1: {"corte": "cortes/01_double-kill_01m40s.mp4", "short": None, "miniatura": "miniaturas/01.jpg"}}
    gravar_momentos(tmp_path, [_abate(1, 100.0)], "v", 200.0, "3", arq)
    assert _ler(tmp_path)["momentos"][0]["arquivos"] == arq[1]


def test_revisao_do_usuario_sobrevive_a_nova_analise(tmp_path):
    gravar_momentos(tmp_path, [_abate(1, 100.0), _abate(2, 300.0)], "v", 500.0, "3")
    d = _ler(tmp_path)
    d["momentos"][0]["aprovado"] = True            # o usuário aprovou o 1º e descartou o 2º
    d["momentos"][1]["excluido"] = True
    (tmp_path / "momentos.json").write_text(json.dumps(d), encoding="utf-8")
    # nova análise: mesmos momentos + um novo no meio (os números mudam, o foco não)
    gravar_momentos(tmp_path, [_abate(1, 100.0), _abate(2, 200.0), _abate(3, 300.0)], "v", 500.0, "3")
    m = {x["foco"]: x for x in _ler(tmp_path)["momentos"]}
    assert m[100.0]["aprovado"] is True
    assert m[300.0]["excluido"] is True and m[300.0]["n"] == 3
    assert m[200.0]["aprovado"] is None and m[200.0]["excluido"] is False


def test_ajustar_antes_e_depois_do_corte_nao_perde_a_revisao(tmp_path):
    gravar_momentos(tmp_path, [_abate(1, 100.0, inicio=94.0)], "v", 500.0, "3")
    d = _ler(tmp_path)
    d["momentos"][0]["aprovado"] = False
    (tmp_path / "momentos.json").write_text(json.dumps(d), encoding="utf-8")
    gravar_momentos(tmp_path, [_abate(1, 100.0, inicio=88.0)], "v", 500.0, "3")   # corte começa 6 s antes
    assert _ler(tmp_path)["momentos"][0]["aprovado"] is False


def test_arquivo_antigo_ou_quebrado_nao_derruba(tmp_path):
    f = tmp_path / "momentos.json"
    f.write_text(json.dumps([{"n": 1, "rotulo": "KILL"}]), encoding="utf-8")        # formato antigo: lista
    assert ler_revisao(f) == {}
    f.write_text("{isto nao e json", encoding="utf-8")
    assert ler_revisao(f) == {}
    assert ler_revisao(tmp_path / "nao_existe.json") == {}
    gravar_momentos(tmp_path, [_abate(1, 100.0)], "v", 200.0, "3")                   # e grava por cima sem erro
    assert _ler(tmp_path)["formato"] == FORMATO
