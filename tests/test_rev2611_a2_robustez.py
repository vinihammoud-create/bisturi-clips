"""G-019 (resto): cache do modelo de voz por (nome, cpu) e PATH sem repetição; qc confere returncode e usa timeout;
classify.salvar_exemplos não apaga antes de gravar; CacheRender.pronto sob a trava."""
from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest

import cs2clip.qc as qc
import cs2clip.transcribe as tr


# ── transcribe: get_model por (nome, cpu) ────────────────────────────────────────────────────────

@pytest.fixture
def whisper_falso(monkeypatch):
    carregados: list[tuple[str, str]] = []

    class Falso:
        def __init__(self, nome, device, compute_type):
            carregados.append((nome, device))

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Falso))
    monkeypatch.setattr(tr, "_add_cuda_dlls", lambda: None)
    monkeypatch.setattr(tr, "_MODEL", None)
    monkeypatch.setattr(tr, "_MODEL_NA_CPU", False)
    monkeypatch.setattr(tr, "_MODEL_CHAVE", None, raising=False)
    yield carregados
    tr.liberar_modelo()


def test_g019_mesmo_nome_reaproveita_o_modelo(whisper_falso):
    a = tr.get_model("pequeno")
    assert tr.get_model("pequeno") is a
    assert whisper_falso == [("pequeno", "cuda")]


def test_g019_outro_nome_carrega_o_modelo_pedido(whisper_falso):
    a = tr.get_model("pequeno")
    b = tr.get_model("grande")
    assert b is not a and whisper_falso == [("pequeno", "cuda"), ("grande", "cuda")]
    assert tr.get_model("grande") is b          # e o novo fica guardado


def test_g019_pedir_cpu_depois_da_gpu_carrega_na_cpu(whisper_falso):
    tr.get_model("pequeno")
    tr.get_model("pequeno", cpu=True)
    assert whisper_falso == [("pequeno", "cuda"), ("pequeno", "cpu")]
    assert tr._MODEL_NA_CPU


def test_g019_modelo_que_ja_caiu_na_cpu_serve_a_quem_pede_cpu(monkeypatch, whisper_falso):
    class SemGpu:
        def __init__(self, nome, device, compute_type):
            whisper_falso.append((nome, device))
            if device == "cuda":
                raise RuntimeError("sem GPU")
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=SemGpu))
    a = tr.get_model("pequeno")
    assert tr.get_model("pequeno", cpu=True) is a
    assert whisper_falso == [("pequeno", "cuda"), ("pequeno", "cpu")]


def test_g019_cuda_dlls_nao_acumula_entradas_no_path(tmp_path, monkeypatch):
    pasta = tmp_path / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin"
    pasta.mkdir(parents=True)
    registros = []
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "add_dll_directory", lambda d: registros.append(d), raising=False)
    monkeypatch.setenv("PATH", "C:\fake")
    monkeypatch.setattr(tr, "_DLLS_REGISTRADAS", set())
    tr._add_cuda_dlls()
    tr._add_cuda_dlls()
    tr._add_cuda_dlls()
    assert registros == [str(pasta)]
    assert os.environ["PATH"].split(os.pathsep).count(str(pasta)) == 1


# ── qc.verificar_conteudo: returncode e timeout ──────────────────────────────────────────────────

def _ffmpeg_falso(monkeypatch, respostas):
    """`subprocess.run` do qc devolve as respostas em ordem; guarda os `timeout` recebidos."""
    prazos, fila = [], list(respostas)

    def run(cmd, **kw):
        prazos.append(kw.get("timeout"))
        r = fila.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r
    monkeypatch.setattr(qc.subprocess, "run", run)
    monkeypatch.setattr(qc, "inspecionar", lambda c: {"duracao": 10.0})
    return prazos


def _ok(stderr=""):
    return subprocess.CompletedProcess([], 0, stdout="", stderr=stderr)


