"""Revisão final de contexto limpo (26/11/2026): ajustes na interface depois dos lotes. Cada teste falha sem o ajuste citado.
Processos, Steam e Twitch FALSOS; nenhuma espera decide pelo relógio (espera por condição, com prazo)."""
from __future__ import annotations

import threading
import types
from typing import Any, cast

import pytest

from cs2clip import sharecode
from ui.receitas import VIVOS, GerenciadorReceitas

from test_rev2611_trava_e_encerramento import BISTURI_ID, _cfg, _cliente_kit, _espera, _Fabrica, _receita_com

CODIGO = sharecode.codificar(3230642215713767580, 3230647599455273103, 55788)


# ── o que segura o programa e o que impede mexer nos kits (M-008, G-007, R-011, R-001) ─────────────────────────

class _Ger:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _coord(fila_rodando=False, fila_pendente=False, receitas=(), editando=False, grav=False, grav_edita=False, vod=False, modelo=False):
    from ui.coordenador import Coordenador
    estados = set(receitas)
    fila = _Ger(esta_processando=lambda: fila_rodando, tem_pendentes=lambda: fila_pendente)
    rec = _Ger(rodando_de_fato=lambda: bool(estados & {"na_fila", "rodando"}), editando=lambda: editando, tem_viva=lambda: bool(estados & VIVOS))
    return Coordenador(fila, rec, _Ger(ocupado=lambda: grav, editando=lambda: grav and grav_edita), _Ger(baixando=lambda: vod),
                       _Ger(baixando=lambda: modelo))


@pytest.mark.parametrize("caso, rodando, segura, mexe_nos_kits", [
    ({}, False, False, False),
    ({"fila_rodando": True}, True, True, True),                     # a análise grava o kit
    ({"fila_pendente": True}, False, True, False),                  # na fila para rodar: segura o programa, mas ainda não grava nada
    ({"receitas": {"rodando"}}, True, True, False),                 # receita gravando pelo CS2 (não mexe em saida/)
    ({"receitas": {"rodando"}, "editando": True}, True, True, True),
    ({"receitas": {"na_fila"}}, True, True, False),
    ({"receitas": {"esperando_voce"}}, False, False, False),        # decisão do dono: "esperando você" não segura nem bloqueia
    ({"receitas": {"pronta", "erro", "interrompida"}}, False, False, False),
    ({"grav": True}, True, True, False),                            # gravação sem "editar em seguida"
    ({"grav": True, "grav_edita": True}, True, True, True),
    ({"vod": True}, True, True, False),                             # download de VOD não mexe em kit nenhum
    ({"modelo": True}, True, True, False),                          # P4.2b: download do modelo de voz segura o programa, não mexe em kit
])
def test_tabela_do_que_segura_o_programa_e_do_que_mexe_nos_kits(caso, rodando, segura, mexe_nos_kits):
    c = _coord(**caso)
    assert (c.rodando(), c.segura_o_programa(), c.mexe_nos_kits()) == (rodando, segura, mexe_nos_kits)


def _editando_o_kit(rid="x", criada_em=0.0):
    r = _receita_com("rodando", rid, criada_em)
    r["etapas"] = [{"nome": "editar", "estado": "rodando"}]
    return r


def _montar(st: Any, cenario: str) -> None:
    if cenario == "analise_na_fila":
        st.fila._active_id = "outro-job"
    elif cenario == "receita_editando":
        st.receitas._receitas["x"] = _editando_o_kit()
    elif cenario == "receita_editando_alem_das_30":               # a tela só mostra as 30 mais recentes: a 31ª também conta
        st.receitas._receitas["velha"] = _editando_o_kit("velha", 1.0)
        for i in range(30):
            st.receitas._receitas[f"n{i}"] = _receita_com("pronta", f"n{i}", 100.0 + i)
    elif cenario == "gravacao_com_edicao":
        st.gravacao._job = {"estado": "rodando", "edita": True}
    elif cenario == "gravacao_sem_edicao":
        st.gravacao._job = {"estado": "rodando", "edita": False}
    elif cenario == "receita_gravando":                             # etapa `gravar`: escreve em gravacao_demo/, não em saida/
        st.receitas._receitas["x"] = _receita_com("rodando")
    elif cenario == "esperando_voce":
        st.receitas._receitas["x"] = _receita_com("esperando_voce")
    elif cenario == "vod":
        st.vods._jobs["v"] = {"id": "v", "estado": "baixando"}
    else:
        raise AssertionError(cenario)


def _desmontar(st: Any) -> None:
    st.fila._active_id = None
    st.receitas._receitas.clear()
    st.gravacao._job = None
    st.vods._jobs.clear()


