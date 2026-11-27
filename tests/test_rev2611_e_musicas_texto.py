"""G-015 (correção da auditoria, lote E): o texto da tela Músicas diz o mesmo que o catálogo sobre StreamBeats e músicas comerciais."""
import re
from pathlib import Path

from cs2clip import musicas_catalogo as mc

HTML = Path(__file__).resolve().parents[1] / "ui" / "static" / "index.html"


def _texto_da_caixa_adicionar() -> str:
    html = HTML.read_text(encoding="utf-8")
    trecho = html.split('id="musica-nova-titulo"', 1)[1].split("</p>", 1)[0]
    return re.sub(r"<[^>]+>", "", trecho)


def test_a_tela_nao_manda_o_streambeats_para_fora_porque_o_catalogo_aceita():
    texto = _texto_da_caixa_adicionar()
    assert "streambeats" in mc.LICENCAS and "streambeats" not in mc.RECUSADAS            # o que o código faz
    assert "StreamBeats" in texto                                                      # a tela cita o StreamBeats entre as fontes
    frase_de_fora = [f for f in re.split(r"(?<=[.!?])\s+", texto) if "ficam de fora" in f or "fica de fora" in f or "nunca entra" in f]
    assert frase_de_fora and not any("StreamBeats" in f for f in frase_de_fora)       # e não na frase do que fica de fora
    assert any("comercial" in f for f in frase_de_fora)                              # só a música comercial fica de fora
    assert "comercial" in mc.RECUSADAS
    for chave, info in mc.LICENCAS.items():                                           # toda fonte aceita que a tela cita existe no catálogo
        if chave == "streambeats":
            assert "StreamBeats" in info["nome"]
