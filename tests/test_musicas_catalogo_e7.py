"""E7 (Plano E): o catálogo de músicas com licença. A medição de áudio é trocada por uma falsa; os arquivos são sintéticos."""
import json
from pathlib import Path

import pytest

from cs2clip import musicas_catalogo as mc
from cs2clip.musicas_catalogo import ErroCatalogo


def _medir(arq):
    return {"duracao_s": 150.0, "bpm": 128.0, "batidas_detectadas": 300, "energia": 0.8, "drop_s": 30.0, "impacto_db": 9.0, "emocao": 0.8}


@pytest.fixture
def pasta(tmp_path):
    p = tmp_path / "musicas"
    p.mkdir()
    (p / "catalogo.json").write_text(json.dumps({"faixas": []}), encoding="utf-8")
    return p


def _arq(tmp_path, nome="Minha Faixa (v2).mp3", tam=80_000):
    a = tmp_path / "origem" / nome
    a.parent.mkdir(exist_ok=True)
    a.write_bytes(b"x" * tam)
    return a


def test_cc0_entra_sem_credito_copia_o_arquivo_mede_e_grava_a_licenca(pasta, tmp_path):
    f = mc.adicionar_faixa(pasta, _arq(tmp_path), "cc0", "Komiku", "Action Techno", "https://example.com/faixa", medir=_medir)
    assert (pasta / "Minha Faixa (v2).mp3").is_file() and f["arquivo"] == "Minha Faixa (v2).mp3"
    assert f["exige_credito"] is False and "credito" not in f and f["licenca_chave"] == "cc0" and "CC0" in f["licenca"] and f["drop_s"] == 30.0
    assert f["intensidade"] == "alta" and f["bpm"] == 128.0
    cat = json.loads((pasta / mc.ARQUIVO_PESSOA).read_text(encoding="utf-8"))                    # P2: a faixa da pessoa vai para o catálogo DELA
    assert [x["autor"] for x in cat["faixas"]] == ["Komiku"] and cat["ajustes"] == {}
    assert json.loads((pasta / "catalogo.json").read_text(encoding="utf-8")) == {"faixas": []}    # o de fábrica não é regravado


def test_ncs_exige_credito_e_gera_a_linha_pronta_para_a_descricao(pasta, tmp_path):
    f = mc.adicionar_faixa(pasta, _arq(tmp_path, "Alan Walker - Fade.mp3"), "ncs", "Alan Walker", "Fade", "https://ncs.io/Fade", medir=_medir)
    assert f["exige_credito"] is True and f["credito"].startswith("Música: Alan Walker — Fade") and "https://ncs.io/Fade" in f["credito"] and "NCS" in f["credito"]
    assert "copie a seção de crédito" in f["aviso"]
    c = mc.credito_da_faixa(pasta, f["arquivo"])
    assert c["exige_credito"] and c["credito"] == f["credito"]
    assert mc.credito_da_faixa(pasta, "nao_existe.mp3") is None


def test_pixabay_avisa_do_content_id_e_pede_o_link(pasta, tmp_path):
    with pytest.raises(ErroCatalogo, match="link da página"):
        mc.adicionar_faixa(pasta, _arq(tmp_path), "pixabay", "Autor", "T", "", medir=_medir)
    f = mc.adicionar_faixa(pasta, _arq(tmp_path), "pixabay", "Autor", "T", "https://pixabay.com/music/x-1/", medir=_medir)
    assert "Content ID" in f["aviso"] and f["exige_credito"] is False


def test_licenca_que_o_dono_precisa_dizer_se_pede_credito(pasta, tmp_path):
    with pytest.raises(ErroCatalogo, match="pede crédito"):
        mc.adicionar_faixa(pasta, _arq(tmp_path), "yt_audio", "Autor", "T", "", medir=_medir)
    f = mc.adicionar_faixa(pasta, _arq(tmp_path, "b.mp3"), "yt_audio", "Autor", "T", "", exige_credito=True, medir=_medir)
    assert f["exige_credito"] is True and f["credito"].startswith("Música: Autor")
    g = mc.adicionar_faixa(pasta, _arq(tmp_path, "c.mp3"), "outra", "Autor", "T", "https://site.com/licenca", exige_credito=False, medir=_medir)
    assert g["exige_credito"] is False


def test_o_que_nao_entra_e_porque(pasta, tmp_path):
    a = _arq(tmp_path)
    for lic, trecho in (("comercial", "nunca"), ("", "Escolha a licença"), ("inventada", "Escolha a licença")):
        with pytest.raises(ErroCatalogo, match=trecho):
            mc.adicionar_faixa(pasta, a, lic, "Autor", "T", "https://x.com/a", medir=_medir)
    with pytest.raises(ErroCatalogo, match="autor"):
        mc.adicionar_faixa(pasta, a, "cc0", " ", "T", medir=_medir)
    with pytest.raises(ErroCatalogo, match="http"):
        mc.adicionar_faixa(pasta, a, "cc0", "Autor", "T", "ftp://x", medir=_medir)
    with pytest.raises(ErroCatalogo, match="Formato"):
        mc.adicionar_faixa(pasta, _arq(tmp_path, "nota.docx"), "cc0", "Autor", "T", medir=_medir)
    with pytest.raises(ErroCatalogo, match="pequeno demais"):
        mc.adicionar_faixa(pasta, _arq(tmp_path, "curta.mp3", tam=100), "cc0", "Autor", "T", medir=_medir)
    with pytest.raises(ErroCatalogo, match="Não achei o arquivo"):
        mc.adicionar_faixa(pasta, tmp_path / "sumiu.mp3", "cc0", "Autor", "T", medir=_medir)
    assert json.loads((pasta / "catalogo.json").read_text(encoding="utf-8"))["faixas"] == [] and not list(pasta.glob("*.mp3"))      # nada entrou, nada foi copiado


