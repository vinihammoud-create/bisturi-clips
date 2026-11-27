"""S3 (revisão do Fable, B4/B5): a tela Músicas diz quais faixas de fato entram no sorteio da montagem (mesma regra do código), em que ordem de preferência,
de onde cada uma veio, e a lista vem ordenada. Mais os textos simples e o ícone próprio do menu."""
import json
import re
from pathlib import Path

from cs2clip import montagem
from cs2clip import musicas_catalogo as mc

from test_musicas_js_e7 import _node

RAIZ = Path(__file__).resolve().parents[1]


def _catalogo(tmp_path):
    """Mistura de propósito: com drop (várias emoções), sem drop, vinheta curta, desligada e sem arquivo."""
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    base = {"licenca": "CC0 1.0 (Domínio Público)", "bpm": 120.0}
    faixas = [
        {**base, "arquivo": "media.mp3", "titulo": "Média", "duracao_s": 200.0, "drop_s": 30.0, "emocao": 0.6},
        {**base, "arquivo": "alta.mp3", "titulo": "Alta", "duracao_s": 200.0, "drop_s": 30.0, "emocao": 0.9},
        {**base, "arquivo": "baixa.mp3", "titulo": "Baixa", "duracao_s": 200.0, "drop_s": 30.0, "emocao": 0.3},
        {**base, "arquivo": "semdrop.mp3", "titulo": "Sem drop", "duracao_s": 200.0, "drop_s": None, "emocao": 0.95},
        {**base, "arquivo": "vinheta.mp3", "titulo": "Vinheta", "duracao_s": 6.0, "drop_s": 2.0, "emocao": 0.99},
        {**base, "arquivo": "desligada.mp3", "titulo": "Desligada", "duracao_s": 200.0, "drop_s": 30.0, "emocao": 0.99, "desligada": True},
        {**base, "arquivo": "sumiu.mp3", "titulo": "Sumiu", "duracao_s": 200.0, "drop_s": 30.0, "emocao": 0.99},
    ]
    for f in faixas:
        if f["arquivo"] != "sumiu.mp3":
            (pasta / f["arquivo"]).write_bytes(b"x" * 60_000)
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": faixas}), encoding="utf-8")
    return pasta


def test_a_tela_diz_quais_faixas_entram_no_sorteio_e_a_preferencia_e_o_motivo_de_ficar_de_fora(tmp_path):
    por = {f["arquivo"]: f for f in mc.listar(_catalogo(tmp_path))}
    assert [(a, por[a]["posicao"]) for a in ("alta.mp3", "media.mp3", "baixa.mp3")] == [("alta.mp3", 1), ("media.mp3", 2), ("baixa.mp3", 3)]
    assert all(por[a]["entra_no_sorteio"] and por[a]["fora_do_sorteio"] == "" for a in ("alta.mp3", "media.mp3", "baixa.mp3"))
    assert por["semdrop.mp3"]["entra_no_sorteio"] is False and por["semdrop.mp3"]["fora_do_sorteio"] == "sem drop" and por["semdrop.mp3"]["posicao"] is None
    assert por["vinheta.mp3"]["entra_no_sorteio"] is False and "muito curta" in por["vinheta.mp3"]["fora_do_sorteio"]
    for desligada in ("desligada.mp3", "sumiu.mp3"):                                                      # desligada ou sem arquivo: não ganha "motivo" (já aparece apagada)
        assert por[desligada]["ativa"] is False and por[desligada]["entra_no_sorteio"] is False and por[desligada]["fora_do_sorteio"] == ""


def test_quem_a_tela_diz_que_entra_no_sorteio_e_exatamente_quem_a_montagem_pode_escolher(tmp_path):
    """Prova cruzada com o código que escolhe de verdade: 400 vídeos diferentes, a faixa escolhida está SEMPRE entre as que a tela marca, nunca fora;
    e sem chave a primeira escolha é a de posição 1."""
    pasta = _catalogo(tmp_path)
    marcadas = {f["arquivo"] for f in mc.listar(pasta) if f["entra_no_sorteio"]}
    escolhidas = set()
    for i in range(400):
        faixa, _ = montagem.escolher_musica_por_ritmo(12.0 + (i % 7), pasta, f"MONTAGEM_{i}", 70.0)
        escolhidas.add(faixa.name)
    assert escolhidas and escolhidas <= marcadas and "alta.mp3" in escolhidas
    assert montagem.escolher_musica_por_ritmo(12.0, pasta, "", 70.0)[0].name == "alta.mp3"


def test_catalogo_sem_nenhuma_faixa_com_drop_mantem_a_regra_do_codigo(tmp_path):
    """Se só há vinheta curta ou ninguém tem drop, a montagem usa o que houver: a tela não pode dizer que ninguém entra."""
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    (pasta / "a.mp3").write_bytes(b"x" * 60_000)
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": [{"arquivo": "a.mp3", "licenca": "CC0", "duracao_s": 8.0, "drop_s": None, "emocao": 0.5}]}), encoding="utf-8")
    [f] = mc.listar(pasta)
    assert f["entra_no_sorteio"] is True and f["posicao"] == 1                                              # só vinhetas curtas: melhor elas que sem música (regra da montagem)
    assert montagem.escolher_musica_por_ritmo(12.0, pasta, "x", 0.0)[0].name == "a.mp3"


