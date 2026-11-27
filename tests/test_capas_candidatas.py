"""C10 (Plano V7 integrado): capas candidatas de jogadas diferentes, com o motivo de cada uma."""
import json
import subprocess

import pytest

from cs2clip.moments import Event, Moment
from cs2clip.tools import FFMPEG


def _m(foco, nota, rotulo="KILL"):
    return Moment(foco - 3, foco + 3, "abate", kills=[Event(foco, "kill")], label=rotulo, score=nota, idx=int(foco))


def test_escolher_distintos_pega_as_melhores_jogadas_diferentes():
    from cs2clip.capa import escolher_distintos
    ms = [_m(100, 40), _m(105, 38), _m(200, 30), _m(300, 20), _m(400, 10)]       # 105 é o mesmo lance de 100
    assert [m.focus for m in escolher_distintos(ms, 3)] == [100, 200, 300]


def test_escolher_distintos_com_poucos_momentos_devolve_o_que_tem():
    from cs2clip.capa import escolher_distintos
    assert [m.focus for m in escolher_distintos([_m(50, 10)], 3)] == [50]
    assert escolher_distintos([], 3) == []


def test_cartao_estatico_comeca_em_zero_e_sem_fade(tmp_path):
    """G-G01: a foto da capa é o quadro de tempo 0; o cartão animado (início 0,10 s + fade) sai invisível nela."""
    from cs2clip.cartao import gerar_ass_cartao
    m = _m(10, 40, "TRIPLE KILL")
    animado = gerar_ass_cartao(tmp_path / "a.ass", m).read_text(encoding="utf-8")
    estatico = gerar_ass_cartao(tmp_path / "e.ass", m, estatico=True).read_text(encoding="utf-8")
    assert "Dialogue: 0,0:00:00.10," in animado and "\\fad(" in animado          # o comportamento antigo segue igual
    assert "Dialogue: 0,0:00:00.00," in estatico and "\\fad(" not in estatico


def test_banner_estourado_so_pega_a_faixa_do_banner():
    import numpy as np

    from cs2clip.capa import banner_estourado
    cena = np.full((180, 320), 90.0, np.float32)
    assert not banner_estourado(cena)
    com_banner = cena.copy()
    com_banner[30:48, 115:205] = 255.0                      # retângulo branco do banner, no topo ao centro
    assert banner_estourado(com_banner)
    fora = cena.copy()
    fora[140:175, 10:120] = 255.0                           # branco em outro lugar (ex.: HUD) não conta
    assert not banner_estourado(fora)


@pytest.mark.lento
def test_capa_mostra_o_titulo_de_verdade(tmp_path):
    """G-G01 com vídeo real: a região do título muda com o cartão estático; com o cartão animado fica igual ao quadro puro."""
    import numpy as np

    from cs2clip import capa as capa_mod
    from cs2clip.cartao import gerar_ass_cartao

    video = tmp_path / "pasta com 'apostrofo'" / "v.mp4"          # caminho difícil de propósito
    video.parent.mkdir()
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x305070:s=640x360:r=10:d=5", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)

    def regiao(jpg):                                               # canto inferior esquerdo, onde fica o cartão
        r = subprocess.run([FFMPEG, "-v", "error", "-i", str(jpg), "-vf", "crop=500:140:0:580", "-pix_fmt", "gray",
                            "-f", "rawvideo", "pipe:1"], capture_output=True, check=True)
        return np.frombuffer(r.stdout, np.uint8).astype(np.float32)

    m = _m(2, 40, "TRIPLE KILL")
    puro = tmp_path / "puro.jpg"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "2", "-i", str(video), "-frames:v", "1", "-vf",
                    "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=contrast=1.08:saturation=1.12",
                    "-q:v", "2", str(puro)], check=True, capture_output=True)
    com_titulo = capa_mod._renderizar_capa(str(video), m, 2.0, tmp_path / "capa.jpg")
    assert float(np.abs(regiao(com_titulo) - regiao(puro)).mean()) > 1.0     # o título aparece

    # controle: com o cartão ANIMADO (comportamento antigo) a foto sai igual à sem texto — o defeito existia
    original = capa_mod.gerar_ass_cartao
    capa_mod.gerar_ass_cartao = lambda d, mm, **k: gerar_ass_cartao(d, mm, largura=1280, altura=720, duracao=5.0)
    try:
        velho = capa_mod._renderizar_capa(str(video), m, 2.0, tmp_path / "capa_velha.jpg")
    finally:
        capa_mod.gerar_ass_cartao = original
    assert float(np.abs(regiao(velho) - regiao(puro)).mean()) < 1.0


