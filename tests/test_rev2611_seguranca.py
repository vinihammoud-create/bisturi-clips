"""Revisão de 26/11/2026, lote C (segurança e validação). Cada teste falha sem a correção do achado citado."""
from typing import Any

import pytest

from cs2clip.netcon import ComandoBloqueado, validar_linha


# ── G-005: a lista branca do console só aceita as formas que o projeto gera ──────────────────────

def test_g005_mirv_cmd_sem_aspas_e_recusado():
    for ruim in ("mirv_cmd addAtTick 100 exec evil.cfg", "mirv_cmd add 0 quit", "mirv_cmd addAtTime 1.0 connect 1.2.3.4",
                 "mirv_cmd add quit", "mirv_cmd addAtTick 100 quit"):
        with pytest.raises(ComandoBloqueado):
            validar_linha(ruim)


def test_g005_mirv_cmd_texto_solto_fora_das_aspas_e_recusado():
    for ruim in ('mirv_cmd addAtTick 100 "demo_pause" exec evil.cfg', 'mirv_cmd addAtTick 100 exec evil.cfg "demo_pause"',
                 'mirv_cmd addAtTick 100 "demo_pause" "quit"', 'mirv_cmd addAtTick 100 "demo_pause" quit',
                 'mirv_cmd addAtTick abc "demo_pause"', 'mirv_cmd addAtTick -5 "demo_pause"', 'mirv_cmd addAtTick 1.5 "demo_pause"',
                 'mirv_cmd addAtTick "demo_pause"', 'mirv_cmd addAtTick 100 ""', 'mirv_cmd addAtTick 100',
                 'mirv_cmd print exec evil.cfg', 'mirv_cmd clear quit', 'mirv_cmd enabled exec evil.cfg'):
        with pytest.raises(ComandoBloqueado):
            validar_linha(ruim)


def test_g005_formas_do_projeto_continuam_aceitas():
    for boa in ('mirv_cmd addAtTick 69481 "mirv_streams record start"', 'mirv_cmd addAtTick 76400 "mirv_streams record end; demo_pause"',
                "mirv_cmd print", "mirv_cmd clear", 'MIRV_CMD ADDATTICK 5 "demo_pause"'):
        assert validar_linha(boa), boa


# ── G-008: JSON dentro de <script> não pode fechar o bloco nem abrir comentário ─────────────────

def _bloco_script(html_txt: str) -> str:
    return html_txt.split("<script>", 1)[1].rsplit("</script>", 1)[0]


def test_g008_revisao_escapa_o_nome_do_video(tmp_path):
    from cs2clip import revisao
    nome = 'v</script><img src=x onerror=alert(1)><!-- \u2028 \u2029 __DATA__.mp4'
    arq = revisao.gerar_revisao(tmp_path, nome, [{"k": "a</script>b", "rotulo": "<!--x"}])
    txt = arq.read_text(encoding="utf-8")
    assert txt.count("</script>") == 1                       # só o fecho do próprio bloco
    corpo = _bloco_script(txt)
    for perigoso in ("</script", "<!--", "\u2028", "\u2029", "<img"):
        assert perigoso not in corpo
    assert "onerror=alert(1)" in corpo                        # o texto continua lá, só que inofensivo (dentro da string JS)


def test_g008_comparador_escapa_o_nome_da_jogada(tmp_path, monkeypatch):
    from cs2clip import comparador_ab
    par = {"id": "jog</script><!--ada|B.mp4", "jogada": "jog</script><!--ada", "efeito": "x y", "um": "A_normal.mp4",
           "dois": "B.mp4", "normal_e": 1}          # o Windows não deixa criar pasta com '<': o par vem montado na mão
    monkeypatch.setattr(comparador_ab, "pares_da_pasta", lambda pasta: [par])
    saida = comparador_ab.gerar_pagina(tmp_path, [tmp_path])
    assert saida is not None
    corpo = _bloco_script(saida.read_text(encoding="utf-8"))
    for perigoso in ("</script", "<!--", " "):
        assert perigoso not in corpo


def test_g008_json_para_script_unico():
    import json
    from cs2clip.revisao import json_para_script
    valor = {"a": "</script><!--", "b": "x\u2028y\u2029z", "c": 'aspas " e \\ barra', "d": [1, 2.5, None]}
    texto = json_para_script(valor)
    assert "</" not in texto and "<!--" not in texto and "\u2028" not in texto and "\u2029" not in texto
    assert json.loads(texto) == valor                         # continua sendo JSON válido e volta igual


# ── G-009: video_id validado como as outras partes; prefixos de objeto do Windows recusados ──────

