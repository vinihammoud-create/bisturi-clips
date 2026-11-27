"""R1 e R2 (revisão do Fable, 09/11/2026): a música automática sempre tem drop e nunca volta à introdução no meio da montagem.

Cada teste tem CONTROLE: o critério anterior reproduz o defeito medido na revisão (RF-01, RF-02).
"""
import hashlib
import json
from pathlib import Path

import numpy as np

from cs2clip import montagem

CATALOGO_REAL = Path(__file__).resolve().parent.parent / "musicas" / "catalogo.json"
CHAVES = ("MONTAGEM_a", "MONTAGEM_b", "MONTAGEM_2026-11-04-23-11-02", "MONTAGEM_x", "MONTAGEM_y", "MONTAGEM_z")
RITMOS = (2.5, 3.4, 5.0, 8.0, 12.0)


def _pasta_com_catalogo(tmp_path, faixas):
    pasta = tmp_path / "m"
    pasta.mkdir(parents=True, exist_ok=True)
    for f in faixas:
        (pasta / f["arquivo"]).write_bytes(b"x" * 100)
        f.setdefault("licenca", "CC0")
        f.setdefault("duracao_s", 180.0)
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": faixas}), encoding="utf-8")
    return pasta


def _criterio_antigo(faixas, cortes_por_min, chave):
    """A escolha de 08/11/2026: ordena por BPM/emoção e sorteia 1 entre as 3 primeiras."""
    alvo = max(95.0, min(145.0, 90.0 + cortes_por_min * 3.0))
    ordem = sorted(faixas, key=lambda f: abs(float(f.get("bpm", 120.0)) - alvo) / 25.0 - 1.5 * float(f.get("emocao") or 0.0))
    top = ordem[:3]
    return top[int(hashlib.sha1(chave.encode()).hexdigest(), 16) % len(top)]


CATALOGO_ORIGINAL = Path(__file__).resolve().parent / "fixtures" / "catalogo_original_15_faixas.json"


def _varre(pasta, faixas):
    escolhidas, antigas = [], []
    for ritmo in RITMOS:
        for chave in CHAVES:
            for dur in (0.0, 36.0, 60.0):
                escolhidas.append(montagem.escolher_musica_por_ritmo(ritmo, pasta, chave, dur)[0].name)
            antigas.append(_criterio_antigo(faixas, ritmo, chave)["arquivo"])
    return escolhidas, antigas


def test_r1_catalogo_original_o_criterio_antigo_sorteava_faixa_sem_drop_e_o_novo_nunca(tmp_path):
    """O catálogo de 15 faixas de 08/11 (congelado em tests/fixtures): 8 sem drop. O critério antigo (BPM, top 3) caía nelas; o R1 não."""
    faixas = json.loads(CATALOGO_ORIGINAL.read_text(encoding="utf-8"))["faixas"]
    sem_drop = {f["arquivo"] for f in faixas if f.get("drop_s") in (None, 0)}
    assert len(sem_drop) >= 8                                                  # o catálogo de antes era majoritariamente calmo
    pasta = _pasta_com_catalogo(tmp_path, [dict(f) for f in faixas])           # os MP3 não vão para o GitHub: arquivos de mentira com os mesmos nomes
    escolhidas, antigas = _varre(pasta, faixas)
    assert not (set(escolhidas) & sem_drop)                                      # R1: nenhuma faixa sem drop
    assert set(antigas) & sem_drop                                               # CONTROLE: o critério antigo caía nelas
    assert "Drop Point.mp3" in antigas and "Drop Point.mp3" not in escolhidas   # a que o dono rejeitou deixou de voltar


def test_r1_catalogo_real_de_hoje_nunca_sorteia_faixa_sem_drop(tmp_path):
    """Vale para o catálogo atual, que cresce (E7): a regra não pode depender de quantas faixas boas existem."""
    faixas = json.loads(CATALOGO_REAL.read_text(encoding="utf-8"))["faixas"]
    sem_drop = {f["arquivo"] for f in faixas if f.get("drop_s") in (None, 0)}
    assert sem_drop                                                            # ainda há faixas calmas no catálogo; elas continuam fora do sorteio
    pasta = _pasta_com_catalogo(tmp_path, [dict(f) for f in faixas])
    escolhidas, _ = _varre(pasta, faixas)
    assert not (set(escolhidas) & sem_drop)                                      # nenhuma sem drop (a variedade vem do rodízio, testado em test_musica_rotacao_e7.py)


