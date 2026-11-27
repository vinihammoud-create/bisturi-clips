"""14/11/2026: uma instalação nova sai com as ESCOLHAS DE PRODUTO do dono, como saía quando o config.toml era versionado.

O C4 tirou o config.toml do git. A primeira versão do `config.exemplo.toml` trazia só os padrões do código, e os padrões do código não
são o que o programa entregava: o config versionado ligava o perfil `esports_blast` (estilo esports limpo, decisão de 29/10), desligava a
versão do short "com efeitos" (reprovada pelo dono), o título de abertura do short (D-G2, 08/11) e o chat. Um clone novo passaria a gerar
tudo isso de novo. Aqui fica travado: o exemplo carrega essas escolhas, tira só o que é pessoal, e o config.toml nasce dele.
"""
import dataclasses
import shutil
from pathlib import Path

from cs2clip.config import Config, load_config

RAIZ = Path(__file__).resolve().parents[1]
EXEMPLO = RAIZ / "config.exemplo.toml"

# O que o programa entrega de fábrica e que difere do padrão do código (igual ao config.toml que era versionado até 13/11, sem o que é do dono).
ESCOLHAS_DE_PRODUTO = {
    ("montagem", "perfil"): "esports_blast",       # estilo esports limpo (BLAST/HLTV): decisão do dono de 29/10
    ("cortes", "pular_parado"): 5.0,               # vem do perfil
    ("cortes", "estender_reacao"): False,          # vem do perfil: corte seco
    ("shorts", "legendas"): False,                 # vem do perfil
    ("shorts", "gancho_titulo"): False,            # sem título de abertura no short (D-G2, 08/11): era spoiler e tapava a mira
}
# Fora da lista de propósito (revisão final 27/11): `shorts.versao_efeitos` ("com efeitos" nunca aprovada) e `chat.usar` (chat da Twitch nunca testado
# com chat real) agora SÃO o padrão do código (False): "Voltar aos padrões" apaga a linha do config.toml e antes ligava o chat da Twitch.


def _diferencas(cfg: Config) -> dict:
    padrao, saida = Config(), {}
    for secao in dataclasses.fields(padrao):
        a, p = getattr(cfg, secao.name), getattr(padrao, secao.name)
        if not dataclasses.is_dataclass(p):
            if a != p:
                saida[(secao.name, "")] = a
            continue
        for campo in dataclasses.fields(p):
            if getattr(a, campo.name) != getattr(p, campo.name):
                saida[(secao.name, campo.name)] = getattr(a, campo.name)
    return saida


def test_o_exemplo_entrega_as_escolhas_de_produto_e_mais_nada():
    assert _diferencas(load_config(EXEMPLO)) == ESCOLHAS_DE_PRODUTO
    assert _diferencas(Config()) == {}                                                   # CONTROLE da própria comparação


def test_nada_pessoal_no_exemplo():
    cfg = load_config(EXEMPLO)
    assert cfg.shorts.marca == "" and cfg.demo.jogador == ""
    assert (cfg.audio.faixa_jogo, cfg.audio.faixa_voz, cfg.audio.faixa_comms) == (0, 0, 0)   # as faixas do OBS são de cada pessoa
    assert cfg.gravacao_demo.conta_steamid == "" and cfg.gravacao_demo.conta_principal_id == ""
    assert cfg.qualidade.renders_paralelos == Config().qualidade.renders_paralelos          # o valor seguro, não o da placa do dono


def test_sem_config_o_programa_nasce_do_exemplo_e_cria_o_config(tmp_path):
    shutil.copy(EXEMPLO, tmp_path / "config.exemplo.toml")
    caminho = tmp_path / "config.toml"
    cfg = load_config(caminho)
    assert _diferencas(cfg) == ESCOLHAS_DE_PRODUTO                                       # clone novo, sem rodar o instalar.bat: mesmas escolhas
    assert caminho.read_bytes() == EXEMPLO.read_bytes()                                  # e o config.toml da pessoa nasce do exemplo
    assert load_config(caminho) == cfg


def test_config_que_ja_existe_nunca_e_tocado(tmp_path):
    shutil.copy(EXEMPLO, tmp_path / "config.exemplo.toml")
    caminho = tmp_path / "config.toml"
    caminho.write_text('[montagem]\nperfil = ""\n[shorts]\nversao_efeitos = true\n', encoding="utf-8")   # a pessoa escolheu outra coisa
    antes = (caminho.read_bytes(), caminho.stat().st_mtime_ns)
    cfg = load_config(caminho)
    assert (caminho.read_bytes(), caminho.stat().st_mtime_ns) == antes
    assert cfg.montagem.perfil == "" and cfg.shorts.versao_efeitos is True               # vale o que está no arquivo dela


def test_sem_exemplo_ao_lado_vale_o_padrao_do_codigo_e_nada_e_criado(tmp_path):
    caminho = tmp_path / "config.toml"
    assert load_config(caminho) == Config() and not caminho.exists()                     # CONTROLE: pasta sem o exemplo (testes, ferramentas)
    outro = tmp_path / "outro_nome.toml"
    shutil.copy(EXEMPLO, tmp_path / "config.exemplo.toml")
    assert load_config(outro) == Config() and not outro.exists()                         # só o arquivo chamado config.toml nasce do exemplo
    assert load_config(None) == Config()


def test_pasta_onde_nao_da_para_gravar_usa_o_exemplo_direto(tmp_path, monkeypatch):
    shutil.copy(EXEMPLO, tmp_path / "config.exemplo.toml")
    caminho = tmp_path / "config.toml"

    def sem_permissao(*a, **k):
        raise PermissionError(13, "sem permissão")
    monkeypatch.setattr(shutil, "copyfile", sem_permissao)
    cfg = load_config(caminho)
    assert _diferencas(cfg) == ESCOLHAS_DE_PRODUTO and not caminho.exists()              # não criou, mas entregou as escolhas mesmo assim
