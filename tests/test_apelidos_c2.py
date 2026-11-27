"""C2 (14/11/2026): apelido de cada conta Steam. É só um RÓTULO para as mensagens: a trava compara accountids e nunca olha o apelido.

SteamIDs FALSOS (accountid 111111111 = conta principal, 222222222 = conta do Bisturi). Nada lê o config.toml do dono.
"""
import pytest

from cs2clip import baixar_demo as bd
from cs2clip import steam_local as sl
from cs2clip.baixar_demo import ErroDownload
from cs2clip.config import Config, load_config
from ui.configuracao import CAMPOS, CAMPOS_POR_CHAVE, ConfiguracaoProjeto, ErroConfig

PRINCIPAL = "76561198071376839"
BISTURI = "76561198182487950"
ID_PRINCIPAL, ID_BISTURI = 111111111, 222222222
ESTRANHOS = ["", "   ", "<b>negrito</b>", "'; DROP TABLE contas; --", "Smurf do Bisturi", "a" * 40, "ñandú 🎮", "gravacao_demo.conta_steamid", "{x}\\n"]


# ── a configuração ───────────────────────────────────────────────────────────────────────────────

def test_os_dois_apelidos_comecam_vazios():
    g = Config().gravacao_demo
    assert g.conta_apelido == "" and g.conta_principal_apelido == ""


def _ler(tmp_path, texto):
    p = tmp_path / "config.toml"
    p.write_text(texto, encoding="utf-8")
    return load_config(p)


def test_apelido_valido_e_lido_do_arquivo(tmp_path):
    g = _ler(tmp_path, '[gravacao_demo]\nconta_apelido = "Smurf do Bisturi"\nconta_principal_apelido = "Conta de jogo"\n').gravacao_demo
    assert g.conta_apelido == "Smurf do Bisturi" and g.conta_principal_apelido == "Conta de jogo"
    assert _ler(tmp_path, '[gravacao_demo]\nconta_apelido = "' + "a" * 40 + '"\n').gravacao_demo.conta_apelido == "a" * 40      # CONTROLE: 40 passa


@pytest.mark.parametrize("campo", ["conta_apelido", "conta_principal_apelido"])
@pytest.mark.parametrize("valor,trecho", [("a" * 41, "40"), ("linha1\\nlinha2", "quebra"), ("tab\\taqui", "controle"), ("nulo\\u0000", "controle"),
                                         ("sep\\u2028linha", "quebra"), ("del\\u007f", "controle")])
def test_apelido_invalido_e_recusado_na_leitura(tmp_path, campo, valor, trecho):
    with pytest.raises(ValueError, match=trecho):
        _ler(tmp_path, f'[gravacao_demo]\n{campo} = "{valor}"\n')


# ── a tela Configurar ───────────────────────────────────────────────────────────────────────────

def test_a_tela_tem_os_dois_campos_de_apelido_junto_de_cada_conta():
    chaves = [c.chave for c in CAMPOS if c.secao == "gravacao_demo"]
    assert chaves[:4] == ["conta_steamid", "conta_apelido", "conta_principal_id", "conta_principal_apelido"]
    for chave in ("conta_apelido", "conta_principal_apelido"):
        campo = CAMPOS_POR_CHAVE[("gravacao_demo", chave)]
        assert campo.tipo == "texto" and campo.comprimento == 40 and campo.rotulo == "Como você chama esta conta" and not campo.so_digitos


@pytest.mark.parametrize("chave", ["conta_apelido", "conta_principal_apelido"])
def test_a_tela_valida_o_apelido(chave):
    campo = CAMPOS_POR_CHAVE[("gravacao_demo", chave)]
    for ok in ("", "Smurf do Bisturi", "<b>x</b>", "a" * 40):
        assert ConfiguracaoProjeto.validar(campo, ok) == ok
    for ruim in ("a" * 41, "duas\nlinhas", "com\rretorno", "tab\taqui", "sep linha", "del\x7f", 5, None):
        with pytest.raises(ErroConfig):
            ConfiguracaoProjeto.validar(campo, ruim)


def test_a_tela_salva_e_le_o_apelido_como_texto(tmp_path):
    cfg = ConfiguracaoProjeto(tmp_path)
    cfg.salvar({"gravacao_demo.conta_apelido": "<b>Smurf</b> do Bisturi", "gravacao_demo.conta_principal_apelido": "Principal"})
    lido = {(c["secao"], c["chave"]): c["valor"] for c in cfg.ler()["campos"]}
    assert lido[("gravacao_demo", "conta_apelido")] == "<b>Smurf</b> do Bisturi" and lido[("gravacao_demo", "conta_principal_apelido")] == "Principal"
    assert load_config(tmp_path / "config.toml").gravacao_demo.conta_apelido == "<b>Smurf</b> do Bisturi"