@pytest.mark.lento
def test_gerar_capas_candidatas_com_video_de_verdade(tmp_path):
    from cs2clip.capa import gerar_capas_candidatas
    video = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=10:d=60", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
    ms = [_m(10, 40, "TRIPLE KILL"), _m(12, 39), _m(30, 30, "DOUBLE KILL"), _m(50, 20)]
    saida = gerar_capas_candidatas(str(video), ms, tmp_path / "capas", 3, 60.0)
    assert [s["arquivo"] for s in saida] == ["capa_1.jpg", "capa_2.jpg", "capa_3.jpg"]
    assert all((tmp_path / "capas" / s["arquivo"]).stat().st_size > 2000 for s in saida)
    assert all((tmp_path / "capas" / s["arquivo_outra_versao"]).stat().st_size > 2000 for s in saida)     # A5: a outra versão sai ao lado
    assert [s["arquivo_outra_versao"] for s in saida] == ["capa_1_discreta.jpg", "capa_2_discreta.jpg", "capa_3_discreta.jpg"]
    dados = json.loads((tmp_path / "capas" / "capas.json").read_text(encoding="utf-8"))
    assert dados[0]["jogada"] == "TRIPLE KILL" and "nota 40" in dados[0]["motivo"] and dados[0]["instante_s"] >= 0
    # rodar de novo com menos jogadas não deixa capa velha para trás
    gerar_capas_candidatas(str(video), ms[:1], tmp_path / "capas", 3, 60.0)
    assert [p.name for p in sorted((tmp_path / "capas").glob("capa_*.jpg"))] == ["capa_1.jpg", "capa_1_discreta.jpg"]


def test_capa_nao_testa_quadros_do_banner_de_fim_de_round():
    """Prova de 65 min: a capa principal pegou o banner VITÓRIA no meio da animação."""
    from cs2clip.capa import BANNER_ANTES_CAPA, candidatos_da_capa
    sem_banner = candidatos_da_capa(100.0)
    assert min(sem_banner) < 99.0 and max(sem_banner) > 100.0                         # sem o banner: a faixa inteira
    com = candidatos_da_capa(100.0, banner_t=100.5)                                    # banner lido 0,5 s depois do abate
    assert com and max(com) < 100.5 - BANNER_ANTES_CAPA                                # nada depois do limite
    muito_cedo = candidatos_da_capa(100.0, banner_t=99.0)                              # banner antes de todos os candidatos
    assert muito_cedo == [min(sem_banner)]                                             # nunca fica sem candidato


def test_capa_grande_tem_estilo_proprio_e_so_o_nome_da_jogada(tmp_path):
    from cs2clip.cartao import gerar_ass_cartao
    m = _m(10, 40, "DOUBLE KILL")
    m.round_n = 24
    discreta = gerar_ass_cartao(tmp_path / "d.ass", m, largura=1280, altura=720, estatico=True).read_text(encoding="utf-8")
    grande = gerar_ass_cartao(tmp_path / "g.ass", m, largura=1280, altura=720, estatico=True, grande=True).read_text(encoding="utf-8")
    assert "Anton,42," in discreta and "ROUND 24" in discreta
    assert "Anton,120," in grande and "DOUBLE KILL" in grande and "ROUND" not in grande
    assert gerar_ass_cartao(tmp_path / "v.ass", _m(10, 40, ""), estatico=True, grande=True) is None     # sem rótulo: sem arquivo


@pytest.mark.lento
def test_capa_grande_cobre_bem_mais_area_que_a_discreta(tmp_path):
    import numpy as np

    from cs2clip import capa as capa_mod
    video = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x305070:s=640x360:r=10:d=5", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)

    def pixels(jpg):
        r = subprocess.run([FFMPEG, "-v", "error", "-i", str(jpg), "-vf", "crop=1000:300:0:420", "-pix_fmt", "gray", "-f", "rawvideo",
                            "pipe:1"], capture_output=True, check=True)
        return np.frombuffer(r.stdout, np.uint8).astype(np.float32)

    m = _m(2, 40, "DOUBLE KILL")
    puro = tmp_path / "puro.jpg"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "2", "-i", str(video), "-frames:v", "1", "-vf",
                    "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=contrast=1.08:saturation=1.12",
                    "-q:v", "2", str(puro)], check=True, capture_output=True)
    base = pixels(puro)
    pequena = float((np.abs(pixels(capa_mod._renderizar_capa(str(video), m, 2.0, tmp_path / "p.jpg")) - base) > 40).sum())
    grande = float((np.abs(pixels(capa_mod._renderizar_capa(str(video), m, 2.0, tmp_path / "g.jpg", grande=True)) - base) > 40).sum())
    assert pequena > 0 and grande > 2.5 * pequena


