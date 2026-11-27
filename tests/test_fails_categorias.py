"""C9 (Plano V7 integrado): FAILS por categoria e compilação só com os que o dono aprovou (opcional)."""
import json

from cs2clip.config import Config
from cs2clip.fails import achar_fails
from cs2clip.transcribe import Word
from test_fails import fala, voz


def test_morte_com_palavrao_e_categoria_morte_com_raiva():
    fs = achar_fails([100.0], fala(101, "meu deus que porra é essa"), voz(), [], [], 300.0)
    assert fs[0].categoria == "morte_com_raiva" and fs[0].label == "FAIL"


def test_morte_com_risada_e_categoria_morte_com_risada():
    falas = [Word(101 + i * 0.5, 101.4 + i * 0.5, "kkkk") for i in range(2)]
    fs = achar_fails([100.0], falas, voz(), [], [], 300.0)
    assert fs[0].categoria == "morte_com_risada" and fs[0].label == "RISADA"


def test_risada_longe_de_morte_e_risada_solta_e_pede_revisao():
    falas = [Word(150 + i, 150.4 + i, "kkkk") for i in range(3)]
    fs = achar_fails([], falas, voz(), [], [], 300.0)
    assert fs[0].categoria == "risada_solta"


def test_categoria_vai_para_o_momentos_json(tmp_path):
    from cs2clip.momentos_io import gravar_momentos
    fs = achar_fails([100.0], fala(101, "porra"), voz(), [], [], 300.0)
    gravar_momentos(tmp_path, fs, "v", 300.0, "7")
    assert json.loads((tmp_path / "momentos.json").read_text(encoding="utf-8"))["momentos"][0]["categoria"] == "morte_com_raiva"


def test_config_exigir_aprovacao_nasce_desligado():
    assert Config().fails.exigir_aprovacao is False


def test_compilacao_so_com_aprovados_quando_a_opcao_esta_ligada():
    from cs2clip.pipeline import fails_para_compilar
    from cs2clip.moments import Moment
    a, b, c = (Moment(0.0, 10.0, "fail", pico=10.0, score=30) for _ in range(3))
    a.pico, b.pico, c.pico = 10.0, 20.0, 30.0
    revisao = {f"{a.focus:.1f}": {"aprovado": True}, f"{b.focus:.1f}": {"aprovado": None}, f"{c.focus:.1f}": {"aprovado": False}}
    assert fails_para_compilar([a, b, c], {}, exigir_aprovacao=False) == [a, b, c]            # padrão: como sempre
    assert fails_para_compilar([a, b, c], revisao, exigir_aprovacao=True) == [a]              # só o que ganhou "Gostei"
    assert fails_para_compilar([a, b, c], {}, exigir_aprovacao=True) == []                    # ninguém revisou ainda
