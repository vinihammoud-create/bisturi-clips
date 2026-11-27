"""Qualidade profissional da montagem (01/11/2026): gravação sem voz ganha música, a faixa 1 pode ser a do jogo
(índice 0 não é "falso"), e o pico real do áudio final fica abaixo de -1 dBTP depois do AAC."""
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

import cs2clip.pipeline as pl
from cs2clip import render
from cs2clip.config import Config
from cs2clip.moments import Event, Moment
from cs2clip.montagem import trilha_jogo
from cs2clip.tools import FFMPEG, FFPROBE

COR = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


def _origem(destino: Path, faixas: int, iguais: bool = True, segundos: int = 12) -> Path:
    """Vídeo 640x360 60 fps com `faixas` trilhas; `iguais` = a MESMA mistura em todas (como o OBS de 23/10)."""
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=60:d={segundos}"]
    tons = (440,) * faixas if iguais else (440, 660, 220)[:faixas]
    for hz in sorted(set(tons), key=tons.index):
        cmd += ["-f", "lavfi", "-i", f"sine=f={hz}:d={segundos}"]
    entradas = {hz: i + 1 for i, hz in enumerate(sorted(set(tons), key=tons.index))}
    cmd += ["-map", "0:v"] + [x for hz in tons for x in ("-map", f"{entradas[hz]}:a")]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(destino)]
    subprocess.run(cmd, check=True, capture_output=True)
    return destino


def _faixas_audio(arq: Path) -> list[str]:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream_tags",
                        "-of", "json", str(arq)], capture_output=True, text=True)
    def titulo(s):
        tags = s.get("tags") or {}
        handler = tags.get("handler_name", "")
        if handler in ("SoundHandler", "Core Media Audio"):   # rótulo padrão do muxer, não é título
            handler = ""
        return tags.get("title") or tags.get("name") or handler or ""
    return [titulo(s) for s in json.loads(r.stdout).get("streams", [])]


@pytest.fixture(scope="module")
def iguais(tmp_path_factory):
    return _origem(tmp_path_factory.mktemp("o") / "tres_iguais.mp4", 3, iguais=True)


# ── gravação sem voz: a mistura já é o som do jogo ───────────────────────────────────────────────

@pytest.mark.lento
def test_faixas_iguais_sem_voz_usam_a_faixa_1_como_jogo(iguais):
    cfg = Config()
    cfg.audio.faixa_jogo = 3
    indice, motivo = trilha_jogo(str(iguais), cfg)
    assert indice is None and "igual à faixa 1" in motivo                     # regra de sempre
    indice, motivo = trilha_jogo(str(iguais), cfg, mistura_sem_voz=True)
    assert indice == 0 and "não tem voz" in motivo                            # sem voz: a faixa 1 serve


@pytest.mark.lento
def test_sem_faixa_configurada_e_sem_voz_tambem_usa_a_faixa_1(iguais):
    assert trilha_jogo(str(iguais), Config(), mistura_sem_voz=True)[0] == 0


@pytest.mark.lento
def test_video_sem_audio_nunca_vira_faixa_de_jogo(tmp_path):
    mudo = tmp_path / "mudo.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=2", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(mudo)], check=True, capture_output=True)
    assert trilha_jogo(str(mudo), Config(), mistura_sem_voz=True)[0] is None


@pytest.mark.lento
def test_tempos_json_guarda_o_tempo_de_cada_etapa(tmp_path, monkeypatch):
    from test_r1_pipeline import _cfg_render, _info, _jogada, _render_falso
    _render_falso(monkeypatch)
    pl._TEMPOS.clear()
    pl._TEMPOS.append({"etapa": "Varrendo o kill feed", "segundos": 12.3})   # como se a análise tivesse rodado
    pl._VIDEO_DOS_TEMPOS[0] = "live"                                         # o analyze marca de qual vídeo são os tempos
    cfg = _cfg_render()
    cfg.shorts.gerar = False
    out = tmp_path / "saida" / "live"
    pl.render_all(tmp_path / "v.mp4", cfg, _info(), "live", out, [_jogada()], [], None)
    dados = json.loads((out / "tempos.json").read_text(encoding="utf-8"))
    etapas = [e["etapa"] for e in dados["etapas"]]
    assert etapas[0] == "Varrendo o kill feed" and "Montagem com as melhores jogadas" in etapas
    assert dados["total_s"] >= 12.3 and all(e["segundos"] >= 0 for e in dados["etapas"])


def _out_com_fala(tmp_path: Path, segs) -> Path:
    out = tmp_path / "saida"
    (out / ".cache").mkdir(parents=True)
    if segs is not None:
        (out / ".cache" / "fala_trechos.json").write_text(json.dumps(segs), encoding="utf-8")
    return out