def test_capa_nunca_fica_antes_do_ultimo_abate_menos_0_6_s():
    """A capa_2 da prova de 65 min saiu numa parede (a nitidez premiou a textura): sem ação não vende."""
    from cs2clip.capa import candidatos_da_capa
    c = candidatos_da_capa(100.0, banner_t=105.0, ultimo_abate=100.0)
    assert c and min(c) >= 99.4 and max(c) < 104.0
    # banner logo depois do abate: não dá para cumprir as duas regras; vale a do banner (nunca o banner por cima)
    c2 = candidatos_da_capa(100.0, banner_t=100.6, ultimo_abate=100.0)
    assert c2 and all(t < 100.6 - 1.0 for t in c2)
    # banner ANTES de todos os candidatos: fica só o mais cedo, nunca lista vazia
    assert candidatos_da_capa(100.0, banner_t=98.0, ultimo_abate=100.0) == [min(candidatos_da_capa(100.0))]


def test_a5_config_padrao_da_capa_e_grande_e_valida_o_valor(tmp_path):
    """A5 (dono, 08/11/2026): a capa grande é o padrão; valor fora da lista dá erro claro."""
    from cs2clip.config import Config, load_config
    assert Config().capa.estilo == "grande"
    toml = tmp_path / "c.toml"
    toml.write_text("[capa]\nestilo = 'discreta'\n", encoding="utf-8")
    assert load_config(toml).capa.estilo == "discreta"
    toml.write_text("[capa]\nestilo = 'enorme'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="capa.estilo"):
        load_config(toml)


def _tamanho_do_texto(jpg):
    """Quantos pixels do terço de baixo da capa mudam em relação ao quadro puro: mede o tamanho do título."""
    import numpy as np
    r = subprocess.run([FFMPEG, "-v", "error", "-i", str(jpg), "-vf", "crop=1000:300:0:420", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).astype(np.float32)


@pytest.mark.lento
def test_a5_capa_principal_sai_grande_no_padrao_e_discreta_quando_pedido(tmp_path):
    """Controle: com estilo="discreta" (o comportamento antigo) a principal é a pequena; no padrão ela cobre bem mais área."""
    import numpy as np

    from cs2clip.capa import gerar_capa
    video = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x305070:s=640x360:r=10:d=5", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
    puro = tmp_path / "puro.jpg"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", "2", "-i", str(video), "-frames:v", "1", "-vf",
                    "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=contrast=1.08:saturation=1.12",
                    "-q:v", "2", str(puro)], check=True, capture_output=True)
    base = _tamanho_do_texto(puro)

    def area(jpg):
        return float((np.abs(_tamanho_do_texto(jpg) - base) > 40).sum())
    m = _m(2, 40, "DOUBLE KILL")
    gerar_capa(str(video), [m], tmp_path / "padrao" / "CAPA_x.jpg", 5.0)
    gerar_capa(str(video), [m], tmp_path / "antigo" / "CAPA_x.jpg", 5.0, estilo="discreta")
    assert sorted(p.name for p in (tmp_path / "padrao").glob("*.jpg")) == ["CAPA_x.jpg", "CAPA_x_discreta.jpg"]
    assert sorted(p.name for p in (tmp_path / "antigo").glob("*.jpg")) == ["CAPA_x.jpg", "CAPA_x_grande.jpg"]
    assert area(tmp_path / "padrao" / "CAPA_x.jpg") > 2.5 * area(tmp_path / "antigo" / "CAPA_x.jpg")      # a principal ficou grande
    assert area(tmp_path / "antigo" / "CAPA_x.jpg") > 0


# ── 23/11/2026: kit de UMA jogada (imagem limpa da demo) também tem capas candidatas ─────────────────────────

def _falso_ffmpeg(monkeypatch, nitidez_por_instante):
    """Troca o que mede a nitidez e o que desenha a capa: o teste vê QUAIS instantes foram escolhidos, sem ffmpeg."""
    from cs2clip import capa as capa_mod
    desenhadas = []

    def nitido(src, t_central, duracao_video=0.0, banner_t=None, ultimo_abate=None):
        return t_central, nitidez_por_instante.get(round(t_central, 1), 5.0)

    def desenhar(src, melhor, t, destino, grande=False):
        destino.write_bytes(b"jpg")
        desenhadas.append((destino.name, round(t, 1)))
        return destino
    monkeypatch.setattr(capa_mod, "escolher_instante_com_nitidez", nitido)
    monkeypatch.setattr(capa_mod, "_renderizar_capa", desenhar)
    return desenhadas