def test_g009_video_id_oculto_ou_com_ponto_no_fim_e_recusado(tmp_path):
    from ui.seguranca import caminho_midia_seguro
    saida = tmp_path / "saida"
    for pasta in (".cache", ".oculta", "v1"):
        (saida / pasta).mkdir(parents=True)
        (saida / pasta / "a.mp4").write_bytes(b"x")
    assert caminho_midia_seguro(saida, "v1", "a.mp4").name == "a.mp4"           # CONTROLE: o id normal continua servindo
    for ruim in (".cache", ".CACHE", ".oculta", "v1.", "v1 ", "v1. "):
        with pytest.raises(PermissionError):
            caminho_midia_seguro(saida, ruim, "a.mp4")


def test_g009_prefixos_de_objeto_do_windows_sao_recusados():
    from cs2clip import entrada as ent
    from ui.seguranca import validar_caminho_sem_unc
    ruins = [r"\??\UNC\srv\share\a.mp4", r"\??\C:\a.mp4", r"/??/UNC/srv/share/a.mp4", r"\\.\pipe\x", r"\\?\C:\a.mp4", r"\\?\UNC\srv\sh\a.mp4"]
    for texto in ruins:
        with pytest.raises(PermissionError):
            validar_caminho_sem_unc(texto)
        assert ent._e_caminho_de_rede(texto), texto
    for bom in (r"C:\videos\a.mp4", "D:/videos/a.mp4", "a.mp4", r"pasta\a.mp4"):          # CONTROLE
        assert validar_caminho_sem_unc(bom)
        assert not ent._e_caminho_de_rede(bom), bom
    assert ent.reconhecer(r"\??\UNC\srv\share\a.mp4").tipo == "recusado"


# ── G-019 (parte): arquivo da demo que não abre não derruba a listagem ──────────────────────────

def test_g019_demo_que_nao_abre_vira_recusa_e_nao_excecao(tmp_path, monkeypatch):
    import builtins
    from cs2clip import entrada as ent
    demo = tmp_path / "partida.dem"
    demo.write_bytes(ent.CABECALHO_CS2 + b"resto")
    assert ent.reconhecer(str(demo)).tipo == "demo"                              # CONTROLE: abre normal
    abrir = builtins.open

    def travado(arq, *a, **k):
        if str(arq).endswith("partida.dem"):
            raise PermissionError(13, "arquivo em uso por outro programa")
        return abrir(arq, *a, **k)
    monkeypatch.setattr(builtins, "open", travado)
    r = ent.reconhecer(str(demo))
    assert r.tipo == "recusado" and "partida.dem" in r.mensagem


# ── G-013: a lista das últimas partidas só roda com a conta PRINCIPAL logada ─────────────────────

def test_g013_listar_ultimas_recusa_uma_terceira_conta(tmp_path):
    from cs2clip import baixar_demo as bd
    exe = tmp_path / "boiler-writter.exe"
    exe.write_bytes(b"MZ")
    chamadas = []
    contas = lambda: ("76561198182487950", "76561198071376839")        # noqa: E731  (Bisturi = 222222222, principal = 111111111)
    comum: dict[str, Any] = dict(contas=contas, boiler=exe, pasta_tmp=tmp_path, jogo_aberto=lambda: [], conta_do_bisturi=lambda: 222222222, permitida=lambda: True)

    def rodar(*a, **k):
        chamadas.append(a)
        raise AssertionError("o boiler não podia ser chamado")
    with pytest.raises(bd.ErroDownload, match="principal") as e:
        bd.listar_ultimas(conta=lambda: 333333333, rodar=rodar, **comum)         # uma terceira conta logada
    assert chamadas == [] and e.value.expirou is False


# ── G-012 (parte): o `.dem.info` é gravado de forma atômica ──────────────────────────────────────

