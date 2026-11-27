"""P3.9: a GPU falha com o `large-v3-turbo` -> cai para o `small` na CPU (com aviso e registro), em vez de repetir o turbo na CPU.
Tudo com um WhisperModel FALSO: nada baixa e nada usa a placa."""
from __future__ import annotations

import json
import sys
import types

import numpy as np
import pytest

import cs2clip.transcribe as tr

AUDIO = np.zeros(16000, np.float32)


class Cenario:
    """Um WhisperModel falso configurável. `pedidos` guarda (nome, dispositivo) de cada carregamento; `transcricoes`, o dispositivo de cada uso."""

    def __init__(self, gpu_carrega=True, gpu_transcreve=True, leve_carrega=True):
        self.gpu_carrega, self.gpu_transcreve, self.leve_carrega = gpu_carrega, gpu_transcreve, leve_carrega
        self.pedidos: list[tuple[str, str]] = []
        self.transcricoes: list[tuple[str, str]] = []
        cenario = self

        class Falso:
            def __init__(self, nome, device, compute_type):
                cenario.pedidos.append((nome, device))
                if device == "cuda" and not cenario.gpu_carrega:
                    raise RuntimeError("CUDA error 35")
                if device == "cpu" and nome == "small" and not cenario.leve_carrega:
                    raise OSError("sem internet para baixar o small")
                self.nome, self.device = nome, device

            def transcribe(self, audio, **k):
                cenario.transcricoes.append((self.nome, self.device))
                if self.device == "cuda" and not cenario.gpu_transcreve:
                    raise RuntimeError("CUDA out of memory")
                seg = types.SimpleNamespace(words=[types.SimpleNamespace(start=0.0, end=0.5, word=" oi ")])
                return iter([seg]), None

        self.classe = Falso


@pytest.fixture
def instalar(monkeypatch):
    def _instalar(**kw):
        c = Cenario(**kw)
        monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=c.classe))
        return c
    monkeypatch.setattr(tr, "_add_cuda_dlls", lambda: None)
    monkeypatch.setattr(tr, "_MODEL", None)
    monkeypatch.setattr(tr, "_MODEL_NA_CPU", False)
    monkeypatch.setattr(tr, "_MODEL_CHAVE", None)
    tr.zerar_troca_de_modelo()
    yield _instalar
    tr.liberar_modelo()
    tr.zerar_troca_de_modelo()


def _palavras(w):
    return [(x.start, x.text) for x in w]


# (a) turbo + GPU falha no carregamento
def test_turbo_gpu_falha_no_carregamento_cai_para_small_na_cpu(instalar, capsys):
    c = instalar(gpu_carrega=False)
    palavras = tr.transcribe(AUDIO, 10.0, "large-v3-turbo")
    assert _palavras(palavras) == [(10.0, "oi")]
    assert c.pedidos == [("large-v3-turbo", "cuda"), ("small", "cpu")]      # o turbo NÃO é repetido na CPU
    assert c.transcricoes == [("small", "cpu")]
    saida = capsys.readouterr().out
    assert "[aviso]" in saida and "small" in saida and "CPU" in saida
    assert tr.troca_de_modelo() == {"modelo_pedido": "large-v3-turbo", "modelo_usado": "small", "motivo": "a placa de vídeo falhou"}


# (b) turbo + GPU falha durante a transcrição
def test_turbo_gpu_falha_durante_a_transcricao_cai_para_small_na_cpu(instalar, capsys):
    c = instalar(gpu_transcreve=False)
    palavras = tr.transcribe(AUDIO, 10.0, "large-v3-turbo")
    assert _palavras(palavras) == [(10.0, "oi")]
    assert c.pedidos == [("large-v3-turbo", "cuda"), ("small", "cpu")]
    assert c.transcricoes == [("large-v3-turbo", "cuda"), ("small", "cpu")]
    saida = capsys.readouterr().out
    assert "[aviso]" in saida and "small" in saida and "durante a transcrição" in saida
    assert (tr.troca_de_modelo() or {}).get("modelo_usado") == "small"


def test_modelo_maior_que_small_tambem_cai_para_small(instalar):
    for nome in ("medium", "large-v3", "large-v2", "medium.en"):
        c = instalar(gpu_carrega=False)
        tr.liberar_modelo()
        tr.transcribe(AUDIO, 0.0, nome)
        assert c.pedidos == [(nome, "cuda"), ("small", "cpu")], nome


def test_depois_da_queda_o_proximo_pedido_do_turbo_reaproveita_o_small(instalar, capsys):
    c = instalar(gpu_transcreve=False)
    tr.transcribe(AUDIO, 0.0, "large-v3-turbo")
    capsys.readouterr()
    tr.transcribe(AUDIO, 5.0, "large-v3-turbo")          # os blocos seguintes (resenha) não recarregam nem repetem o aviso
    assert c.pedidos == [("large-v3-turbo", "cuda"), ("small", "cpu")]
    assert "[aviso]" not in capsys.readouterr().out


