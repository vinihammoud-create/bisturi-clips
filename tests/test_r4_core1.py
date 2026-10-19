"""Regressões da rodada R4, grupo core1: __main__, registro, relatório, publicar, checagem, demo e README.
Cada teste aqui falharia antes da correção do achado que ele cita (número no nome)."""
import json
import types
from pathlib import Path

import pytest

from cs2clip import checagem
from cs2clip.demo import Abate, PartidaDemo, RoundDemo, momentos_da_demo
from cs2clip.moments import Event, Moment
from cs2clip.publicar import gerar_publicar, hashtags, titulos
from cs2clip.registro import Registro
from cs2clip.report import PAGE, entry, write_report_video

RAIZ = Path(__file__).resolve().parent.parent


def _info(duracao=10.0):
    return types.SimpleNamespace(duration=duracao)


def _momento(idx, t, label="KILL", score=40.0):
    m = Moment(t, t + 10, "abate", kills=[Event(t + 5, "kill")], idx=idx, label=label, score=score)
    m.parte_tipo = "partida"
    return m


@pytest.fixture
def sem_trava(monkeypatch):
    monkeypatch.setattr(checagem, "gpu_ocupada", lambda ativos=None: [])


# ── item 2: vídeo longo sem nenhum abate não termina com "Pronto" ───────────────────────────────

def test_item2_video_longo_sem_abate_avisa_e_nao_diz_pronto(tmp_path, monkeypatch, capsys, sem_trava):
    import cs2clip.pipeline as pl
    from cs2clip.__main__ import main
    v = tmp_path / "live.mp4"
    v.write_bytes(b"x")
    monkeypatch.setattr(pl, "analyze", lambda p, cfg, o, pd, R: (_info(3600.0), "live", tmp_path / "o", [], [], None))
    monkeypatch.setattr(pl, "render_all", lambda *a, **k: ([], [], []))
    codigo = main([str(v), "--saida", str(tmp_path / "saida")])
    out = capsys.readouterr().out
    assert codigo == 3 and "Pronto" not in out and "Nenhum abate" in out


def test_item2_video_curto_sem_abate_continua_normal(tmp_path, monkeypatch, capsys, sem_trava):
    import cs2clip.pipeline as pl
    from cs2clip.__main__ import main
    v = tmp_path / "clipe.mp4"
    v.write_bytes(b"x")
    monkeypatch.setattr(pl, "analyze", lambda p, cfg, o, pd, R: (_info(60.0), "clipe", tmp_path / "o", [], [], None))
    monkeypatch.setattr(pl, "render_all", lambda *a, **k: ([], [], []))
    assert main([str(v), "--saida", str(tmp_path / "saida")]) == 0
    assert "Pronto" in capsys.readouterr().out


# ── item 6: a lista de jogadores da demo não é cortada em 200 caracteres ─────────────────────────

def test_item6_registro_erro_mantem_200_por_padrao_e_aceita_limite_maior(tmp_path):
    r = Registro(tmp_path / "log.txt")
    assert len(r.erro("x", "a" * 500)) < 260                       # padrão preservado (cortado em 200)
    assert len(r.erro("x", "a" * 500, limite=1500)) > 500


def test_item6_demo_sem_nick_imprime_todos_os_jogadores(tmp_path, monkeypatch, capsys, sem_trava):
    import cs2clip.demo as demo
    from cs2clip.__main__ import main
    nomes = ", ".join(f"Jogador Numero {i:02d} Com Nome Comprido ({i} abates)" for i in range(1, 11))
    msg = f"Diga quem é você na demo. Use --jogador \"nome\" (ou [demo] jogador no config.toml). Jogadores: {nomes}"
    assert len(msg) > 400

    def analisar(*a, **k):
        raise demo.DemoInvalida(msg)

    monkeypatch.setattr(demo, "analisar_demo", analisar)
    d = tmp_path / "partida.dem"
    d.write_bytes(b"x")
    main([str(d), "--saida", str(tmp_path / "saida")])
    out = capsys.readouterr().out
    assert "Jogador Numero 10 Com Nome Comprido (10 abates)" in out


# ── itens 15 e 52: um relatório.html por vídeo, com os caminhos certos ───────────────────────────

