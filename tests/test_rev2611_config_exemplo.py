"""Revisão de 26/11/2026, lote D1 (G-015 e M-010): `config.exemplo.toml` e `Config()` não se desencontram sem que ninguém perceba,
`musicas/licencas.txt` bate com o catálogo e o README não aceita o que a tela recusa.
"""
import dataclasses
import importlib.util
import json
import tomllib
from pathlib import Path

from cs2clip.config import Config, load_config

RAIZ = Path(__file__).resolve().parents[1]
EXEMPLO = RAIZ / "config.exemplo.toml"

# Valores que o exemplo escreve e que de propósito NÃO são o padrão do `Config()` (o exemplo é "como o programa é entregue", com a escolha de estilo do dono).
# chave -> (padrão do Config(), valor do exemplo, motivo)
DIFERENCAS_ESCRITAS_NO_EXEMPLO = {
    "montagem.perfil": ("", "esports_blast", "perfil de entrega: esports limpo, estilo BLAST/HLTV; sem arquivo, o programa não aplica perfil nenhum"),
}
# O que o PERFIL do exemplo muda além das chaves escritas (efeito de `montagem.perfil = "esports_blast"`, em `cs2clip/perfis.py`).
DIFERENCAS_DO_PERFIL = {
    "cortes.pular_parado": (6.0, 5.0, "esports_blast corta o trecho parado mais cedo"),
    "cortes.estender_reacao": (True, False, "esports_blast: corte seco, sem esperar a reação"),
    "shorts.legendas": (True, False, "esports_blast: short limpo, sem legenda"),
    "shorts.gancho_titulo": (True, False, "esports_blast: sem título de abertura"),
}


def _secoes(cfg) -> dict[str, dict]:
    return {k: v for k, v in dataclasses.asdict(cfg).items() if isinstance(v, dict)}


# ── G-015 / M-010: exemplo x Config() ────────────────────────────────────────────────────────────

def test_toda_chave_do_exemplo_existe_no_config():
    exemplo = tomllib.loads(EXEMPLO.read_text(encoding="utf-8"))
    campos = _secoes(Config())
    for secao, valores in exemplo.items():
        assert secao in campos, f"a seção [{secao}] do exemplo não existe no Config()"
        for chave in valores:
            assert chave in campos[secao], f"a chave {secao}.{chave} do exemplo não existe no Config()"


def test_exemplo_carrega_sem_aviso(capsys):
    cfg = load_config(EXEMPLO)
    assert capsys.readouterr().out == "" and cfg.montagem.perfil == "esports_blast"


def test_o_teste_pega_chave_que_nao_existe_e_aviso_de_carga(tmp_path, capsys):
    """CONTROLE: um exemplo com chave errada faria as duas conferências acima falharem."""
    ruim = tmp_path / "config.toml"
    ruim.write_text("[chat]\nusarr = true\n", encoding="utf-8")
    assert "usarr" not in _secoes(Config())["chat"]
    load_config(ruim)
    assert "opção desconhecida 'usarr'" in capsys.readouterr().out


def test_valores_escritos_no_exemplo_so_diferem_do_padrao_nos_casos_declarados():
    exemplo = tomllib.loads(EXEMPLO.read_text(encoding="utf-8"))
    padrao = _secoes(Config())
    achadas = {f"{s}.{k}": (padrao[s][k], v) for s, valores in exemplo.items() for k, v in valores.items() if padrao[s][k] != v}
    assert achadas == {k: (a, b) for k, (a, b, _motivo) in DIFERENCAS_ESCRITAS_NO_EXEMPLO.items()}


def test_efeito_total_do_exemplo_sobre_o_padrao_e_so_o_declarado():
    """O que muda de fato ao carregar o exemplo (chaves escritas + efeito do perfil): nada além do que está na lista."""
    antes, depois = _secoes(Config()), _secoes(load_config(EXEMPLO))
    mudou = {f"{s}.{k}": (antes[s][k], depois[s][k]) for s in antes for k in antes[s] if antes[s][k] != depois[s][k]}
    esperado = {k: (a, b) for k, (a, b, _m) in {**DIFERENCAS_ESCRITAS_NO_EXEMPLO, **DIFERENCAS_DO_PERFIL}.items()}
    assert mudou == esperado


def test_cabecalho_do_exemplo_diz_o_que_e_de_proposito():
    topo = "\n".join(EXEMPLO.read_text(encoding="utf-8").splitlines()[:16])
    for trecho in ('perfil "esports_blast"', "versão", "chat desligado", "capa, resenha e gravacao_demo"):
        assert trecho in topo, trecho


# ── G-015: licencas.txt igual ao catálogo ─────────────────────────────────────────────────────────

def _baixador():
    spec = importlib.util.spec_from_file_location("baixar_musicas_cc0_cfg", RAIZ / "tools" / "baixar_musicas_cc0.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_licencas_txt_e_o_catalogo_em_texto():
    catalogo = json.loads((RAIZ / "musicas" / "catalogo.json").read_text(encoding="utf-8"))
    texto = (RAIZ / "musicas" / "licencas.txt").read_text(encoding="utf-8")
    assert texto == _baixador().texto_de_licencas(catalogo)
    linhas = [linha for linha in texto.splitlines() if linha.startswith("- ")]
    assert [linha[2:].split(" | ")[0] for linha in linhas] == [f["arquivo"] for f in catalogo["faixas"]] and len(linhas) == 32
    for linha, faixa in zip(linhas, catalogo["faixas"]):
        assert f"| Licença: {faixa['licenca']} |" in linha                                    # a licença de cada uma, nunca outra
    pix = next(linha for linha in linhas if linha.startswith("- black-box"))
    assert "| Licença: Pixabay Content License" in pix


# ── G-015: README x tela ──────────────────────────────────────────────────────────────────────────

def test_readme_e_catalogo_dizem_o_mesmo_sobre_o_streambeats():
    """G-015, corrigido pelo orquestrador: a auditoria leu ao contrário. O catálogo ACEITA o StreamBeats (o FAQ oficial permite monetizar,
    decisão do dono de 08-13/11); o README tem de dizer o mesmo que o código."""
    from cs2clip import musicas_catalogo
    assert "streambeats" in musicas_catalogo.LICENCAS
    assert "StreamBeats" in (RAIZ / "README.md").read_text(encoding="utf-8")
