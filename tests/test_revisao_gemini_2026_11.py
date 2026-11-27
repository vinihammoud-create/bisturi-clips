"""Correções dos achados da revisão cega do Gemini (G1, 06/11/2026) — um teste por achado (docs/HISTORICO.md §3.5 e §3.7)."""
import json
import re
from pathlib import Path

import pytest

from cs2clip import pipeline as pl
from cs2clip.momentos_io import ajustes_dos_momentos, gravar_momentos

from test_r1_pipeline import _cfg_render, _info, _jogada, _render_falso


def test_g02_render_nao_grava_o_ajuste_dentro_do_inicio_e_fim(monkeypatch, tmp_path):
    """O render aplica o ajuste do dono em memória; o momentos.json guarda os tempos da ANÁLISE + o delta (`ajuste`).
    Se o arquivo recebesse início/fim já deslocados, a próxima renderização somaria o ajuste outra vez."""
    _render_falso(monkeypatch)
    out = tmp_path / "saida" / "live"
    cfg = _cfg_render()
    cfg.shorts.gerar = False
    cfg.montagem.gerar = False
    m = _jogada()
    base = (m.start, m.end)
    gravar_momentos(out, [m], "live", 600.0, "7")                     # análise
    arq = out / "momentos.json"
    d = json.loads(arq.read_text(encoding="utf-8"))
    d["momentos"][0]["ajuste"] = {"inicio": -1.0, "fim": 1.5}          # o dono ajustou na revisão
    arq.write_text(json.dumps(d), encoding="utf-8")

    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [m], [], None)
    assert (m.start, m.end) == (base[0] - 1.0, base[1] + 1.5)          # o corte renderizado usou o ajuste
    depois = json.loads(arq.read_text(encoding="utf-8"))["momentos"][0]
    assert (depois["inicio"], depois["fim"]) == (round(base[0], 2), round(base[1], 2))   # arquivo = tempos da análise
    assert depois["ajuste"] == {"inicio": -1.0, "fim": 1.5}

    # segunda renderização com os momentos relidos da análise: o corte sai igual, sem somar de novo
    m2 = _jogada()
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [m2], [], None)
    assert (m2.start, m2.end) == (base[0] - 1.0, base[1] + 1.5)
    assert ajustes_dos_momentos(arq) == {"51.0": (-1.0, 1.5)}


def test_g02_controle_sem_bases_o_arquivo_receberia_o_tempo_deslocado(tmp_path):
    """Controle: provar que o defeito existia (gravar o momento já ajustado sem passar as bases)."""
    m = _jogada()
    base = m.start
    m.start -= 1.0                                                     # o que aplicar_ajustes faz em memória
    gravar_momentos(tmp_path, [m], "live", 600.0, "7")
    assert json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]["inicio"] == round(base - 1.0, 2)
    gravar_momentos(tmp_path, [m], "live", 600.0, "7", bases={m.idx: (base, m.end, [])})
    assert json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]["inicio"] == round(base, 2)


# ── G-G03: faixas de voz/jogo/Discord ────────────────────────────────────────────────────────────

def _cfg_audio(**kw):
    from cs2clip.config import Config
    cfg = Config()
    for k, v in kw.items():
        setattr(cfg.audio, k, v)
    return cfg


def test_g03_config_recusa_voz_na_faixa_1_e_voz_igual_ao_jogo():
    import pytest

    from cs2clip.config import _validar
    _validar(_cfg_audio(faixa_voz=2, faixa_jogo=3, faixa_comms=4))                   # o arranjo do dono segue valendo
    with pytest.raises(ValueError, match="faixa_voz = 1"):
        _validar(_cfg_audio(faixa_voz=1))
    with pytest.raises(ValueError, match="mesma de audio.faixa_jogo"):
        _validar(_cfg_audio(faixa_voz=3, faixa_jogo=3))