def test_repetida_arquivo_igual_e_medicao_que_falha_nao_deixam_lixo(pasta, tmp_path):
    a = _arq(tmp_path)
    mc.adicionar_faixa(pasta, a, "cc0", "Autor", "T", medir=_medir)
    with pytest.raises(ErroCatalogo, match="já está no catálogo"):
        mc.adicionar_faixa(pasta, a, "cc0", "Autor", "T", medir=_medir)
    b = _arq(tmp_path, "Outra.mp3")

    def quebra(arq):
        raise RuntimeError("ffmpeg falhou")
    with pytest.raises(ErroCatalogo, match="analisar"):
        mc.adicionar_faixa(pasta, b, "cc0", "Autor", "T", medir=quebra)
    assert not (pasta / "Outra.mp3").exists() and len(mc.ler(pasta)["faixas"]) == 1                # a cópia foi desfeita
    (pasta / "Colide.mp3").write_bytes(b"y" * 70_000)                                              # arquivo de mesmo nome que não é do catálogo
    with pytest.raises(ErroCatalogo, match="Renomeie"):
        mc.adicionar_faixa(pasta, _arq(tmp_path, "Colide.mp3"), "cc0", "Autor", "T", medir=_medir)


def test_ligar_desligar_e_listar_para_a_tela(pasta, tmp_path):
    f = mc.adicionar_faixa(pasta, _arq(tmp_path), "cc0", "Autor", "T", medir=_medir)
    [item] = mc.listar(pasta)
    assert item["ativa"] and item["tem_drop"] and item["existe"] and item["titulo"] == "T"
    mc.definir_ativa(pasta, f["arquivo"], False)
    assert mc.listar(pasta)[0]["ativa"] is False and mc.ler(pasta)["faixas"][0]["desligada"] is True
    mc.definir_ativa(pasta, f["arquivo"], True)
    assert mc.listar(pasta)[0]["ativa"] is True and "desligada" not in mc.ler(pasta)["faixas"][0]
    with pytest.raises(ErroCatalogo, match="não está no catálogo"):
        mc.definir_ativa(pasta, "x.mp3", False)
    (pasta / f["arquivo"]).unlink()                                                                  # arquivo sumiu: aparece, mas não fica ativa
    assert mc.listar(pasta)[0]["existe"] is False and mc.listar(pasta)[0]["ativa"] is False


def test_catalogo_ilegivel_nao_e_sobrescrito(pasta, tmp_path):
    (pasta / "catalogo.json").write_text("{lixo", encoding="utf-8")
    with pytest.raises(ErroCatalogo, match="ilegível"):
        mc.adicionar_faixa(pasta, _arq(tmp_path), "cc0", "Autor", "T", medir=_medir)
    assert (pasta / "catalogo.json").read_text(encoding="utf-8") == "{lixo" and not list(pasta.glob("*.mp3"))   # nem o arquivo foi copiado
    assert mc.nome_seguro("../../a:b*c?.MP3") == "abc.mp3" and Path(mc.nome_seguro("x" * 300 + ".mp3")).stem.__len__() == 90


def test_streambeats_entra_sem_credito_com_aviso_de_content_id_e_o_texto_oficial_de_credito_vale_mais(pasta, tmp_path):
    """Corrigido em 09/11: o FAQ oficial do StreamBeats libera monetizar no YouTube (o blog que dizia o contrário era secundário)."""
    f = mc.adicionar_faixa(pasta, _arq(tmp_path, "sb.mp3"), "streambeats", "Harris Heller", "Faixa", "https://www.streambeats.com/", medir=_medir)
    assert f["exige_credito"] is False and "nenhum sistema é perfeito" in f["aviso"]
    oficial = "Music provided by NoCopyrightSounds. Watch: https://youtu.be/xxx Free Download / Stream: http://ncs.io/Fade"
    g = mc.adicionar_faixa(pasta, _arq(tmp_path, "ncs.mp3"), "ncs", "Alan Walker", "Fade", "https://ncs.io/Fade", medir=_medir, credito_texto=oficial)
    assert g["credito"] == oficial                                                                  # o texto colado da fonte, sem reescrever
    h = mc.adicionar_faixa(pasta, _arq(tmp_path, "ncs2.mp3"), "ncs", "Alan Walker", "Spectre", "https://ncs.io/Spectre", medir=_medir)
    assert h["credito"].startswith("Música: Alan Walker — Spectre")                                 # sem texto oficial, monta o credito padrão