def _faz_main_com_dois_videos(tmp_path, monkeypatch, so_analise=False):
    import cs2clip.pipeline as pl
    from cs2clip.__main__ import main
    videos = []
    for nome in ("a", "b"):
        v = tmp_path / f"{nome}.mp4"
        v.write_bytes(b"x")
        videos.append(str(v))
    por_video = {"a": _momento(1, 10.0, "TRIPLE KILL"), "b": _momento(1, 50.0, "DOUBLE KILL")}

    def analyze(p, cfg, o, pd, R):
        out = o / p.stem
        out.mkdir(parents=True, exist_ok=True)
        return _info(100.0), p.stem, out, [por_video[p.stem]], [], None

    def render_all(p, cfg, info, name, out, moments, words, lay, registro, R):
        m = moments[0]
        return [entry(m, p.name, f"{name}/miniaturas/01.jpg", f"{name}/cortes/01.mp4", f"{name}/shorts/01.mp4")], [], []

    monkeypatch.setattr(pl, "analyze", analyze)
    monkeypatch.setattr(pl, "render_all", render_all)
    args = videos + ["--saida", str(tmp_path / "saida")] + (["--so-analise"] if so_analise else [])
    return main(args), tmp_path / "saida"


def test_item15_cada_video_ganha_o_seu_relatorio(tmp_path, monkeypatch, capsys, sem_trava):
    codigo, saida = _faz_main_com_dois_videos(tmp_path, monkeypatch)
    assert codigo == 0
    ra = (saida / "a" / "relatorio.html").read_text(encoding="utf-8")
    rb = (saida / "b" / "relatorio.html").read_text(encoding="utf-8")
    assert "TRIPLE KILL" in ra and "DOUBLE KILL" not in ra
    assert "DOUBLE KILL" in rb and "TRIPLE KILL" not in rb
    # caminhos relativos à pasta do vídeo (o arquivo mora em saida/<id>/): sem o prefixo "<id>/"
    assert '"thumb": "miniaturas/01.jpg"' in ra and '"clip": "cortes/01.mp4"' in ra and '"short": "shorts/01.mp4"' in ra
    assert '"thumb": "a/miniaturas' not in ra
    # o global continua existindo, com os dois vídeos e os caminhos a partir de saida/
    g = (saida / "relatorio.html").read_text(encoding="utf-8")
    assert "TRIPLE KILL" in g and "DOUBLE KILL" in g and '"thumb": "a/miniaturas/01.jpg"' in g


def test_item15_so_analise_tambem_grava_o_relatorio_do_video(tmp_path, monkeypatch, capsys, sem_trava):
    codigo, saida = _faz_main_com_dois_videos(tmp_path, monkeypatch, so_analise=True)
    assert codigo == 0
    assert "TRIPLE KILL" in (saida / "a" / "relatorio.html").read_text(encoding="utf-8")
    assert "DOUBLE KILL" not in (saida / "a" / "relatorio.html").read_text(encoding="utf-8")


def test_item15_write_report_video_tira_so_o_prefixo_da_propria_pasta(tmp_path):
    pasta = tmp_path / "live"
    pasta.mkdir()
    e = entry(_momento(1, 10.0), "live.mp4", "live/miniaturas/1.jpg", None, "")
    write_report_video(pasta, [e], {"abates": "1"}, "sub")
    html = (pasta / "relatorio.html").read_text(encoding="utf-8")
    assert '"thumb": "miniaturas/1.jpg"' in html and '"clip": null' in html and '"short": ""' in html


# ── itens 24 e 45: nada de CS Demo Manager na checagem ───────────────────────────────────────────

def test_item24_45_checagem_nao_fala_do_cs_demo_manager(tmp_path):
    itens = checagem.checar(tmp_path, tmp_path, ativos=set())
    assert not any("Demo Manager" in i.nome for i in itens)
    t = checagem.texto(itens)
    assert "Demo Manager" not in t and "formos" not in t


# ── item 26: momento descartado mantém o corte e a miniatura que já existem ──────────────────────