def test_g03_render_nao_trata_voz_com_faixa_muda(monkeypatch):
    """Faixa de voz muda no vídeo: a análise já cai na mistura; o render não pode usar a faixa muda e calar o dono."""
    from cs2clip import render
    monkeypatch.setattr(render, "_AVISADOS_AUDIO", set())
    monkeypatch.setattr(render, "_n_faixas", lambda src: 4)
    cfg = _cfg_audio(tratar_voz=True, faixa_voz=2, faixa_jogo=3)
    monkeypatch.setattr(render, "_faixa_voz_valida", lambda src, k: (None, f"a faixa {k} (audio.faixa_voz) está muda"))
    assert render._voz_ativa(cfg, "v.mp4", 2) is None                                # controle do defeito: antes devolvia (1, 2)
    monkeypatch.setattr(render, "_faixa_voz_valida", lambda src, k: (k - 1, ""))
    assert render._voz_ativa(cfg, "v.mp4", 2) == (1, 2)


# ── G-G04: música curta na montagem ─────────────────────────────────────────────────────────────

def _pasta_musicas(tmp_path, faixas):
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    for f in faixas:
        (pasta / f["arquivo"]).write_bytes(b"x")
        f.setdefault("licenca", "CC0")
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": faixas}), encoding="utf-8")
    return pasta


def test_g04_vinheta_curta_nao_e_sorteada_para_a_montagem(tmp_path):
    """Montagem de 19,5 s caiu num riff de 6,66 s tocando 3 vezes em loop. Faixa < 30 s fica de fora do sorteio."""
    from cs2clip.montagem import escolher_musica_por_ritmo
    pasta = _pasta_musicas(tmp_path, [
        {"arquivo": "curta.mp3", "duracao_s": 6.66, "bpm": 145.5, "drop_s": 2.0},     # a mais perto do ritmo pedido
        {"arquivo": "longa.mp3", "duracao_s": 180.0, "bpm": 120.0, "drop_s": 2.1},
    ])
    for chave in ("a", "b", "2026-10-23-01-52-44", ""):
        assert escolher_musica_por_ritmo(18.0, pasta, chave)[0].name == "longa.mp3"
    # controle: sem o filtro a curta ganhava (era a mais próxima do BPM-alvo 144)
    cat = json.loads((pasta / "catalogo.json").read_text(encoding="utf-8"))
    assert min(cat["faixas"], key=lambda f: abs(f["bpm"] - 144.0))["arquivo"] == "curta.mp3"


def test_g04_so_vinhetas_no_catalogo_ainda_da_musica(tmp_path):
    from cs2clip.montagem import escolher_musica_por_ritmo
    pasta = _pasta_musicas(tmp_path, [{"arquivo": "curta.mp3", "duracao_s": 6.66, "bpm": 145.5, "drop_s": 2.0}])
    assert escolher_musica_por_ritmo(18.0, pasta, "x")[0].name == "curta.mp3"


def test_g2_arco_com_duas_jogadas_fecha_na_melhor():
    from cs2clip.moments import Moment
    from cs2clip.montagem import ordenar_montagem
    a, b = Moment(10, 15, "abate", score=36, idx=1), Moment(20, 25, "abate", score=22, idx=2)
    assert [m.score for m in ordenar_montagem([a, b], "arco")] == [22, 36]
    assert [m.score for m in ordenar_montagem([a, b], "melhores")] == [36, 22]     # "melhores" continua pela nota
    assert [m.score for m in ordenar_montagem([b, a], "cronologica")] == [22, 36]
    assert [m.score for m in ordenar_montagem([a], "arco")] == [36]


def test_g10_avatar_do_placar_nao_vira_webcam():
    from cs2clip.layout import SH, SW, _avatar_do_placar
    assert _avatar_do_placar(SW * 0.45, 4, 32, 32)                      # foto de perfil no topo, ao centro (o caso do Gemini)
    assert not _avatar_do_placar(20, 300, 120, 120)                     # webcam de verdade, canto de baixo
    assert not _avatar_do_placar(SW * 0.45, 4, 80, 80)                  # rosto grande no topo: pode ser câmera
    assert not _avatar_do_placar(10, 4, 32, 32)                         # topo, mas no canto (fora da faixa do placar)
    assert not _avatar_do_placar(SW * 0.45, SH * 0.30, 32, 32)          # pequeno no centro, mas abaixo da faixa