@pytest.mark.parametrize("segs,palavras,previa,esperado", [
    ([], 0, False, True),                     # nada de fala e nenhuma palavra: sem voz
    ([[10.0, 13.0]], 0, False, True),         # 3 s de 600 s (0,5%): ruído, ainda sem voz
    ([[10.0, 40.0]], 0, False, False),        # 5% de fala: tem voz (o Whisper pode ter errado)
    ([], 3, False, False),                    # o Whisper ouviu palavras: tem voz
    ([], 0, True, False),                     # prévia (sem Whisper): não dá para saber
    (None, 0, False, False),                  # sem a análise de fala guardada: não dá para saber
])
def test_mistura_sem_voz_exige_os_dois_sinais(tmp_path, segs, palavras, previa, esperado):
    cfg = Config()
    cfg.modo_previa = previa
    out = _out_com_fala(tmp_path, segs)
    words = [object()] * palavras
    assert pl._mistura_sem_voz(out, types.SimpleNamespace(duration=600.0), words, cfg) is esperado


def _pico_depois_do_aac(tmp_path: Path, nome: str, cadeia: str) -> float:
    """Estalos + ruído (como tiros sobre o som do jogo) empurrados 6 dB ACIMA do máximo, passados por `cadeia` e
    comprimidos em AAC 192k como no arquivo final. Devolve o pico real (dBTP) medido no resultado."""
    from cs2clip import qc
    sinal = "aevalsrc=0.15*(2*random(0)-1)+0.97*lt(mod(t\\,0.25)\\,0.0006):s=48000:d=6"
    out = tmp_path / f"{nome}.m4a"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", sinal, "-af", f"volume=6dB,{cadeia}",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)], check=True, capture_output=True)
    return qc.medir_loudness(out)[1]


@pytest.mark.lento
def test_limitador_segura_o_pico_real_abaixo_de_menos_1_dbtp_depois_do_aac(tmp_path):
    # O loudnorm sozinho deixava passar os tiros: a montagem de 23/10 saiu com -0,5 dBTP (padrão: no máximo -1).
    assert _pico_depois_do_aac(tmp_path, "sem", "aresample=48000") > -1.0      # controle: o teste enxerga o estouro
    assert _pico_depois_do_aac(tmp_path, "com", render.LIMITADOR_PICO) <= -1.0


def test_todo_audio_final_passa_pelo_limitador():
    meas = {"input_i": "-20", "input_tp": "-3", "input_lra": "5", "input_thresh": "-30", "target_offset": "0"}
    assert render.LIMITADOR_PICO in render.audio_filter(None, -14.0, 10.0, 0.1)          # sem medição
    assert render.LIMITADOR_PICO in render.audio_filter(meas, -14.0, 10.0, 0.0)          # com loudnorm (corte, montagem)
    assert render.LIMITADOR_PICO in render._af_voz_tratada(Config(), 10.0, 0.1)         # voz tratada


@pytest.mark.lento
def test_medir_loudness_sem_audio_devolve_none(tmp_path):
    from cs2clip import qc
    mudo = tmp_path / "mudo.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=2", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(mudo)], check=True, capture_output=True)
    assert qc.medir_loudness(mudo) is None


@pytest.mark.lento
def test_corte_com_a_faixa_1_como_jogo_leva_a_segunda_faixa(iguais, tmp_path, monkeypatch):
    monkeypatch.setattr(render, "video_args", lambda cfg: ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                                                           "-g", "120", "-pix_fmt", "yuv420p", *COR])
    m = Moment(2.0, 8.0, "abate", label="DOUBLE KILL", score=40.0, idx=1,
               kills=[Event(3.0, "kill"), Event(5.0, "kill")])
    cfg = Config()
    cfg._trilha_jogo = 0                    # antes: 0 era tratado como "sem faixa" e a 2ª faixa não saía
    out = tmp_path / "corte.mp4"
    render.render_clip(str(iguais), m, out, cfg)
    assert _faixas_audio(out) == ["", "jogo"]


# ── separação de voz por IA (Demucs, opcional): modelo seguro, CPU, sem rede depois do 1º uso ───────

DEMUCS_OK = """
import json, os, shutil, sys
from pathlib import Path
a = sys.argv[1:]
Path(__file__).with_name("args.json").write_text(json.dumps({"args": a, "offline": os.environ.get("HF_HUB_OFFLINE", "")}))
entrada = Path(a[-1])
destino = Path(a[a.index("-o") + 1]) / "htdemucs" / entrada.stem
destino.mkdir(parents=True)
shutil.copy(entrada, destino / "no_vocals.wav")
"""
DEMUCS_FALHA = """
import sys
sys.stderr.write("Loading model..." + chr(10) + "RuntimeError: modelo corrompido" + chr(10))
sys.exit(1)
"""


