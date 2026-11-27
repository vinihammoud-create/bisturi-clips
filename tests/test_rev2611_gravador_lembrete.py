"""Revisão de 26/11/2026, lote A1: R-021 (lembrete de trocar de volta para a conta principal, mesmo com erro ou Ctrl+C)."""
from __future__ import annotations

import signal
import sys

import pytest

from cs2clip import demo as demo_mod
from cs2clip import gravador_demo as gd

LEMBRETE = "troque de volta"


@pytest.fixture
def rodar(tmp_path, monkeypatch, capsys):
    """Roda `main` com a leitura da demo, a espera de conta e a gravação trocadas por falsos. `quando` diz o que acontece em cada etapa."""
    if sys.platform == "win32":                       # `main` instala um tratador de CTRL+BREAK: aqui ele não é instalado
        monkeypatch.setattr(signal, "signal", lambda *a, **k: None)

    class PartidaFalsa:
        jogador, nome, user_id, accountid, patch_version, titulo = 1, "eu", 9, 7, "1", "falsa"
        jogadores = {1: "eu"}
        abates: list = []
        rounds: list = []

    def executar(esperar=None, gravar=None, extra=()):
        monkeypatch.setattr(demo_mod, "ler_demo", lambda c, q=None: PartidaFalsa())
        monkeypatch.setattr(gd, "rounds_para_gravar", lambda *a, **k: [type("T", (), {"round": 1, "abates": 1, "duracao_s": lambda self: 10.0})()])
        monkeypatch.setattr(gd, "dependencias_reais", lambda: object())
        monkeypatch.setattr(gd, "checklist", lambda *a, **k: [])
        monkeypatch.setattr(gd, "esperar_conta", esperar or (lambda *a, **k: 1))
        monkeypatch.setattr(gd, "gravar", gravar or (lambda *a, **k: {"guarda": {"ok": True}}))
        cod = gd.main(["--demo", str(tmp_path / "x.dem"), "--config", str(tmp_path / "config.toml"), "--saida", str(tmp_path / "saida"), *extra])
        return cod, capsys.readouterr().out

    return executar


def _erro(exc):
    def f(*a, **k):
        raise exc
    return f


def test_r021_sucesso_continua_lembrando(rodar):
    cod, saida = rodar()
    assert cod == 0 and LEMBRETE in saida


@pytest.mark.parametrize("exc", [gd.ErroGravacao("HLAE não respondeu", "hlae"), KeyboardInterrupt()])
def test_r021_erro_na_gravacao_ou_ctrl_c_ainda_lembra_de_trocar_a_conta(rodar, exc):
    cod, saida = rodar(gravar=_erro(exc))
    assert cod == 1 and LEMBRETE in saida


def test_r021_ctrl_c_durante_a_gravacao_lembra_uma_vez_so(rodar):
    cod, saida = rodar(gravar=_erro(KeyboardInterrupt()))
    assert saida.count(LEMBRETE) == 1


def test_r021_sem_ter_chegado_na_conta_do_bisturi_nao_lembra(rodar):
    # a espera da conta falhou: o dono nunca esteve na conta do Bisturi, não há o que trocar de volta
    cod, saida = rodar(esperar=_erro(gd.ErroGravacao("Esperei a troca de conta e ela não aconteceu.", "conta")))
    assert cod == 1 and LEMBRETE not in saida


def test_r021_so_checar_nao_lembra(rodar):
    cod, saida = rodar(extra=("--so-checar",))
    assert cod == 0 and LEMBRETE not in saida