@pytest.fixture
def arquivo(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    return f


def test_g019_ffmpeg_que_falha_nao_vira_video_perfeito(arquivo, monkeypatch):
    _ffmpeg_falso(monkeypatch, [subprocess.CompletedProcess([], 1, stdout="", stderr="Invalid data found when processing input")])
    problemas = qc.verificar_conteudo(arquivo)
    assert len(problemas) == 1 and "não consegui conferir" in problemas[0] and "Invalid data" in problemas[0]


def test_g019_silencio_que_falha_tambem_e_avisado(arquivo, monkeypatch):
    _ffmpeg_falso(monkeypatch, [_ok(), subprocess.CompletedProcess([], 1, stdout="", stderr="boom")])
    problemas = qc.verificar_conteudo(arquivo, silencio=True)
    assert len(problemas) == 1 and "áudio" in problemas[0] and "não consegui conferir" in problemas[0]


def test_g019_conferencia_usa_timeout_e_trata_estouro(arquivo, monkeypatch):
    prazos = _ffmpeg_falso(monkeypatch, [subprocess.TimeoutExpired("ffmpeg", 120)])
    problemas = qc.verificar_conteudo(arquivo)
    assert prazos == [qc.TIMEOUT_CONFERENCIA_S] and qc.TIMEOUT_CONFERENCIA_S > 0
    assert len(problemas) == 1 and "passou de" in problemas[0]


def test_g019_video_bom_continua_sem_nenhum_problema(arquivo, monkeypatch):
    prazos = _ffmpeg_falso(monkeypatch, [_ok("frame=  300 fps=0.0"), _ok("size=N/A time=00:00:10")])
    assert qc.verificar_conteudo(arquivo, silencio=True) == []
    assert prazos == [qc.TIMEOUT_CONFERENCIA_S] * 2


def test_g019_preto_e_mudo_de_verdade_continuam_sendo_achados(arquivo, monkeypatch):
    _ffmpeg_falso(monkeypatch, [_ok("[blackdetect @ 0x1] black_start:0 black_end:10 black_duration:10"),
                                _ok("[silencedetect @ 0x2] silence_start: 0")])
    assert qc.verificar_conteudo(arquivo, silencio=True) == ["vídeo inteiramente preto", "áudio inteiramente mudo"]


# ── classify.salvar_exemplos: não apaga antes de gravar ──────────────────────────────────────────

def _modelo(*larguras):
    import numpy as np
    from cs2clip.classify import PlayerModel, Segment
    return PlayerModel([Segment(0, w - 1, np.full((16, w), 255, np.uint8)) for w in larguras])


def test_g019_exemplos_novos_trocam_os_antigos(tmp_path):
    from cs2clip.classify import PlayerModel
    _modelo(20, 30, 40).save(tmp_path)
    assert sorted(p.name for p in tmp_path.glob("*.png")) == ["00_20.png", "01_30.png", "02_40.png"]
    _modelo(22, 30).save(tmp_path)
    assert sorted(p.name for p in tmp_path.glob("*.png")) == ["00_22.png", "01_30.png"]
    assert not (tmp_path / "_novo").exists()
    salvo = PlayerModel.load(tmp_path)
    assert salvo is not None and [e.w for e in salvo.exemplars] == [22, 30]


def test_g019_erro_ao_gravar_no_meio_nao_apaga_o_treino_que_ja_existia(tmp_path, monkeypatch):
    import cs2clip.classify as classify
    _modelo(20, 30, 40).save(tmp_path)
    antes = {p.name: p.read_bytes() for p in tmp_path.glob("*.png")}
    real = classify.imwrite
    chamadas = []

    def falha_no_segundo(caminho, img, *a, **k):
        chamadas.append(caminho)
        if len(chamadas) == 2:
            raise OSError("disco cheio")
        real(caminho, img, *a, **k)
    monkeypatch.setattr(classify, "imwrite", falha_no_segundo)
    with pytest.raises(OSError):
        _modelo(22, 32, 42).save(tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.glob("*.png")} == antes
    assert not (tmp_path / "_novo").exists()


# ── CacheRender.pronto sob a trava ───────────────────────────────────────────────────────────────

def test_g019_cacherender_pronto_le_o_meta_com_a_trava_fechada(tmp_path):
    from cs2clip.refazer import CacheRender

    class MetaEspia(dict):
        lido_com_trava: list[bool] = []

        def get(self, chave, padrao=None):
            self.lido_com_trava.append(cache._lock.locked())
            return super().get(chave, padrao)
    cache = CacheRender(tmp_path / ".cache", forcar=False)
    video = tmp_path / "corte.mp4"
    video.write_bytes(b"x")
    cache.meta = MetaEspia({"corte.mp4": "imp"})
    assert cache.pronto(video, "imp") is True
    assert cache.pronto(video, "outra") is False
    assert MetaEspia.lido_com_trava == [True, True]


def test_g019_cacherender_registrar_e_pronto_juntos_em_threads(tmp_path):
    import threading

    from cs2clip.refazer import CacheRender
    cache = CacheRender(tmp_path / ".cache", forcar=False)
    arquivos = []
    for i in range(40):
        f = tmp_path / f"c{i}.mp4"
        f.write_bytes(b"x")
        arquivos.append(f)
    erros: list[Exception] = []

    def escreve():
        try:
            for f in arquivos:
                cache.registrar(f, "i")
        except Exception as e:  # noqa: BLE001
            erros.append(e)

    def le():
        try:
            for _ in range(200):
                for f in arquivos:
                    cache.pronto(f, "i")
        except Exception as e:  # noqa: BLE001
            erros.append(e)
    ts = [threading.Thread(target=escreve), threading.Thread(target=le), threading.Thread(target=le)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert erros == [] and all(cache.pronto(f, "i") for f in arquivos)
