"""P2, item 6: o catálogo de músicas dividido em FÁBRICA (`catalogo.json`, vem com o programa, somente leitura) e PESSOA
(`catalogo_usuario.json` na pasta de dados: as faixas dela e os `ajustes` por cima das de fábrica).

A prova de que nada mudou para quem roda da pasta do projeto: `tests/fixtures/musicas_referencia_antes_p2.json` foi gravado com o
código de ANTES da divisão (commit f10e220), rodando `_resultado` sobre o `musicas/catalogo.json` real do repositório; o código novo
tem de dar exatamente o mesmo. Arquivos de música sintéticos; nenhuma medição de áudio."""
import json
import sys
from pathlib import Path

import pytest

from cs2clip import compasso, montagem
from cs2clip import musicas_catalogo as mc
from cs2clip.config import Config
from cs2clip.musicas_catalogo import ErroCatalogo

RAIZ = Path(__file__).resolve().parents[1]
CATALOGO_REAL = RAIZ / "musicas" / "catalogo.json"
REFERENCIA = Path(__file__).resolve().parent / "fixtures" / "musicas_referencia_antes_p2.json"
GRANDE = b"x" * 60_000
CHAVES = ["", "MONTAGEM_a", "MONTAGEM_b", "MONTAGEM_vod_1234500001", "MONTAGEM_partida_x", "MONTAGEM_x_limpo", "v1", "v2", "v3", "MONTAGEM_z"]
RITMOS = [4.0, 8.0, 12.0, 14.0, 18.0, 24.0, 30.0]
DURACOES = [0.0, 45.0, 70.0, 120.0, 300.0]


# ── o cenário de referência (o mesmo código rodou ANTES e roda DEPOIS) ──────────────────────────────────────────────────

def _montar(pasta: Path, variante: str) -> Path:
    """O catálogo real numa pasta temporária, com um arquivo falso por faixa. `com_flags`: o que a tela grava hoje (duas desligadas,
    uma sem licença) e uma faixa sem arquivo, escolhidas entre as de maior emoção para mexer de fato no sorteio."""
    cat = json.loads(CATALOGO_REAL.read_text(encoding="utf-8"))
    faixas = cat["faixas"]
    sem_arquivo = None
    if variante == "com_flags":
        ordem = sorted(range(len(faixas)), key=lambda i: (-float(faixas[i].get("emocao") or 0.0), faixas[i]["arquivo"]))
        faixas[ordem[0]]["desligada"] = True
        faixas[ordem[3]]["desligada"] = True
        faixas[ordem[1]]["licenca"] = ""
        sem_arquivo = faixas[ordem[2]]["arquivo"]
    pasta.mkdir(parents=True)
    (pasta / "catalogo.json").write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for f in faixas:
        if f["arquivo"] != sem_arquivo:
            (pasta / f["arquivo"]).write_bytes(GRANDE)
    return pasta


def _nome(pasta: Path, p: Path | None) -> str | None:
    return None if p is None else Path(p).relative_to(pasta).as_posix()


def _resultado(pasta: Path) -> dict:
    """Tudo o que o catálogo decide para o dono: quem é liberada, a tela inteira, o crédito, a linha do compasso e o sorteio da montagem
    (a "semente" do sorteio é a chave do vídeo: mesma chave, mesma faixa)."""
    cfg = Config()
    cfg.montagem.musica = "auto"
    nomes = [f["arquivo"] for f in json.loads((pasta / "catalogo.json").read_text(encoding="utf-8"))["faixas"]]
    r: dict = {"liberadas": [_nome(pasta, p) for p in montagem.musicas_liberadas(pasta)], "listar": mc.listar(pasta)}
    r["no_sorteio"] = [f["arquivo"] for f in r["listar"] if f["entra_no_sorteio"]]
    r["credito"] = {n: mc.credito_da_faixa(pasta, n) for n in nomes}
    r["entrada"] = {n: compasso.entrada_do_catalogo(pasta / n) for n in nomes}
    primeira = montagem.escolher_musica_por_ritmo(12.0, pasta, "", 70.0)[0]
    evitar = () if primeira is None else (primeira.name,)
    r["ritmo"] = [[c, k, d, list(ev), _nome(pasta, faixa), drop]
                  for c in RITMOS for k in CHAVES for d in DURACOES for ev in ((), evitar)
                  for faixa, drop in [montagem.escolher_musica_por_ritmo(c, pasta, k, d, evitar=ev)]]
    r["auto"] = {k: _nome(pasta, montagem.escolher_musica(cfg, k, pasta)) for k in CHAVES}
    r["montagem"] = [_nome(pasta, montagem.musica_da_montagem(cfg, f"MONTAGEM_v{i % 6}", 12.0 + i, pasta, 70.0 + 20 * i)) for i in range(9)]
    return json.loads(json.dumps(r, ensure_ascii=False))


