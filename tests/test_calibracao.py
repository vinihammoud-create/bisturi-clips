"""Calibração do limiar pelos votos (cs2clip/calibracao.py)."""
from cs2clip.calibracao import sugerir_limiar


def test_poucos_votos_nao_sugere():
    s = sugerir_limiar([(50, True)] * 10)
    assert s.limiar is None and "10 votos" in s.mensagem


def test_escolhe_a_menor_nota_com_70_por_cento_aprovados():
    votos = [(n, n >= 40) for n in range(10, 70)]      # aprovados só de 40 para cima
    s = sugerir_limiar(votos)
    assert s.limiar == 28 and s.aprovacao_acima >= 0.70        # acima de 28: 30 aprovados em 42 = 71%


def test_sem_separacao_nao_inventa_limiar():
    votos = [(n, n % 2 == 0) for n in range(10, 70)]     # 50% em qualquer faixa
    assert sugerir_limiar(votos).limiar is None


def test_ferramenta_junta_votos_do_csv_e_da_interface(tmp_path, capsys):
    import importlib.util
    import json
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("calibrar", Path(__file__).parents[1] / "tools" / "calibrar.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    csv_ = tmp_path / "g.csv"
    linhas = ["vod_id,tempo,categoria,observacao,abate_certo,programa_rotulo,programa_emocao,programa_confianca,"
              "round_resultado,voto,programa_nota"]
    linhas += [f"v,00:{i:02d},,,,,,,,{'sim' if i >= 20 else 'nao'},{i * 2}" for i in range(40)]
    csv_.write_text("\n".join(linhas), encoding="utf-8")
    (tmp_path / "s" / "v").mkdir(parents=True)
    (tmp_path / "s" / "v" / "momentos.json").write_text(json.dumps({"momentos": [
        {"nota": 90, "aprovado": True}, {"nota": 5, "excluido": True}, {"nota": 50, "aprovado": None}]}), encoding="utf-8")
    assert mod.main([str(csv_), "--saida", str(tmp_path / "s")]) == 0
    out = capsys.readouterr().out
    assert "Votos: 42" in out and "nota_short =" in out