# (c) small + GPU falha: repete na CPU com o small, sem aviso de troca
@pytest.mark.parametrize("nome", ["small", "base", "tiny"])
def test_modelo_leve_com_gpu_que_falha_repete_na_cpu_com_o_mesmo(instalar, capsys, nome):
    c = instalar(gpu_carrega=False)
    assert _palavras(tr.transcribe(AUDIO, 0.0, nome)) == [(0.0, "oi")]
    assert c.pedidos == [(nome, "cuda"), (nome, "cpu")]
    saida = capsys.readouterr().out
    assert "modelo leve" not in saida and "driver da NVIDIA" not in saida
    assert tr.troca_de_modelo() is None


def test_small_com_gpu_que_falha_durante_a_transcricao_repete_na_cpu_com_o_mesmo(instalar, capsys):
    c = instalar(gpu_transcreve=False)
    tr.transcribe(AUDIO, 0.0, "small")
    assert c.pedidos == [("small", "cuda"), ("small", "cpu")]
    assert "modelo leve" not in capsys.readouterr().out and tr.troca_de_modelo() is None


# (d) GPU funciona: nada muda
def test_gpu_funcionando_nao_muda_nada(instalar, capsys):
    c = instalar()
    assert _palavras(tr.transcribe(AUDIO, 3.0, "large-v3-turbo")) == [(3.0, "oi")]
    assert c.pedidos == [("large-v3-turbo", "cuda")]
    assert c.transcricoes == [("large-v3-turbo", "cuda")]
    assert capsys.readouterr().out == ""
    assert tr.troca_de_modelo() is None


# (e) o aviso diz o que o usuário precisa saber
@pytest.mark.parametrize("cenario", [{"gpu_carrega": False}, {"gpu_transcreve": False}])
def test_aviso_manda_atualizar_o_driver(instalar, capsys, cenario):
    instalar(**cenario)
    tr.transcribe(AUDIO, 0.0, "large-v3-turbo")
    linhas = [x for x in capsys.readouterr().out.splitlines() if "[aviso]" in x]
    assert len(linhas) == 1
    t = linhas[0]
    assert "placa de vídeo falhou" in t and "(small)" in t and "CPU" in t
    assert "menos precisa e mais lenta" in t
    assert "driver da NVIDIA" in t and "feche o que está usando a placa" in t


# (f) CPU explícita não tenta a GPU
def test_cpu_explicita_nao_tenta_gpu(instalar, capsys):
    c = instalar()
    tr.get_model("large-v3-turbo", cpu=True)
    assert c.pedidos == [("large-v3-turbo", "cpu")]      # pedido explícito: o modelo pedido, sem troca
    assert capsys.readouterr().out == "" and tr.troca_de_modelo() is None


# (g) o small na CPU que falha ao transcrever sobe como hoje; sem o small (sem internet), a fala é pulada com aviso
def test_erro_do_small_na_cpu_ao_transcrever_sobe_como_hoje(instalar):
    c = instalar(gpu_carrega=False)

    def quebrar(self, audio, **k):
        raise RuntimeError("falhou de verdade")
    c.classe.transcribe = quebrar
    with pytest.raises(RuntimeError, match="falhou de verdade"):
        tr.transcribe(AUDIO, 0.0, "large-v3-turbo")


@pytest.mark.parametrize("cenario", [{"gpu_carrega": False}, {"gpu_transcreve": False}])
def test_sem_o_small_a_transcricao_e_pulada_com_aviso(instalar, capsys, cenario):
    instalar(leve_carrega=False, **cenario)
    assert tr.transcribe(AUDIO, 0.0, "large-v3-turbo") == []           # como "sem fala": o pipeline segue
    saida = capsys.readouterr().out
    assert "não consegui preparar o modelo leve" in saida and "internet" in saida and "pulada" in saida
    assert tr.troca_de_modelo() == {"modelo_pedido": "large-v3-turbo", "modelo_usado": None, "motivo": "sem internet para baixar o small"}


# o registro chega ao tempos.json (e só aparece quando houve troca)
def test_tempos_json_registra_o_modelo_usado(tmp_path, instalar):
    import cs2clip.pipeline as pl
    pl._gravar_tempos(tmp_path)
    assert "fala_modelo" not in json.loads((tmp_path / "tempos.json").read_text(encoding="utf-8"))
    instalar(gpu_carrega=False)
    tr.transcribe(AUDIO, 0.0, "large-v3-turbo")
    pl._gravar_tempos(tmp_path)
    dados = json.loads((tmp_path / "tempos.json").read_text(encoding="utf-8"))
    assert dados["fala_modelo"]["modelo_usado"] == "small" and dados["fala_modelo"]["modelo_pedido"] == "large-v3-turbo"