def test_g012_dem_info_nao_e_gravado_direto(tmp_path, monkeypatch):
    import bz2
    import io
    import json
    from pathlib import Path
    from cs2clip import baixar_demo as bd

    class Resp(io.BytesIO):
        headers: dict = {}

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None
    demo = b"PBDEMS2\x00" + bytes(range(256)) * 100
    gravados = []
    original = Path.write_text

    def espiao(self, *a, **k):
        gravados.append(self.name)
        return original(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", espiao)
    p = {"matchid": 1, "reservationid": 2, "token": 3, "matchtime": 1773870517,
         "url": "https://replay188.valve.net/730/003809357928926806424_1188481574.dem.bz2"}
    monkeypatch.setattr(bd, "validar_url", lambda u: u)
    arq = bd.baixar_partida(p, tmp_path / "demos", abrir=lambda req, timeout=0: Resp(bz2.compress(demo)))
    info = arq.with_name(arq.name + ".info")
    assert json.loads(info.read_text(encoding="utf-8"))["matchid"] == 1
    assert info.name not in gravados and info.name + ".tmp" in gravados             # só o temporário recebeu o texto; o nome final veio da troca
    assert not list((tmp_path / "demos").glob("*.tmp"))


# ── R-006: a tela Configurar recusa NaN e infinito ───────────────────────────────────────────────

def test_r006_configurar_recusa_nan_e_infinito(tmp_path):
    from ui.configuracao import CAMPOS_POR_CHAVE, ConfiguracaoProjeto, ErroConfig
    campo = CAMPOS_POR_CHAVE[("cortes", "antes")]
    assert ConfiguracaoProjeto.validar(campo, 3.5) == 3.5                          # CONTROLE: número normal passa
    for ruim in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ErroConfig, match="número válido"):
            ConfiguracaoProjeto.validar(campo, ruim)
    cfg = ConfiguracaoProjeto(tmp_path)
    with pytest.raises(ErroConfig):
        cfg.salvar({"cortes.antes": float("nan")})
    assert "nan" not in (tmp_path / "config.toml").read_text(encoding="utf-8").lower() if (tmp_path / "config.toml").exists() else True


# ── R-018: config._validar recusa NaN, infinito e valores fisicamente impossíveis (faixas largas) ──

# (secao, campo, valor ruim): cada um tem de estourar com ValueError que cita o campo
RUINS_DO_CONFIG = [
    ("cortes", "antes", float("nan")), ("cortes", "antes", -1.0), ("cortes", "depois", float("inf")), ("cortes", "depois", -5.0),
    ("cortes", "juntar_intervalo", float("nan")), ("cortes", "juntar_intervalo", -2.0), ("cortes", "juntar_no_round", -1.0),
    ("cortes", "pular_parado", float("nan")), ("cortes", "depois_de_cada_abate", float("nan")), ("cortes", "antes_do_proximo", float("inf")),
    ("cortes", "pulo_minimo", float("nan")), ("cortes", "sequencia_max", 0.0), ("cortes", "sequencia_max", float("nan")),
    ("cortes", "duracao_minima", -3.0), ("cortes", "duracao_minima", float("nan")), ("cortes", "limiar_reacao", float("nan")),
    ("cortes", "limiar_reacao", 1.5), ("cortes", "limiar_reacao", -0.1), ("cortes", "nota_montagem", float("nan")),
    ("cortes", "nota_montagem", -1.0), ("cortes", "nota_short", float("inf")), ("cortes", "nota_short", -10.0),
    ("cortes", "reacoes_sem_abate", -1),
    ("shorts", "duracao_maxima", 0.0), ("shorts", "duracao_maxima", float("nan")), ("shorts", "antes", -1.0), ("shorts", "depois", float("nan")),
    ("shorts", "gancho", -1.0), ("shorts", "zoom_camera", float("nan")), ("shorts", "zoom_gameplay", float("nan")),
    ("montagem", "transicao", float("nan")), ("montagem", "transicao", -0.5), ("montagem", "transicao", 100.0),
    ("montagem", "volume_musica", float("nan")), ("montagem", "volume_musica", float("inf")), ("montagem", "volume_musica", -500.0),
    ("montagem", "ducking_tiros_db", float("nan")), ("montagem", "ducking_tiros_db", -3.0),
    ("chat", "atraso", float("nan")), ("chat", "atraso", -4.0),
    ("deteccao", "fps_analise", float("nan")), ("deteccao", "fps_analise", float("inf")),
    ("qualidade", "lufs", float("nan")), ("qualidade", "lufs", 5.0), ("qualidade", "lufs", -200.0), ("qualidade", "cq", -1), ("qualidade", "cq", 99),
    ("gravacao_demo", "margem_antes_s", float("nan")), ("gravacao_demo", "margem_depois_s", float("inf")), ("gravacao_demo", "margem_antes_s", 5000.0),
]


def _com(secao, campo, valor):
    from cs2clip.config import Config
    cfg = Config()
    setattr(getattr(cfg, secao), campo, valor)
    return cfg


def test_r018_valores_impossiveis_sao_recusados_com_o_nome_do_campo():
    from cs2clip.config import _validar
    for secao, campo, valor in RUINS_DO_CONFIG:
        with pytest.raises(ValueError, match=campo):
            _validar(_com(secao, campo, valor))


