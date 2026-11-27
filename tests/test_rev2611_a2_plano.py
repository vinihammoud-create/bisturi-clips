"""R-024 + A-010: o EditPlan não é mais estreito que o config.toml, e um momento estranho não leva os planos dos outros."""
from __future__ import annotations

import json

import pytest

import cs2clip.pipeline as pl
from cs2clip.config import FAIXAS, Config, _validar
from cs2clip.moments import Event, Moment
from cs2clip.plano import criar_plano_momento


def _momento(idx=1, ini=10.0, fim=20.0):
    m = Moment(ini, fim, "abate", kills=[Event(ini + 2, "kill", [])], label="DOUBLE KILL")
    m.idx = idx
    m.round_n = 5
    return m


def _com(secao, campo, valor):
    cfg = Config()
    setattr(getattr(cfg, secao), campo, valor)
    _validar(cfg)                       # o config ACEITA
    return cfg


# os casos da prova do R-024 (todos aceitos pelo config e todos recusados pelo EditPlan estreito)
@pytest.mark.parametrize("secao,campo,valor,formato", [
    ("montagem", "transicao", 1.5, "16:9"),
    ("qualidade", "lufs", -30.0, "16:9"),
    ("qualidade", "lufs", -6.0, "16:9"),
    ("montagem", "volume_musica", -30.0, "16:9"),
    ("shorts", "largura", 4000, "9:16"),
    ("shorts", "altura", 4096, "9:16"),
])
def test_r024_config_valido_gera_plano(secao, campo, valor, formato):
    cfg = _com(secao, campo, valor)
    plano = criar_plano_momento(_momento(), cfg, formato=formato)
    assert plano.trechos


def _extremos(campo_faixa):
    secao, campo, minimo, maximo, incluso = campo_faixa
    return [(secao, campo, v) for v in (minimo, maximo) if incluso or v == maximo]


@pytest.mark.parametrize("secao,campo,valor", [e for f in FAIXAS if (f[0], f[1]) in (
    ("qualidade", "lufs"), ("montagem", "volume_musica"), ("montagem", "transicao")) for e in _extremos(f)])
def test_a010_os_extremos_do_config_valem_no_plano(secao, campo, valor):
    """O EditPlan usa as MESMAS faixas de config.FAIXAS: o valor-limite do config não deixa o kit sem plano."""
    cfg = _com(secao, campo, valor)
    assert criar_plano_momento(_momento(), cfg).audio is not None


def test_a010_plano_continua_validando_fora_das_faixas_do_config():
    from pydantic import ValidationError

    from cs2clip.plano import PlanoAudio
    with pytest.raises(ValidationError):
        PlanoAudio(lufs=5.0)               # acima de 0 LUFS não existe
    with pytest.raises(ValidationError):
        PlanoAudio(volume_musica_db=-500.0)
    with pytest.raises(ValidationError):
        PlanoAudio(lufs=float("nan"))


def test_r024_um_momento_ruim_nao_leva_os_planos_dos_outros(tmp_path, capsys):
    ruim = _momento(idx=2)
    ruim.segmentos = [(30.0, 30.0)]        # trecho de duração zero: o EditPlan recusa só este
    momentos = [_momento(idx=1), ruim, _momento(idx=3)]
    gravados = pl._gravar_planos(momentos, Config(), tmp_path / "planos", lambda m: f"m{m.idx:02d}")
    assert gravados == 2
    assert sorted(p.name for p in (tmp_path / "planos").glob("*.json")) == ["m01.json", "m03.json"]
    assert json.loads((tmp_path / "planos" / "m01.json").read_text(encoding="utf-8"))["id_momento"].startswith("01_")
    assert "plano do momento 2" in capsys.readouterr().out


def test_r024_prova_do_relatorio_agora_sai_com_planos(tmp_path):
    """O cenário exato do R-024: montagem.transicao = 1.5 e 3 momentos -> os 3 planos saem."""
    cfg = _com("montagem", "transicao", 1.5)
    momentos = [_momento(idx=i) for i in (1, 2, 3)]
    assert pl._gravar_planos(momentos, cfg, tmp_path / "planos", lambda m: f"{m.idx:02d}") == 3
