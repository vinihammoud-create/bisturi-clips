"""23/11/2026 (dono): "as capas devem ter uma imagem apenas do jogo ou junto com minha webcam, mas com uma escrita chamativa para criar alerta em quem vê o
vídeo e sentir vontade de clicar". Estilo `capa.estilo = "chamativa"` (o padrão continua "grande" até o dono aprovar a prévia, CONSTITUICAO §3.2).
O texto segue a marca (MARCA_E_VOZ §3): até 3 palavras, gíria do CS2, número em vez de nome, reticências valem, e só afirma o que o kit sabe."""
import subprocess

import numpy as np
import pytest

from cs2clip.moments import Event, Moment
from cs2clip.tools import FFMPEG


def _m(n=3, **kw):
    m = Moment(10.0, 20.0, "abate", kills=[Event(11.0 + i, "kill", icons=list(kw.pop("icones", [])) if i == 0 else []) for i in range(n)],
               label={1: "KILL", 2: "DOUBLE KILL", 3: "TRIPLE KILL", 4: "QUADRA KILL", 5: "ACE"}.get(n, "KILL"), idx=1)
    for k, v in kw.items():
        setattr(m, k, v)
    return m


@pytest.mark.parametrize("momento, esperado", [
    (_m(3), ("3", "KILLS")),
    (_m(4), ("4", "KILLS")),
    (_m(5), ("", "ACE")),
    (_m(2, clutch=(3, True)), ("CLUTCH", "1v3")),
    (_m(1, clutch=(2, False)), ("QUASE", "1v2...")),
    (_m(2, morreu_no_fim=True), ("COMEÇOU", "BEM...")),
    (_m(1, icones=["smoke"]), ("PELA", "SMOKE")),
    (_m(2, icones=["noscope"]), ("SEM", "MIRA")),
    (_m(1, icones=["wallbang"]), ("PELA", "PAREDE")),
    (_m(1), ("1", "KILL")),
])
def test_texto_chamativo_e_curto_verdadeiro_e_na_giria_do_cs2(momento, esperado):
    from cs2clip.capa import texto_chamativo
    assert texto_chamativo(momento) == esperado
    assert len(" ".join(esperado).split()) <= 3


def test_texto_chamativo_dos_fails_e_da_resenha():
    from cs2clip.capa import texto_chamativo
    raiva = Moment(10.0, 20.0, "fail", label="FAIL", idx=1, categoria="morte_com_raiva")
    risada = Moment(10.0, 20.0, "fail", label="RISADA", idx=1, categoria="morte_com_risada")
    resenha = Moment(10.0, 20.0, "reacao", label="RESENHA", idx=1, categoria="resenha")
    assert texto_chamativo(raiva) == ("DEU", "RUIM...") and texto_chamativo(risada) == ("QUE", "ISSO?") and texto_chamativo(resenha) == ("", "RESENHA")


def test_ass_da_capa_chamativa_tem_o_destaque_em_laranja_da_marca(tmp_path):
    from cs2clip.capa import gerar_ass_chamativa
    ass = gerar_ass_chamativa(tmp_path / "c.ass", _m(3), com_webcam=True).read_text(encoding="utf-8")
    assert "Anton" in ass and "&H001F9AFF" in ass and "KILLS" in ass and "PlayResX: 1280" in ass    # #FF9A1F em BGR do ASS


def test_estilo_novo_e_aceito_e_o_padrao_continua_grande():
    from cs2clip.config import Config, _validar
    from ui.configuracao import CAMPOS_POR_CHAVE
    cfg = Config()
    assert cfg.capa.estilo == "grande"
    cfg.capa.estilo = "chamativa"
    _validar(cfg)
    assert "chamativa" in CAMPOS_POR_CHAVE[("capa", "estilo")].opcoes


def _video(destino, com_webcam=True):
    """Jogo verde 1920x1080; webcam vermelha onde a do dono fica (32,428,456,276)."""
    vf = "color=c=0x20a020:s=1920x1080:r=10:d=3" + (",drawbox=x=32:y=428:w=456:h=276:color=0xe01010:t=fill" if com_webcam else "")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", vf, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(destino)],
                   check=True, capture_output=True)
    return destino


