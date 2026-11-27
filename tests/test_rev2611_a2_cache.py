"""R-028 (recorte que não foi gravado) e R-020 + A-006 (cache do kill feed numa gravação só)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

import cs2clip.pipeline as pl
import cs2clip.tools as tools
from cs2clip.config import Config


def _info():
    return SimpleNamespace(duration=600.0, path="x.mp4", width=1920, height=1080, fps=60.0)


class _ColetorFalso:
    def __init__(self, *a, **k):
        pass

    def feed(self, *a):
        pass

    def result(self):
        return []


def _trilha(t, fim=None):
    return SimpleNamespace(first=t, last=fim or t + 5, hits=4, box=(1, 2, 3, 4), best_crop=np.full((8, 8), 200, np.uint8))


def _varredura_falsa(monkeypatch, trilhas, vezes=None):
    monkeypatch.setattr(pl, "RoundCollector", _ColetorFalso)

    def falsa(*a, **k):
        if vezes is not None:
            vezes.append(1)
        return list(trilhas)
    monkeypatch.setattr(pl, "scan_video_fast", falsa)


# ── R-028 ────────────────────────────────────────────────────────────────────────────────────────

def test_r028_imwrite_levanta_quando_o_opencv_nao_codifica(tmp_path, monkeypatch):
    import cv2
    monkeypatch.setattr(cv2, "imencode", lambda *a, **k: (False, None))
    with pytest.raises(OSError):
        tools.imwrite(tmp_path / "a.png", np.zeros((4, 4), np.uint8))
    assert not (tmp_path / "a.png").exists() and not list(tmp_path.glob("*.tmp"))


def test_r028_imwrite_grava_por_tmp_e_troca(tmp_path):
    destino = tmp_path / "ok.png"
    tools.imwrite(destino, np.full((4, 4), 200, np.uint8))
    assert tools.imread(destino) is not None and not list(tmp_path.glob("*.tmp"))


def test_r028_recorte_nao_gravado_fica_fora_do_caixas_json(tmp_path, monkeypatch, capsys):
    cache = tmp_path / ".cache"
    _varredura_falsa(monkeypatch, [_trilha(10.0), _trilha(40.0), _trilha(70.0)])
    real = pl.imwrite

    def imwrite_que_falha_no_segundo(f, img, *a, **k):
        if f.name.startswith("00040."):
            raise OSError("disco cheio")
        real(f, img, *a, **k)
    monkeypatch.setattr(pl, "imwrite", imwrite_que_falha_no_segundo)

    saiu = pl._scan_cached(_info(), cache, Config(), False)

    assert [c["t"] for c in saiu] == [10.0, 70.0]
    for c in saiu:                                 # todo recorte citado existe de verdade
        assert (cache / "caixas" / c["crop"]).exists()
    assert "sem recorte gravado" in capsys.readouterr().out


# ── R-020 + A-006 ────────────────────────────────────────────────────────────────────────────────

def test_r020_interromper_depois_da_gravacao_nao_descarta_o_cache(tmp_path, monkeypatch):
    """A varredura gravou o cache e o processo morreu logo depois: a próxima análise reaproveita, não varre de novo."""
    cache = tmp_path / ".cache"
    _varredura_falsa(monkeypatch, [_trilha(10.0), _trilha(40.0)])
    pl._scan_cached(_info(), cache, Config(), False)
    # a interrupção entre "as duas gravações" do formato antigo: aqui só existe o caixas.json
    assert not (cache / "detector.versao").exists()
    varreu = []
    _varredura_falsa(monkeypatch, [], varreu)
    caixas = pl._scan_cached(_info(), cache, Config(), False)
    assert varreu == [] and [c["t"] for c in caixas] == [10.0, 40.0]


def test_r020_uma_gravacao_so_com_versao_e_caixas_no_mesmo_arquivo(tmp_path, monkeypatch):
    cache = tmp_path / ".cache"
    _varredura_falsa(monkeypatch, [_trilha(10.0)])
    chamadas = []
    real = pl.gravar_atomico
    monkeypatch.setattr(pl, "gravar_atomico", lambda arq, dados, *a, **k: (chamadas.append(arq.name), real(arq, dados, *a, **k))[1])
    pl._scan_cached(_info(), cache, Config(), False)
    assert chamadas == ["caixas.json"]
    dado = json.loads((cache / "caixas.json").read_text(encoding="utf-8"))
    assert dado["versao"] == pl._versao_cache(None) and len(dado["caixas"]) == 1


def test_r020_cache_do_formato_antigo_continua_valendo(tmp_path, monkeypatch):
    """Cache de dois arquivos (lista + detector.versao) que já existe no disco do dono não pode ser invalidado."""
    cache = tmp_path / ".cache"
    cache.mkdir()
    antigo = [{"t": 12.0, "fim": 17.0, "hits": 3, "box": [1, 2, 3, 4], "crop": "00012.00.png"}]
    (cache / "caixas.json").write_text(json.dumps(antigo), encoding="utf-8")
    (cache / "detector.versao").write_text(pl._versao_cache(None), encoding="utf-8")
    varreu = []
    _varredura_falsa(monkeypatch, [], varreu)
    assert pl._scan_cached(_info(), cache, Config(), False) == antigo
    assert varreu == []


def test_r020_formato_antigo_de_versao_velha_varre_de_novo(tmp_path, monkeypatch):
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "caixas.json").write_text("[]", encoding="utf-8")
    (cache / "detector.versao").write_text("1", encoding="utf-8")
    varreu = []
    _varredura_falsa(monkeypatch, [], varreu)
    pl._scan_cached(_info(), cache, Config(), False)
    assert varreu == [1]


def test_a006_cache_de_versao_velha_nunca_e_aceito_como_novo(tmp_path, monkeypatch):
    """A ressalva do Gemini: com `caixas.json` de uma versão antiga, uma varredura nova interrompida (morta antes de
    gravar) não pode deixar o cache velho passar por novo. Como versão e caixas andam juntas, o velho segue velho."""
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "caixas.json").write_text(json.dumps({"formato": 2, "versao": "1", "caixas": []}), encoding="utf-8")
    monkeypatch.setattr(pl, "gravar_atomico", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    _varredura_falsa(monkeypatch, [_trilha(10.0)])
    with pytest.raises(KeyboardInterrupt):
        pl._scan_cached(_info(), cache, Config(), False)
    monkeypatch.undo()
    varreu = []
    _varredura_falsa(monkeypatch, [], varreu)
    pl._scan_cached(_info(), cache, Config(), False)
    assert varreu == [1]                      # a versão "1" do que sobrou não vale: varre de novo


def test_r020_arquivo_novo_malformado_e_ilegivel(tmp_path, monkeypatch):
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "caixas.json").write_text(json.dumps({"formato": 2, "versao": pl._versao_cache(None)}), encoding="utf-8")
    varreu = []
    _varredura_falsa(monkeypatch, [], varreu)
    pl._scan_cached(_info(), cache, Config(), False)
    assert varreu == [1]