# ── G-G05: estado de uma render não vaza para a seguinte ─────────────────────────────────────────

def test_g05_render_de_outro_video_nao_herda_conferencias_nem_tempos(monkeypatch, tmp_path):
    _render_falso(monkeypatch)
    cfg = _cfg_render()
    cfg.shorts.gerar = False
    cfg.montagem.gerar = False
    pl._GATES["MONTAGEM_video_anterior.mp4"] = ["sobra da render anterior"]
    pl._SOM_MONTAGEM["musica"] = "anterior.mp3"
    pl._RITMO_MONTAGEM["tiros_na_batida"] = 1.0
    pl._TEMPOS.append({"etapa": "Render do outro vídeo", "segundos": 99.0})
    pl._VIDEO_DOS_TEMPOS[0] = "video_anterior"
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "video_novo", tmp_path / "saida" / "video_novo", [_jogada()], [], None)
    assert "MONTAGEM_video_anterior.mp4" not in pl._GATES and not pl._SOM_MONTAGEM and not pl._RITMO_MONTAGEM
    assert all(e["etapa"] != "Render do outro vídeo" for e in pl._TEMPOS)
    # mesmo vídeo (analisou e renderizou na mesma execução): os tempos da análise são mantidos
    pl._TEMPOS.append({"etapa": "Análise", "segundos": 5.0})
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "video_novo", tmp_path / "saida" / "video_novo", [_jogada()], [], None)
    assert any(e["etapa"] == "Análise" for e in pl._TEMPOS)


# ── G-G06: guarda de pico uma vez só na sequência ───────────────────────────────────────────────

def test_g06_sequencia_passa_pelo_guarda_de_pico_uma_vez_so_no_arquivo_final(monkeypatch, tmp_path):
    """Antes, cada trecho .segN passava pelo guarda: o trecho com o tiro alto perdia volume e a emenda ganhava um degrau."""
    from cs2clip import render
    from cs2clip.config import Config
    from cs2clip.moments import Event, Moment

    chamadas = []
    monkeypatch.setattr(render, "garantir_pico", lambda arq, cfg=None, **k: chamadas.append(arq.name))
    monkeypatch.setattr(render, "concat", lambda partes, out, *a, **k: out.write_bytes(b"juntado"))

    @render.com_pico_garantido
    def render_falso(src, mm, out, cfg):          # mesma forma de render_clip: out é o 3º argumento, cfg o 4º
        out.write_bytes(b"trecho")

    m = Moment(10, 30, "abate", kills=[Event(12, "kill"), Event(28, "kill")], label="DOUBLE KILL", score=40, idx=1,
               segmentos=[(10.0, 15.0), (25.0, 30.0)])
    out = tmp_path / "c.mp4"
    cfg = Config()
    render.render_por_janelas(m, out, lambda mm, dest: render_falso("v.mp4", mm, dest, cfg), cfg)
    assert chamadas == ["c.mp4"]                                  # só o arquivo final, nunca c.seg0.mp4 / c.seg1.mp4

    # controle: um render de arquivo único (fora da sequência) segue passando pelo guarda
    chamadas.clear()
    render_falso("v.mp4", m, tmp_path / "solto.mp4", cfg)
    assert chamadas == ["solto.mp4"]
    # e o adiamento não vaza para o render seguinte na mesma thread
    render.render_por_janelas(m, out, lambda mm, dest: render_falso("v.mp4", mm, dest, cfg), cfg)
    chamadas.clear()
    render_falso("v.mp4", m, tmp_path / "outro.mp4", cfg)
    assert chamadas == ["outro.mp4"]


# ── G-G07: comparar.html com votos antigos de outra rodada ───────────────────────────────────────

