"""Regressões dos problemas confirmados pela revisão independente (17/10/2026)."""
import json
import types

import pytest

from cs2clip.moments import Event, Moment
from cs2clip.momentos_io import gravar_momentos
from cs2clip.partes import capitulos_youtube, dividir_partes, round_de
from cs2clip.refazer import CacheRender, impressao
from cs2clip.rounds import RoundTimelineEntry as R


# ── banner perdido: round_de não herda o resultado do round seguinte ───────────────────────────

def _partida_com_banner_perdido():
    # banners a cada 100 s; o do round 5 (~t=700) não foi lido
    rs = [R(300.0, "vitoria", .9), R(400.0, "vitoria", .9), R(500.0, "derrota", .9), R(600.0, "vitoria", .9),
          R(800.0, "derrota", .9)]
    return dividir_partes(rs, [], 900.0)


def test_momento_do_round_sem_banner_nao_herda_o_resultado_do_seguinte():
    partes = _partida_com_banner_perdido()
    p = [x for x in partes if x.tipo == "partida"][0]
    assert p.rounds_faltando == 1 and p.dur_round == 100.0
    _, n, res = round_de(650.0, partes)       # o banner de ~700 foi perdido; o seguinte (800) está a 150 s
    assert n is None and res is None


def test_momento_do_round_seguinte_ao_perdido_continua_resolvido():
    partes = _partida_com_banner_perdido()
    _, n, res = round_de(750.0, partes)       # 50 s antes do banner de 800
    assert res == "derrota"
    assert n == 6                             # o banner perdido conta: 1, 2, 3, 4, (5 perdido), 6


def test_numeros_dos_rounds_contam_os_perdidos():
    p = [x for x in _partida_com_banner_perdido() if x.tipo == "partida"][0]
    assert p.numeros == [1, 2, 3, 4, 6]


# ── fim da partida: o último round sem banner ainda é da partida ───────────────────────────────

def test_video_que_acaba_logo_depois_do_ultimo_banner_fica_dentro_da_partida():
    rs = [R(100.0 * i, "vitoria", .9) for i in range(1, 5)]       # 100..400
    p = [x for x in dividir_partes(rs, [], 520.0) if x.tipo == "partida"][0]
    assert p.fim == 520.0                                          # 520 - 412 = 108 s <= 1,5 round


def test_video_com_muito_material_depois_do_ultimo_banner_nao_estende():
    rs = [R(100.0 * i, "vitoria", .9) for i in range(1, 5)]
    p = [x for x in dividir_partes(rs, [], 2000.0) if x.tipo == "partida"][0]
    assert p.fim == pytest.approx(412.0)


# ── capítulos: 1º a menos de 10 s do início ancora em 00:00 ────────────────────────────────────

def test_capitulos_com_partida_comecando_perto_do_zero():
    r1 = [R(95.0 + 90 * i, "vitoria", .9) for i in range(4)]       # partida começa em 5 s
    r2 = [R(3000.0 + 90 * i, "derrota", .9) for i in range(4)]
    partes = dividir_partes(r1 + r2, [], 6000.0)
    caps = capitulos_youtube(partes, 6000.0)
    assert caps and caps[0][0] == 0.0 and caps[0][1].startswith("Partida 1")


# ── cache de render ────────────────────────────────────────────────────────────────────────────

def _m(fim=20.0):
    return Moment(10.0, fim, "abate", kills=[Event(15.0, "kill")], label="KILL", idx=1)


def test_impressao_muda_quando_o_fim_ou_os_abates_mudam():
    assert impressao(_m()) == impressao(_m())
    assert impressao(_m(20.0)) != impressao(_m(23.5))
    m2 = _m()
    m2.kills.append(Event(16.0, "kill"))
    assert impressao(m2) != impressao(_m())
    assert impressao(_m(), "webcam A") != impressao(_m(), "webcam B")


def test_cache_render_so_reaproveita_com_a_mesma_impressao(tmp_path):
    f = tmp_path / "01_kill.mp4"
    f.write_bytes(b"x" * 10)
    c = CacheRender(tmp_path, forcar=False)
    assert not c.pronto(f, "a")                     # nunca registrado
    c.registrar(f, "a")
    assert c.pronto(f, "a") and not c.pronto(f, "b")
    c.salvar()
    assert CacheRender(tmp_path, forcar=False).pronto(f, "a")        # sobrevive entre execuções
    assert not CacheRender(tmp_path, forcar=True).pronto(f, "a")     # --refazer cortes
    f.write_bytes(b"")
    assert not CacheRender(tmp_path, forcar=False).pronto(f, "a")    # vazio = pela metade


