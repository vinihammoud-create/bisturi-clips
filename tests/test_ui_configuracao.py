import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from ui.configuracao import CAMPOS, ConfiguracaoProjeto, ErroConfig


DEFAULTS = {
    "cortes": {"antes": 3, "depois": 5, "juntar_intervalo": 8, "duracao_minima": 3,
               "estender_reacao": True, "reacoes_sem_abate": 4, "so_partidas": True,
               "limiar_reacao": 0.7, "nota_montagem": 30, "nota_short": 40, "min_abates_round": 3, "juntar_no_round": 120, "pular_parado": 6,
               "depois_de_cada_abate": 6.0, "antes_do_proximo": 6.0, "pulo_minimo": 2.0,   # N2/N2c: a tela Configurar passou a mostrar estas 3 (e a esconder pular_parado)
               "speed_ramp": False, "freeze_headshot": False, "cartao_tatico": False,
               "falha_com_reacao": False},   # 23/11: "começou bem e terminou mal, com a reação" (desligada)
    "shorts": {"gerar": True, "quantidade": 8, "duracao_maxima": 60, "legendas": True, "selo_jogada": True, "zoom_no_abate": False,
               "marca": "@canaldeexemplo", "mostrar_killfeed": True, "pan_suave": False, "gancho_titulo": True,
               "layout": "classico", "webcam_retangulo": ""},   # 23/11: formato do short em pé (o de sempre) e o retângulo da webcam (vazio)
    "montagem": {"gerar": True, "ordem": "melhores", "perfil": "", "transicao_tipo": "corte", "musica": "auto",
                 "corte_na_batida": True, "kill_sync": True, "sfx": False, "grade_de_corte": "batida", "versao_com_voz": False, "ducking_tiros_db": 0.0,
                 "tiro_no_drop": True, "incluir_resenha": False},
    "audio": {"faixa_jogo": 0, "faixa_voz": 0, "faixa_comms": 0, "tratar_voz": False, "separar_jogo_faixa_unica": False},
    "fails": {"exigir_aprovacao": False},
    "transcricao": {"modelo": "small", "idioma": "pt"},
    "chat": {"usar": False},
    "qualidade": {"encoder": "auto", "cq": 21, "renders_paralelos": 1, "checar_conteudo": True, "checar_silencio": False, "gate_loudness": False, "garantir_pico": True},
    "demo": {"jogador": "", "automatica": False},
    "capa": {"estilo": "grande", "alternativas_da_mesma_jogada": False},
    "resenha": {"gerar": True, "discord_basta": False},
    "gravacao_demo": {"conta_steamid": "", "conta_apelido": "", "conta_principal_id": "", "conta_principal_apelido": "", "so_rounds_bons": True},
}


class FakeConfig:
    def __init__(self):
        for secao, itens in DEFAULTS.items():
            setattr(self, secao, SimpleNamespace(**itens))


def load_config(path: Path):
    texto = path.read_text(encoding="utf-8") if path.exists() else ""
    dados = tomllib.loads(texto) if texto.strip() else {}
    if dados.get("cortes", {}).get("antes") == 14:
        raise ValueError("configuração recusada pelo teste")
    return dados


def test_edicao_pontual_preserva_comentarios_outras_linhas_e_crlf(tmp_path):
    original = (
        "# comentário inicial\r\n"
        "[cortes]\r\n"
        "antes = 4 # comentário da chave\r\n"
        "depois = 6\r\n"
        "# comentário da seção\r\n"
        "[shorts]\r\n"
        "gerar = true\r\n"
        "[nao_expor]\r\n"
        "secreto = \"continua aqui\"\r\n"
    )
    (tmp_path / "config.toml").write_bytes(original.encode("utf-8"))
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    config.salvar({"cortes.antes": 8, "cortes.duracao_minima": 10, "shorts.gerar": False})
    bytes_depois = (tmp_path / "config.toml").read_bytes()
    texto = bytes_depois.decode("utf-8")
    assert "# comentário inicial" in texto
    assert "antes = 8 # comentário da chave" in texto
    assert "# comentário da seção" in texto
    assert b'[nao_expor]\r\nsecreto = "continua aqui"\r\n' in bytes_depois
    dados = tomllib.loads(texto)
    assert dados["cortes"]["antes"] == 8
    assert dados["cortes"]["duracao_minima"] == 10
    assert dados["shorts"]["gerar"] is False
    assert dados["nao_expor"]["secreto"] == "continua aqui"
    assert b"\r\n" in bytes_depois
    assert (tmp_path / "config.toml.bak").exists()


def test_limites_e_chaves_fora_da_lista_sao_rejeitados(tmp_path):
    (tmp_path / "config.toml").write_text("[cortes]\nantes = 4\n", encoding="utf-8")
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    with pytest.raises(ErroConfig):
        config.salvar({"cortes.antes": 16})
    with pytest.raises(ErroConfig):
        config.salvar({"deteccao.ativar": True})
    with pytest.raises(ErroConfig):
        config.salvar({"shorts.camera_x": 123})
    assert "antes = 4" in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_restaura_backup_quando_load_config_falha(tmp_path):
    original = "# manter\n[cortes]\nantes = 4\ndepois = 3\n"
    caminho = tmp_path / "config.toml"
    caminho.write_text(original, encoding="utf-8")
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    with pytest.raises(ErroConfig):
        config.salvar({"cortes.antes": 14})
    assert caminho.read_text(encoding="utf-8") == original
    assert (tmp_path / "config.toml.bak").read_text(encoding="utf-8") == original