def test_item26_descartado_mantem_corte_e_miniatura(tmp_path, monkeypatch):
    import cs2clip.pipeline as pl
    from cs2clip.config import Config
    from cs2clip.momentos_io import gravar_momentos
    cfg = Config()
    cfg.shorts.gerar = False
    cfg.montagem.gerar = False
    m1, m2 = _momento(1, 10.0), _momento(2, 40.0)
    gravar_momentos(tmp_path, [m1, m2], "v", 100.0, "3")
    d = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))
    d["momentos"][1]["excluido"] = True
    (tmp_path / "momentos.json").write_text(json.dumps(d), encoding="utf-8")
    # cortes e miniaturas de uma rodada anterior, quando o 2º ainda não estava descartado
    for pasta in ("cortes", "miniaturas"):
        (tmp_path / pasta).mkdir()
    nome2 = f"{m2.idx:02d}_{pl._slug(m2.label)}_{pl._fmt(m2.start)}"
    (tmp_path / "cortes" / f"{nome2}.mp4").write_bytes(b"x")
    (tmp_path / "miniaturas" / f"{nome2}.jpg").write_bytes(b"x")
    monkeypatch.setattr(pl, "render_clip", lambda src, m, dest, cfg: dest.write_bytes(b"x"))
    monkeypatch.setattr(pl, "thumbnail", lambda src, t, dest: dest.write_bytes(b"x"))
    info = types.SimpleNamespace(duration=100.0, fps=60.0)
    pl.render_all(tmp_path / "v.mp4", cfg, info, "v", tmp_path, [m1, m2], [], None, None, frozenset())
    assert (tmp_path / "cortes" / f"{nome2}.mp4").exists() and (tmp_path / "miniaturas" / f"{nome2}.jpg").exists()
    arq = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][1]["arquivos"]
    assert arq is not None and arq["corte"] and arq["miniatura"] and arq["short"] is None


# ── itens 27, 46 e 47: texto para postar não afirma o modo nem mostra "None" ─────────────────────

def _jogada(n=2):
    return Moment(10, 20, "abate", kills=[Event(10 + i * 0.5, "kill") for i in range(n)], idx=1)


def test_item46_27_titulos_e_hashtags_nao_afirmam_premier():
    m = _jogada()
    assert not any("Premier" in t for t in titulos(m))
    assert not any("premier" in t.lower() for t in hashtags(m, "@canal"))
    assert titulos(_jogada(3))[2].endswith("| CS2") and all(len(t) <= 60 for t in titulos(m))


def test_item47_27_publicar_txt_sem_none_e_com_faixa_legivel(tmp_path):
    m = _jogada()
    m.faixa = "RELATORIO"
    txt = gerar_publicar(tmp_path, "v", [m], {1: {"short": "shorts/01.mp4", "miniatura": None}}, "")
    t = txt.read_text(encoding="utf-8")
    assert "None" not in t and "capa sugerida: nenhuma" in t
    assert "RELATORIO" not in t and "Fraco" in t
    # o json guarda o valor interno (a interface usa)
    assert json.loads((tmp_path / "publicar.json").read_text(encoding="utf-8"))["itens"][0]["faixa"] == "RELATORIO"


# ── item 28: momentos da demo passam pela nota 0-99 ──────────────────────────────────────────────

def _partida(abates):
    return PartidaDemo("de_x", {1: "Eu"}, 1, "Eu", [RoundDemo(1, 0.0, 100.0, "T")], abates, [], [], (1, 0), {1: "T"})


def test_item28_ace_da_demo_fica_ate_99_e_com_faixa():
    ab = [Abate(10 + 2 * i, 1, "v", "ak47", headshot=True, parede=True, sem_mira=True) for i in range(5)]
    [m] = momentos_da_demo(_partida(ab))
    assert m.score <= 99 and m.faixa in ("DESTAQUE", "PADRAO")
    assert m.faixa == "DESTAQUE"


def test_item28_tiro_unico_tem_faixa_preenchida():
    [m] = momentos_da_demo(_partida([Abate(10, 1, "v", "ak47")]))
    assert m.faixa in ("PADRAO", "RELATORIO") and 0 < m.score < 30


# ── item 44: fails têm filtro e cor própria no relatório ─────────────────────────────────────────

def test_item44_relatorio_tem_filtro_e_etiqueta_de_fails():
    assert 'data-f="fail"' in PAGE and ".tag.fail{" in PAGE


# ── itens 25 e 20: README descreve os dois avisos do Windows e quando o @ aparece ───────────────

def test_item25_20_readme():
    txt = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "Aviso de Segurança" in txt and "**Executar**" in txt and "SmartScreen" in txt
    assert "quando a webcam é detectada" in txt