def test_cache_render_ilegivel_nao_derruba(tmp_path):
    (tmp_path / "render_meta.json").write_text("{quebrado", encoding="utf-8")
    assert CacheRender(tmp_path, forcar=False).meta == {}


# ── escrita atômica ────────────────────────────────────────────────────────────────────────────

def test_atomico_troca_o_arquivo_so_no_fim(tmp_path):
    from cs2clip.pipeline import _atomico
    destino = tmp_path / "corte.mp4"
    destino.write_bytes(b"velho")
    _atomico(destino, lambda tmp: tmp.write_bytes(b"novo"))
    assert destino.read_bytes() == b"novo"
    assert not list(tmp_path.glob("*.tmp.*"))


def test_atomico_com_falha_nao_toca_no_destino_e_limpa_o_temporario(tmp_path):
    from cs2clip.pipeline import _atomico
    destino = tmp_path / "corte.mp4"
    destino.write_bytes(b"velho")

    def quebra(tmp):
        tmp.write_bytes(b"metade")
        raise RuntimeError("ffmpeg falhou")

    with pytest.raises(RuntimeError):
        _atomico(destino, quebra)
    assert destino.read_bytes() == b"velho"                  # o corte bom continua lá
    assert not list(tmp_path.glob("*.tmp.*"))                # e não sobra arquivo pela metade


def test_atomico_destino_que_e_pasta_da_erro_sem_derrubar_a_limpeza(tmp_path):
    from cs2clip.pipeline import _atomico
    destino = tmp_path / "corte.mp4"
    destino.mkdir()
    with pytest.raises(OSError):
        _atomico(destino, lambda tmp: tmp.write_bytes(b"x"))
    assert not list(tmp_path.glob("*.tmp.*"))


# ── revisão do usuário em reação e vínculo com arquivos ────────────────────────────────────────

def test_foco_da_reacao_usa_o_pico_e_nao_muda_com_o_ajuste_as_falas():
    a = Moment(92.0, 104.0, "reacao", pico=99.0)
    b = Moment(92.0, 106.8, "reacao", pico=99.0)          # snap_to_speech estendeu o fim
    assert a.focus == b.focus == 99.0


def test_aprovacao_de_reacao_sobrevive_a_novo_ajuste_das_falas(tmp_path):
    gravar_momentos(tmp_path, [Moment(92.0, 104.0, "reacao", pico=99.0, idx=1)], "v", 300.0, "3")
    d = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))
    d["momentos"][0]["aprovado"] = True
    (tmp_path / "momentos.json").write_text(json.dumps(d), encoding="utf-8")
    gravar_momentos(tmp_path, [Moment(92.0, 106.8, "reacao", pico=99.0, idx=1)], "v", 300.0, "3")
    assert json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]["aprovado"] is True


def test_reanalise_sem_render_mantem_os_arquivos_que_ainda_existem(tmp_path):
    (tmp_path / "cortes").mkdir()
    (tmp_path / "cortes" / "01.mp4").write_bytes(b"x")
    mom = [_m()]
    gravar_momentos(tmp_path, mom, "v", 100.0, "3",
                    {1: {"corte": "cortes/01.mp4", "short": "shorts/01.mp4", "miniatura": None}})
    gravar_momentos(tmp_path, mom, "v", 100.0, "3")          # --so-analise depois de um render
    arqs = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]["arquivos"]
    assert arqs == {"corte": "cortes/01.mp4", "short": None, "miniatura": None}   # o short não existe mais


# ── main: códigos de saída, config e montagem final ───────────────────────────────────────────

def _falso_info():
    return types.SimpleNamespace(duration=10.0)


def test_main_termina_com_problemas_devolve_3_e_nao_diz_pronto(tmp_path, monkeypatch, capsys):
    import cs2clip.pipeline as pl
    from cs2clip.__main__ import main
    v = tmp_path / "live.mp4"
    v.write_bytes(b"x")
    monkeypatch.setattr(pl, "analyze", lambda p, cfg, o, pd, R: (_falso_info(), "live", tmp_path / "o", [], [], None))

    def render_com_erro(p, cfg, info, name, out, moments, words, lay, registro, R):
        print(registro.erro("corte 01", "ffmpeg falhou:\nalgo deu errado"))
        return [], [], []

    monkeypatch.setattr(pl, "render_all", render_com_erro)
    codigo = main([str(v), "--saida", str(tmp_path / "saida")])
    out = capsys.readouterr().out
    assert codigo == 3 and "Pronto" not in out and "1 problema" in out