def test_uma_jogada_so_gera_alternativas_dos_abates_da_mesma_jogada(tmp_path, monkeypatch):
    from cs2clip.capa import gerar_capas_candidatas
    m = Moment(100, 140, "abate", kills=[Event(105.0, "kill"), Event(120.0, "kill"), Event(135.0, "kill")], label="TRIPLE KILL", score=60, idx=1)
    desenhadas = _falso_ffmpeg(monkeypatch, {105.0: 30.0, 120.0: 80.0, 135.0: 50.0})
    saida = gerar_capas_candidatas("v.mp4", [m], tmp_path / "capas", 3, 200.0, "grande", mesma_jogada=True)
    assert [x["instante_s"] for x in saida] == [135.0, 120.0, 105.0]              # 1ª = a própria jogada (foco = último abate); depois, a mais nítida de cada outro abate
    assert not saida[0].get("mesma_jogada") and all(x["mesma_jogada"] for x in saida[1:]) and "abate 2 de 3" in saida[1]["motivo"]
    assert json.loads((tmp_path / "capas" / "capas.json").read_text(encoding="utf-8")) == saida
    assert len(desenhadas) == 6                                                    # grande + discreta de cada uma
    assert (tmp_path / "capas" / "capa_1.jpg").is_file() and (tmp_path / "capas" / "capa_3.jpg").is_file()


def test_alternativas_da_mesma_jogada_nao_repetem_quadros_colados(tmp_path, monkeypatch):
    from cs2clip.capa import gerar_capas_candidatas
    m = Moment(100, 140, "abate", kills=[Event(105.0, "kill"), Event(105.8, "kill"), Event(120.0, "kill")], label="TRIPLE KILL", score=60, idx=1)
    _falso_ffmpeg(monkeypatch, {105.0: 80.0, 105.8: 70.0, 120.0: 10.0})
    saida = gerar_capas_candidatas("v.mp4", [m], tmp_path / "capas", 3, 200.0, "grande", mesma_jogada=True)
    assert [x["instante_s"] for x in saida] == [120.0, 105.0]                      # 105,8 é quase o mesmo quadro de 105,0: fica de fora


def test_sem_a_opcao_uma_jogada_so_nao_gera_alternativas_como_antes(tmp_path, monkeypatch):
    from cs2clip.capa import gerar_capas_candidatas
    m = Moment(100, 140, "abate", kills=[Event(105.0, "kill"), Event(120.0, "kill")], label="DOUBLE KILL", score=40, idx=1)
    _falso_ffmpeg(monkeypatch, {})
    saida = gerar_capas_candidatas("v.mp4", [m], tmp_path / "capas", 3, 200.0, "grande")
    assert len(saida) == 1 and not saida[0].get("mesma_jogada")                    # só a jogada em si (comportamento antigo)


def test_com_jogadas_diferentes_suficientes_a_opcao_nao_mexe_em_nada(tmp_path, monkeypatch):
    from cs2clip.capa import gerar_capas_candidatas
    ms = [_m(100, 40), _m(200, 30), _m(300, 20)]
    _falso_ffmpeg(monkeypatch, {})
    saida = gerar_capas_candidatas("v.mp4", ms, tmp_path / "capas", 3, 400.0, "grande", mesma_jogada=True)
    assert [x["instante_s"] for x in saida] == [100.0, 200.0, 300.0] and not any(x.get("mesma_jogada") for x in saida)


def test_config_do_kit_limpo_liga_as_alternativas_da_mesma_jogada_e_o_padrao_fica_desligado():
    from cs2clip.config import Config
    from cs2clip.editar_demo import config_do_video_limpo
    assert Config().capa.alternativas_da_mesma_jogada is False
    assert config_do_video_limpo(Config()).capa.alternativas_da_mesma_jogada is True


def test_opcao_das_capas_alternativas_aparece_em_configurar():
    from ui.configuracao import CAMPOS_POR_CHAVE
    assert ("capa", "alternativas_da_mesma_jogada") in CAMPOS_POR_CHAVE and CAMPOS_POR_CHAVE[("capa", "alternativas_da_mesma_jogada")].tipo == "bool"