CENARIOS = [("analise_na_fila", True), ("receita_editando", True), ("receita_editando_alem_das_30", True), ("gravacao_com_edicao", True),
            ("gravacao_sem_edicao", False), ("receita_gravando", False), ("esperando_voce", False), ("vod", False)]


@pytest.mark.parametrize("cenario, bloqueia", CENARIOS)
def test_apagar_kit_so_espera_o_que_grava_nos_kits(tmp_path, cenario, bloqueia):
    """G-007 (ajuste): antes QUALQUER trabalho vivo barrava (até um download de VOD, que precisa justamente do disco que apagar libera)."""
    with _cliente_kit(tmp_path) as c:
        st: Any = c.app_state
        _montar(st, cenario)
        r = c.post("/api/videos/v1/apagar", json={"confirmar": "v1"}, headers={"X-Token": st.token})
        if bloqueia:
            assert r.status_code == 409 and "gravando os kits" in r.json()["erro"] and (tmp_path / "saida" / "v1").is_dir()
            _desmontar(st)                                           # CONTROLE: sem nada gravando, apaga
            r = c.post("/api/videos/v1/apagar", json={"confirmar": "v1"}, headers={"X-Token": st.token})
        assert r.status_code == 200 and not (tmp_path / "saida" / "v1").exists()


@pytest.mark.parametrize("cenario, bloqueia", CENARIOS)
def test_gostei_descartar_so_espera_o_que_grava_nos_kits(tmp_path, cenario, bloqueia):
    """R-011 (ajuste): um download de 4 h ou uma gravação pelo CS2 não mexem no momentos.json e não podem travar a revisão dos kits."""
    with _cliente_kit(tmp_path) as c:
        st: Any = c.app_state
        _montar(st, cenario)
        r = c.post("/api/videos/v1/momento", json={"foco": 12.0, "acao": "aprovar"}, headers={"X-Token": st.token})
        assert r.status_code == (409 if bloqueia else 200), r.text
        _desmontar(st)


def _ger(tmp_path, fabrica, trava):
    return GerenciadorReceitas(tmp_path / "raiz", popen_factory=fabrica, python="PY", conta_logada=lambda: BISTURI_ID, config_fn=_cfg,
                               poll_s=0.01, procurar_em=lambda: [tmp_path], trava_cs2=trava)


def test_r001_quem_espera_a_trava_de_quem_espera_o_dono_nao_segura_o_programa(tmp_path):
    """A receita A está "esperando você" (trocar a conta) com a trava do CS2; a B espera a trava com estado `rodando`. Nada anda sem o dono:
    isso não pode segurar o programa aberto (decisão do dono). Se a A estiver de fato gravando, aí a B segura."""
    g = _ger(tmp_path, _Fabrica(), threading.Lock())
    try:
        g._receitas["a"] = _receita_com("esperando_voce", "a")
        g._receitas["b"] = _receita_com("rodando", "b")
        g._dono_cs2 = "a"
        g._esperando_trava.add("b")
        assert g.rodando_de_fato() is False
        g._receitas["a"]["estado"] = "rodando"                       # a A voltou a trabalhar (o dono trocou a conta): a B vai rodar em seguida
        assert g.rodando_de_fato() is True
        g._receitas["a"]["estado"] = "esperando_voce"
        g._esperando_trava.clear()                                   # CONTROLE: a B rodando de verdade (não esperando a trava) segura
        assert g.rodando_de_fato() is True
    finally:
        g._receitas.clear()


def test_g003_na_retomada_o_download_por_codigo_espera_a_trava_do_cs2(tmp_path):
    """No caminho normal a trava vem da etapa de conta. Numa retomada com a etapa de conta já ok, o download (que abre o CS2 por segundos)
    rodava sem a trava, no meio da gravação de outro trabalho."""
    trava = threading.Lock()
    fab = _Fabrica()
    g = _ger(tmp_path, fab, trava)
    et = {"nome": "obter_demo", "estado": "rodando", "detalhe": ""}
    r = {"id": "x", "estado": "rodando", "etapas": [et], "entrada": {"tipo": "codigo_partilha", "valor": CODIGO}, "dados": {}, "opcoes": {},
         "criada_em": 0.0, "titulo": "t"}
    g._receitas["x"] = r
    g._cancel["x"] = threading.Event()
    assert trava.acquire(blocking=False)                             # outra gravação está com o CS2
    erros: list[BaseException] = []

    def rodar():
        try:
            g._etapa_obter_demo(r, et)
        except BaseException as e:  # noqa: BLE001  (o baixador falso não devolve demo: a etapa termina em erro, e é só isso que se espera)
            erros.append(e)
    fio = threading.Thread(target=rodar, daemon=True)
    fio.start()
    assert _espera(lambda: "Esperando outra gravação" in et["detalhe"])
    assert fab.comandos == []                                        # o baixador NÃO abriu enquanto o CS2 é de outro
    trava.release()
    assert _espera(lambda: len(fab.comandos) == 1) and fab.comandos[0][2:4] == ["cs2clip.baixar_demo", "codigo"]
    assert g._dono_cs2 == "x"                                        # e a trava continua com esta receita (até o fim da gravação)
    fab.processos[0].fim.set()
    fio.join(10)
    assert not fio.is_alive() and len(erros) == 1
    g._soltar_cs2("x")
    g._receitas.clear()