def _rodar_exportacao_ab(tmp_path, html, votos_guardados):
    """Executa o <script> da página no Node com um navegador falso e devolve (CSV exportado, erro)."""
    import re
    import subprocess

    import pytest
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    js = tmp_path / "ab.js"
    js.write_text("""
const guardado = %s;
const baixados = [];
const chaves = [];
global.localStorage = { getItem: k => { chaves.push(k); return guardado; }, setItem() {} };
const botoes = {};
const fakeEl = () => ({ dataset: {}, classList: { toggle() {} }, querySelectorAll: () => [], querySelector: () => ({}),
                        addEventListener() {}, remove() {}, click() {} });
global.document = {
  querySelectorAll: () => [],
  getElementById: id => ({ addEventListener: (_, f) => { botoes[id] = f; } }),
  createElement: () => { const a = fakeEl(); a.click = () => baixados.push(a.href); return a; },
  body: { appendChild() {} },
};
global.URL = { createObjectURL: b => b.partes.join('') };
global.Blob = class { constructor(partes) { this.partes = partes; } };
%s
try { botoes.exportar(); console.log(JSON.stringify({ csv: baixados[0], chaves })); }
catch (e) { console.log(JSON.stringify({ erro: String(e), chaves })); }
""" % (json.dumps(votos_guardados), script), encoding="utf-8")
    r = subprocess.run(["node", str(js)], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0 and not r.stdout.strip():
        pytest.fail(r.stderr)
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_g07_exportar_votos_ignora_ids_de_outra_rodada(tmp_path):
    from cs2clip.comparador_ab import gerar_pagina
    pasta = tmp_path / "efeitos" / "jogada_1_triple"
    pasta.mkdir(parents=True)
    for nome in ("A_normal.mp4", "B_acelerar_entre_abates.mp4"):
        (pasta / nome).write_bytes(b"x")
    html = gerar_pagina(tmp_path, [pasta]).read_text(encoding="utf-8")
    id_certo = re.search(r"const INFO = \{\"([^\"]+)\"", html).group(1)
    antigos = json.dumps({"jogada_velha_de_outro_video::x": 1, id_certo: 1})
    r = _rodar_exportacao_ab(tmp_path, html, antigos)
    assert "erro" not in r, r                                   # antes: TypeError ao ler INFO[id] de um id que não existe
    assert len(r["csv"].splitlines()) == 2                      # cabeçalho + só o voto desta página
    assert r["chaves"][0].startswith("bisturi_ab_votos:") and id_certo in r["chaves"][0]


# ── G-G08: `ouvir` usa a grade da música inteira ────────────────────────────────────────────────

@pytest.mark.lento
@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / "tools" / "anotar_compasso.py").is_file(), reason="a ferramenta de compasso é só da oficina")
def test_g08_ouvir_calcula_as_batidas_na_musica_inteira_e_so_recorta_o_wav(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from test_compasso import _catalogo_com_musica

    from tools import anotar_compasso as ac
    cat = _catalogo_com_musica(tmp_path)
    ac.estimar(cat)
    tamanhos = []
    original = ac.batidas_do_audio
    monkeypatch.setattr(ac, "batidas_do_audio", lambda a, sr: (tamanhos.append(len(a) / sr), original(a, sr))[1])
    destino = ac.ouvir("m.wav", cat, segundos=10.0)
    assert destino.is_file()
    assert tamanhos and tamanhos[0] > 20.0           # a música tem ~24 s; antes a grade saía dos 10 s recortados


# ── Prova de 65 min (08/11): emendas internas de uma sequência não mergulham no preto ───────────

def _luma_minima(arq, ponta=0.4):
    """Menor luminância média entre os quadros DE DENTRO do arquivo (as pontas têm fade por desenho)."""
    import re
    import subprocess

    from cs2clip.tools import FFMPEG
    r = subprocess.run([FFMPEG, "-v", "info", "-nostdin", "-i", str(arq), "-vf",
                        "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    pares = []
    tempo = None
    for linha in r.stderr.splitlines():
        achou_t = re.search(r"pts_time:([0-9.]+)", linha)
        if achou_t:
            tempo = float(achou_t.group(1))
        achou_y = re.search(r"YAVG=([0-9.]+)", linha)
        if achou_y and tempo is not None:
            pares.append((tempo, float(achou_y.group(1))))
    fim = max(t for t, _ in pares)
    return min(v for t, v in pares if ponta < t < fim - ponta)


@__import__("pytest").mark.lento
def test_sequencia_em_corte_seco_nao_tem_mergulho_no_preto(tmp_path):
    import subprocess

    from cs2clip import render
    from cs2clip.config import Config
    from cs2clip.moments import Event, Moment
    from cs2clip.tools import FFMPEG
    src = tmp_path / "claro.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x8a9ab0:s=640x360:r=60:d=20",
                    "-f", "lavfi", "-i", "sine=f=440:d=20", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(src)], check=True, capture_output=True)

    def jogada():
        return Moment(2.0, 15.0, "abate", kills=[Event(4.0, "kill"), Event(12.0, "kill")], label="DOUBLE KILL", score=40, idx=1,
                      segmentos=[(2.0, 6.0), (10.0, 15.0)])

    def renderiza(tipo, nome):
        cfg = Config()
        cfg.qualidade.encoder = "libx264"
        cfg.qualidade.garantir_pico = False
        cfg.montagem.transicao_tipo = tipo
        out = tmp_path / nome
        render.render_por_janelas(jogada(), out, lambda mm, dest: render.render_clip(str(src), mm, dest, cfg), cfg)
        return out

    seco = renderiza("corte", "seco.mp4")
    assert _luma_minima(seco) > 60                       # nenhum quadro escuro: a emenda interna é corte seco
    legado = renderiza("fade", "fade.mp4")
    assert _luma_minima(legado) < 40                     # controle: com "fade" o mergulho continua existindo (comportamento antigo)


@pytest.mark.lento
def test_sem_nenhum_short_o_programa_diz_por_que(monkeypatch, tmp_path, capsys):
    """Na prova de 65 min a maior nota foi 28 (corte 30): saiu '0 shorts' sem explicação."""
    _render_falso(monkeypatch)
    cfg = _cfg_render()
    cfg.montagem.gerar = False
    cfg.cortes.nota_short = 50.0                       # a jogada de teste tem nota 40
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", tmp_path / "saida" / "live", [_jogada()], [], None)
    saida = capsys.readouterr().out
    assert "nenhum short" in saida and "40" in saida and "nota_short" in saida
    cfg.cortes.nota_short = 30.0                       # com nota suficiente, o aviso não aparece
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", tmp_path / "saida" / "live2", [_jogada()], [], None)
    assert "nenhum short" not in capsys.readouterr().out


# ── D-G2 (dono, 08/11): sem título "spoiler" antes do 1º abate no perfil esports_blast ───────────

def _ass_do_short(tmp_path, gancho):
    from cs2clip.captions import build_ass
    from cs2clip.moments import Event, Moment
    m = Moment(10.0, 20.0, "abate", kills=[Event(12.0, "kill"), Event(14.0, "kill"), Event(16.0, "kill")], label="TRIPLE KILL",
               score=40, idx=1)
    ass = tmp_path / "s.ass"
    build_ass(ass, [], m, 10.0, 20.0, 1080, 1920, 768, False, True, gancho=gancho)
    return [linha for linha in ass.read_text(encoding="utf-8").splitlines() if linha.startswith("Dialogue")]


def test_g2_gancho_do_titulo_so_aparece_quando_pedido(tmp_path):
    com = _ass_do_short(tmp_path, None)                          # padrão antigo: título em 0,0 s (antes do 1º abate em 2,0 s)
    assert any(linha.startswith("Dialogue: 2,0:00:00.00") and "TRIPLE KILL" in linha for linha in com)       # controle
    sem = _ass_do_short(tmp_path, False)
    assert not any(linha.startswith("Dialogue: 2,0:00:00.00") for linha in sem)
    assert any("DOUBLE KILL" in linha for linha in sem) and any("TRIPLE KILL" in linha for linha in sem)     # os selos dos abates ficam


def test_g2_perfil_esports_blast_desliga_o_gancho_e_os_outros_nao():
    from cs2clip.config import Config
    from cs2clip.perfis import aplicar_perfil
    assert Config().shorts.gancho_titulo is True                  # padrão sem perfil: igual ao de antes
    assert aplicar_perfil(Config(), "esports_blast").shorts.gancho_titulo is False
    assert aplicar_perfil(Config(), "shorts_viral").shorts.gancho_titulo is True


# ── D-G4 (dono, 08/11): o recorte do kill feed some enquanto o banner de fim de round está na tela ──

def test_g4_expressao_do_killfeed_sem_banner_fica_como_antes():
    from cs2clip.render import expressao_janelas_killfeed
    assert expressao_janelas_killfeed([2.0, 5.0], None) == r"between(t\,1.750\,5.200)+between(t\,4.750\,8.200)"
    assert "not(between" in expressao_janelas_killfeed([2.0], 6.0)


@__import__("pytest").mark.lento
def test_g4_overlay_do_killfeed_some_no_banner_com_ffmpeg_de_verdade(tmp_path):
    """Aplica a expressão num overlay real: preto de fundo, quadrado branco = recorte do kill feed."""
    import subprocess

    import numpy as np

    from cs2clip.render import expressao_janelas_killfeed
    from cs2clip.tools import FFMPEG

    def quadros(expr):
        cmd = [FFMPEG, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=black:s=160x90:r=10:d=10",
               "-f", "lavfi", "-i", "color=c=white:s=80x45:r=10:d=10",
               "-filter_complex", f"[0:v][1:v]overlay=0:0:enable='{expr}',format=gray", "-f", "rawvideo", "pipe:1"]
        r = subprocess.run(cmd, capture_output=True)
        a = np.frombuffer(r.stdout, np.uint8).reshape(-1, 90, 160)
        return {round(i / 10, 1): bool(a[i, 5, 5] > 128) for i in range(len(a))}   # canto do recorte: aceso ou não

    abate_em, banner_em = 2.0, 3.5                                   # banner lido 1,5 s depois do último abate
    antigo = quadros(expressao_janelas_killfeed([abate_em], None))
    assert antigo[3.0] and antigo[4.0] and antigo[5.0]               # controle: antes o recorte ficava por cima do banner
    novo = quadros(expressao_janelas_killfeed([abate_em], banner_em))
    assert novo[2.0] and novo[2.4]                                   # mostra o kill feed logo depois do abate
    assert not novo[3.0] and not novo[4.0] and not novo[7.9]         # some de 1 s antes do banner até 4,5 s depois
    assert not novo[8.5]                                             # fim natural da janela (abate + 3,2 s) e do banner


def test_g4_banner_do_round_so_existe_dentro_de_partida():
    from cs2clip.partes import Parte, banner_do_round
    from cs2clip.rounds import RoundTimelineEntry as Round
    p = Parte("partida", 0.0, 500.0, rounds=[Round(100.0, "vitoria", 0.9), Round(220.0, "derrota", 0.9)], dur_round=110.0)
    assert banner_do_round(90.0, [p]) == 100.0
    assert banner_do_round(150.0, [p]) == 220.0
    assert banner_do_round(300.0, [p]) is None                       # depois do último banner
    assert banner_do_round(90.0, [Parte("fora", 0.0, 500.0)]) is None


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / "tools" / "exportar_publico.py").is_file(), reason="o workflow desta cópia é o público (só o job rapidos)")
def test_workflow_do_github_e_yaml_valido():
    """Em 08/11 um ':' no nome de um passo deixou o workflow inválido e o CI falhou em 0 s em 2 pushes sem ninguém ver."""
    import pathlib

    import pytest
    yaml = pytest.importorskip("yaml")
    arq = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "tests.yml"
    dados = yaml.safe_load(arq.read_text(encoding="utf-8"))
    assert set(dados["jobs"]) == {"rapidos", "lentos"}
    for job in dados["jobs"].values():
        assert all(isinstance(p.get("name", ""), str) for p in job["steps"])


