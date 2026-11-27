"""Revisão do Claude na branch fix/auditoria-g001-g010 (31/10/2026): cada teste falharia antes das correções.
P1: diversificar_shorts perdia as vagas reservadas · P2: erro do concat era engolido e a montagem rodava 2 vezes ·
P3: vídeo e .fcpxml escolhiam músicas diferentes, e o ritmo real nunca chegava à escolha."""
import json
from pathlib import Path

import pytest

import cs2clip.pipeline as pl
from cs2clip.config import Config
from cs2clip.moments import Moment, diversificar_shorts, escolher_shorts
from test_r1_pipeline import _cfg_render, _info, _jogada, _render_falso


# ── P1 ────────────────────────────────────────────────────────────────────────────────────────────

def _lista_cheia() -> list[Moment]:
    rotulos = ["ACE", "QUADRA KILL", "TRIPLE KILL", "DOUBLE KILL", "HEADSHOT", "Que tiro", "CLUTCH 1v2",
               "TRIPLE KILL", "DOUBLE KILL", "QUADRA KILL"]
    ms = []
    for i, rotulo in enumerate(rotulos):
        m = Moment(float(i * 100), float(i * 100 + 20), "abate", label=rotulo)
        m.score, m.idx = 55.0 - i, i + 1                        # todas acima do corte (30)
        ms.append(m)
    return ms


def test_diversificar_mantem_a_vaga_reservada_para_emocao():
    ms = _lista_cheia()
    raiva = Moment(2000.0, 2015.0, "reacao", label="RAIVA")
    raiva.score, raiva.emocao, raiva.confianca, raiva.idx = 25.0, "RAIVA", "alta", 99   # abaixo do corte, com emoção
    ms.append(raiva)
    assert raiva in escolher_shorts(ms, 30.0, 8)                # a regra que já existia
    escolhidos = diversificar_shorts(ms, 30.0, 8)
    assert raiva in escolhidos and len(escolhidos) == 8         # antes: a RAIVA saía da lista


def test_diversificar_mantem_o_pedido_clipa_isso_mesmo_com_nota_baixa():
    ms = _lista_cheia()
    pedido = Moment(3000.0, 3010.0, "abate", label="DOUBLE KILL")
    pedido.score, pedido.pedido, pedido.idx = 8.0, True, 98
    ms.append(pedido)
    assert pedido in diversificar_shorts(ms, 30.0, 8)


def test_diversificar_sem_vagas_e_sem_candidatos():
    assert diversificar_shorts(_lista_cheia(), 30.0, 0) == []
    assert diversificar_shorts([], 30.0, 8) == []
    pouca = _lista_cheia()[:3]
    assert len(diversificar_shorts(pouca, 30.0, 8)) == 3          # menos jogadas que vagas: devolve todas


def test_diversificar_continua_variando_o_restante_das_vagas():
    ms = [Moment(float(i * 50), float(i * 50 + 5), "abate", label="DOUBLE KILL") for i in range(3)]
    for m, nota in zip(ms, (50, 49, 48)):
        m.score = float(nota)
    clutch = Moment(500.0, 505.0, "abate", label="CLUTCH 1v2")
    clutch.score = 46.0
    escolhidos = diversificar_shorts(ms + [clutch], 30.0, 2)
    assert sorted(m.label for m in escolhidos) == ["CLUTCH 1v2", "DOUBLE KILL"]
    assert [m.score for m in escolhidos] == sorted((m.score for m in escolhidos), reverse=True)   # ordem de nota


# ── P2 e P3 no pipeline ──────────────────────────────────────────────────────────────────────────

def test_erro_do_concat_aparece_e_a_montagem_nao_roda_duas_vezes(monkeypatch, tmp_path, capsys):
    _render_falso(monkeypatch)
    chamadas = []

    def concat_quebrado(parts, out, cfg=None, **kw):
        chamadas.append(kw)
        raise ValueError("falha de verdade dentro do concat")
    monkeypatch.setattr(pl, "concat", concat_quebrado)
    cfg = _cfg_render()
    cfg.shorts.gerar = False
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", tmp_path / "saida" / "live", [_jogada()], [], None)
    assert len(chamadas) == 1                                   # antes: ValueError/TypeError davam uma 2ª chamada
    assert "falha de verdade dentro do concat" in capsys.readouterr().out