def test_toda_faixa_do_catalogo_real_diz_de_onde_veio_inclusive_as_15_do_pacote_inicial():
    faixas = mc.listar(RAIZ / "musicas")
    assert len(faixas) >= 30
    assert all(f["procedencia"].startswith("Origem") for f in faixas)
    antiga = next(f for f in faixas if f["arquivo"] == "Action Strike.mp3")                               # sem `fonte` nem `aviso` no catálogo
    assert not antiga["fonte"] and not antiga["aviso"] and "SoundSafari" in antiga["procedencia"] and "CC0" in antiga["procedencia"]
    pixabay = next(f for f in faixas if f["arquivo"].startswith("black-box-legendary"))
    assert "Pixabay" in pixabay["procedencia"] and "não é CC0" in pixabay["procedencia"] and "Content ID" in pixabay["aviso"]      # o aviso de Content ID já vem no campo `aviso`


def test_procedencia_de_faixa_do_dono_mostra_o_link_e_sem_nada_diz_que_nao_ha():
    assert mc.procedencia({"licenca": "NCS (NoCopyrightSounds) — Usage Policy", "fonte": "https://ncs.io/Fade"}) == "Origem: https://ncs.io/Fade"
    assert mc.procedencia({"licenca": "Outra licença", "fonte": ""}) == "Origem não registrada."


def test_tela_ordena_ligadas_primeiro_depois_as_do_sorteio_pela_preferencia_e_mostra_o_motivo_e_a_origem():
    saida = _node(r"""
const f = (titulo, o) => Object.assign({ arquivo: titulo + ".mp3", titulo, autor: "A", licenca: "CC0", bpm: 120, drop_s: 30, duracao_s: 200, emocao: 0.5, tem_drop: true,
  exige_credito: false, existe: true, ativa: true, entra_no_sorteio: true, posicao: 9, fora_do_sorteio: "", procedencia: "Origem: lugar nenhum." }, o);
const lista = [
  f("Desligada", { ativa: false, entra_no_sorteio: false, posicao: null, emocao: 0.99 }),
  f("SemDrop", { entra_no_sorteio: false, posicao: null, fora_do_sorteio: "sem drop", tem_drop: false, drop_s: null, emocao: 0.95 }),
  f("Terceira", { posicao: 3, emocao: 0.3 }), f("Primeira", { posicao: 1, emocao: 0.9 }), f("Segunda", { posicao: 2, emocao: 0.6 }),
  f("Vinheta", { entra_no_sorteio: false, posicao: null, fora_do_sorteio: "muito curta para a montagem (menos de 30 s)", emocao: 0.2 })];
const ordem = m.ordenar(lista).map(x => x.titulo);
const primeira = m.linhaDaFaixa(lista[3]), semdrop = m.linhaDaFaixa(lista[1]), desligada = m.linhaDaFaixa(lista[0]);
console.log(JSON.stringify({ ordem, original: lista.map(x => x.titulo), primeira: primeira.textContent, semdrop: semdrop.textContent, desligada: desligada.textContent }));
""")
    assert saida["ordem"] == ["Primeira", "Segunda", "Terceira", "SemDrop", "Vinheta", "Desligada"]        # ligadas (sorteio por preferência, depois as demais por emoção), desligadas no fim
    assert saida["original"][0] == "Desligada"                                                              # não mexe na lista original
    assert "entra no sorteio · preferência nº 1" in saida["primeira"] and "Origem: lugar nenhum." in saida["primeira"]
    assert "fora do sorteio: sem drop" in saida["semdrop"] and "entra no sorteio" not in saida["semdrop"]
    assert "sorteio" not in saida["desligada"]                                                              # desligada: sem chip de sorteio (já aparece apagada)


def test_html_e_css_do_s3_icone_proprio_dicas_largas_e_textos_sem_linguagem_de_programador():
    html = (RAIZ / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    css = (RAIZ / "ui" / "static" / "style.css").read_text(encoding="utf-8")
    assert 'id="nav-musicas" data-view="musicas" type="button"><i class="i i-music"' in html and ".i-music {" in css
    assert 'id="nav-trabalhos" data-view="trabalhos" type="button"><i class="i i-play"' in html                 # cada tela do menu com ícone diferente
    icones = re.findall(r'class="nav-item[^"]*" id="nav-[a-z]+" data-view="[a-z]+" type="button"><i class="i (i-[a-z]+)"', html)
    assert len(icones) == len(set(icones)) >= 5
    assert "max-width: min(640px, 100%)" in css.split(".drop-hint {", 1)[1].split("}", 1)[0]
    gravador = (RAIZ / "cs2clip" / "gravador_demo.py").read_text(encoding="utf-8")
    assert "terminal comum" not in gravador and "não elevado" not in gravador and "Executar como administrador" in gravador