# ── E1 (dono, 08/11/2026): o selo de multi-kill começa no 3º abate do round; nunca aparece "DOUBLE" ─────────

def _selos(tmp_path, tempos, a, b, **kw):
    from cs2clip.captions import build_ass
    from cs2clip.moments import Event, Moment
    m = Moment(tempos[0] - 5, tempos[-1] + 3, "abate", kills=[Event(t, "kill") for t in tempos], label="TRIPLE KILL", score=61, idx=1)
    ass = tmp_path / "s.ass"
    build_ass(ass, [], m, a, b, 1080, 1920, 768, False, True, gancho=False, **kw)
    return [linha for linha in ass.read_text(encoding="utf-8").splitlines() if linha.startswith("Dialogue")]


def test_e1_o_triple_do_kit_de_04_11_mostra_triple_kill_e_nunca_double(tmp_path):
    # tempos reais do round 14 da gravação 04/11 23-11-02: 1227 s, 1280 s e 1288 s; o short tem 2 trechos (como no kit)
    abates = [1227.0, 1280.0, 1288.0]
    trecho1 = _selos(tmp_path, abates, 1222.7, 1229.7, selo_a_partir=3)
    trecho2 = _selos(tmp_path, abates, 1275.3, 1292.2, selo_a_partir=3)
    assert not any("KILL" in linha for linha in trecho1)                      # o 1º abate nunca leva selo de multi-kill
    assert sum("TRIPLE KILL" in linha for linha in trecho2) == 1               # o 3º abate mostra TRIPLE KILL
    assert not any("DOUBLE" in linha for linha in trecho1 + trecho2)           # o 2º abate não mostra DOUBLE
    # CONTROLE: com o selo desde o 2º abate (o jeito antigo) aparece o DOUBLE que o dono não quer
    antigo = _selos(tmp_path, abates, 1275.3, 1292.2, selo_a_partir=2)
    assert any("DOUBLE KILL" in linha for linha in antigo)


