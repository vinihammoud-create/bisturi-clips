"""Revisão final da 1.1.0 (itens 3 e 11): "Voltar aos padrões" não liga o chat da Twitch; textos da tela e do README sem erro."""
import tomllib
from pathlib import Path

from cs2clip.config import Config, load_config

RAIZ = Path(__file__).resolve().parents[1]


def test_padrao_do_codigo_deixa_o_chat_e_a_versao_com_efeitos_desligados():
    """Privacidade: o chat da Twitch só liga se a pessoa ligar. O exemplo (de fábrica) já entrega as duas desligadas."""
    padrao = Config()
    assert padrao.chat.usar is False and padrao.shorts.versao_efeitos is False
    exemplo = tomllib.loads((RAIZ / "config.exemplo.toml").read_text(encoding="utf-8"))
    assert exemplo["chat"]["usar"] is padrao.chat.usar and exemplo["shorts"]["versao_efeitos"] is padrao.shorts.versao_efeitos


def test_voltar_aos_padroes_de_todas_as_opcoes_nao_liga_o_chat(tmp_path):
    from ui.configuracao import CAMPOS, ConfiguracaoProjeto
    caminho = tmp_path / "config.toml"
    caminho.write_text("[chat]\nusar = true\n\n[shorts]\nversao_efeitos = true\n", encoding="utf-8")   # a pessoa ligou os dois
    assert load_config(caminho).chat.usar is True
    ConfiguracaoProjeto(tmp_path).voltar_padrao([f"{c.secao}.{c.chave}" for c in CAMPOS])          # o botão geral manda TODAS as chaves
    cfg = load_config(caminho)
    assert cfg.chat.usar is False and "usar" not in caminho.read_text(encoding="utf-8")     # a linha saiu e vale o padrão: desligado


# ── item 11: textos errados ──────────────────────────────────────────────────────────────────────

def test_aviso_de_musicas_serve_no_programa_instalado_e_no_portatil():
    from test_rev2611_e_musicas_tela import _FAIXA, _node
    r = _node(_FAIXA + """
console.log(JSON.stringify({ aviso: m.avisoSemMusicas({ total: 2, no_sorteio: 0 }, [faixa("a", false), faixa("b", false)]) }));
""")
    aviso = r["aviso"]
    assert "instalador" in aviso and "tarefa das músicas" in aviso                    # instalado: não existe a pasta tools\
    assert "tools\\baixar_musicas_cc0.py" in aviso and "portátil" in aviso            # portátil: o script
    assert "licença" in aviso


def test_textos_da_tela_configurar_nao_falam_do_dono():
    from ui.configuracao import CAMPOS
    for c in CAMPOS:
        assert "dono" not in f"{c.rotulo} {c.dica}".lower(), c.chave                          # a tela é de quem usa o programa


def test_readme_com_os_numeros_medidos():
    texto = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "245 MB" not in texto and "1,3 GB" not in texto
    assert "cerca de 1,1 GB" in texto and "cerca de 130 MB" in texto and "cerca de 10 MB de download" in texto