def _px(jpg, x, y):
    r = subprocess.run([FFMPEG, "-v", "error", "-i", str(jpg), "-vf", f"crop=6:6:{x}:{y},scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                       capture_output=True, check=True)
    return tuple(r.stdout[:3])


@pytest.mark.lento
def test_capa_chamativa_com_webcam_poe_a_webcam_no_canto_com_borda_laranja(tmp_path):
    from cs2clip.capa import ALTURA_WEBCAM_CAPA, MARGEM_WEBCAM_CAPA, _renderizar_capa_chamativa
    video = _video(tmp_path / "v.mp4")
    jpg = _renderizar_capa_chamativa(str(video), _m(3), 1.0, tmp_path / "capa.jpg", cam_box=(32, 428, 456, 276))
    larg = int(ALTURA_WEBCAM_CAPA * 456 / 276) // 2 * 2
    cx, cy = 1280 - MARGEM_WEBCAM_CAPA - larg // 2, 720 - MARGEM_WEBCAM_CAPA - ALTURA_WEBCAM_CAPA // 2
    r, g, b = _px(jpg, cx, cy)
    assert r > 150 and g < 90 and b < 90                                   # a webcam (vermelha) no canto de baixo à direita
    r, g, b = _px(jpg, 1280 - MARGEM_WEBCAM_CAPA - larg - 3, cy)
    assert r > 200 and 110 < g < 190 and b < 90                            # a borda laranja da marca


@pytest.mark.lento
def test_capa_chamativa_sem_webcam_e_so_o_jogo_com_o_texto(tmp_path):
    from cs2clip.capa import _renderizar_capa_chamativa
    video = _video(tmp_path / "v.mp4", com_webcam=False)
    jpg = _renderizar_capa_chamativa(str(video), _m(3), 1.0, tmp_path / "capa.jpg", cam_box=None)
    r, g, b = _px(jpg, 1100, 600)
    assert g > 90 and r < 90                                                # canto de baixo à direita: jogo (verde), sem webcam
    img = subprocess.run([FFMPEG, "-v", "error", "-i", str(jpg), "-vf", "crop=500:400:40:160", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
                         capture_output=True, check=True).stdout
    assert float(np.mean(np.frombuffer(img, np.uint8) > 230)) > 0.02       # o texto branco grande está à esquerda


@pytest.mark.lento
def test_gerar_capa_no_estilo_chamativo_faz_a_principal_com_webcam_e_a_outra_so_com_o_jogo(tmp_path):
    from cs2clip.capa import gerar_capa
    video = _video(tmp_path / "v.mp4")
    principal = gerar_capa(str(video), [_m(3)], tmp_path / "CAPA_x.jpg", 3.0, "chamativa", cam_box=(32, 428, 456, 276))
    assert principal is not None and principal.is_file() and (tmp_path / "CAPA_x_so_jogo.jpg").is_file()
    sem = gerar_capa(str(video), [_m(3)], tmp_path / "CAPA_y.jpg", 3.0, "chamativa", cam_box=None)
    assert sem is not None and sem.is_file() and (tmp_path / "CAPA_y_grande.jpg").is_file()    # sem webcam: a outra é a "grande" de sempre


@pytest.mark.lento
def test_a_webcam_original_da_live_nao_aparece_no_fundo_nem_na_versao_so_do_jogo(tmp_path):
    # prévia real de 23/11: a webcam pequena da live ficava no fundo, à esquerda, atrás do texto (webcam dupla; e "só o jogo" mostrava a webcam)
    from cs2clip.capa import _renderizar_capa_chamativa
    video = _video(tmp_path / "v.mp4")
    for mostrar in (True, False):
        jpg = _renderizar_capa_chamativa(str(video), _m(1), 1.0, tmp_path / f"c{mostrar}.jpg", cam_box=(32, 428, 456, 276), mostrar_webcam=mostrar)
        for x, y in ((40, 300), (170, 380), (300, 440)):          # onde a webcam original cairia com o quadro inteiro
            r, g, b = _px(jpg, x, y)
            assert not (r > 150 and g < 90), (mostrar, x, y)


def test_foco_da_capa_do_fail_e_antes_da_morte():
    # a prévia real pegou a tela do placar (TAB) logo depois da morte
    from cs2clip.capa import foco_da_capa
    fail = Moment(100.0, 112.0, "fail", label="FAIL", idx=1, categoria="morte_com_raiva", pico=106.0)
    assert foco_da_capa(fail, "chamativa") == 104.5 and foco_da_capa(fail, "grande") == fail.focus     # o estilo de sempre não muda
    jogada = _m(3)
    assert foco_da_capa(jogada, "chamativa") == jogada.focus


# ── revisão final (23/11, Fable): ordem das frases e a ligação com o pipeline ─────────────────────

@pytest.mark.parametrize("momento, esperado", [
    (_m(3, morreu_no_fim=True), ("3", "KILLS")),          # o dono aprovou "3 KILLS" para a jogada de 3: com 3+ kills, a contagem ganha
    (_m(4, morreu_no_fim=True), ("4", "KILLS")),
    (_m(3, icones=["smoke"]), ("3", "KILLS")),
    (_m(2, morreu_no_fim=True), ("COMEÇOU", "BEM...")),   # CONTROLE: com 1 ou 2 kills, as frases de antes continuam
    (_m(1, icones=["smoke"]), ("PELA", "SMOKE")),
    (_m(5, morreu_no_fim=True), ("", "ACE")),
    (_m(3, clutch=(2, True)), ("CLUTCH", "1v2")),
])
def test_com_3_ou_mais_kills_a_capa_mostra_a_contagem(momento, esperado):
    from cs2clip.capa import texto_chamativo
    assert texto_chamativo(momento) == esperado


@pytest.mark.lento
def test_pipeline_passa_a_webcam_inteira_para_a_capa_chamativa_e_nada_para_o_estilo_de_sempre(monkeypatch, tmp_path):
    import cs2clip.capa as capa_mod
    import cs2clip.pipeline as pl
    from cs2clip.layout import Layout
    from test_r1_pipeline import _cfg_render, _info, _render_falso
    chamadas = []
    monkeypatch.setattr(capa_mod, "gerar_capa", lambda src, momentos, destino, dur=0.0, estilo="grande", cam_box=None: chamadas.append(("capa", estilo, cam_box)))
    monkeypatch.setattr(capa_mod, "gerar_capas_candidatas",
                        lambda src, momentos, pasta, n=3, dur=0.0, estilo="grande", mesma_jogada=False, cam_box=None: chamadas.append(("candidatas", estilo, cam_box)) or [])
    lay = Layout((0.0, 0.4, 0.25, 0.25), (0.05, 0.45, 0.05, 0.08), 0.9)

    def roda(nome, estilo):
        _render_falso(monkeypatch)
        cfg = _cfg_render()
        cfg.capa.estilo = estilo
        cfg.shorts.layout, cfg.shorts.webcam_retangulo = "webcam_inteira", "32,428,456,276"
        video = tmp_path / nome / "v.mp4"
        video.parent.mkdir()
        video.write_bytes(b"video")                              # a capa só é feita quando o vídeo existe
        jogadas = [Moment(46.0, 53.0, "abate", kills=[Event(50.0, "kill"), Event(51.0, "kill")], label="DOUBLE KILL", score=40.0, idx=1, parte_tipo="partida"),
                   Moment(96.0, 103.0, "abate", kills=[Event(100.0, "kill"), Event(101.0, "kill")], label="DOUBLE KILL", score=38.0, idx=2, parte_tipo="partida")]
        chamadas.clear()
        pl.render_all(video, cfg, _info(), "live", tmp_path / nome / "saida", jogadas, [], lay)
        return list(chamadas)

    assert roda("a", "chamativa") == [("capa", "chamativa", (32, 428, 456, 276)), ("candidatas", "chamativa", (32, 428, 456, 276))]
    assert roda("b", "grande") == [("capa", "grande", None), ("candidatas", "grande", None)]      # CONTROLE: o estilo de sempre não recebe webcam
