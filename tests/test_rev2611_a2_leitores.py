"""R-017 + A-004: o ffmpeg que alimenta a leitura de quadros não fica órfão quando o OCR/classificador levanta."""
from __future__ import annotations

import subprocess
import sys

import pytest

from cs2clip import mortes, rounds, scan
from cs2clip.tools import fechar_leitor, sem_janela


class _Saida:
    """stdout do ffmpeg falso: entrega quadros inteiros para sempre."""

    def __init__(self):
        self.fechado = False

    def read(self, n):
        return b"\x00" * n

    def close(self):
        self.fechado = True


class _FFmpegFalso:
    """Um ffmpeg que só sai se for mandado sair (`terminate`); com `teimoso`, só com `kill`."""

    def __init__(self, teimoso: bool = False):
        self.stdout = _Saida()
        self.teimoso = teimoso
        self.terminado = self.morto = False
        self.esperas: list[float | None] = []

    def poll(self):
        return None if not (self.terminado or self.morto) else -15

    def terminate(self):
        self.terminado = True

    def kill(self):
        self.morto = True

    def wait(self, timeout=None) -> int:
        self.esperas.append(timeout)
        if self.morto or (self.terminado and not self.teimoso):
            return -15
        raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)


class _Estoura(Exception):
    pass


def _quadro_que_estoura(*_a, **_k):
    raise _Estoura("OCR falhou")


def _ligar(monkeypatch, falso):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: falso)


def _chamadas():
    """As quatro funções que leem quadros do ffmpeg, cada uma com o ponto de falha trocado por um que estoura."""
    return ["scan_range", "frames_tracks", "trecho", "scan_rounds"]


def _preparar(nome, monkeypatch):
    monkeypatch.setattr(scan, "find_boxes", _quadro_que_estoura)
    monkeypatch.setattr(rounds, "detect_round_end", _quadro_que_estoura)
    monkeypatch.setattr(mortes, "detect_own_death", _quadro_que_estoura)
    monkeypatch.setattr(mortes, "_passa_filtro", lambda q: True)
    monkeypatch.setattr(rounds, "criar_ocr", lambda: object())
    if nome == "scan_range":
        return lambda: scan._scan_range("v.mp4", 0.0, 10.0, 10.0, 0.0, 10.0)
    if nome == "frames_tracks":
        return lambda: scan._frames_tracks("v.mp4", 0.0, 10.0, 10.0)
    if nome == "trecho":
        return lambda: mortes._trecho("v.mp4", 0.0, 10.0, 100)
    return lambda: rounds.scan_rounds("v.mp4", ocr=object())


@pytest.mark.parametrize("nome", _chamadas())
def test_r017_erro_no_meio_encerra_o_ffmpeg_em_vez_de_deixar_orfao(nome, monkeypatch):
    falso = _FFmpegFalso()
    _ligar(monkeypatch, falso)
    chamar = _preparar(nome, monkeypatch)
    with pytest.raises(_Estoura):
        chamar()
    assert falso.stdout.fechado
    assert falso.terminado, "o ffmpeg tinha de receber terminate() quando o laço sai por erro"
    assert falso.esperas and falso.esperas[-1] is not None, "o wait() precisa de prazo"


@pytest.mark.parametrize("nome", _chamadas())
def test_a004_ffmpeg_que_nao_obedece_o_terminate_leva_kill(nome, monkeypatch):
    falso = _FFmpegFalso(teimoso=True)
    _ligar(monkeypatch, falso)
    chamar = _preparar(nome, monkeypatch)
    with pytest.raises(_Estoura):
        chamar()
    assert falso.terminado and falso.morto


def test_fim_normal_do_video_espera_sem_prazo_e_sem_terminate():
    """O caminho sem erro não mudou: fecha o pipe e espera o ffmpeg acabar (nada de terminate/kill)."""

    class _Acabou(_FFmpegFalso):
        def wait(self, timeout=None) -> int:
            self.esperas.append(timeout)
            return 0

    falso = _Acabou()
    assert fechar_leitor(falso, com_erro=False) == 0
    assert falso.stdout.fechado and not falso.terminado and not falso.morto
    assert falso.esperas == [None]


def test_fechar_leitor_com_processo_de_verdade_que_escreve_sem_parar():
    codigo = "import sys\nwhile True:\n    sys.stdout.buffer.write(b'x' * 65536)\n"
    proc = subprocess.Popen([sys.executable, "-c", codigo], stdout=subprocess.PIPE, **sem_janela())
    assert proc.stdout is not None
    proc.stdout.read(10)
    fechar_leitor(proc, com_erro=True, prazo_s=10.0)
    assert proc.poll() is not None


def test_varredura_que_termina_bem_continua_devolvendo_o_mesmo(monkeypatch):
    """Sem erro nada muda: com 3 quadros e nenhuma caixa, o resultado é a lista vazia e o código de saída é o do ffmpeg."""

    class _TresQuadros(_FFmpegFalso):
        def __init__(self):
            super().__init__()
            self.stdout = _Tres()

        def wait(self, timeout=None) -> int:
            self.esperas.append(timeout)
            return 0

    class _Tres(_Saida):
        def __init__(self):
            super().__init__()
            self.restam = 3

        def read(self, n):
            if self.restam <= 0:
                return b""
            self.restam -= 1
            return b"\x00" * n

    falso = _TresQuadros()
    _ligar(monkeypatch, falso)
    monkeypatch.setattr(scan, "find_boxes", lambda img, keep_crop=False: [])
    assert scan._frames_tracks("v.mp4", 0.0, 3.0, 10.0) == []
    assert falso.esperas == [None] and not falso.terminado
