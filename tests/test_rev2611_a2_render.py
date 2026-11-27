"""R-027: no conserto da GPU que caiu no meio, os trechos são juntados com a configuração do dono, não a padrão."""
from __future__ import annotations

from cs2clip import render
from cs2clip.config import Config
from cs2clip.moments import Event, Moment


def _momento_com_dois_trechos():
    m = Moment(100, 160, "abate", kills=[Event(110.0, "kill"), Event(150.0, "kill")])
    m.segmentos = [(106.0, 112.5), (146.0, 152.5)]
    return m


def test_r027_trechos_de_encoders_diferentes_sao_juntados_com_o_cq_do_dono(tmp_path, monkeypatch):
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    cfg.qualidade.cq = 31                       # o padrão do código é outro: a diferença tem de aparecer no comando
    assert Config().qualidade.cq != 31
    comandos = []
    codecs = iter(["h264 | x264", "h264"])      # um trecho em x264, outro em NVENC: formatos diferentes
    monkeypatch.setattr(render, "_NVENC_QUEBROU", False)
    monkeypatch.setattr(render, "_codec", lambda f: next(codecs))
    monkeypatch.setattr(render, "_faixa", lambda arq, sel, campos: "1920,1080" if "width" in campos else "aac")
    monkeypatch.setattr(render, "_duracao", lambda arq: 5.0)
    monkeypatch.setattr(render, "_run_enc", lambda fabrica, cwd=None: comandos.append(fabrica()))
    monkeypatch.setattr(render, "garantir_pico", lambda *a, **k: None)

    render.render_por_janelas(m := _momento_com_dois_trechos(), tmp_path / "c.mp4",
                              lambda mm, dest: dest.write_bytes(b"x"), cfg)

    assert m.janelas and len(comandos) == 1
    cmd = comandos[0]
    assert cmd[cmd.index("-crf") + 1] == "30", "o reencode tem de usar o qualidade.cq do config.toml (cq 31 -> crf 30)"


def test_r027_render_por_janelas_passa_a_config_ao_concat(tmp_path, monkeypatch):
    vistos = []
    monkeypatch.setattr(render, "concat", lambda files, out, cfg=None: (vistos.append(cfg), out.write_bytes(b"ok")))
    monkeypatch.setattr(render, "garantir_pico", lambda *a, **k: None)
    cfg = Config()
    render.render_por_janelas(_momento_com_dois_trechos(), tmp_path / "c.mp4", lambda mm, dest: dest.write_bytes(b"x"), cfg)
    assert vistos == [cfg]


def test_r027_sem_config_o_reencode_avisa_que_usa_o_padrao(tmp_path, monkeypatch, capsys):
    arquivos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    codecs = iter(["h264 | x264", "h264"])
    monkeypatch.setattr(render, "_codec", lambda f: next(codecs))
    monkeypatch.setattr(render, "_juntar_reencodando", lambda files, out, cfg: None)
    render.concat(arquivos, tmp_path / "saida.mp4")
    assert "qualidade padrão" in capsys.readouterr().out