@pytest.fixture(scope="module")
def referencia() -> dict:
    return json.loads(REFERENCIA.read_text(encoding="utf-8"))


# a referência foi gravada no Windows (ordem de caminhos sem diferenciar maiúsculas, igual ao código antigo): fora dele a ordem muda
SO_WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="a referência de antes da P2 foi gravada com a ordem de caminhos do Windows")


@SO_WINDOWS
@pytest.mark.parametrize("variante", ["real", "com_flags"])
def test_com_o_catalogo_real_a_lista_as_flags_e_o_sorteio_sao_os_de_antes(tmp_path, referencia, variante):
    agora = _resultado(_montar(tmp_path / "musicas", variante))
    antes = referencia[variante]
    for chave in ("liberadas", "listar", "no_sorteio", "credito", "entrada"):
        assert agora[chave] == antes[chave], chave
    assert len(antes["liberadas"]) >= (30 if variante == "real" else 27) and not (tmp_path / "musicas" / mc.ARQUIVO_PESSOA).exists()


@SO_WINDOWS
@pytest.mark.parametrize("variante", ["real", "com_flags"])
def test_o_sorteio_da_montagem_devolve_a_mesma_faixa_para_as_mesmas_entradas(tmp_path, referencia, variante):
    agora = _resultado(_montar(tmp_path / "musicas", variante))
    antes = referencia[variante]
    assert agora["ritmo"] == antes["ritmo"] and agora["auto"] == antes["auto"] and agora["montagem"] == antes["montagem"]
    assert len({linha[4] for linha in antes["ritmo"]}) >= 3                          # o cenário de fato varia a escolha


def test_sem_catalogo_da_pessoa_o_efetivo_e_a_fabrica_como_esta(tmp_path):
    for variante in ("real", "com_flags"):
        pasta = _montar(tmp_path / variante, variante)
        fabrica = json.loads((pasta / "catalogo.json").read_text(encoding="utf-8"))
        assert mc.catalogo_efetivo(pasta, pasta)["faixas"] == fabrica["faixas"]
        assert mc.catalogo_efetivo(pasta, tmp_path / "pessoa_vazia")["faixas"] == fabrica["faixas"]


# ── mescla, toggle, nome repetido, busca, ilegível, sem licença ────────────────────────────────────────────────────────

def _medir(arq):
    return {"duracao_s": 150.0, "bpm": 128.0, "batidas_detectadas": 300, "energia": 0.8, "drop_s": 30.0, "impacto_db": 9.0, "emocao": 0.8}


def _ok(x):
    assert x is not None
    return x


def _faixa(arquivo, **extra):
    return {"arquivo": arquivo, "autor": "A", "licenca": "CC0 1.0", "bpm": 120.0, "duracao_s": 150.0, "drop_s": 30.0, "emocao": 0.5, **extra}


@pytest.fixture
def pastas(tmp_path):
    """Modo instalado: a fábrica (programa) e a pessoa (dados) em pastas diferentes."""
    fab, pes = tmp_path / "programa" / "musicas", tmp_path / "dados" / "musicas"
    fab.mkdir(parents=True)
    pes.mkdir(parents=True)
    (fab / "catalogo.json").write_text(json.dumps({"fonte": "f", "faixas": [_faixa("a.mp3"), _faixa("b.mp3", desligada=True, emocao=0.9)]}), encoding="utf-8")
    for n in ("a.mp3", "b.mp3"):
        (fab / n).write_bytes(GRANDE)
    return fab, pes


def _gravar_pessoa(pes: Path, dados) -> None:
    (pes / mc.ARQUIVO_PESSOA).write_text(dados if isinstance(dados, str) else json.dumps(dados), encoding="utf-8")


