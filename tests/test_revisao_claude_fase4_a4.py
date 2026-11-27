"""A4 da revisão do Claude (31/10/2026): o resto do G-001, ligado por opções (todas DESLIGADAS por padrão):
speed-ramp (M7), freeze no headshot (V8), cartão tático (V4), pan suave (V2), voz tratada + ducking (A2/A3) e faixa
do jogo vinda do filtro para vídeo de 1 faixa (A4). Renders de verdade com ffmpeg em vídeo sintético pequeno."""
import json
import subprocess
from pathlib import Path

import pytest

import cs2clip.pipeline as pl
from cs2clip import qc, render
from cs2clip.config import Config
from cs2clip.efeitos import DUR_FREEZE, duracao_do_corte, pecas_do_corte, tem_efeito_de_tempo
from cs2clip.moments import Event, Moment
from cs2clip.tools import FFMPEG, FFPROBE

# Arquivo inteiro: toda a cobertura aqui depende de render/decodificacao real com o ffmpeg.
pytestmark = pytest.mark.lento

COR = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


@pytest.fixture(autouse=True)
def _x264_rapido(monkeypatch):
    """O teste não precisa de qualidade: x264 ultrafast (e nunca NVENC)."""
    monkeypatch.setattr(render, "video_args", lambda cfg: ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                                                           "-g", "120", "-pix_fmt", "yuv420p", *COR])


def _origem(destino: Path, faixas: int) -> Path:
    """12 s, 640x360, 60 fps, com `faixas` trilhas de áudio (mistura 440 Hz, voz 660 Hz, jogo 220 Hz)."""
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=60:d=12"]
    for hz in (440, 660, 220)[:faixas]:
        cmd += ["-f", "lavfi", "-i", f"sine=f={hz}:d=12"]
    cmd += ["-map", "0:v"] + [x for i in range(faixas) for x in ("-map", f"{i + 1}:a")]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