# ── o rótulo (função única) ──────────────────────────────────────────────────────────────────────

def test_rotulo_sem_apelido_e_o_generico_e_com_apelido_vai_entre_parenteses():
    assert sl.rotulo_conta("bisturi") == "a conta do Bisturi" and sl.rotulo_conta("principal") == "a sua conta principal"
    assert sl.rotulo_conta("bisturi", "Smurf do Bisturi") == "a conta do Bisturi (Smurf do Bisturi)"
    assert sl.rotulo_conta("principal", "Conta de jogo", prep="na") == "na sua conta principal (Conta de jogo)"
    assert sl.rotulo_conta("bisturi", prep="A") == "A conta do Bisturi" and sl.rotulo_conta("principal", prep="à") == "à sua conta principal"
    assert sl.rotulo_conta("principal", destaque=True) == "a sua conta PRINCIPAL"
    for vazio in ("", "   ", "\n\t "):
        assert sl.rotulo_conta("bisturi", vazio) == "a conta do Bisturi"                    # apelido em branco = sem apelido
    assert sl.rotulo_conta("bisturi", "  Smurf  ") == "a conta do Bisturi (Smurf)"
    assert sl.rotulo_conta("bisturi", "uma\nlinha\x00so") == "a conta do Bisturi (uma linha so)"       # nunca quebra a linha da mensagem
    with pytest.raises(ValueError):
        sl.rotulo_conta("outra")


# ── as mensagens usam o rótulo ───────────────────────────────────────────────────────────────────

def test_mensagens_da_trava_trazem_o_apelido_quando_ha_e_o_generico_quando_nao_ha():
    casos = [("", PRINCIPAL, ID_BISTURI), (PRINCIPAL, PRINCIPAL, ID_PRINCIPAL), (BISTURI, PRINCIPAL, 0), (BISTURI, PRINCIPAL, ID_PRINCIPAL), (BISTURI, PRINCIPAL, 123456)]
    for segunda, principal, logada in casos:
        with pytest.raises(sl.ContaErrada) as sem:
            sl.exigir_conta_do_bisturi(segunda, principal, logada=logada)
        with pytest.raises(sl.ContaErrada) as com:
            sl.exigir_conta_do_bisturi(segunda, principal, logada=logada, apelido_bisturi="Smurf do Bisturi", apelido_principal="Conta de jogo")
        assert "(" not in str(sem.value) and "Smurf do Bisturi" not in str(sem.value)                                   # sem apelido: só a frase genérica
        assert "Smurf do Bisturi" in str(com.value)
        assert ("Conta de jogo" in str(com.value)) == (principal != "" and "principal" in str(com.value).lower())


def test_exemplo_do_pedido_abra_o_steam_na_conta_do_bisturi_com_o_apelido():
    with pytest.raises(sl.ContaErrada, match=r"entre na conta do Bisturi \(Smurf do Bisturi\) e clique em Continuar"):
        sl.exigir_conta_do_bisturi(BISTURI, PRINCIPAL, logada=0, apelido_bisturi="Smurf do Bisturi")


@pytest.mark.parametrize("apelido_b", ESTRANHOS)
@pytest.mark.parametrize("apelido_p", ESTRANHOS[:4])
def test_o_apelido_nunca_muda_o_resultado_da_trava(apelido_b, apelido_p):
    """O apelido é só rótulo: a mesma conta passa, a mesma recusa continua recusa (mesma classe e mesma marca de configuração), com qualquer apelido."""
    def resultado(segunda, principal, logada, **ap):
        try:
            return ("ok", sl.exigir_conta_do_bisturi(segunda, principal, logada=logada, **ap))
        except sl.ContaErrada as e:
            return ("recusou", e.de_configuracao)
    for caso in [(BISTURI, PRINCIPAL, ID_BISTURI), (BISTURI, PRINCIPAL, ID_PRINCIPAL), (BISTURI, PRINCIPAL, 0), (BISTURI, PRINCIPAL, 123456),
                 ("", PRINCIPAL, ID_BISTURI), (PRINCIPAL, PRINCIPAL, ID_PRINCIPAL), (BISTURI, "", ID_PRINCIPAL), (BISTURI, "", ID_BISTURI)]:
        assert resultado(*caso, apelido_bisturi=apelido_b, apelido_principal=apelido_p) == resultado(*caso)