def _lido_pessoa(pes: Path) -> dict:
    return json.loads((pes / mc.ARQUIVO_PESSOA).read_text(encoding="utf-8"))


def test_mescla_faixas_de_fabrica_com_ajustes_por_cima_e_depois_as_da_pessoa(pastas):
    fab, pes = pastas
    (pes / "c.mp3").write_bytes(GRANDE)
    _gravar_pessoa(pes, {"faixas": [_faixa("c.mp3", autor="Pessoa")], "ajustes": {
        "a.mp3": {"downbeat_s": 1.5, "downbeat_fonte": "manual", "arquivo": "../fora.mp3"},     # `arquivo` nunca é trocado por ajuste
        "b.mp3": {"desligada": False}, "sumiu.mp3": {"desligada": True}}})
    cat = mc.catalogo_efetivo(fab, pes)
    assert cat["fonte"] == "f" and [f["arquivo"] for f in cat["faixas"]] == ["a.mp3", "b.mp3", "c.mp3"]
    a, b, c = cat["faixas"]
    assert a["downbeat_s"] == 1.5 and a["downbeat_fonte"] == "manual" and a["autor"] == "A"
    assert not b["desligada"] and c["autor"] == "Pessoa"
    assert json.loads((fab / "catalogo.json").read_text(encoding="utf-8"))["faixas"][1]["desligada"] is True        # a fábrica não mudou
    assert montagem.musicas_liberadas(fab, pes) == sorted([fab / "a.mp3", fab / "b.mp3", pes / "c.mp3"])
    assert _ok(compasso.entrada_do_catalogo(fab / "a.mp3", fab, pes))["downbeat_s"] == 1.5
    assert _ok(mc.credito_da_faixa(fab, "c.mp3", pes))["autor"] == "Pessoa"


def test_faixa_da_pessoa_com_o_nome_de_uma_de_fabrica_vale_a_da_pessoa_no_lugar_dela(pastas):
    """Uma versão nova do programa pode trazer de fábrica um nome que a pessoa já tinha: vale a linha da pessoa (é o arquivo dela que toca)."""
    fab, pes = pastas
    (pes / "a.mp3").write_bytes(GRANDE)
    _gravar_pessoa(pes, {"faixas": [_faixa("a.mp3", autor="Pessoa")]})
    cat = mc.catalogo_efetivo(fab, pes)
    assert [(f["arquivo"], f["autor"]) for f in cat["faixas"]] == [("a.mp3", "Pessoa"), ("b.mp3", "A")]
    assert mc.caminho_da_faixa("a.mp3", fab, pes) == pes / "a.mp3"


def test_ligar_e_desligar_faixa_de_fabrica_grava_nos_ajustes_e_nunca_na_fabrica(pastas):
    fab, pes = pastas
    antes = (fab / "catalogo.json").read_bytes()
    f = mc.definir_ativa(fab, "a.mp3", False, pes)
    assert f["desligada"] is True and _lido_pessoa(pes)["ajustes"] == {"a.mp3": {"desligada": True}}
    assert [x["ativa"] for x in mc.listar(fab, pes)] == [False, False]
    assert mc.definir_ativa(fab, "b.mp3", True, pes)["desligada"] is False                     # de fábrica desligada: a pessoa liga
    assert _lido_pessoa(pes)["ajustes"] == {"a.mp3": {"desligada": True}, "b.mp3": {"desligada": False}}
    assert [p.name for p in montagem.musicas_liberadas(fab, pes)] == ["b.mp3"]
    mc.definir_ativa(fab, "a.mp3", True, pes)                                                    # desligar e religar: volta ao estado de fábrica
    mc.definir_ativa(fab, "b.mp3", False, pes)
    assert _lido_pessoa(pes)["ajustes"] == {} and (fab / "catalogo.json").read_bytes() == antes
    assert mc.catalogo_efetivo(fab, pes)["faixas"] == json.loads(antes)["faixas"]
    with pytest.raises(ErroCatalogo, match="não está no catálogo"):
        mc.definir_ativa(fab, "x.mp3", False, pes)