def test_r018_padrao_exemplo_e_valores_comuns_continuam_validos():
    from pathlib import Path
    from cs2clip.config import Config, _validar, load_config
    _validar(Config())
    load_config(Path(__file__).resolve().parents[1] / "config.exemplo.toml")
    # faixas LARGAS: o que o EditPlan estreito recusa (A-010) mas é um config legítimo
    for secao, campo, valor in (("montagem", "volume_musica", -28.0), ("montagem", "volume_musica", 6.0), ("montagem", "transicao", 1.5),
                                ("qualidade", "lufs", -30.0), ("qualidade", "lufs", -9.0), ("cortes", "juntar_no_round", 0.0),
                                ("cortes", "nota_short", 0.0), ("cortes", "nota_short", 100.0), ("cortes", "limiar_reacao", 0.0),
                                ("cortes", "limiar_reacao", 1.0), ("shorts", "duracao_maxima", 180.0), ("montagem", "ducking_tiros_db", 0.0)):
        _validar(_com(secao, campo, valor))


def test_r018_tudo_que_a_tela_configurar_permite_continua_valido():
    from cs2clip.config import _validar
    from ui.configuracao import CAMPOS
    for c in CAMPOS:
        if c.tipo in ("numero", "inteiro"):
            for valor in (c.minimo, c.maximo):
                if valor is not None:
                    _validar(_com(c.secao, c.chave, valor))


def test_r018_nan_e_infinito_no_toml_dao_erro_claro(tmp_path):
    from cs2clip.config import load_config
    for texto, campo in (("[cortes]\nantes = nan\n", "antes"), ("[cortes]\ndepois = -5.0\n", "depois"), ("[montagem]\nvolume_musica = inf\n", "volume_musica"),
                         ("[qualidade]\ncq = inf\n", "cq"), ("[shorts]\nquantidade = nan\n", "quantidade")):
        arq = tmp_path / "config.toml"
        arq.write_text(texto, encoding="utf-8")
        with pytest.raises(ValueError, match=campo):
            load_config(arq)


# ── R-007 (parte do servidor): "Voltar aos padrões" não apaga as contas nem a única cópia ────────

TEXTO_COM_CONTAS = ('[cortes]\nantes = 5.0\ndepois = 3.0\n\n[gravacao_demo]\nconta_steamid = "76561198182487950"\n'
                    'conta_principal_id = "76561198071376839"\nconta_apelido = "Reserva"\nconta_principal_apelido = "Principal"\n\n[demo]\njogador = "Fulano"\n')


def test_r007_voltar_a_todos_os_padroes_mantem_contas_e_nome_na_demo(tmp_path):
    import tomllib
    from ui.configuracao import CAMPOS, ConfiguracaoProjeto
    (tmp_path / "config.toml").write_text(TEXTO_COM_CONTAS, encoding="utf-8")
    cfg = ConfiguracaoProjeto(tmp_path)
    r = cfg.voltar_padrao([f"{c.secao}.{c.chave}" for c in CAMPOS])               # o botão geral manda TODAS as chaves
    dados = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert "antes" not in dados.get("cortes", {}) and "depois" not in dados.get("cortes", {})        # o resto voltou ao padrão
    g = dados["gravacao_demo"]
    assert g["conta_steamid"] == "76561198182487950" and g["conta_principal_id"] == "76561198071376839"
    assert g["conta_apelido"] == "Reserva" and g["conta_principal_apelido"] == "Principal" and dados["demo"]["jogador"] == "Fulano"
    assert "mantid" in r["mensagem"]                                                # a resposta diz que as contas ficaram


def test_r007_so_contas_na_lista_vira_erro_claro_e_nao_apaga(tmp_path):
    from ui.configuracao import ConfiguracaoProjeto, ErroConfig
    (tmp_path / "config.toml").write_text(TEXTO_COM_CONTAS, encoding="utf-8")
    with pytest.raises(ErroConfig, match="conta"):
        ConfiguracaoProjeto(tmp_path).voltar_padrao(["gravacao_demo.conta_steamid", "demo.jogador"])
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == TEXTO_COM_CONTAS


def test_r007_copia_de_seguranca_do_reset_sobrevive_aos_salvamentos_seguintes(tmp_path):
    from ui.configuracao import ConfiguracaoProjeto
    caminho = tmp_path / "config.toml"
    caminho.write_text(TEXTO_COM_CONTAS, encoding="utf-8")
    cfg = ConfiguracaoProjeto(tmp_path)
    cfg.voltar_padrao(["cortes.antes"])
    for i in range(8):                                                              # vários salvamentos depois: o `.bak` simples é trocado a cada um
        cfg.salvar({"cortes.depois": 3.0 + i})
    copia = tmp_path / "config.toml.bak-antes-dos-padroes"
    assert "antes = 5.0" in copia.read_text(encoding="utf-8")                        # a cópia de antes do reset continua lá
    assert sorted(a.name for a in tmp_path.glob("config.toml.*")) == ["config.toml.bak", "config.toml.bak-antes-dos-padroes"]   # e nada de pilha de cópias