def test_apelido_igual_ao_da_outra_conta_ou_ao_numero_da_outra_conta_nao_engana_a_trava():
    """Um apelido que parece o SteamID da outra conta (ou o da conta certa) não faz a principal passar."""
    with pytest.raises(sl.ContaErrada, match="PRINCIPAL"):
        sl.exigir_conta_do_bisturi(BISTURI, PRINCIPAL, logada=ID_PRINCIPAL, apelido_bisturi=PRINCIPAL, apelido_principal=BISTURI)
    assert sl.exigir_conta_do_bisturi(BISTURI, PRINCIPAL, logada=ID_BISTURI, apelido_bisturi=PRINCIPAL, apelido_principal=BISTURI) == ID_BISTURI


# ── o download por código ────────────────────────────────────────────────────────────────────────

def test_download_sem_conta_configurada_cita_o_apelido_se_houver():
    with pytest.raises(ErroDownload) as e:
        bd.exigir_conta_para_baixar(lambda: ID_BISTURI, lambda: ("", "", "Smurf do Bisturi", ""))
    assert str(e.value) == "Antes de baixar, diga qual é a conta do Bisturi (Smurf do Bisturi) em Configurar → Contas do Steam."
    with pytest.raises(ErroDownload) as e2:
        bd.exigir_conta_para_baixar(lambda: ID_BISTURI, lambda: ("", ""))                      # sem apelido (e a forma antiga de 2 valores continua valendo)
    assert str(e2.value) == "Antes de baixar, diga qual é a conta do Bisturi em Configurar → Contas do Steam."


def test_download_na_conta_errada_cita_o_apelido_e_a_trava_nao_muda():
    contas = lambda: (BISTURI, PRINCIPAL, "Smurf do Bisturi", "Conta de jogo")                  # noqa: E731
    with pytest.raises(ErroDownload) as e:
        bd.exigir_conta_para_baixar(lambda: ID_PRINCIPAL, contas)
    texto = str(e.value)
    assert "Conta de jogo" in texto and "Smurf do Bisturi" in texto and "Continuar" not in texto and "tente de novo" in texto
    bd.exigir_conta_para_baixar(lambda: ID_BISTURI, contas)                                     # CONTROLE: a conta certa segue


def test_contas_do_config_devolve_os_ids_e_os_apelidos(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text(f'[gravacao_demo]\nconta_steamid = "{BISTURI}"\nconta_principal_id = "{PRINCIPAL}"\nconta_apelido = "Smurf"\nconta_principal_apelido = "Jogo"\n', encoding="utf-8")
    monkeypatch.setattr(bd, "CONFIG", p)
    assert bd.contas_do_config() == (BISTURI, PRINCIPAL, "Smurf", "Jogo")
    p.write_text("[gravacao_demo]\nconta_apelido = \"x\"\nfps = 1000\n", encoding="utf-8")       # config inválido: recusa dizendo que o problema é o config
    with pytest.raises(ErroDownload, match="config.toml tem um erro"):                            # (até a conferência de 14/11 devolvia tudo vazio e a trava culpava a conta)
        bd.contas_do_config()
    assert bd.apelidos_do_config() == ("", "")                                                    # quem só consulta continua sem estourar


def test_listar_ultimas_na_conta_do_bisturi_cita_os_apelidos(tmp_path):
    exe = tmp_path / "boiler-writter.exe"
    exe.write_bytes(b"MZ")
    chamadas: list = []
    for apelidos, esperado in ((("", ""), False), (("Smurf do Bisturi", "Conta de jogo"), True)):
        with pytest.raises(ErroDownload) as e:
            bd.listar_ultimas(contas=lambda: (BISTURI, PRINCIPAL), boiler=exe, pasta_tmp=tmp_path, jogo_aberto=lambda: [], conta=lambda: ID_BISTURI, rodar=lambda *a, **k: chamadas.append(a),
                              conta_do_bisturi=lambda: ID_BISTURI, permitida=lambda: True, apelidos=lambda apelidos=apelidos: apelidos)
        texto = str(e.value)
        assert ("na conta do Bisturi (Smurf do Bisturi)" in texto) is esperado and ("a sua conta principal (Conta de jogo)" in texto) is esperado
        assert "conta do Bisturi" in texto and "a sua conta principal" in texto
    assert chamadas == []                                                                       # a recusa vem antes do boiler, com ou sem apelido