def test_no_modo_portatil_o_toggle_cria_o_catalogo_da_pessoa_ao_lado_e_nao_toca_no_de_fabrica(tmp_path):
    pasta = _montar(tmp_path / "musicas", "real")
    antes = (pasta / "catalogo.json").read_bytes()
    nome = json.loads(antes)["faixas"][0]["arquivo"]
    mc.definir_ativa(pasta, nome, False)
    assert (pasta / "catalogo.json").read_bytes() == antes and _lido_pessoa(pasta)["ajustes"] == {nome: {"desligada": True}}
    assert nome not in [p.name for p in montagem.musicas_liberadas(pasta)]
    mc.definir_ativa(pasta, nome, True)
    assert (pasta / "catalogo.json").read_bytes() == antes and _lido_pessoa(pasta)["ajustes"] == {}
    mc.definir_ativa(pasta, nome, True)                                                          # já no estado de fábrica: nada muda
    assert _lido_pessoa(pasta)["ajustes"] == {}


def test_faixa_da_pessoa_liga_e_desliga_na_propria_linha(pastas, tmp_path):
    fab, pes = pastas
    origem = tmp_path / "origem" / "minha.mp3"
    origem.parent.mkdir()
    origem.write_bytes(b"y" * 80_000)
    f = mc.adicionar_faixa(fab, origem, "cc0", "Eu", "Minha", medir=_medir, pasta_pessoa=pes)
    assert (pes / "minha.mp3").is_file() and not (fab / "minha.mp3").exists() and f["arquivo"] == "minha.mp3"
    assert [x["arquivo"] for x in _lido_pessoa(pes)["faixas"]] == ["minha.mp3"]
    assert "minha.mp3" not in (fab / "catalogo.json").read_text(encoding="utf-8")
    mc.definir_ativa(fab, "minha.mp3", False, pes)
    assert _lido_pessoa(pes)["faixas"][0]["desligada"] is True and _lido_pessoa(pes).get("ajustes", {}) == {}
    mc.definir_ativa(fab, "minha.mp3", True, pes)
    assert "desligada" not in _lido_pessoa(pes)["faixas"][0]


def test_nome_repetido_e_recusado_nos_dois_catalogos(pastas, tmp_path):
    fab, pes = pastas
    (tmp_path / "origem").mkdir()
    de_fabrica = tmp_path / "origem" / "a.mp3"                                                   # mesmo nome de uma faixa de fábrica
    de_fabrica.write_bytes(b"y" * 80_000)
    with pytest.raises(ErroCatalogo, match="já está no catálogo"):
        mc.adicionar_faixa(fab, de_fabrica, "cc0", "Eu", medir=_medir, pasta_pessoa=pes)
    assert not (pes / "a.mp3").exists() and not (pes / mc.ARQUIVO_PESSOA).exists()
    nova = tmp_path / "origem" / "nova.mp3"
    nova.write_bytes(b"y" * 80_000)
    mc.adicionar_faixa(fab, nova, "cc0", "Eu", medir=_medir, pasta_pessoa=pes)
    with pytest.raises(ErroCatalogo, match="já está no catálogo"):                              # e de uma faixa da própria pessoa
        mc.adicionar_faixa(fab, nova, "cc0", "Eu", medir=_medir, pasta_pessoa=pes)
    assert [x["arquivo"] for x in _lido_pessoa(pes)["faixas"]] == ["nova.mp3"]


def test_busca_do_arquivo_olha_a_pessoa_depois_a_fabrica_e_so_aceita_nome_simples(pastas):
    fab, pes = pastas
    assert mc.caminho_da_faixa("a.mp3", fab, pes) == fab / "a.mp3"
    (pes / "a.mp3").write_bytes(GRANDE)
    assert mc.caminho_da_faixa("a.mp3", fab, pes) == pes / "a.mp3"
    (pes / "so_pessoa.mp3").write_bytes(GRANDE)
    assert mc.caminho_da_faixa("so_pessoa.mp3", fab, pes) == pes / "so_pessoa.mp3"
    (fab.parent / "fora.mp3").write_bytes(GRANDE)
    for ruim in ("nao_tem.mp3", "", ".", "..", "../fora.mp3", "sub/x.mp3", "..\\fora.mp3", str(fab / "a.mp3")):
        assert mc.caminho_da_faixa(ruim, fab, pes) is None, ruim