def test_voltar_aos_padroes_remove_a_linha_sem_apagar_comentarios(tmp_path):
    original = "# principal\n[cortes]\n# nota preservada\nantes = 4 # valor atual\ndepois = 5\n[shorts]\ngerar = true\n"
    caminho = tmp_path / "config.toml"
    caminho.write_text(original, encoding="utf-8")
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    config.voltar_padrao(["cortes.antes"])
    texto = caminho.read_text(encoding="utf-8")
    assert "antes = 4" not in texto
    assert "# principal" in texto
    assert "# nota preservada" in texto
    assert "depois = 5" in texto
    assert tomllib.loads(texto)["shorts"]["gerar"] is True


def test_padrao_vem_de_config_e_nao_de_valor_inventado(tmp_path):
    config = ConfiguracaoProjeto(tmp_path, load_config_fn=load_config, config_cls=FakeConfig)
    dados = config.ler()
    por_nome = {f"{c['secao']}.{c['chave']}": c for c in dados["campos"]}
    assert len(por_nome) == len(CAMPOS)
    assert por_nome["cortes.antes"]["valor"] == DEFAULTS["cortes"]["antes"]
    assert por_nome["demo.jogador"]["valor"] == ""
    assert por_nome["qualidade.encoder"]["valor"] == "auto"


# ── 01/11/2026: todo campo da tela existe de verdade no Config() do programa ─────────────────────────

def test_todo_campo_da_tela_existe_no_config_real_com_tipo_e_padrao_validos():
    from cs2clip.config import Config
    cfg = Config()
    tipos = {"bool": (bool,), "inteiro": (int,), "numero": (int, float), "texto": (str,), "opcao": (str,)}
    for campo in CAMPOS:
        grupo = getattr(cfg, campo.secao, None)
        assert grupo is not None and hasattr(grupo, campo.chave), f"[{campo.secao}] {campo.chave} não existe no Config()"
        padrao = getattr(grupo, campo.chave)
        assert isinstance(padrao, tipos[campo.tipo]), f"[{campo.secao}] {campo.chave}: padrão {padrao!r} não é {campo.tipo}"
        assert campo.tipo == "bool" or not isinstance(padrao, bool)
        if campo.tipo in {"numero", "inteiro"}:
            assert (campo.minimo is None or padrao >= campo.minimo) and (campo.maximo is None or padrao <= campo.maximo)
        if campo.tipo == "opcao":
            assert padrao in campo.opcoes, f"[{campo.secao}] {campo.chave}: padrão {padrao!r} fora da lista"


def test_efeitos_e_faixas_novas_salvam_e_o_programa_aceita(tmp_path):
    from cs2clip.config import load_config
    (tmp_path / "config.toml").write_text("[cortes]\nantes = 4\n", encoding="utf-8")
    config = ConfiguracaoProjeto(tmp_path)          # núcleo de verdade: o load_config valida o arquivo salvo
    config.salvar({"audio.faixa_jogo": 2, "audio.faixa_voz": 3, "audio.separar_jogo_faixa_unica": True,
                   "cortes.freeze_headshot": True, "shorts.pan_suave": True, "qualidade.checar_silencio": True})
    cfg = load_config(tmp_path / "config.toml")
    assert (cfg.audio.faixa_jogo, cfg.audio.faixa_voz, cfg.audio.separar_jogo_faixa_unica) == (2, 3, True)
    assert cfg.cortes.freeze_headshot and cfg.shorts.pan_suave and cfg.qualidade.checar_silencio
    assert cfg.cortes.antes == 4 and cfg.cortes.speed_ramp is False          # o resto continua igual
    with pytest.raises(ErroConfig):
        config.salvar({"audio.faixa_jogo": 7})                               # o OBS só tem 6 faixas
    assert load_config(tmp_path / "config.toml").audio.faixa_jogo == 2


def test_r5_campos_do_drop_e_da_resenha_aparecem_e_gravam(tmp_path):
    from ui.configuracao import CAMPOS
    chaves = {(c.secao, c.chave): c for c in CAMPOS}
    assert chaves[("montagem", "tiro_no_drop")].tipo == "bool" and chaves[("montagem", "incluir_resenha")].tipo == "bool"
    assert "drop" in chaves[("montagem", "tiro_no_drop")].rotulo.lower()


def test_campos_das_contas_do_steam_so_aceitam_17_numeros_e_podem_ficar_vazios():
    from ui.configuracao import CAMPOS_POR_CHAVE, ConfiguracaoProjeto, ErroConfig
    campo = CAMPOS_POR_CHAVE[("gravacao_demo", "conta_steamid")]
    assert ConfiguracaoProjeto.validar(campo, "76561198182487950") == "76561198182487950" and ConfiguracaoProjeto.validar(campo, "") == ""
    for ruim in ("abcdefghijklmnopq", "7656119818248795", "765611981824879500", "76561198182487950 ", "7656119818248795x"):
        with __import__("pytest").raises(ErroConfig):
            ConfiguracaoProjeto.validar(campo, ruim)
    assert ("gravacao_demo", "conta_principal_id") in CAMPOS_POR_CHAVE and ("gravacao_demo", "so_rounds_bons") in CAMPOS_POR_CHAVE