def test_main_com_config_quebrado_da_mensagem_clara(tmp_path, capsys):
    from cs2clip.__main__ import main
    cfg = tmp_path / "config.toml"
    cfg.write_text("[cortes\nantes = ", encoding="utf-8")
    codigo = main(["x.mp4", "--config", str(cfg), "--saida", str(tmp_path / "s")])
    out = capsys.readouterr().out
    assert codigo == 1 and "problema" in out and "Traceback" not in out


def test_melhores_de_todos_usa_os_arquivos_exatos(tmp_path, monkeypatch):
    import cs2clip.pipeline as pl
    import cs2clip.render as rd
    from cs2clip.__main__ import main
    v1, v2 = tmp_path / "a.mp4", tmp_path / "b.mp4"
    v1.write_bytes(b"x")
    v2.write_bytes(b"x")
    exato = {}

    def analyze(p, cfg, o, pd, R):
        return _falso_info(), p.stem, tmp_path / p.stem, [], [], None

    def render_all(p, cfg, info, name, out, moments, words, lay, registro, R):
        m = Moment(1.0, 9.0, "abate", score=50.0 if name == "a" else 40.0, idx=5)
        arq = tmp_path / f"{name}_exato.mp4"
        arq.write_bytes(b"x")
        exato[name] = arq
        return [], [], [(m, arq)]

    chamadas = []
    monkeypatch.setattr(pl, "analyze", analyze)
    monkeypatch.setattr(pl, "render_all", render_all)
    monkeypatch.setattr(rd, "concat", lambda files, out, cfg=None: chamadas.append(list(files)))
    main([str(v1), str(v2), "--saida", str(tmp_path / "saida")])
    assert chamadas == [[exato["a"], exato["b"]]]            # na ordem das notas, nos arquivos exatos


# ── descartar na revisão tem efeito de verdade ─────────────────────────────────────────────────

def test_chaves_excluidas_le_o_que_o_usuario_descartou(tmp_path):
    from cs2clip.momentos_io import chaves_excluidas
    m1, m2 = Moment(10.0, 20.0, "abate", kills=[Event(15.0, "kill")], idx=1), \
        Moment(40.0, 50.0, "abate", kills=[Event(45.0, "kill")], idx=2)
    gravar_momentos(tmp_path, [m1, m2], "v", 100.0, "3")
    d = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))
    d["momentos"][1]["excluido"] = True
    (tmp_path / "momentos.json").write_text(json.dumps(d), encoding="utf-8")
    assert chaves_excluidas(tmp_path / "momentos.json") == {"45.0"}
    assert chaves_excluidas(tmp_path / "nao_existe.json") == set()
    assert not list(tmp_path.glob("*.tmp"))                       # gravação atômica não deixa sobra


def test_render_nao_gera_corte_de_momento_descartado(tmp_path, monkeypatch):
    import cs2clip.pipeline as pl
    from cs2clip.config import Config
    cfg = Config()
    cfg.shorts.gerar = False
    cfg.montagem.gerar = False
    m1 = Moment(10.0, 20.0, "abate", kills=[Event(15.0, "kill")], idx=1, label="KILL", score=30.0)
    m2 = Moment(40.0, 50.0, "abate", kills=[Event(45.0, "kill")], idx=2, label="KILL", score=30.0)
    for m in (m1, m2):
        m.parte_tipo = "partida"
    gravar_momentos(tmp_path, [m1, m2], "v", 100.0, "3")
    d = json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))
    d["momentos"][1]["excluido"] = True
    (tmp_path / "momentos.json").write_text(json.dumps(d), encoding="utf-8")
    feitos = []
    monkeypatch.setattr(pl, "render_clip", lambda src, m, dest, cfg: (feitos.append(m.idx), dest.write_bytes(b"x")))
    monkeypatch.setattr(pl, "thumbnail", lambda src, t, dest: dest.write_bytes(b"x"))
    info = types.SimpleNamespace(duration=100.0, fps=60.0)
    pl.render_all(tmp_path / "v.mp4", cfg, info, "v", tmp_path, [m1, m2], [], None, None, frozenset())
    assert feitos == [1]
