"""E4: checagem do ambiente e trava de GPU (cs2clip/checagem.py)."""
import json
import types

from cs2clip import checagem
from cs2clip.checagem import Item


def test_gpu_ocupada_reconhece_cs2_e_obs_sem_importar_maiuscula():
    assert checagem.gpu_ocupada({"explorer.exe", "chrome.exe"}) == []
    assert checagem.gpu_ocupada({"CS2.exe"}) == ["o CS2"]
    assert checagem.gpu_ocupada({"obs64.exe", "cs2.exe"}) == ["o CS2", "o OBS"]


def test_aviso_de_trava_fala_a_acao_e_a_saida():
    t = checagem.aviso_trava(["o CS2"])
    assert "o CS2 está aberto" in t and "PRÉVIA" in t and "--forcar-gpu" in t
    assert "estão aberto" in checagem.aviso_trava(["o CS2", "o OBS"])


def test_texto_marca_erro_aviso_e_ok():
    itens = [Item("A", True, "bem"), Item("B", False, "quebrou", True, "faça isto"), Item("C", False, "meh", False)]
    t = checagem.texto(itens)
    assert "[OK" in t and "[ERRO" in t and "[AVISO" in t and "→ faça isto" in t
    assert "1 problema(s) grave(s)" in t and checagem.tem_problema_grave(itens)
    assert not checagem.tem_problema_grave([itens[0], itens[2]])
    assert "Tudo certo" in checagem.texto([itens[0], itens[2]])


def test_checar_roda_e_aponta_o_jogo_aberto(tmp_path):
    itens = {i.nome: i for i in checagem.checar(tmp_path, tmp_path, ativos={"cs2.exe"})}
    assert not itens["CS2 e OBS fechados"].ok and "o CS2" in itens["CS2 e OBS fechados"].detalhe
    assert not itens["CS2 e OBS fechados"].grave                      # é aviso, não impede o trabalho
    assert itens["Python"].ok and "Espaço em disco" in itens


# ── a trava dentro do main ────────────────────────────────────────────────────────────────────

def _prepara(tmp_path, monkeypatch, ativos):
    import cs2clip.pipeline as pl
    monkeypatch.setattr(checagem, "processos_ativos", lambda: set(ativos))
    v = tmp_path / "live.mp4"
    v.write_bytes(b"x")
    vistos = {}

    def analyze(p, cfg, o, pd, R):
        vistos["previa"] = cfg.modo_previa
        return types.SimpleNamespace(duration=10.0), "live", tmp_path / "o", [], [], None

    def render_all(*a, **k):
        vistos["render"] = True
        return [], [], []

    monkeypatch.setattr(pl, "analyze", analyze)
    monkeypatch.setattr(pl, "render_all", render_all)
    return v, vistos


def test_com_cs2_aberto_so_roda_a_previa_e_nao_renderiza(tmp_path, monkeypatch, capsys):
    from cs2clip.__main__ import main
    v, vistos = _prepara(tmp_path, monkeypatch, {"cs2.exe"})
    assert main([str(v), "--saida", str(tmp_path / "s")]) == 0
    assert vistos == {"previa": True}                                  # análise sim, render não
    assert "PRÉVIA" in capsys.readouterr().out
    assert json.loads((tmp_path / "s" / "progresso.json").read_text(encoding="utf-8"))["modo"] == "previa"


def test_forcar_gpu_ignora_a_trava(tmp_path, monkeypatch):
    from cs2clip.__main__ import main
    v, vistos = _prepara(tmp_path, monkeypatch, {"cs2.exe", "obs64.exe"})
    assert main([str(v), "--saida", str(tmp_path / "s"), "--forcar-gpu"]) == 0
    assert vistos == {"previa": False, "render": True}
    assert json.loads((tmp_path / "s" / "progresso.json").read_text(encoding="utf-8"))["modo"] == "completo"


def test_sem_jogo_aberto_roda_completo(tmp_path, monkeypatch):
    from cs2clip.__main__ import main
    v, vistos = _prepara(tmp_path, monkeypatch, set())
    assert main([str(v), "--saida", str(tmp_path / "s")]) == 0
    assert vistos == {"previa": False, "render": True}


def test_checar_pela_linha_de_comando_nao_processa_nada(tmp_path, monkeypatch, capsys):
    from cs2clip.__main__ import main
    codigo = main(["--checar", "--saida", str(tmp_path / "s")])
    out = capsys.readouterr().out
    assert codigo in (0, 1) and "Checagem do ambiente" in out
    assert not (tmp_path / "s").exists()                               # --checar não cria nem toca em saida


def test_previa_pula_o_whisper_quando_nao_ha_falas_guardadas(tmp_path, monkeypatch, capsys):
    """analyze() de verdade: modo_previa + sem cache de falas = nunca chama o Whisper."""
    import numpy as np
    import cs2clip.pipeline as pl
    from test_r1_pipeline import _analise_falsa, _video

    def rodar(previa, pasta):
        chamou = []
        cfg = _analise_falsa(monkeypatch, tmp_path / pasta, tem_som=True)
        cfg.chat.usar = False
        cfg.modo_previa = previa
        monkeypatch.setattr(pl.aud, "load_audio", lambda p: np.zeros(16000, np.float32))
        monkeypatch.setattr(pl.aud, "speech_segments", lambda a: [[1.0, 2.0]])
        monkeypatch.setattr(pl.aud, "voice_curve", lambda a, s: np.zeros(int(120 / pl.aud.HOP), np.float32))
        monkeypatch.setattr(pl, "transcribe_full", lambda *a: (chamou.append(1), [])[1])
        monkeypatch.setattr(pl, "liberar_modelo", lambda: None)
        monkeypatch.setattr(pl, "detect_layout", lambda *a: pl.Layout(None, None, 0.0))
        monkeypatch.setattr(pl, "preview", lambda *a: None)
        pl.analyze(_video(tmp_path / pasta / "in", "live.mp4"), cfg, tmp_path / pasta / "saida",
                   tmp_path / pasta / "j")
        return chamou

    assert rodar(True, "a") == []
    assert "prévia" in capsys.readouterr().out
    assert not (tmp_path / "a" / "saida" / "live" / ".cache" / "falas.json").exists()
    assert rodar(False, "b") == [1]   # espelho: prova que o falso é alcançável
