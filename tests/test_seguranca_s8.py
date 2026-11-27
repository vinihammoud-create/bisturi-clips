"""S8 (revisão de segurança independente de 13/11): três furos CONFIRMADOS na prática e corrigidos.

1. Caminho de rede na caixa única: `/\\srv\\x`, `\\/srv/x` e `file:///\\\\srv\\x` passavam pelo filtro que só olhava os 2 primeiros caracteres, e o Windows os trata como rede.
   Antes de recusar o programa chamava `.exists()`, e abrir um caminho de rede pode entregar o hash de login do Windows a quem montou o servidor.
2. `.cache.`, `.cache ` e `.cache::$INDEX_ALLOCATION` abrem a pasta interna `.cache` no Windows e furavam o bloqueio dela nas rotas /media.
3. `montagem.musica` na tela Configurar aceitava `//srv/x/a.mp3` (só a barra invertida era proibida).
"""
from pathlib import Path

import pytest

from cs2clip import entrada as ent
from ui import seguranca
from ui.configuracao import CAMPOS, ConfiguracaoProjeto, ErroConfig

from test_ui_dados_seguranca import cliente, criar_projeto

VARIANTES_DE_REDE = [r"\\srv\share\a.dem", "//srv/share/a.dem", r"/\srv\share\a.dem", r"\/srv/share/a.dem", r"file:///\\srv\share\a.dem", r"\\?\UNC\srv\share\a.dem",
                     r'"\\srv\share\a.dem"', "file://srv/share/a.dem"]


@pytest.fixture
def nao_toca_na_rede(monkeypatch):
    """Falha o teste se QUALQUER leitura do disco (stat/exists/is_file/is_dir/iterdir) chegar perto de um caminho de rede."""
    original_stat = Path.stat
    original_iterdir = Path.iterdir

    def stat(self, *a, **k):
        if self.drive.startswith("\\\\"):
            pytest.fail(f"tocou no caminho de rede: {self}")
        return original_stat(self, *a, **k)

    def iterdir(self):
        if self.drive.startswith("\\\\"):
            pytest.fail(f"listou o caminho de rede: {self}")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "iterdir", iterdir)


@pytest.mark.parametrize("texto", VARIANTES_DE_REDE)
def test_caixa_unica_recusa_toda_grafia_de_caminho_de_rede_sem_tocar_na_rede(texto, nao_toca_na_rede):
    r = ent.reconhecer(texto)
    assert r.tipo == "recusado" and "rede" in r.mensagem.lower() and not r.aceita


def test_caminho_local_continua_funcionando_o_controle(tmp_path):
    demo = tmp_path / "match730_003849321203751714819_0599033075_201.dem"
    demo.write_bytes(ent.CABECALHO_CS2 + b"corpo")
    assert ent.reconhecer(str(demo)).tipo == "demo"
    assert ent.reconhecer(str(demo).replace("\\", "/")).tipo == "demo"                                          # barra normal num caminho de disco local é vida normal
    assert "Não achei esse arquivo" in ent.reconhecer(str(tmp_path / "nao_existe.dem")).mensagem


def _saida_com_cache(tmp_path):
    saida = tmp_path / "saida"
    (saida / "v1" / ".cache").mkdir(parents=True)
    (saida / "v1" / "cortes").mkdir()
    (saida / "v1" / ".cache" / "segredo.json").write_text("{}", encoding="utf-8")
    (saida / "v1" / "cortes" / "ok.json").write_text("{}", encoding="utf-8")
    return saida


@pytest.mark.parametrize("relativo", [".cache/segredo.json", ".CACHE/segredo.json", ".cache./segredo.json", ".cache /segredo.json", ".cache../segredo.json",
                                      ".cache::$INDEX_ALLOCATION/segredo.json", "cortes/ok.json:x.json", "cortes./ok.json", "cortes /ok.json", "cortes/ok.json."])
def test_media_recusa_as_grafias_do_windows_que_abrem_a_pasta_cache_e_os_fluxos_alternativos(tmp_path, relativo):
    saida = _saida_com_cache(tmp_path)
    with pytest.raises(PermissionError):
        seguranca.caminho_midia_seguro(saida, "v1", relativo)
    with pytest.raises(PermissionError):
        seguranca.caminho_biblioteca_seguro(saida, "v1/" + relativo)


def test_media_continua_servindo_o_que_e_normal_o_controle(tmp_path):
    saida = _saida_com_cache(tmp_path)
    assert seguranca.caminho_midia_seguro(saida, "v1", "cortes/ok.json").name == "ok.json"
    assert seguranca.caminho_biblioteca_seguro(saida, "v1/cortes/ok.json").name == "ok.json"


def test_rota_media_nao_entrega_o_cache_por_grafia_alternativa(tmp_path):
    criar_projeto(tmp_path)
    (tmp_path / "saida" / "v1" / ".cache").mkdir(exist_ok=True)
    (tmp_path / "saida" / "v1" / ".cache" / "segredo.json").write_text('{"segredo": 1}', encoding="utf-8")
    with cliente(tmp_path) as c:
        for caminho in ("/media/v1/.cache/segredo.json", "/media/v1/.cache./segredo.json", "/media/v1/.cache%20/segredo.json", "/media/v1/.cache::$INDEX_ALLOCATION/segredo.json"):
            r = c.get(caminho)
            assert r.status_code in (400, 403, 404) and "segredo" not in r.text, caminho


@pytest.mark.parametrize("valor", ["//srv/x/a.mp3", r"\\srv\x\a.mp3", "C:/musicas/a.mp3", "../a.mp3", "pasta/a.mp3", "a.mp3:fluxo"])
def test_configurar_so_aceita_nome_de_arquivo_para_a_musica_da_montagem(valor):
    campo = next(c for c in CAMPOS if c.secao == "montagem" and c.chave == "musica")
    with pytest.raises(ErroConfig):
        ConfiguracaoProjeto.validar(campo, valor)
    for ok in ("auto", "", "Komiku - 15 - Victory.mp3"):                                                         # CONTROLE: o que o dono usa de verdade continua valendo
        assert ConfiguracaoProjeto.validar(campo, ok) == ok
