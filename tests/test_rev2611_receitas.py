"""Revisão de 26/11/2026 (lote B): concorrência das receitas (G-004) e a análise mais recente no pareamento (G-006).
Processos, Steam, fila e Twitch FALSOS. Nada decide pelo relógio: toda espera é por condição, com prazo."""
from __future__ import annotations

import json
import os
import threading
import time
import types
from pathlib import Path

import pytest

import ui.receitas as receitas_mod
from cs2clip import entrada as ent
from cs2clip.config import Config
from ui.receitas import GerenciadorReceitas

BASE = 76561197960265728
PRINCIPAL_ID, BISTURI_ID = 100, 200
LINK = "https://www.twitch.tv/videos/1234567890"


def _espera(condicao, tempo=10.0):
    fim = time.monotonic() + tempo
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.01)
    return condicao()


def _cfg():
    cfg = Config()
    cfg.gravacao_demo.conta_steamid = str(BASE + BISTURI_ID)
    cfg.gravacao_demo.conta_principal_id = str(BASE + PRINCIPAL_ID)
    return cfg


class _Proc:
    def __init__(self, linhas, codigo=0):
        self._linhas, self._codigo = list(linhas), codigo
        self.stdout = iter(x + "\n" for x in self._linhas)

    def send_signal(self, s):
        pass

    def terminate(self):
        pass

    def poll(self):
        return self._codigo

    def wait(self, timeout=None):
        return self._codigo


def _popen(cmd, **kw):
    modulo = cmd[cmd.index("-m") + 1]
    if modulo.endswith("editar_demo"):
        return _Proc(["3 jogada(s)", "Edição pronta em C:\\saida\\x_limpo"])
    return _Proc([f"linha {i}" for i in range(30)] + ["Pronto."])


class _Fila:
    """Fila falsa: a análise fica rodando até `liberar`."""

    def __init__(self):
        self.cancelados: list[str] = []
        self.liberar = threading.Event()

    def adicionar(self, arquivo, opcoes):
        return {"id": "job1"}

    def listar(self):
        return {"itens": [{"id": "job1", "estado": "pronto" if self.liberar.is_set() else "rodando", "mensagem": ""}]}

    def cancelar(self, job_id):
        self.cancelados.append(job_id)


class _Vods:
    """Baixador falso: o VOD da receita (vod1) fica baixando até `liberar`."""

    def __init__(self):
        self.cancelados: list = []
        self.liberar = threading.Event()

    def iniciar(self, link):
        return {"id": "vod1"}

    def listar(self):
        estado = "pronto" if self.liberar.is_set() else "baixando"
        return {"itens": [{"id": "vod1", "estado": estado, "percentual": 50, "arquivo": "1234567890-twitch.mp4", "erro": ""}]}

    def cancelar(self, job_id=None):
        self.cancelados.append(job_id)


def _ger(tmp_path, **extra):
    return GerenciadorReceitas(tmp_path / "raiz", popen_factory=_popen, python="PY", conta_logada=lambda: BISTURI_ID, config_fn=_cfg,
                               poll_s=0.01, procurar_em=lambda: [tmp_path], **extra)


def _demo(tmp_path):
    arq = tmp_path / "match730_003849321203751714819_0599033075_201.dem"
    arq.write_bytes(ent.CABECALHO_CS2 + b"corpo")
    return arq


# ── G-004: tudo o que muda a receita (e a cópia que vai para a tela ou o disco) acontece sob a trava ──────────────

class _Vigiado(dict):
    """Dicionário que anota toda mudança feita SEM a trava do gerenciador."""

    def __init__(self, trava, fora, *a, **kw):
        super().__init__(*a, **kw)
        self._trava, self._fora = trava, fora

    def _conferir(self, op):
        if not self._trava._is_owned():
            self._fora.append(op)

    def __setitem__(self, k, v):
        self._conferir(f"[{k}]=")
        super().__setitem__(k, v)

    def __delitem__(self, k):
        self._conferir(f"del {k}")
        super().__delitem__(k)

    def pop(self, *a):
        self._conferir(f"pop {a[0]}")
        return super().pop(*a)

    def setdefault(self, k, d=None):
        self._conferir(f"setdefault {k}")
        return super().setdefault(k, d)