@pytest.fixture(scope="module")
def fontes(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("origens")
    return {"tres": _origem(pasta / "tres_faixas.mp4", 3), "uma": _origem(pasta / "uma_faixa.mp4", 1)}


def _momento() -> Moment:
    m = Moment(2.0, 10.0, "abate", label="DOUBLE KILL", score=40.0, idx=1,
               kills=[Event(3.0, "kill"), Event(7.0, "kill", icons=["headshot"])])
    m.round_n = 12
    return m


def _cfg(**opcoes) -> Config:
    cfg = Config()
    cfg.qualidade.encoder = "libx264"
    for chave, valor in opcoes.items():
        secao, nome = chave.split("__")
        setattr(getattr(cfg, secao), nome, valor)
    return cfg


def _faixas_audio(arq: Path) -> list[str]:
    """Título de cada faixa de áudio ('' = sem título). No MP4 o título de `-metadata:s:a:N title=` aparece em `name`."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream_tags",
                        "-of", "json", str(arq)], capture_output=True, text=True)
    def titulo(s):
        tags = s.get("tags") or {}
        handler = tags.get("handler_name", "")
        if handler in ("SoundHandler", "Core Media Audio"):   # rótulo padrão do muxer, não é título
            handler = ""
        return tags.get("title") or tags.get("name") or handler or ""
    return [titulo(s) for s in json.loads(r.stdout).get("streams", [])]


def _md5_quadro(arq: Path, t: float) -> str:
    r = subprocess.run([FFMPEG, "-v", "error", "-ss", str(t), "-i", str(arq), "-frames:v", "1", "-f", "md5", "-"],
                       capture_output=True, text=True)
    return r.stdout.strip()


# ── as peças do corte (sem ffmpeg) ────────────────────────────────────────────────────────────────

def test_sem_opcoes_o_corte_e_uma_peca_so():
    pecas = pecas_do_corte(_momento(), Config())
    assert len(pecas) == 1 and not tem_efeito_de_tempo(pecas)
    assert duracao_do_corte(_momento(), Config()) == pytest.approx(8.0)


def test_speed_ramp_encurta_o_corte_e_o_freeze_alonga():
    m = _momento()
    assert duracao_do_corte(m, _cfg(cortes__speed_ramp=True)) == pytest.approx(8.0 - 2.35 + 2.35 / 1.75, abs=0.02)
    assert duracao_do_corte(m, _cfg(cortes__freeze_headshot=True)) == pytest.approx(8.0 + DUR_FREEZE, abs=0.02)
    ambos = pecas_do_corte(m, _cfg(cortes__speed_ramp=True, cortes__freeze_headshot=True))
    assert [p.congela for p in ambos].count(True) == 1 and any(p.vel == 1.75 for p in ambos)
    assert sum(p.dur_saida for p in ambos) == pytest.approx(8.0 - 2.35 + 2.35 / 1.75 + DUR_FREEZE, abs=0.02)


def test_freeze_so_com_headshot_longe_das_pontas():
    sem_hs = _momento()
    sem_hs.kills = [Event(3.0, "kill"), Event(7.0, "kill")]
    assert len(pecas_do_corte(sem_hs, _cfg(cortes__freeze_headshot=True))) == 1
    na_ponta = _momento()
    na_ponta.kills = [Event(3.0, "kill"), Event(9.9, "kill", icons=["headshot"])]
    assert len(pecas_do_corte(na_ponta, _cfg(cortes__freeze_headshot=True))) == 1


def test_pipeline_espera_a_duracao_com_efeitos_e_a_normal_sem_eles():
    import types
    info = types.SimpleNamespace(duration=600.0)
    m = _momento()
    assert pl._dur_corte(m, info, Config()) == pytest.approx(8.0)
    assert pl._dur_corte(m, info, _cfg(cortes__speed_ramp=True)) == pytest.approx(8.0 - 2.35 + 2.35 / 1.75, abs=0.02)
    seq = _momento()
    seq.segmentos = [(2.0, 4.0), (6.0, 10.0)]          # sequência: 2 trechos, sem o tempo parado do meio
    assert pl._dur_corte(seq, info, Config()) == pytest.approx(6.0)
    # o headshot (7,0 s) está no 2º trecho: um congelamento só, mesmo com 2 trechos
    assert pl._dur_corte(seq, info, _cfg(cortes__freeze_headshot=True)) == pytest.approx(6.0 + DUR_FREEZE, abs=0.02)


# ── o caminho de sempre não muda ──────────────────────────────────────────────────────────────────

def test_corte_padrao_usa_o_caminho_antigo(fontes, monkeypatch, tmp_path):
    monkeypatch.setattr(render, "_render_clip_complexo", lambda *a, **k: pytest.fail("não devia usar o caminho novo"))
    cfg = _cfg()
    cfg._trilha_jogo = 2
    out = tmp_path / "corte.mp4"
    render.render_clip(str(fontes["tres"]), _momento(), out, cfg)
    assert qc.verificar(out, qc.Esperado(1920, 1080, 8.0, True, 60.0, conteudo=True)) == []
    assert _faixas_audio(out) == ["", "jogo"]                     # faixa da live + faixa só do jogo (A1)


# ── efeitos de tempo ──────────────────────────────────────────────────────────────────────────────

def test_speed_ramp_e_freeze_geram_a_duracao_prevista_e_mantem_as_faixas(fontes, tmp_path):
    cfg = _cfg(cortes__speed_ramp=True, cortes__freeze_headshot=True)
    cfg._trilha_jogo = 2
    m = _momento()
    out = tmp_path / "efeitos.mp4"
    render.render_clip(str(fontes["tres"]), m, out, cfg)
    esperado = duracao_do_corte(m, cfg)
    assert esperado == pytest.approx(8.0 - 2.35 + 2.35 / 1.75 + DUR_FREEZE, abs=0.02)
    assert qc.verificar(out, qc.Esperado(1920, 1080, esperado, True, 60.0, conteudo=True)) == []
    assert _faixas_audio(out) == ["", "jogo"]                     # a faixa do jogo acompanha o mesmo tempo
    sem = tmp_path / "sem.mp4"
    render.render_clip(str(fontes["tres"]), m, sem, _cfg())
    assert qc.inspecionar(out)["duracao"] < qc.inspecionar(sem)["duracao"]     # speed-ramp ganhou do freeze aqui


def test_trecho_sem_o_que_ajustar_sai_como_o_normal(fontes, monkeypatch, tmp_path):
    monkeypatch.setattr(render, "_render_clip_complexo", lambda *a, **k: pytest.fail("não devia usar o caminho novo"))
    m = _momento()
    m.kills = [Event(3.0, "kill")]                                  # 1 abate: nada para acelerar nem congelar
    out = tmp_path / "um.mp4"
    render.render_clip(str(fontes["tres"]), m, out, _cfg(cortes__speed_ramp=True, cortes__freeze_headshot=True))
    assert out.is_file()


# ── cartão tático ─────────────────────────────────────────────────────────────────────────────────

def test_cartao_tatico_aparece_no_comeco_e_o_arquivo_auxiliar_some(fontes, tmp_path):
    sem, com = tmp_path / "sem.mp4", tmp_path / "com.mp4"
    render.render_clip(str(fontes["tres"]), _momento(), sem, _cfg())
    render.render_clip(str(fontes["tres"]), _momento(), com, _cfg(cortes__cartao_tatico=True))
    assert qc.verificar(com, qc.Esperado(1920, 1080, 8.0, True, 60.0, conteudo=True)) == []
    assert _md5_quadro(com, 0.8) != _md5_quadro(sem, 0.8)           # o cartão (até 1,9 s) está no quadro
    assert _md5_quadro(com, 4.0) == _md5_quadro(sem, 4.0)           # depois dele o quadro é o mesmo
    assert not list(tmp_path.glob("*.cartao.ass"))                  # o .ass temporário foi apagado


def test_cartao_so_no_primeiro_trecho_de_uma_sequencia(fontes, monkeypatch, tmp_path):
    pedidos = []
    import cs2clip.cartao as cartao
    original = cartao.gerar_ass_cartao
    monkeypatch.setattr(cartao, "gerar_ass_cartao", lambda destino, m, *a, **k: (pedidos.append(m.trecho), original(destino, m))[1])
    m = _momento()
    m.segmentos = [(2.0, 4.0), (6.0, 10.0)]
    render.render_por_janelas(m, tmp_path / "seq.mp4",
                              lambda mm, dest: render.render_clip(str(fontes["tres"]), mm, dest,
                                                                  _cfg(cortes__cartao_tatico=True)))
    assert pedidos == [0]
    assert (tmp_path / "seq.mp4").is_file()


# ── voz tratada + ducking ─────────────────────────────────────────────────────────────────────────

def test_voz_tratada_usa_as_faixas_e_mantem_o_corte_valido(fontes, tmp_path):
    cfg = _cfg(audio__tratar_voz=True, audio__faixa_voz=2, audio__faixa_jogo=3)
    cfg._trilha_jogo = 2
    out = tmp_path / "voz.mp4"
    render.render_clip(str(fontes["tres"]), _momento(), out, cfg)
    assert qc.verificar(out, qc.Esperado(1920, 1080, 8.0, True, 60.0, conteudo=True, silencio=True)) == []
    assert _faixas_audio(out) == ["", "jogo"]
    padrao = tmp_path / "padrao.mp4"
    cfg0 = _cfg()
    cfg0._trilha_jogo = 2
    render.render_clip(str(fontes["tres"]), _momento(), padrao, cfg0)
    assert out.read_bytes() != padrao.read_bytes()                  # o áudio mudou (voz tratada, jogo abaixando)


def test_voz_sem_as_faixas_avisa_uma_vez_e_segue_como_sempre(fontes, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(render, "_AVISADOS_AUDIO", set())
    monkeypatch.setattr(render, "_render_clip_complexo", lambda *a, **k: pytest.fail("não devia usar o caminho novo"))
    cfg = _cfg(audio__tratar_voz=True)                              # sem faixa_voz
    for n in (1, 2):
        render.render_clip(str(fontes["tres"]), _momento(), tmp_path / f"c{n}.mp4", cfg)
    assert capsys.readouterr().out.count("audio.tratar_voz precisa") == 1


def test_voz_tratada_no_short(fontes, tmp_path):
    cfg = _cfg(audio__tratar_voz=True, audio__faixa_voz=2, audio__faixa_jogo=3)
    cfg._trilha_jogo = 2
    out = tmp_path / "short_voz.mp4"
    render.render_short(str(fontes["tres"]), _momento(), out, cfg, None, None)
    esp = qc.Esperado(1080, 1920, 8.0, True, 60.0, conteudo=True, silencio=True)
    assert qc.verificar(out, esp) == []


# ── pan suave no short ────────────────────────────────────────────────────────────────────────────

def test_pan_suave_mexe_o_recorte_no_abate_e_so_nele(fontes, tmp_path):
    parado, pan = tmp_path / "parado.mp4", tmp_path / "pan.mp4"
    render.render_short(str(fontes["tres"]), _momento(), parado, _cfg(), None, None)
    render.render_short(str(fontes["tres"]), _momento(), pan, _cfg(shorts__pan_suave=True), None, None)
    esp = qc.Esperado(1080, 1920, 8.0, True, 60.0, conteudo=True)
    assert qc.verificar(pan, esp) == []
    assert _md5_quadro(pan, 0.1) == _md5_quadro(parado, 0.1)        # bem antes do 1º abate (1,0 s do trecho): igual
    assert _md5_quadro(pan, 1.1) != _md5_quadro(parado, 1.1)        # logo depois do abate o recorte balança


# ── A4: vídeo de uma faixa só ganha a faixa "jogo" ────────────────────────────────────────────────

def test_faixa_unica_ganha_a_segunda_faixa_jogo_e_limpa_o_wav(fontes, tmp_path):
    cfg = _cfg(audio__separar_jogo_faixa_unica=True)
    out = tmp_path / "unica.mp4"
    render.render_clip(str(fontes["uma"]), _momento(), out, cfg)
    assert _faixas_audio(out) == ["", "jogo"]
    assert qc.verificar(out, qc.Esperado(1920, 1080, 8.0, True, 60.0, conteudo=True)) == []
    assert not list(tmp_path.glob("*.wav"))
    assert _faixas_audio(_origem(tmp_path / "ref.mp4", 1)) == [""]
    sem = tmp_path / "sem.mp4"
    render.render_clip(str(fontes["uma"]), _momento(), sem, _cfg())
    assert _faixas_audio(sem) == [""]                                # desligado: continua com 1 faixa


# ── cache: ligar uma opção refaz o que ela muda, e só isso ────────────────────────────────────────

def test_hash_do_cache_nao_muda_com_tudo_desligado_e_muda_com_cada_opcao_ligada():
    from cs2clip.refazer import RENDER_VERSAO, _sha, hash_corte, hash_short, versao_ffmpeg
    cfg = Config()
    # desde o A2 (02/11/2026) a versão do FFmpeg faz parte da base: os efeitos desligados não acrescentam nada a ela
    antigo = _sha({"v": RENDER_VERSAO, "encoder": cfg.qualidade.encoder, "cq": cfg.qualidade.cq,
                   "lufs": cfg.qualidade.lufs, "transicao": cfg.montagem.transicao, "ffmpeg": versao_ffmpeg()})
    assert hash_corte(cfg) == antigo                                 # quem não usa nada mantém os arquivos prontos
    base_c, base_s = hash_corte(_cfg()), hash_short(_cfg())          # `_cfg` fixa o encoder: compara igual com igual
    for opcao in ("cortes__speed_ramp", "cortes__freeze_headshot", "cortes__cartao_tatico",
                  "audio__separar_jogo_faixa_unica"):
        assert hash_corte(_cfg(**{opcao: True})) != base_c, opcao
        assert hash_short(_cfg(**{opcao: True})) == base_s, opcao     # efeito só do corte: o short não é refeito
    assert hash_short(_cfg(shorts__pan_suave=True)) != base_s
    assert hash_corte(_cfg(shorts__pan_suave=True)) == base_c
    assert hash_short(_cfg(shorts__mostrar_killfeed=False)) != base_s
    voz = _cfg(audio__tratar_voz=True, audio__faixa_voz=2, audio__faixa_jogo=3)
    assert hash_corte(voz) != base_c and hash_short(voz) != base_s


def test_config_valida_faixa_de_voz():
    from cs2clip.config import _validar
    cfg = Config()
    cfg.audio.faixa_voz = 7
    with pytest.raises(ValueError, match="faixa_voz"):
        _validar(cfg)
    cfg.audio.faixa_voz = 2
    _validar(cfg)
