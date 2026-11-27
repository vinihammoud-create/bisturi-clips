"""Títulos e hashtags por modelo (cs2clip/publicar.py)."""
import json

from cs2clip.moments import Event, Moment
from cs2clip.publicar import gerar_publicar, hashtags, titulos


def jogada(n, hs=False, motivos=(), faixa="PADRAO"):
    m = Moment(10, 20, "abate", kills=[Event(10 + i * 0.5, "kill", ["headshot"] if hs else []) for i in range(n)], idx=1)
    m.reasons, m.faixa = list(motivos), faixa
    return m


def test_titulo_usa_o_rotulo_e_o_contexto_mais_forte():
    t = titulos(jogada(5, motivos=["match point (+10)"], faixa="DESTAQUE"))
    assert t[0] == "ACE no match point 🔥" and len(t) == 3 and all(len(x) <= 60 for x in t)
    assert titulos(jogada(3, hs=True))[0] == "TRIPLE KILL só de HS"
    assert titulos(jogada(1, hs=True))[0] == "Que HS"


def test_fail_e_raiva_tem_modelos_proprios():
    f = Moment(10, 20, "fail")
    assert "😂" in titulos(f)[0] and "#fail" in hashtags(f, "@canaldeexemplo")
    r = Moment(10, 20, "reacao")
    r.emocao = "RAIVA"
    assert "😡" in titulos(r)[0] and "#rage" in hashtags(r, "")


def test_hashtags_sem_repetir_e_com_a_marca():
    tags = hashtags(jogada(5, hs=True, motivos=["match point (+10)"]), "@canaldeexemplo")
    assert tags[:3] == ["#CS2", "#CounterStrike2", "#canaldeexemplo"]
    assert {"#ace", "#multikill", "#headshot", "#matchpoint", "#shorts"} <= set(tags) and len(tags) == len(set(tags))


def test_gerar_publicar_escreve_txt_e_json_so_dos_shorts_gerados(tmp_path):
    a, b = jogada(2), jogada(1)
    b.idx = 2
    arq = {1: {"short": "shorts/01_x_vertical.mp4", "short_efeitos": "shorts/01_x_vertical_efeitos.mp4",
               "miniatura": "miniaturas/01_x.jpg"}, 2: {"short": None}}
    txt = gerar_publicar(tmp_path, "v", [a, b], arq, "@canaldeexemplo")
    d = json.loads((tmp_path / "publicar.json").read_text(encoding="utf-8"))
    assert len(d["itens"]) == 1 and "twitch.tv/canaldeexemplo" in d["itens"][0]["descricao"]
    assert "Título 1: DOUBLE KILL" in txt.read_text(encoding="utf-8")
    assert gerar_publicar(tmp_path / "x", "v", [b], arq, "") is None


def test_publicar_poe_o_credito_da_musica_quando_a_licenca_exige_e_so_a_licenca_quando_nao():
    from cs2clip.publicar import bloco_musica
    ncs = {"arquivo": "fade.mp3", "titulo": "Fade", "autor": "Alan Walker", "licenca": "NCS", "exige_credito": True,
           "credito": "Música: Alan Walker — Fade (NCS) https://ncs.io/Fade", "aviso": "Crédito obrigatório."}
    txt = "\n".join(bloco_musica(ncs))
    assert "A LICENÇA EXIGE CRÉDITO" in txt and "Música: Alan Walker — Fade (NCS) https://ncs.io/Fade" in txt and "Atenção: Crédito obrigatório." in txt
    cc0 = {"arquivo": "a.mp3", "titulo": "Victory", "autor": "Komiku", "licenca": "CC0 1.0", "exige_credito": False, "aviso": "Licença CC0 conforme o repositório."}
    txt2 = "\n".join(bloco_musica(cc0))
    assert "Não exige crédito" in txt2 and "EXIGE" not in txt2 and "Atenção" not in txt2                    # aviso de rotina não polui
    pix = dict(cc0, aviso="Pode haver reclamação automática (Content ID) no YouTube.")
    assert "Atenção: Pode haver reclamação" in "\n".join(bloco_musica(pix))
    assert bloco_musica(None) == []


def test_publicar_escreve_a_musica_no_txt_e_no_json_e_funciona_ate_sem_shorts(tmp_path):
    musica = {"arquivo": "fade.mp3", "titulo": "Fade", "autor": "Alan Walker", "licenca": "NCS", "exige_credito": True, "credito": "Música: Alan Walker — Fade", "aviso": ""}
    txt = gerar_publicar(tmp_path, "v", [], {}, "", musica)                                                # sem nenhum short, mas com música
    assert txt is not None and "Música: Alan Walker — Fade" in txt.read_text(encoding="utf-8")
    assert json.loads((tmp_path / "publicar.json").read_text(encoding="utf-8"))["musica"]["autor"] == "Alan Walker"
    assert gerar_publicar(tmp_path, "v", [], {}, "", None) is None and not (tmp_path / "publicar.txt").exists()   # CONTROLE: sem shorts e sem música, apaga como antes

