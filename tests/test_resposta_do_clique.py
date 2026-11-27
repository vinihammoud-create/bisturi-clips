"""14/11/2026: a resposta de "iniciar um trabalho" é o retrato do CLIQUE, não do que a execução já fez.

Achado pelo portão antes do envio: `iniciar` disparava a execução ANTES de copiar a resposta. Com a máquina ocupada, a linha de execução
trocava o título (e o estado) antes da cópia, e a tela recebia um trabalho "já andando" (1 falha em ~10 rodadas da suíte rápida).
"""
from test_receitas_e2 import EDITAR_OK, GRAVAR_OK, Roteiro, _com_data, _demo, _ger
from ui import receitas


def test_a_resposta_do_clique_nao_depende_de_a_execucao_ja_ter_comecado(tmp_path, monkeypatch):
    demo = _com_data(_demo(tmp_path))
    monkeypatch.setattr(receitas, "_mapa_da_demo", lambda c: "de_mirage")
    monkeypatch.setattr(receitas, "placar_da_demo", lambda caminho, cfg: (11, 13))
    g = _ger(tmp_path, Roteiro(gravador_demo=[GRAVAR_OK], editar_demo=[EDITAR_OK]))
    disparar = g._disparar

    def execucao_que_ganha_a_corrida(rid):
        disparar(rid)
        g.esperar(rid)                       # a execução inteira termina ANTES de `iniciar` devolver a resposta (o pior caso da corrida)

    monkeypatch.setattr(g, "_disparar", execucao_que_ganha_a_corrida)
    r = g.iniciar(str(demo))
    assert r["titulo"] == "Mirage · 08/11" and r["estado"] == "na_fila"        # o retrato do clique
    fim = g.esperar(r["id"])                                                   # CONTROLE: o trabalho rodou de verdade e ganhou o placar
    assert fim["estado"] == "pronta" and fim["titulo"] == "Mirage 11x13 · 08/11"