@pytest.mark.lento
def test_video_e_fcpxml_usam_a_mesma_musica_decidida_uma_vez_com_o_ritmo_real(monkeypatch, tmp_path):
    _render_falso(monkeypatch)
    faixa = tmp_path / "faixa_escolhida.mp3"
    faixa.write_bytes(b"x")
    pedidos, repassados = [], []
    monkeypatch.setattr(pl, "musica_da_montagem",
                        lambda cfg, chave, cortes_por_min=12.0, *a, **k: (pedidos.append((chave, cortes_por_min)), faixa)[1])

    def concat_falso(parts, out, cfg=None, **kw):
        repassados.append(kw)
        Path(out).write_bytes(b"video")
    monkeypatch.setattr(pl, "concat", concat_falso)
    cfg = _cfg_render()
    cfg.shorts.gerar = False
    jogada = _jogada()
    out = tmp_path / "saida" / "live"
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [jogada], [], None)
    assert len(pedidos) == 1                                    # decidida UMA vez
    assert pedidos[0][0] == "MONTAGEM_live"
    assert pedidos[0][1] == pytest.approx(60.0 / jogada.duration)            # o ritmo real, não o 12 fixo
    assert repassados and repassados[0]["musica"] == faixa      # o vídeo usa a faixa decidida
    assert repassados[0]["tiros_rel"] == [pytest.approx(jogada.kills[0].t - jogada.start)]
    assert "faixa_escolhida.mp3" in (out / "marcadores_live.fcpxml").read_text(encoding="utf-8")   # e o .fcpxml também


def test_concat_usa_a_musica_recebida_e_so_escolhe_sozinho_sem_ela(monkeypatch, tmp_path):
    from cs2clip import montagem, render
    recebidas = []
    monkeypatch.setattr(montagem, "render_montagem",
                        lambda files, out, cfg, musica=None, tiros_rel=None, abates_rel=None: recebidas.append(musica))
    faixa = tmp_path / "x.mp3"
    corte = tmp_path / "c.mp4"
    corte.write_bytes(b"1")
    cfg = Config()
    render.concat([corte], tmp_path / "MONTAGEM_a.mp4", cfg, musica=faixa)
    render.concat([corte], tmp_path / "MONTAGEM_a.mp4", cfg, musica=None)           # None = sem música (decidido)
    monkeypatch.setattr(montagem, "musica_da_montagem", lambda cfg, chave, *a, **k: faixa)
    render.concat([corte], tmp_path / "MONTAGEM_a.mp4", cfg)                        # não informada: escolhe sozinho
    render.concat([corte], tmp_path / "FAILS_a.mp4", cfg, musica=faixa)             # fails nunca levam música
    assert recebidas == [faixa, None, faixa, None]


def test_musica_da_montagem_segue_o_ritmo_e_a_configuracao(tmp_path):
    from cs2clip.montagem import escolher_musica_por_ritmo, musica_da_montagem
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    faixas = [("lenta.mp3", 96.0), ("media.mp3", 126.0), ("rapida.mp3", 144.0)]
    for nome, _ in faixas:
        (pasta / nome).write_bytes(b"x")
    (pasta / "catalogo.json").write_text(
        json.dumps({"faixas": [{"arquivo": n, "bpm": b, "drop_s": 0.0} for n, b in faixas]}), encoding="utf-8")
    cfg = Config()
    cfg.montagem.musica = "auto"
    # a mesma decisão da função "por ritmo", e o ritmo muda a faixa (com 1 só candidata por faixa de BPM)
    assert musica_da_montagem(cfg, "MONTAGEM_x", 4.0, pasta) == escolher_musica_por_ritmo(4.0, pasta, "MONTAGEM_x")[0]
    assert musica_da_montagem(cfg, "MONTAGEM_x", 30.0, pasta) == escolher_musica_por_ritmo(30.0, pasta, "MONTAGEM_x")[0]
    # mesma chave = mesma faixa, sempre
    assert musica_da_montagem(cfg, "MONTAGEM_x", 14.0, pasta) == musica_da_montagem(cfg, "MONTAGEM_x", 14.0, pasta)
    cfg.montagem.musica = ""
    assert musica_da_montagem(cfg, "MONTAGEM_x", 14.0, pasta) is None
    cfg.montagem.musica = "rapida.mp3"
    assert musica_da_montagem(cfg, "MONTAGEM_x", 14.0, pasta) == pasta / "rapida.mp3"
