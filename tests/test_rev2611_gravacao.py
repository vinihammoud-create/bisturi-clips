"""Revisão de 26/11/2026 (lote B): a tela "Gravar pela demo". G-014 (o processo novo nunca escapa do cancelar; a leitura da saída sob a trava)
e R-005 (config.toml com erro aparece na tela em vez de virar os padrões calado). Processos FALSOS; espera por condição, com prazo."""
from __future__ import annotations

import threading
import time
from typing import Any, cast

import pytest

from cs2clip import entrada as ent
from ui.gravacao import GerenciadorGravacao

DEMO = "match730_003849321203751714819_0599033075_201.dem"


def _espera(condicao, tempo=10.0):
    fim = time.monotonic() + tempo
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.01)
    return condicao()


class _Proc:
    def __init__(self, linhas=("a", "b"), termina=False):
        self.fim = threading.Event()
        if termina:
            self.fim.set()
        self.sinais: list = []
        self.stdout = self._ler(list(linhas))

    def _ler(self, linhas):
        yield from (x + "\n" for x in linhas)
        self.fim.wait(10)

    def send_signal(self, s):
        self.sinais.append(s)
        self.fim.set()

    def terminate(self):
        self.send_signal("terminate")

    def poll(self):
        return 0 if self.fim.is_set() else None

    def wait(self, timeout=None):
        self.fim.wait(10)
        return 0


def _raiz(tmp_path):
    replays = tmp_path / "replays"
    replays.mkdir()
    (replays / DEMO).write_bytes(ent.CABECALHO_CS2 + b"corpo")
    return tmp_path / "raiz", replays


# ── G-014 ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_g014_cancelar_no_meio_da_abertura_do_processo_para_o_processo_novo_e_nao_o_antigo(tmp_path):
    raiz, replays = _raiz(tmp_path)
    procs: list[_Proc] = []
    entrou, solta = threading.Event(), threading.Event()

    def fabrica(cmd, **kw):
        if procs:                                                     # a 2ª abertura demora (antivírus, disco): o cancelar chega no meio
            entrou.set()
            solta.wait(10)
        procs.append(_Proc(termina=not procs))
        return procs[-1]
    g = GerenciadorGravacao(raiz, popen_factory=fabrica, python="PY", pasta_replays=lambda: replays)
    g.iniciar_gravacao(DEMO)                                           # 1º trabalho: termina sozinho
    assert _espera(lambda: g.estado()["job"]["estado"] == "pronto")
    fio = threading.Thread(target=g.iniciar_gravacao, args=(DEMO,), daemon=True)
    fio.start()
    assert entrou.wait(10)
    assert g.cancelar()["cancelado"] is True                           # aceito mesmo antes de o processo existir
    solta.set()
    fio.join(10)
    assert _espera(lambda: g.estado()["job"]["estado"] == "cancelado")
    assert procs[1].sinais                                              # o processo NOVO recebeu o pedido de parar
    assert procs[0].sinais == []                                        # e o antigo (que já tinha saído) não


class _TravaVigiada:
    """Trava que sabe quem a segura."""

    def __init__(self):
        self._t = threading.Lock()
        self.dono = None

    def acquire(self, *a, **kw):
        ok = self._t.acquire(*a, **kw)
        if ok:
            self.dono = threading.get_ident()
        return ok

    def release(self):
        self.dono = None
        self._t.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.release()

    def minha(self):
        return self.dono == threading.get_ident()


class _ListaVigiada(list):
    def __init__(self, trava, fora):
        super().__init__()
        self._trava, self._fora = trava, fora

    def append(self, x):
        if not self._trava.minha():
            self._fora.append(x)
        super().append(x)


def test_g014_a_leitura_da_saida_mexe_nas_linhas_sob_a_trava(tmp_path):
    raiz, replays = _raiz(tmp_path)
    fora: list = []
    g = GerenciadorGravacao(raiz, python="PY", pasta_replays=lambda: replays)
    cast(Any, g)._lock = _TravaVigiada()

    def fabrica(cmd, **kw):
        g._linhas = _ListaVigiada(g._lock, fora)                       # o `_iniciar` acabou de zerar as linhas: troca pela vigiada
        return _Proc(linhas=[f"linha {i}" for i in range(20)], termina=True)
    g._popen = fabrica
    g.iniciar_gravacao(DEMO)
    assert _espera(lambda: g.estado()["job"]["estado"] == "pronto")
    assert g._leitor is not None
    g._leitor.join(10)
    assert fora == []



# ── R-005: config.toml com erro aparece na tela "Gravar pela demo" (antes: planejava calado com os padrões) ──────────

@pytest.mark.parametrize("conteudo, trecho", [
    ("[gravacao_demo]\nfps = 999\n", "fps"),                           # valor que o load_config recusa
    ("[cortes\nantes = 1\n", "config.toml"),                           # erro de digitação no arquivo
])
def test_r005_plano_e_checklist_mostram_o_erro_do_config(tmp_path, monkeypatch, conteudo, trecho):
    import cs2clip.demo as demo_mod
    raiz, replays = _raiz(tmp_path)
    raiz.mkdir()
    (raiz / "config.toml").write_text(conteudo, encoding="utf-8")
    monkeypatch.setattr(demo_mod, "ler_demo", lambda *a, **kw: pytest.fail("planejou com os padrões do código"))
    g = GerenciadorGravacao(raiz, python="PY", pasta_replays=lambda: replays)
    with pytest.raises(ValueError, match="config.toml") as erro:
        g.plano(DEMO)
    assert trecho in str(erro.value)
    with pytest.raises(ValueError, match="config.toml"):
        g.checklist(DEMO)


def test_r005_a_rota_devolve_400_com_a_mensagem(tmp_path):
    from fastapi.testclient import TestClient
    from ui.servidor import create_app
    raiz, replays = _raiz(tmp_path)
    raiz.mkdir()
    (raiz / "config.toml").write_text("[gravacao_demo]\nfps = 999\n", encoding="utf-8")
    with TestClient(create_app(root_dir=raiz, port=8765, gravacao_replays=lambda: replays), base_url="http://127.0.0.1:8765") as c:
        r = c.get(f"/api/gravacao/plano?demo={DEMO}", headers={"X-Token": cast(Any, c.app).state.token})
        assert r.status_code == 400 and "config.toml" in r.json()["erro"] and "fps" in r.json()["erro"]
        r = c.get(f"/api/gravacao/checklist?demo={DEMO}", headers={"X-Token": cast(Any, c.app).state.token})
        assert r.status_code == 400 and "config.toml" in r.json()["erro"]