def _falso_demucs(tmp_path: Path, monkeypatch, corpo: str):
    from cs2clip import efeitos
    script = tmp_path / "demucs_falso.py"
    script.write_text(corpo, encoding="utf-8")
    monkeypatch.setattr(efeitos, "comando_demucs", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(efeitos, "CACHE_MODELO_DEMUCS", tmp_path / "sem_cache")
    return efeitos, script.with_name("args.json")


@pytest.mark.lento
def test_separacao_por_ia_usa_o_modelo_seguro_na_cpu_e_limpa_os_temporarios(iguais, tmp_path, monkeypatch):
    efeitos, args_json = _falso_demucs(tmp_path, monkeypatch, DEMUCS_OK)
    wav, metodo = efeitos.extrair_jogo_de_faixa_unica(str(iguais), tmp_path / "c1.jogo.wav", 2.0, 4.0)
    assert metodo == "IA (Demucs htdemucs)" and wav.is_file() and wav.stat().st_size > 10_000
    dados = json.loads(args_json.read_text())
    a = dados["args"]
    # hf:// = pesos em safetensors do Hugging Face; sem ele o demucs pode cair no servidor antigo (pickle)
    assert a[a.index("-n") + 1] == "hf://htdemucs" and a[a.index("-d") + 1] == "cpu" and "--two-stems=vocals" in a
    assert dados["offline"] == ""                                     # 1º uso: o modelo ainda vai ser baixado
    assert not list(tmp_path.glob("_ia_*")) and not list(tmp_path.glob("*.in.wav"))


@pytest.mark.lento
def test_com_o_modelo_ja_baixado_a_ia_roda_sem_internet(iguais, tmp_path, monkeypatch):
    efeitos, args_json = _falso_demucs(tmp_path, monkeypatch, DEMUCS_OK)
    cache = tmp_path / "hf"
    (cache / "snapshots" / "cbc8a9b1").mkdir(parents=True)
    (cache / "snapshots" / "cbc8a9b1" / "955717e8.safetensors").write_bytes(b"x")
    monkeypatch.setattr(efeitos, "CACHE_MODELO_DEMUCS", cache)
    efeitos.extrair_jogo_de_faixa_unica(str(iguais), tmp_path / "c2.jogo.wav", 2.0, 4.0)
    assert json.loads(args_json.read_text())["offline"] == "1"


@pytest.mark.lento
def test_ia_que_falha_cai_no_filtro_e_diz_o_motivo(iguais, tmp_path, monkeypatch):
    efeitos, _ = _falso_demucs(tmp_path, monkeypatch, DEMUCS_FALHA)
    wav, metodo = efeitos.extrair_jogo_de_faixa_unica(str(iguais), tmp_path / "c3.jogo.wav", 2.0, 4.0)
    assert wav.is_file() and metodo.startswith("filtro") and "modelo corrompido" in metodo
    assert not list(tmp_path.glob("_ia_*"))


@pytest.mark.lento
def test_sem_a_ia_instalada_usa_o_filtro_e_avisa_a_qualidade(iguais, tmp_path, monkeypatch):
    from cs2clip import efeitos
    monkeypatch.setattr(efeitos, "comando_demucs", lambda: None)
    wav, metodo = efeitos.extrair_jogo_de_faixa_unica(str(iguais), tmp_path / "c4.jogo.wav", 2.0, 4.0)
    assert wav.is_file() and "não instalada" in metodo


def test_comando_demucs_usa_a_pasta_separada_ou_o_python_do_programa_nunca_o_path(tmp_path, monkeypatch):
    from cs2clip import efeitos
    monkeypatch.setattr(efeitos, "ROOT", tmp_path)
    monkeypatch.setattr(efeitos.shutil, "which", lambda nome: r"C:\outro\demucs.exe")    # um demucs qualquer no PATH
    monkeypatch.setattr(efeitos.importlib.util, "find_spec", lambda nome: None)
    assert efeitos.comando_demucs() is None                                             # o do PATH não conta
    monkeypatch.setattr(efeitos.importlib.util, "find_spec", lambda nome: object())
    assert efeitos.comando_demucs() == [sys.executable, "-m", "demucs"]                 # no Python do programa
    py = tmp_path / ".venv-ia" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    assert efeitos.comando_demucs() == [str(py), "-m", "demucs"]                        # a pasta separada vem antes