@pytest.mark.parametrize("conteudo", ["{lixo", "[]", '{"faixas": "x"}', '{"faixas": [], "ajustes": []}', '{"faixas": [1]}'])
def test_catalogo_da_pessoa_ilegivel_nao_derruba_e_a_fabrica_continua_valendo(pastas, tmp_path, capsys, conteudo):
    fab, pes = pastas
    _gravar_pessoa(pes, conteudo)
    assert mc.catalogo_efetivo(fab, pes)["faixas"] == json.loads((fab / "catalogo.json").read_text(encoding="utf-8"))["faixas"]
    assert mc.ARQUIVO_PESSOA in capsys.readouterr().out                                         # avisa
    assert [p.name for p in montagem.musicas_liberadas(fab, pes)] == ["a.mp3"]
    assert [x["arquivo"] for x in mc.listar(fab, pes)] == ["a.mp3", "b.mp3"]
    origem = tmp_path / "origem.mp3"
    origem.write_bytes(b"y" * 80_000)
    with pytest.raises(ErroCatalogo, match="ilegível"):                                         # mas ninguém grava por cima dele
        mc.definir_ativa(fab, "a.mp3", False, pes)
    with pytest.raises(ErroCatalogo, match="ilegível"):
        mc.adicionar_faixa(fab, origem, "cc0", "Eu", medir=_medir, pasta_pessoa=pes)
    assert (pes / mc.ARQUIVO_PESSOA).read_text(encoding="utf-8") == conteudo and not (pes / "origem.mp3").exists()


def test_faixa_da_pessoa_sem_licenca_nao_entra_no_sorteio(pastas):
    fab, pes = pastas
    for n in ("sem.mp3", "com.mp3"):
        (pes / n).write_bytes(GRANDE)
    _gravar_pessoa(pes, {"faixas": [_faixa("sem.mp3", licenca="  ", emocao=0.99), _faixa("com.mp3", emocao=0.7)]})
    assert montagem.musicas_liberadas(fab, pes) == sorted([fab / "a.mp3", pes / "com.mp3"])
    tela = {x["arquivo"]: x for x in mc.listar(fab, pes)}
    assert tela["sem.mp3"]["ativa"] is False and tela["sem.mp3"]["entra_no_sorteio"] is False and tela["com.mp3"]["posicao"] == 1
    assert {montagem.escolher_musica_por_ritmo(12.0, fab, f"MONTAGEM_{i}", 70.0, pasta_pessoa=pes)[0] for i in range(60)} == {pes / "com.mp3"}


def test_sem_catalogo_de_fabrica_so_entra_faixa_da_pessoa_com_licenca(tmp_path, capsys):
    """A-012 continua: sem catálogo nenhum, nenhuma música; apagar o de fábrica não libera arquivo solto."""
    fab, pes = tmp_path / "f", tmp_path / "p"
    fab.mkdir()
    pes.mkdir()
    (fab / "solta.mp3").write_bytes(GRANDE)
    assert montagem.musicas_liberadas(fab, pes) == [] and "catalogo.json não existe" in capsys.readouterr().out
    (pes / "minha.mp3").write_bytes(GRANDE)
    _gravar_pessoa(pes, {"faixas": [_faixa("minha.mp3")]})
    assert montagem.musicas_liberadas(fab, pes) == [pes / "minha.mp3"]
    assert montagem.escolher_musica_por_ritmo(12.0, fab, "k", pasta_pessoa=pes)[0] == pes / "minha.mp3"


