"""saida/progresso.json: o andamento que a interface lê (cs2clip/progresso.py)."""
import json
import types

from cs2clip import progresso


def ler(pasta):
    return json.loads((pasta / "progresso.json").read_text(encoding="utf-8"))


def test_ciclo_basico(tmp_path):
    progresso.iniciar(tmp_path, 2)
    d = ler(tmp_path)
    assert d["formato"] == 1 and d["estado"] == "rodando" and d["video_total"] == 2 and d["etapas_feitas"] == 0
    progresso.video("a.mp4", 1)
    progresso.etapa("Varrendo")
    progresso.detalhe("3 de 10")
    progresso.etapa_feita()
    d = ler(tmp_path)
    assert (d["video"], d["video_n"], d["etapa"], d["detalhe"], d["etapas_feitas"]) == ("a.mp4", 1, "Varrendo", "3 de 10", 1)
    progresso.etapa("Outra")
    assert ler(tmp_path)["detalhe"] == ""                  # o detalhe é da etapa: some quando ela muda
    progresso.fim("pronto")
    d = ler(tmp_path)
    assert d["estado"] == "pronto" and d["etapas_feitas"] == d["etapas_estimadas"]


def test_nao_deixa_arquivo_temporario_nem_derruba_sem_pasta(tmp_path):
    progresso.iniciar(tmp_path, 1)
    progresso.etapa("x")
    assert not list(tmp_path.glob("*.tmp.*"))
    progresso.iniciar(tmp_path / "nao" / "existe", 1)     # pasta inexistente: segue sem erro
    progresso.etapa("y")
    progresso.fim("erro", "falhou")


def test_main_grava_pronto_e_com_problemas(tmp_path, monkeypatch):
    import cs2clip.pipeline as pl
    from cs2clip.__main__ import main
    v = tmp_path / "live.mp4"
    v.write_bytes(b"x")
    saida = tmp_path / "saida"
    monkeypatch.setattr(pl, "analyze", lambda p, cfg, o, pd, R: (types.SimpleNamespace(duration=10.0), "live", tmp_path / "o", [], [], None))
    monkeypatch.setattr(pl, "render_all", lambda *a, **k: ([], [], []))
    assert main([str(v), "--saida", str(saida)]) == 0
    d = ler(saida)
    assert d["estado"] == "pronto" and d["video"] == "live.mp4" and d["video_n"] == 1

    def render_com_erro(p, cfg, info, name, out, moments, words, lay, registro, R):
        registro.erro("corte 01", "ffmpeg falhou")
        return [], [], []

    monkeypatch.setattr(pl, "render_all", render_com_erro)
    assert main([str(v), "--saida", str(saida)]) == 3
    assert ler(saida)["estado"] == "com_problemas"


def test_main_sem_nenhum_video_valido_grava_erro(tmp_path, monkeypatch):
    import cs2clip.pipeline as pl
    from cs2clip.__main__ import main
    from cs2clip.scan import VideoInvalido
    v = tmp_path / "live.mp4"
    v.write_bytes(b"x")

    def quebra(*a, **k):
        raise VideoInvalido("corrompido")

    monkeypatch.setattr(pl, "analyze", quebra)
    assert main([str(v), "--saida", str(tmp_path / "s")]) == 1
    assert ler(tmp_path / "s")["estado"] == "erro"