def _vigiar(g, monkeypatch):
    """Liga os vigias: `json.dumps` da receita (disco e tela), `_procs`, `_linhas` e o `dados` de cada receita nova."""
    fora: list[str] = []
    real = json.dumps

    def dumps(obj, *a, **kw):
        if not g._lock._is_owned():
            fora.append(f"json.dumps({type(obj).__name__})")
        return real(obj, *a, **kw)
    monkeypatch.setattr(receitas_mod, "json", types.SimpleNamespace(dumps=dumps, loads=json.loads))
    g._procs = _Vigiado(g._lock, fora)
    g._linhas = _Vigiado(g._lock, fora)
    original = g._disparar

    def disparar(rid):
        with g._lock:
            r = g._receitas[rid]
            r["dados"] = _Vigiado(g._lock, fora, r["dados"])
        original(rid)
    monkeypatch.setattr(g, "_disparar", disparar)
    return fora


def test_g004_receita_de_demo_so_muda_e_se_copia_sob_a_trava(tmp_path, monkeypatch):
    g = _ger(tmp_path)
    fora = _vigiar(g, monkeypatch)
    r = g.iniciar(str(_demo(tmp_path)))
    assert g.esperar(r["id"])["estado"] == "pronta"
    assert fora == []


def test_g004_receita_de_vod_so_muda_e_se_copia_sob_a_trava(tmp_path, monkeypatch):
    fila, vods = _Fila(), _Vods()
    vods.liberar.set()
    fila.liberar.set()
    g = _ger(tmp_path, fila=fila, vods=vods)
    fora = _vigiar(g, monkeypatch)
    r = g.iniciar(LINK)
    assert g.esperar(r["id"])["estado"] == "pronta"
    assert fora == []


def test_g004_cancelar_e_confirmar_copiam_a_receita_sob_a_trava(tmp_path, monkeypatch):
    fila, vods = _Fila(), _Vods()
    g = _ger(tmp_path, fila=fila, vods=vods)
    r = g.iniciar(LINK)
    assert _espera(lambda: g.obter(r["id"])["etapas"][0]["detalhe"] == "50%")
    fora = _vigiar(g, monkeypatch)
    g.cancelar(r["id"])
    assert g.esperar(r["id"])["estado"] == "cancelada"
    assert fora == []


def test_g004_vod_da_receita_sai_dos_dados_quando_a_etapa_termina_e_cancelar_depois_nao_mata_o_download_de_outro(tmp_path):
    fila, vods = _Fila(), _Vods()
    vods.liberar.set()                                                # o VOD da receita já baixou; a análise fica rodando
    g = _ger(tmp_path, fila=fila, vods=vods)
    r = g.iniciar(LINK)
    assert _espera(lambda: g.obter(r["id"])["etapas"][1]["estado"] == "rodando")
    assert "vod_id" not in g.obter(r["id"])["dados"]
    g.cancelar(r["id"])
    assert g.esperar(r["id"])["estado"] == "cancelada"
    assert vods.cancelados == []                                      # o download em andamento (de outro pedido) seguiu
    assert _espera(lambda: fila.cancelados == ["job1"])               # a análise DESTA receita foi cancelada (espera: sob carga o cancelamento chega um instante depois)


def test_g004_cancelar_no_meio_do_download_cancela_o_vod_desta_receita_pelo_id(tmp_path):
    fila, vods = _Fila(), _Vods()
    g = _ger(tmp_path, fila=fila, vods=vods)
    r = g.iniciar(LINK)
    assert _espera(lambda: g.obter(r["id"])["etapas"][0]["detalhe"] == "50%")
    g.cancelar(r["id"])
    assert g.esperar(r["id"])["estado"] == "cancelada"
    assert vods.cancelados == ["vod1"]


