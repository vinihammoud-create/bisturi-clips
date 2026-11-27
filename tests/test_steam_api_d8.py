"""D8 (Plano D, opcional): avisar de partidas novas pela API do Steam, sem abrir o CS2. Sem rede (respostas falsas); os segredos nunca vazam."""
import io
import json
import urllib.error
from pathlib import Path

import pytest

from cs2clip import sharecode, steam_api
from cs2clip.steam_api import ErroSteamApi

CHAVE = "0123456789ABCDEF0123456789ABCDEF"
AUTH = "ABCD-" + "12345-WXYZ"      # valor falso montado em pedaços (o detector de segredos lê o literal inteiro como chave)
STEAMID = "76561198071376839"
C0 = sharecode.codificar(3230642215713767580, 3230647599455273103, 55788)
C1 = sharecode.codificar(3230642215713767581, 3230647599455273104, 55789)
C2 = sharecode.codificar(3230642215713767582, 3230647599455273105, 55790)


class _Resp(io.BytesIO):
    def __init__(self, corpo: dict | str, status=200):
        super().__init__((corpo if isinstance(corpo, str) else json.dumps(corpo)).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _seg(tmp_path):
    arq = steam_api.gravar_segredos(CHAVE, STEAMID, AUTH, C0, arquivo=tmp_path / "dados" / "segredos_steam.json")
    return arq, steam_api.ler_segredos(arq)


def test_segredos_sao_conferidos_gravados_fora_do_projeto_publico_e_lidos_de_volta(tmp_path):
    arq, seg = _seg(tmp_path)
    assert arq.is_file() and seg == {"api_key": CHAVE, "steamid": STEAMID, "codigo_autenticacao": AUTH, "ultimo_codigo": C0}
    for args, trecho in (((CHAVE[:-1], STEAMID, AUTH, C0), "32"), ((CHAVE, "123", AUTH, C0), "17"), ((CHAVE, STEAMID, "abc", C0), "AAAA"),
                         ((CHAVE, STEAMID, AUTH, "CSGO-curto"), "partilha"), (("Z" * 32, STEAMID, AUTH, C0), "32")):
        with pytest.raises(ErroSteamApi, match=trecho):
            steam_api.gravar_segredos(*args, arquivo=tmp_path / "x.json")
    assert not (tmp_path / "x.json").exists()
    with pytest.raises(ErroSteamApi, match="Ainda não configurei"):
        steam_api.ler_segredos(tmp_path / "nao_existe.json")
    (tmp_path / "ruim.json").write_text(json.dumps({"api_key": CHAVE}), encoding="utf-8")
    with pytest.raises(ErroSteamApi, match="sem 'steamid'"):
        steam_api.ler_segredos(tmp_path / "ruim.json")


def test_o_arquivo_de_segredos_fica_na_pasta_dados_que_o_git_ignora():
    ignorado = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "dados/" in ignorado and steam_api.ARQUIVO.parent.name == "dados"


def test_proximo_codigo_traduz_cada_resposta_da_api(tmp_path):
    _, seg = _seg(tmp_path)
    pedidos = []

    def abrir_com(resp):
        def abrir(req, timeout=0):
            pedidos.append(req.full_url)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return abrir
    assert steam_api.proximo_codigo(seg, C0, abrir_com(_Resp({"result": {"nextcode": C1}}))) == (C1, "novo")
    assert steam_api.proximo_codigo(seg, C0, abrir_com(_Resp({"result": {"nextcode": "n/a"}}))) == (None, "sem_novo")
    assert steam_api.proximo_codigo(seg, C0, abrir_com(_Resp("", status=202))) == (None, "sem_novo")
    http = lambda c: urllib.error.HTTPError("u", c, "x", {}, None)  # noqa: E731
    assert steam_api.proximo_codigo(seg, C0, abrir_com(http(412))) == (None, "conhecido_invalido")
    assert steam_api.proximo_codigo(seg, C0, abrir_com(http(202))) == (None, "sem_novo")
    with pytest.raises(ErroSteamApi, match="recusou a chave"):
        steam_api.proximo_codigo(seg, C0, abrir_com(http(403)))
    with pytest.raises(ErroSteamApi, match="erro 500"):
        steam_api.proximo_codigo(seg, C0, abrir_com(http(500)))
    with pytest.raises(ErroSteamApi, match="formato"):
        steam_api.proximo_codigo(seg, C0, abrir_com(_Resp("não é json")))
    with pytest.raises(ErroSteamApi, match="desconhecido"):
        steam_api.proximo_codigo(seg, C0, abrir_com(_Resp({"result": {"nextcode": "CSGO-xxxxx"}})))
    assert pedidos[0].startswith("https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1?") and f"knowncode={C0}" in pedidos[0]
    assert f"steamid={STEAMID}" in pedidos[0] and "steamidkey=" in pedidos[0]


def test_a_chave_e_o_codigo_de_autenticacao_nunca_vazam_nas_mensagens_de_erro(tmp_path):
    _, seg = _seg(tmp_path)

    def cai(req, timeout=0):
        raise OSError(f"falha ao conectar em {req.full_url}")
    with pytest.raises(ErroSteamApi) as e:
        steam_api.proximo_codigo(seg, C0, cai)
    assert CHAVE not in str(e.value) and AUTH not in str(e.value) and "***" in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__ is True        # o erro original (com a URL e a chave) não aparece no traceback
    # CONTROLE: o limpador troca os dois segredos e os parâmetros da URL
    sujo = f"https://x/?key={CHAVE}&steamidkey={AUTH}&knowncode={C0} {CHAVE} {AUTH}"
    limpo = steam_api._limpar(sujo, seg)
    assert CHAVE not in limpo and AUTH not in limpo and C0 in limpo


def test_cadeia_anda_ate_acabar_e_procurar_guarda_o_ultimo_codigo_como_novo_ponto_de_partida(tmp_path):
    arq, seg = _seg(tmp_path)
    respostas = iter([_Resp({"result": {"nextcode": C1}}), _Resp({"result": {"nextcode": C2}}), _Resp({"result": {"nextcode": "n/a"}})])
    chamados = []

    def abrir(req, timeout=0):
        chamados.append(req.full_url)
        return next(respostas)
    saidas = []
    novos = steam_api.procurar(arq, abrir=abrir, imprimir=saidas.append)
    assert novos == [C1, C2] and f"knowncode={C0}" in chamados[0] and f"knowncode={C1}" in chamados[1] and f"knowncode={C2}" in chamados[2]
    assert steam_api.ler_segredos(arq)["ultimo_codigo"] == C2                                  # o próximo `procurar` começa daqui
    assert any(C1 in s for s in saidas) and not any(CHAVE in s or AUTH in s for s in saidas)
    # nada novo: o ponto de partida não muda
    saidas.clear()
    assert steam_api.procurar(arq, abrir=lambda req, timeout=0: _Resp({"result": {"nextcode": "n/a"}}), imprimir=saidas.append) == []
    assert steam_api.ler_segredos(arq)["ultimo_codigo"] == C2 and "Nenhuma partida nova" in saidas[0]
    # código inicial recusado pelo Steam: mensagem com o que fazer
    with pytest.raises(ErroSteamApi, match="não aceitou"):
        steam_api.procurar(arq, abrir=lambda req, timeout=0: (_ for _ in ()).throw(urllib.error.HTTPError("u", 412, "x", {}, None)))


def test_cadeia_tem_limite_para_nao_ficar_em_laco(tmp_path):
    _, seg = _seg(tmp_path)
    n = []

    def sempre_novo(req, timeout=0):
        n.append(1)
        return _Resp({"result": {"nextcode": sharecode.codificar(1000 + len(n), 5, 7)}})
    novos, situacao = steam_api.novas_partidas(seg, sempre_novo, limite=5)
    assert len(novos) == 5 and len(n) == 5 and situacao == "novo"