def test_o_padrao_das_funcoes_usa_as_pastas_de_fabrica_e_da_pessoa(pastas, monkeypatch):
    """Quem não passa a pasta da pessoa (pipeline, render, montagem) e usa a pasta de fábrica padrão cai na pasta de dados; uma pasta
    qualquer (os testes, o modo portátil) é ela mesma a da pessoa."""
    fab, pes = pastas
    monkeypatch.setattr(mc, "PASTA_FABRICA", fab)
    monkeypatch.setattr(mc, "PASTA_PESSOA", pes)
    monkeypatch.delenv("BISTURI_HISTORICO_MUSICA", raising=False)
    (pes / "c.mp3").write_bytes(GRANDE)
    _gravar_pessoa(pes, {"faixas": [_faixa("c.mp3", emocao=0.95, autor="Pessoa")], "ajustes": {"a.mp3": {"phrase_bars": 8}}})
    assert mc.pasta_da_pessoa(fab) == pes and mc.pasta_da_pessoa(pes) == pes and mc.pasta_da_pessoa(fab.parent) == fab.parent
    assert mc.catalogo_efetivo()["faixas"][-1]["arquivo"] == "c.mp3"
    assert pes / "c.mp3" in montagem.musicas_liberadas(fab)
    assert _ok(mc.credito_da_faixa(fab, "c.mp3"))["autor"] == "Pessoa"
    assert _ok(compasso.entrada_do_catalogo(pes / "c.mp3"))["autor"] == "Pessoa"                      # a música da pessoa acha a linha dela
    assert _ok(compasso.entrada_do_catalogo(fab / "a.mp3"))["phrase_bars"] == 8                       # e a de fábrica, com o ajuste
    assert montagem.arquivo_historico(fab) == pes / "historico.json"                             # o rodízio mora nos dados
    cfg = Config()
    cfg.montagem.musica = "auto"
    assert montagem.musica_da_montagem(cfg, "MONTAGEM_v1", 12.0, fab, 70.0) == pes / "c.mp3"
    assert json.loads((pes / "historico.json").read_text(encoding="utf-8"))["por_chave"] == {"MONTAGEM_v1": "c.mp3"}
    assert not (fab / "historico.json").exists() and montagem.musica_da_montagem(cfg, "MONTAGEM_v1", 12.0, fab, 70.0) == pes / "c.mp3"
    cfg.montagem.musica = "c.mp3"                                                                # escolha pelo nome: pessoa, depois fábrica
    assert montagem.escolher_musica(cfg, "k", fab) == pes / "c.mp3"


def test_baixador_e_licencas_olham_so_o_catalogo_de_fabrica(tmp_path, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("baixar_musicas_cc0_fp", RAIZ / "tools" / "baixar_musicas_cc0.py")
    assert spec is not None and spec.loader is not None
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    pasta = tmp_path / "musicas"                                                                 # modo portátil: a pessoa mora na mesma pasta
    pasta.mkdir()
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": [{"arquivo": "Spec Ops.mp3", "autor": "a", "licenca": "CC0 1.0"}]}), encoding="utf-8")
    (pasta / "minha.mp3").write_bytes(GRANDE)
    _gravar_pessoa(pasta, {"faixas": [_faixa("minha.mp3")], "ajustes": {"Spec Ops.mp3": {"desligada": True}}})
    pessoa_antes = (pasta / mc.ARQUIVO_PESSOA).read_bytes()

    def rede(cam, destino):
        Path(destino).write_bytes(GRANDE)
        return Path(destino)
    b.baixar_musicas(pasta, baixar=rede, medir=lambda arq: {"duracao_s": 100.0, "bpm": 120.0})
    fabrica = json.loads((pasta / "catalogo.json").read_text(encoding="utf-8"))
    assert "minha.mp3" not in {f["arquivo"] for f in fabrica["faixas"]} and "desligada" not in fabrica["faixas"][0]
    assert "minha.mp3" not in capsys.readouterr().out                                            # é da pessoa: não é "arquivo solto"
    texto = (pasta / "licencas.txt").read_text(encoding="utf-8")
    assert texto == b.texto_de_licencas(fabrica) and "minha.mp3" not in texto
    assert (pasta / mc.ARQUIVO_PESSOA).read_bytes() == pessoa_antes


def test_rotas_da_tela_gravam_na_pessoa_e_tocam_o_arquivo_da_pessoa(tmp_path):
    from test_ui_dados_seguranca import cliente, criar_projeto, token_headers
    criar_projeto(tmp_path)
    pasta = tmp_path / "musicas"
    pasta.mkdir()
    (pasta / "a.mp3").write_bytes(b"x" * 70_000)
    (pasta / "catalogo.json").write_text(json.dumps({"faixas": [_faixa("a.mp3")]}), encoding="utf-8")
    antes = (pasta / "catalogo.json").read_bytes()
    with cliente(tmp_path) as c:
        h = token_headers(c)
        assert c.post("/api/musicas/ativar", json={"arquivo": "a.mp3", "ativa": False}, headers=h).status_code == 200
        assert (pasta / "catalogo.json").read_bytes() == antes and _lido_pessoa(pasta)["ajustes"] == {"a.mp3": {"desligada": True}}
        assert c.get("/api/musicas", headers=token_headers(c)).json()["resumo"]["ativas"] == 0
        assert c.get("/api/musicas/audio/a.mp3").status_code == 200