def test_r1_so_o_empate_real_e_sorteado_e_faixa_sem_drop_nunca_ganha(tmp_path):
    pasta = _pasta_com_catalogo(tmp_path, [
        {"arquivo": "a.mp3", "bpm": 120.0, "emocao": 0.90, "drop_s": 40.0, "impacto_db": 12.0},
        {"arquivo": "b.mp3", "bpm": 125.0, "emocao": 0.84, "drop_s": 35.0, "impacto_db": 9.0},       # empate (até 0,10 da melhor)
        {"arquivo": "c.mp3", "bpm": 120.0, "emocao": 0.60, "drop_s": 30.0, "impacto_db": 5.0},       # fraca demais: fora
        {"arquivo": "parelha.mp3", "bpm": 126.0, "emocao": 0.99, "drop_s": None, "impacto_db": 1.0},  # sem drop: nunca
    ])
    vistos = {montagem.escolher_musica_por_ritmo(12.0, pasta, f"MONTAGEM_{i}")[0].name for i in range(40)}
    assert vistos == {"a.mp3", "b.mp3"}
    # a mesma chave dá sempre a mesma faixa (o vídeo e o fcpxml usam a mesma música)
    assert (montagem.escolher_musica_por_ritmo(12.0, pasta, "MONTAGEM_k")
            == montagem.escolher_musica_por_ritmo(12.0, pasta, "MONTAGEM_k"))
    # sem nenhuma faixa com drop, escolhe entre todas (melhor música sem drop que sem música)
    so_calmas = _pasta_com_catalogo(tmp_path / "calmas", [
        {"arquivo": "x.mp3", "bpm": 120.0, "emocao": 0.4, "drop_s": None}, {"arquivo": "y.mp3", "bpm": 100.0, "emocao": 0.2, "drop_s": None}])
    assert montagem.escolher_musica_por_ritmo(12.0, so_calmas, "k")[0].name == "x.mp3"


def test_r2_a_escolha_prefere_a_faixa_que_toca_a_montagem_sem_voltar_a_introducao(tmp_path):
    pasta = _pasta_com_catalogo(tmp_path, [
        {"arquivo": "tardia.mp3", "bpm": 120.0, "emocao": 0.95, "drop_s": 100.0, "impacto_db": 15.0, "duracao_s": 120.0},   # sobra 26 s
        {"arquivo": "cabe.mp3", "bpm": 120.0, "emocao": 0.70, "drop_s": 30.0, "impacto_db": 8.0, "duracao_s": 180.0},
    ])
    assert montagem.escolher_musica_por_ritmo(12.0, pasta, "k")[0].name == "tardia.mp3"            # montagem curta (ou desconhecida): a melhor
    assert montagem.escolher_musica_por_ritmo(12.0, pasta, "k", duracao_montagem=90.0)[0].name == "cabe.mp3"   # 94 + 90 > 120: voltaria ao começo
    # só existe a que não cabe: vale ela mesma (o render decide pular ou não, ver abaixo)
    so_tardia = _pasta_com_catalogo(tmp_path / "t", [{"arquivo": "tardia.mp3", "bpm": 120.0, "emocao": 0.95, "drop_s": 100.0, "duracao_s": 120.0}])
    assert montagem.escolher_musica_por_ritmo(12.0, so_tardia, "k", duracao_montagem=90.0)[0].name == "tardia.mp3"


def test_r2_pulo_so_se_a_musica_dura_a_montagem_inteira_a_partir_do_pulo():
    batidas = np.arange(0.25, 100.0, 0.5)
    # música de 100 s, drop em 50 s, tiro em 4 s: pulo ~46 s; uma montagem de 60 s não cabe (46 + 60 > 100)
    desloc, motivo = montagem.deslocamento_para_o_drop(50.1, batidas, 100.0, 4.0, total_montagem=60.0)
    assert desloc == 0.0 and "voltaria à introdução" in motivo
    # CONTROLE: a regra antiga (só 20 s de folga) deixava pular, e a música voltaria ao começo no meio do vídeo
    antigo, _ = montagem.deslocamento_para_o_drop(50.1, batidas, 100.0, 4.0)
    assert 45.0 < antigo < 47.0
    # montagem curta o bastante: cabe e pula
    desloc, motivo = montagem.deslocamento_para_o_drop(50.1, batidas, 100.0, 4.0, total_montagem=12.0)
    assert motivo == "" and 45.0 < desloc < 47.0
    # limite exato: pulo 26,25 s; 26,25 + 73 = 99,25 cabe em 100 s; 26,25 + 74 não
    assert montagem.deslocamento_para_o_drop(30.1, batidas, 100.0, 4.0, total_montagem=73.0)[0] == 26.25
    assert montagem.deslocamento_para_o_drop(30.1, batidas, 100.0, 4.0, total_montagem=74.0)[0] == 0.0