# ── G-009 completo: o id do kit vale a mesma regra em /media e em /api/videos ───────────────────────────────

@pytest.mark.parametrize("ruim", ["v1.", "v1 ", ".cache", ".v1", "v1.. "])
def test_g009_id_de_kit_que_o_windows_abriria_como_outra_pasta_e_recusado(tmp_path, ruim):
    from ui.dados import DadosProjeto
    from test_ui_dados_seguranca import criar_projeto
    criar_projeto(tmp_path)
    (tmp_path / "saida" / ".cache").mkdir()
    dados = DadosProjeto(tmp_path)
    with pytest.raises(PermissionError):
        dados.pasta_video(ruim)
    with pytest.raises(PermissionError):                             # "apagar v1." apagava a pasta v1 e deixava o índice errado
        dados.apagar_kit(ruim)
    assert (tmp_path / "saida" / "v1").is_dir() and dados.pasta_video("v1").name == "v1"


# ── R-010: dois "Salvar" ao mesmo tempo não se cruzam (as rotas rodam em threads) ───────────────────────────

def test_r010_salvar_e_voltar_aos_padroes_rodam_um_de_cada_vez(tmp_path, monkeypatch):
    from ui.configuracao import ConfiguracaoProjeto
    cfg = ConfiguracaoProjeto(tmp_path)
    dentro = threading.Event()
    soltar = threading.Event()
    ativos = {"agora": 0, "maximo": 0}
    trava_conta = threading.Lock()

    def lento(*a, **k):
        with trava_conta:
            ativos["agora"] += 1
            ativos["maximo"] = max(ativos["maximo"], ativos["agora"])
        dentro.set()
        soltar.wait(10)
        with trava_conta:
            ativos["agora"] -= 1
        return {"sucesso": True}
    monkeypatch.setattr(cfg, "_salvar", lento)
    monkeypatch.setattr(cfg, "_voltar_padrao", lento)
    fios = [threading.Thread(target=cfg.salvar, args=({"cortes.antes": 5.0},), daemon=True),
            threading.Thread(target=cfg.voltar_padrao, args=(["cortes.antes"],), daemon=True),
            threading.Thread(target=cfg.salvar, args=({"cortes.depois": 3.0},), daemon=True)]
    for f in fios:
        f.start()
    assert dentro.wait(10)
    # as outras duas chamadas já foram disparadas e estão paradas na trava: nenhuma entrou junto com a primeira
    assert _espera(lambda: all(f.is_alive() for f in fios), 2) and ativos["maximo"] == 1
    soltar.set()
    for f in fios:
        f.join(10)
    assert not any(f.is_alive() for f in fios) and ativos["maximo"] == 1


# ── o vigia da janela fechada não morre se a pergunta "há trabalho?" der erro ───────────────────────────────

def test_vigia_sobrevive_a_erro_na_pergunta_de_trabalho_e_o_programa_ainda_sai(monkeypatch, capsys):
    import ui.__main__ as ui_main
    relogio = {"t": 1000.0, "n": 0}
    server = types.SimpleNamespace(should_exit=False, pelo_vigia=True)

    def dorme(_):
        relogio["n"] += 1
        relogio["t"] += 5
        if relogio["n"] > 80:
            server.pelo_vigia = False
            server.should_exit = True
    monkeypatch.setattr(ui_main, "time", types.SimpleNamespace(sleep=dorme, monotonic=lambda: relogio["t"]))

    def quebra():
        raise RuntimeError("estado estranho")
    estado = types.SimpleNamespace(coordenador=types.SimpleNamespace(segura_o_programa=quebra), fila=None, pedir_saida=False,
                                   janela_fechada_em=1000.0, ultima_atividade=1000.0, midia_em=None)
    ui_main.vigia_encerramento(cast(Any, server), estado, agora=lambda: relogio["t"], intervalo=0)
    assert server.should_exit and server.pelo_vigia                   # foi o VIGIA que mandou sair (antes ele morria na exceção e o programa ficava para sempre)
    assert "não consegui saber se há trabalho" in capsys.readouterr().out
