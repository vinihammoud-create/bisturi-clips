"""N3 / cancelar no meio da edição não pode passar por kit bom.

Hoje a edição reescreve o kit no lugar (momentos.json primeiro, depois cortes, shorts, montagem, capas): cancelar no meio deixa momentos NOVOS com
montagem/shorts/capas ANTIGOS (visto em 15/11 na Mirage 20/10). Trocar tudo só no fim exigiria refazer o cache do `render_all` (nomes e impressões por arquivo);
o menor conserto seguro é a tela AVISAR, no cartão do trabalho, que o kit ficou pela metade e como terminar (Retomar).
"""
from __future__ import annotations

import json
import threading
import time

from ui import receitas
from ui.receitas import GerenciadorReceitas, _etapa_nova


class Processo:
    def __init__(self, linhas, codigo=0, bloqueio=None):
        self._linhas, self._codigo, self._bloqueio = linhas, codigo, bloqueio
        self.stdout = self._ler()

    def _ler(self):
        if self._bloqueio is not None:
            self._bloqueio.wait(10)
        yield from (x + "\n" for x in self._linhas)

    def send_signal(self, sinal):
        if self._bloqueio is not None:
            self._bloqueio.set()

    def terminate(self):
        self.send_signal("terminate")

    def poll(self):
        return self._codigo

    def wait(self):
        return self._codigo


def _ger(tmp_path, linhas, codigo=0, bloqueio=None):
    def popen(cmd, **kw):
        return Processo(linhas, codigo, bloqueio)
    g = GerenciadorReceitas(tmp_path / "raiz", popen_factory=popen, python="PY", poll_s=0.01)
    return g


def _receita(g, nome_da_etapa="editar", so_editar=True):
    r = {"id": "abc123", "criada_em": time.time(), "titulo": "Refazer a edição · Mirage · 20/10", "entrada": {"tipo": "demo", "valor": "x.dem"},
         "opcoes": {"so_editar": "x"} if so_editar else {}, "estado": "na_fila", "etapas": [_etapa_nova(nome_da_etapa)],
         "dados": {"demo": "x.dem", "titulo_com_placar": True}, "resultado": "", "erro": ""}
    g._receitas[r["id"]] = r
    g._cancel[r["id"]] = threading.Event()
    return r


def _cancelar_quando_comecar(g, rid):
    for _ in range(500):
        if g._procs.get(rid) is not None:
            break
        time.sleep(0.01)
    g.cancelar(rid)


def test_cancelar_no_meio_da_edicao_avisa_que_o_kit_ficou_pela_metade(tmp_path):
    bloqueio = threading.Event()
    g = _ger(tmp_path, ["Montagem com as melhores jogadas…"], bloqueio=bloqueio)
    r = _receita(g)
    g._disparar(r["id"])
    _cancelar_quando_comecar(g, r["id"])
    fim = g.esperar(r["id"])
    assert fim["estado"] == "cancelada"
    assert "pela metade" in fim["erro"] and "Retomar" in fim["erro"]
    assert json.loads((g.pasta / r["id"] / "receita.json").read_text(encoding="utf-8"))["erro"] == fim["erro"]       # o aviso fica no disco


def test_erro_no_meio_da_edicao_mantem_o_motivo_e_acrescenta_o_aviso(tmp_path):
    g = _ger(tmp_path, ["[parei] Nenhuma jogada sua cai dentro dos rounds gravados."], codigo=1)
    r = _receita(g)
    g._disparar(r["id"])
    fim = g.esperar(r["id"])
    assert fim["estado"] == "erro"
    assert "Nenhuma jogada sua" in fim["erro"] and "pela metade" in fim["erro"]


def test_controle_cancelar_outra_etapa_nao_fala_de_kit_pela_metade(tmp_path):
    bloqueio = threading.Event()
    g = _ger(tmp_path, ["Abrindo o CS2…"], bloqueio=bloqueio)
    r = _receita(g, nome_da_etapa="gravar", so_editar=False)
    g._disparar(r["id"])
    _cancelar_quando_comecar(g, r["id"])
    fim = g.esperar(r["id"])
    assert fim["estado"] == "cancelada" and fim["erro"] == ""


def test_controle_edicao_que_termina_nao_tem_aviso(tmp_path):
    g = _ger(tmp_path, ["Edição pronta em C:\\saida\\x_limpo"])
    r = _receita(g)
    g._disparar(r["id"])
    fim = g.esperar(r["id"])
    assert fim["estado"] == "pronta" and fim["erro"] == ""


def test_retomar_apaga_o_aviso_e_termina_a_edicao(tmp_path):
    bloqueio = threading.Event()
    g = _ger(tmp_path, ["Montagem…"], bloqueio=bloqueio)
    r = _receita(g)
    g._disparar(r["id"])
    _cancelar_quando_comecar(g, r["id"])
    assert "pela metade" in g.esperar(r["id"])["erro"]
    g._popen = lambda cmd, **kw: Processo(["Edição pronta em C:\\saida\\x_limpo"])
    g.retomar(r["id"])
    fim = g.esperar(r["id"])
    assert fim["estado"] == "pronta" and fim["erro"] == ""


def test_programa_fechado_no_meio_da_edicao_tambem_avisa(tmp_path):
    pasta = tmp_path / "raiz" / "trabalhos" / "abc123"
    pasta.mkdir(parents=True)
    et = _etapa_nova("editar")
    et["estado"] = "rodando"
    r = {"id": "abc123", "criada_em": time.time(), "titulo": "Mirage", "entrada": {"tipo": "demo", "valor": "x.dem"}, "opcoes": {"so_editar": "x"},
         "estado": "rodando", "etapas": [et], "dados": {}, "resultado": "", "erro": ""}
    (pasta / "receita.json").write_text(json.dumps(r), encoding="utf-8")
    g = GerenciadorReceitas(tmp_path / "raiz", popen_factory=lambda *a, **k: None, python="PY")
    carregada = g.obter("abc123")
    assert carregada["estado"] == "interrompida" and "pela metade" in carregada["erro"]


def test_o_aviso_usa_a_constante_do_modulo():
    assert receitas.AVISO_KIT_PELA_METADE.startswith("Atenção") and "Retomar" in receitas.AVISO_KIT_PELA_METADE


def test_erro_inesperado_no_meio_da_edicao_tambem_avisa(tmp_path):
    g = _ger(tmp_path, [])

    def estoura(cmd, **kw):
        raise RuntimeError("falha qualquer")
    g._popen = estoura
    r = _receita(g)
    g._disparar(r["id"])
    fim = g.esperar(r["id"])
    assert fim["estado"] == "erro" and "inesperado" in fim["erro"].lower() and "pela metade" in fim["erro"]
