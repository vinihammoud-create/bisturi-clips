"""Configuração comum dos testes: nenhum teste pode depender de o CS2 ou o OBS estarem abertos neste PC."""
import pytest


@pytest.fixture(autouse=True)
def _sem_jogo_aberto(monkeypatch):
    import cs2clip.checagem as checagem
    monkeypatch.setattr(checagem, "processos_ativos", lambda: set())