def test_g004_gerenciador_de_vod_so_cancela_o_download_pedido(tmp_path):
    from ui.vods import GerenciadorVod
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    cli = tmp_path / "TwitchDownloaderCLI.exe"
    cli.write_bytes(b"x")
    parar = threading.Event()

    class Baixador:
        def __init__(self):
            self.stdout = self._linhas()

        def _linhas(self):
            yield "[STATUS] - Downloading 3%\n"
            parar.wait(10)

        def poll(self):
            return 1 if parar.is_set() else None

        def terminate(self):
            parar.set()

        def kill(self):
            parar.set()

        def wait(self, timeout=None):
            parar.wait(10)
            return 1
    g = GerenciadorVod(entrada, popen_factory=lambda cmd, **kw: Baixador(), cli=cli)
    job = g.iniciar("twitch.tv/videos/1234567890")
    assert _espera(lambda: job["id"] in g._procs)
    with pytest.raises(RuntimeError):
        g.cancelar("outro")                                            # um id que não é o do download em andamento
    assert not parar.is_set() and g.listar()["itens"][0]["estado"] == "baixando"
    assert g.cancelar(job["id"])["id"] == job["id"]
    assert _espera(lambda: g.listar()["itens"][0]["estado"] == "cancelado")


def test_g004_falha_ao_gravar_o_estado_nao_deixa_temporario_para_tras(tmp_path, monkeypatch):
    g = _ger(tmp_path)
    original = os.replace

    def nega(origem, alvo):
        if Path(origem).name.startswith("receita.json"):
            raise PermissionError("sempre")
        return original(origem, alvo)
    monkeypatch.setattr("cs2clip.tools.time", types.SimpleNamespace(sleep=lambda s: None))
    monkeypatch.setattr(os, "replace", nega)
    r = g.iniciar(str(_demo(tmp_path)))
    fim = g.esperar(r["id"])
    assert fim["estado"] == "pronta" and fim.get("disco_falhou") is True
    assert list((tmp_path / "raiz" / "trabalhos" / r["id"]).glob("*.tmp")) == []


# ── G-006: o pareamento lê a análise MAIS RECENTE do vídeo ─────────────────────────────────────────────────────

def test_g006_com_duas_analises_do_mesmo_video_vale_a_mais_recente(tmp_path):
    from cs2clip.indice import atualizar_indice
    raiz = tmp_path / "raiz"
    saida = raiz / "saida"
    (saida / "vid_antigo").mkdir(parents=True)
    (saida / "vid_novo").mkdir(parents=True)
    live = str(raiz / "entrada" / "live.mp4")
    atualizar_indice(saida, "vid_antigo", "live.mp4", 100.0, [], [], "2026-11-01 10:00", caminho=live)
    atualizar_indice(saida, "vid_novo", "live.mp4", 100.0, [], [], "2026-11-20 10:00", caminho=live)
    g = GerenciadorReceitas(raiz, python="PY")
    assert getattr(g._pasta_da_analise(live), "name", None) == "vid_novo"
    # a ordem do arquivo não importa: a data decide (índice escrito do mais velho para o mais novo)
    itens = json.loads((saida / "indice.json").read_text(encoding="utf-8"))
    (saida / "indice.json").write_text(json.dumps(sorted(itens, key=lambda x: x["processado_em"])), encoding="utf-8")
    assert getattr(g._pasta_da_analise(live), "name", None) == "vid_novo"
    # a mais recente sem pasta no disco (kit apagado) não vale: fica a que existe
    (saida / "vid_novo").rmdir()
    assert getattr(g._pasta_da_analise(live), "name", None) == "vid_antigo"



def test_g004_esperar_o_fim_tambem_espera_a_execucao_soltar_a_trava_do_cs2(tmp_path):
    """O estado final aparece antes de o `finally` da execução soltar a trava do CS2 (B6). Quem espera o fim (testes e scripts) tem de ver a
    trava já solta: senão a conferência logo depois depende de quem ganha a corrida."""
    def popen(cmd, **kw):
        return _Proc(["[parei] O HLAE não começou a gravar o round 9."], 1)
    g = GerenciadorReceitas(tmp_path / "raiz", popen_factory=popen, python="PY", conta_logada=lambda: BISTURI_ID, config_fn=_cfg,
                            poll_s=0.01, procurar_em=lambda: [tmp_path])
    soltar_original = g._soltar_cs2
    devagar = threading.Event()

    def soltar_devagar(rid):                                          # o `finally` demora um pouco (meio segundo) para soltar a trava
        if g.obter(rid)["estado"] == "erro":
            devagar.wait(0.5)
        soltar_original(rid)
    g._soltar_cs2 = soltar_devagar
    r = g.iniciar(str(_demo(tmp_path)))
    assert g.esperar(r["id"])["estado"] == "erro"
    assert not g._threads[r["id"]].is_alive() and g._dono_cs2 is None and not g._trava_cs2.locked()
