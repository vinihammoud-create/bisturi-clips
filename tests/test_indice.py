"""E3: índice de todas as lives analisadas."""
import json

from cs2clip.indice import atualizar_indice, ler_indice, texto_indice
from cs2clip.moments import Event, Moment
from cs2clip.partes import dividir_partes
from cs2clip.rounds import RoundTimelineEntry as R


def _partes():
    rounds = [R(300.0 + 100 * i, "vitoria" if i % 2 else "derrota", 0.9) for i in range(4)]
    return dividir_partes(rounds, [], 800.0)


def _momentos():
    return [Moment(10, 20, "abate", kills=[Event(15, "kill")], idx=1),
            Moment(30, 40, "reacao", idx=2, emocao="RAIVA", confianca="alta")]


def test_cria_o_indice_com_uma_live(tmp_path):
    idx = atualizar_indice(tmp_path, "111", "live1.mp4", 800.0, _momentos(), _partes(), "2026-10-17 14:00")
    assert len(idx) == 1
    x = json.loads((tmp_path / "indice.json").read_text(encoding="utf-8"))[0]
    assert x["id"] == "111" and x["momentos"] == 2 and x["abates"] == 1 and x["emocoes"] == {"RAIVA": 1}
    assert x["partidas"][0]["placar"] == "2x2" and x["partidas"][0]["rounds"] == 4
    assert "Lives analisadas (1)" in (tmp_path / "indice.txt").read_text(encoding="utf-8")


def test_nova_analise_do_mesmo_video_atualiza_so_a_linha_dele(tmp_path):
    atualizar_indice(tmp_path, "111", "a.mp4", 800.0, _momentos(), _partes(), "2026-10-17 14:00")
    atualizar_indice(tmp_path, "222", "b.mp4", 500.0, [], [], "2026-10-17 15:00")
    atualizar_indice(tmp_path, "111", "a.mp4", 800.0, _momentos()[:1], _partes(), "2026-10-17 16:00")
    idx = ler_indice(tmp_path)
    assert [x["id"] for x in idx] == ["111", "222"]          # mais recente primeiro, sem duplicar
    assert idx[0]["momentos"] == 1 and idx[0]["processado_em"] == "2026-10-17 16:00"
    assert idx[1]["momentos"] == 0


def test_live_sem_partida_e_sem_emocao_no_texto():
    txt = texto_indice([{"id": "9", "arquivo": "x.mp4", "duracao": 600.0, "processado_em": "d", "momentos": 0,
                         "abates": 0, "emocoes": {}, "partidas": []}])
    assert "nenhuma partida detectada" in txt and "sem emoção" in txt


def test_indice_ilegivel_nao_derruba(tmp_path):
    (tmp_path / "indice.json").write_text("{quebrado", encoding="utf-8")
    assert ler_indice(tmp_path) == []
    assert len(atualizar_indice(tmp_path, "1", "a.mp4", 10.0, [], [], "d")) == 1


def test_indice_guarda_o_caminho_completo_do_video(tmp_path):
    idx = atualizar_indice(tmp_path, "1", "a.mp4", 10.0, [], [], "d", r"C:ideos.mp4")
    assert idx[0]["caminho"] == r"C:ideos.mp4" and idx[0]["arquivo"] == "a.mp4"
    assert atualizar_indice(tmp_path, "2", "b.mp4", 10.0, [], [], "e")[0]["caminho"] == ""   # antigo/sem caminho