def test_e1_quatro_e_cinco_abates_sobem_o_selo(tmp_path):
    quatro = " ".join(_selos(tmp_path, [10.0, 12.0, 14.0, 16.0], 5.0, 25.0, selo_a_partir=3))
    assert "TRIPLE KILL" in quatro and "QUADRA KILL" in quatro and "DOUBLE" not in quatro
    cinco = " ".join(_selos(tmp_path, [10.0, 12.0, 14.0, 16.0, 18.0], 5.0, 25.0, selo_a_partir=3))
    assert "ACE" in cinco and "DOUBLE" not in cinco


def test_e1_headshot_continua_nos_abates_sem_selo(tmp_path):
    from cs2clip.captions import build_ass
    from cs2clip.moments import Event, Moment
    m = Moment(5.0, 25.0, "abate", kills=[Event(10.0, "kill"), Event(12.0, "kill", ["headshot"]), Event(14.0, "kill")], label="TRIPLE KILL",
               score=40, idx=1)
    ass = tmp_path / "h.ass"
    build_ass(ass, [], m, 5.0, 25.0, 1080, 1920, 768, False, True, gancho=False, selo_a_partir=3)
    texto = ass.read_text(encoding="utf-8")
    assert "HEADSHOT" in texto and "TRIPLE KILL" in texto and "DOUBLE" not in texto


def test_e1_o_pipeline_passa_o_minimo_da_regra_dos_abates():
    from cs2clip.config import Config
    assert Config().cortes.min_abates_round == 3 and not hasattr(Config().shorts, "selo_janela")
