"""D5 (Plano D): baixar a demo da partida como os sites e programas fazem.

Caminho: o `boiler-writter` (já instalado em `bin\\boiler\\`, versão e SHA-256 fixos) pergunta ao Game Coordinator da Valve, pela sessão do Steam
que JÁ está aberta (sem senha), a lista das 8 últimas partidas da conta logada, ou uma partida por código de partilha (de qualquer conta);
o arquivo que ele grava traz a URL da demo; o Bisturi baixa o `.dem.bz2`, descompacta e guarda em `demos\\`.

Condições do auditor (`docs/audit/AUDITORIA_PARTE_D.md`): argumentos do boiler só numéricos; saída dentro do projeto; host da demo
`replayNNN.valve.net` e caminho `/730/<números>.dem.bz2` (a URL é http puro, então a forma é validada); limite de tamanho descompactado
(bomba de descompressão); cabeçalho `PBDEMS2` conferido; nome do arquivo só com números; nada de matar processo pelo nome.
Precisa do Steam aberto e logado e do CS2 FECHADO (o boiler abre o CS2 por segundos em segundo plano).
"""
from __future__ import annotations

import bz2
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from collections.abc import Callable

from . import gc_protobuf, sharecode, steam_local, terceiros
from .caminhos import PASTA_DADOS
from .tools import gravar_atomico, substituir_arquivo
from .tools import sem_janela

PASTA_DEMOS = PASTA_DADOS / "demos"       # dado da pessoa: na pasta de dados (no modo portátil é a raiz do projeto, como sempre)
URL_VALIDA = re.compile(r"^http://replay\d{1,4}\.valve\.net/730/\d{21}_\d{10}\.dem\.bz2$")
LIMITE_COMPRIMIDO = 600 * 1024 * 1024
LIMITE_DESCOMPACTADO = 2 * 1024 * 1024 * 1024
CABECALHO_DEMO = b"PBDEMS2\x00"
CODIGOS_DO_BOILER = {
    0: "ok", 1: "argumentos inválidos", 2: "o Steam precisa ser reiniciado", 3: "o Steam não está aberto",
    4: "sem conta logada no Steam, ou o Game Coordinator está ocupado", 5: "o CS2 não está instalado",
    6: "erro de conexão com o Game Coordinator", 7: "o Game Coordinator demorou demais", 8: "partida não encontrada (expirou, mais de ~30 dias)",
}


class ErroDownload(Exception):
    """Falha ao achar ou baixar a demo (mensagem pronta, em português). `expirou` = a Valve já apagou a demo (não adianta tentar de novo)."""

    def __init__(self, mensagem: str, expirou: bool = False):
        super().__init__(mensagem)
        self.expirou = expirou


def validar_url(url: str) -> str:
    """A URL tem a forma exata das demos da Valve; qualquer outra coisa é recusada (o Bisturi nunca baixa de outro lugar)."""
    if not isinstance(url, str) or not URL_VALIDA.fullmatch(url.strip()):
        raise ErroDownload("O endereço da demo não tem a forma esperada da Valve (http://replayNNN.valve.net/730/<números>.dem.bz2); não baixei.")
    return url.strip()


# ── boiler-writter ────────────────────────────────────────────────────────────────────────────────

def chamar_boiler(exe: Path, pasta_tmp: Path, numeros: tuple[int, int, int] | None = None, tempo_maximo: float = 60.0,
                  rodar=None, dormir=time.sleep) -> bytes:
    """Roda o boiler (só com argumentos numéricos) e devolve os bytes do protobuf. Código 4 ("já conectado"/GC ocupado): espera 6 s e repete uma vez."""
    rodar = subprocess.run if rodar is None else rodar   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    saida = pasta_tmp / f"gc_{int(time.time() * 1000)}.bin"
    args = [str(exe), str(saida)]
    if numeros is not None:
        args += [str(int(n)) for n in numeros]
    try:
        for tentativa in (1, 2):
            try:
                r = rodar(args, capture_output=True, timeout=tempo_maximo, **sem_janela())
            except subprocess.TimeoutExpired as e:
                raise ErroDownload("O boiler-writter demorou demais (o Steam está logado e o CS2 fechado?).") from e
            if r.returncode == 0:
                dados = saida.read_bytes() if saida.exists() else b""
                if not dados:
                    raise ErroDownload("O Game Coordinator respondeu vazio.")
                return dados
            if r.returncode == 4 and tentativa == 1:
                dormir(6.0)
                continue
            motivo = CODIGOS_DO_BOILER.get(r.returncode, f"código {r.returncode}")
            if r.returncode == 8 and numeros is None:
                # sem código de partilha o boiler lista as partidas da conta LOGADA: "não encontrada" aqui é conta sem partidas recentes
                raise ErroDownload("O Game Coordinator não achou partidas recentes nesta conta. Confira se o Steam está na conta que JOGOU "
                                   "(a principal, não a do Bisturi) e se ela jogou competitivo nos últimos ~30 dias.")
            raise ErroDownload(f"O Game Coordinator recusou: {motivo}.", expirou=(r.returncode == 8))
        raise ErroDownload("O Game Coordinator não respondeu.")
    finally:
        saida.unlink(missing_ok=True)


def _exigir_pre_requisitos(boiler: Path | None, jogo_aberto: Callable[[], list[str]], conta: Callable[[], int]) -> Path:
    if boiler is None:
        raise ErroDownload(f"O boiler-writter não está instalado ({terceiros.dica_instalar('boiler')}).")
    aberto = [x for x in jogo_aberto() if x.lower() == "cs2.exe"]
    if aberto:
        raise ErroDownload("Feche o CS2 antes de baixar (o boiler o abre por segundos em segundo plano).")
    if not conta():
        raise ErroDownload("O Steam está fechado ou sem conta. Abra o Steam e entre na conta.")
    return boiler


CONFIG = PASTA_DADOS / "config.toml"        # o config do dono (fora do git); os testes trocam este caminho


def _gravacao_e_erro():
    """(seção `[gravacao_demo]` do config.toml, "") ou (None, motivo) quando o arquivo existe mas não vale. Sem arquivo: os padrões. Nada estoura."""
    try:
        from .config import load_config
        return load_config(CONFIG).gravacao_demo, ""
    except Exception as e:  # noqa: BLE001
        return None, (str(e).strip().splitlines() or [type(e).__name__])[0][:200]


def _gravacao_do_config():
    """A seção `[gravacao_demo]` do config.toml, ou None se o arquivo não vale (nada estoura: quem só consulta usa os padrões)."""
    return _gravacao_e_erro()[0]


def lista_permitida() -> bool:
    """`gravacao_demo.permitir_lista_da_principal` do config.toml (padrão False)."""
    g = _gravacao_do_config()
    return bool(g.permitir_lista_da_principal) if g else False


MENSAGEM_LISTA_DESLIGADA = ("A lista das últimas partidas está desligada: ela precisaria da sua conta principal logada no Steam. Use o código de partilha: no CS2, "
                            "Assistir → Suas partidas → copiar código, e cole na caixa da tela Início. (Para religar: gravacao_demo.permitir_lista_da_principal = true.)")


def conta_do_bisturi() -> int:
    """O accountid da conta do Bisturi, do `config.toml`; 0 se não estiver configurado."""
    g = _gravacao_do_config()
    try:
        return steam_local.accountid_do_steamid64(g.conta_steamid) if g and g.conta_steamid else 0
    except Exception:  # noqa: BLE001
        return 0


def contas_do_config() -> tuple[str, ...]:
    """(SteamID64 da conta do Bisturi, SteamID64 da conta principal, apelido da do Bisturi, apelido da principal) do `config.toml`; vazio onde não está configurado."""
    g, erro = _gravacao_e_erro()
    if g is None:       # config ilegível: a trava continua fechada, mas a mensagem fala do config (antes mandava "dizer qual é a conta" já configurada)
        raise ErroDownload(f"O config.toml tem um erro e o Bisturi não conseguiu ler as contas ({erro}). Abra Configurar e corrija; "
                           "se a tela não abrir, apague a linha errada do arquivo.")
    return str(g.conta_steamid or ""), str(g.conta_principal_id or ""), str(g.conta_apelido or ""), str(g.conta_principal_apelido or "")


def apelidos_do_config() -> tuple[str, str]:
    """(apelido da conta do Bisturi, apelido da conta principal) do `config.toml`; vazio se não tem."""
    g = _gravacao_do_config()
    return (str(g.conta_apelido or ""), str(g.conta_principal_apelido or "")) if g else ("", "")


def exigir_conta_para_baixar(conta: Callable[[], int], contas: Callable[[], tuple[str, ...]]) -> None:
    """Trava do download por código (A1, 13/11): o boiler SÓ roda com o Steam na conta do Bisturi, nunca na principal (regra do dono: nem por engano). Antes a checagem só via que havia
    ALGUMA conta logada, e a receita baixava antes de pedir a troca. C5 (14/11): sem a conta do Bisturi configurada também RECUSA (antes valia qualquer conta logada,
    inclusive a principal de quem nunca configurou nada); a lista das últimas partidas (`listar_ultimas`) tem trava própria e não passa por aqui."""
    segunda, principal, apelido_b, apelido_p = (tuple(contas()) + ("", "", "", ""))[:4]       # aceita só os dois SteamIDs (sem apelido)
    if not segunda:
        raise ErroDownload(f"Antes de baixar, diga qual é {steam_local.rotulo_conta('bisturi', apelido_b)} em Configurar → Contas do Steam.")
    try:
        steam_local.exigir_conta_do_bisturi(segunda, principal, conta(), apelido_bisturi=apelido_b, apelido_principal=apelido_p)
    except steam_local.ContaErrada as e:
        raise ErroDownload(str(e).replace(" e clique em Continuar", " e tente de novo")
                           + f" (O download por código só roda {steam_local.rotulo_conta('bisturi', apelido_b, 'na')}.)") from e


def listar_ultimas(boiler: Path | None = None, pasta_tmp: Path | None = None, jogo_aberto=None,
                   conta=None, rodar=None, dormir=time.sleep,
                   conta_do_bisturi=None, permitida=None, apelidos=None, contas=None) -> list[dict]:
    """As 8 últimas partidas da conta logada no Steam (a conta precisa ser a que jogou; CS2 fechado). Desligado por padrão (decisão do dono, 08/11).
    B2 (14/11): recusa se a conta principal não está configurada. Tudo é lido na hora da chamada (nada preso na definição)."""
    jogo_aberto = jogo_aberto or steam_local.jogo_aberto        # lidos na hora da chamada (ver `resolver_por_codigo`)
    conta = conta or steam_local.conta_logada
    rodar = rodar or subprocess.run
    if not (permitida or lista_permitida)():
        raise ErroDownload(MENSAGEM_LISTA_DESLIGADA)
    segunda, principal = (tuple((contas or contas_do_config)()) + ("", ""))[:2]
    if not principal:
        raise ErroDownload(f"Falta dizer qual é {steam_local.rotulo_conta('principal', '')} (a que jogou): sem ela o Bisturi não tem como conferir "
                           "a conta antes de listar. Diga em Configurar → Contas do Steam.")
    exe = _exigir_pre_requisitos(boiler or terceiros.caminho_boiler(), jogo_aberto, conta)
    if conta_do_bisturi is not None:
        de_bisturi = conta_do_bisturi()
    else:
        de_bisturi = steam_local.accountid_do_steamid64(segunda) if segunda else 0
    if de_bisturi and conta() == de_bisturi:
        apelido_b, apelido_p = (apelidos or apelidos_do_config)()
        raise ErroDownload(f"O Steam está {steam_local.rotulo_conta('bisturi', apelido_b, 'na')}, que nunca jogou: ela não tem partidas. Para LISTAR as últimas partidas, "
                           f"troque o Steam para {steam_local.rotulo_conta('principal', apelido_p)}, a que jogou. Para baixar uma partida específica não precisa trocar: "
                           "use o código de partilha.")
    de_principal = steam_local.accountid_do_steamid64(principal)
    if de_principal and conta() != de_principal:          # G-013: uma TERCEIRA conta logada também não lista (só a que jogou)
        _, apelido_p = (apelidos or apelidos_do_config)()
        raise ErroDownload(f"O Steam não está {steam_local.rotulo_conta('principal', apelido_p, 'na')}, a que jogou. Para LISTAR as últimas partidas, entre "
                           f"{steam_local.rotulo_conta('principal', apelido_p, 'na')} (CS2 fechado). Para baixar uma partida específica não precisa trocar: "
                           "use o código de partilha.")
    dados = chamar_boiler(exe, pasta_tmp or PASTA_DEMOS / ".tmp", None, rodar=rodar, dormir=dormir)
    try:
        return gc_protobuf.ler_lista_de_partidas(dados)
    except ValueError as e:
        raise ErroDownload(f"Não consegui ler a resposta do Game Coordinator ({e}).") from e


def resolver_por_codigo(codigo: str, boiler: Path | None = None, pasta_tmp: Path | None = None, jogo_aberto=None,
                        conta=None, rodar=None, dormir=time.sleep, contas=None) -> dict:
    """A partida do código de partilha. O código vale de qualquer conta, mas o boiler SÓ roda com o Steam na conta do Bisturi (`exigir_conta_para_baixar`)."""
    rodar = subprocess.run if rodar is None else rodar   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    # Lidos na hora da chamada (não na definição): assim um teste que troca `steam_local.*` troca de verdade,
    # e a trava nunca depende do Steam real da máquina de quem roda os testes (achado de 13/11).
    jogo_aberto = jogo_aberto or steam_local.jogo_aberto
    conta = conta or steam_local.conta_logada
    try:
        numeros = sharecode.decodificar(codigo)
    except sharecode.CodigoInvalido as e:
        raise ErroDownload(f"Código de partilha inválido: {e}") from e
    exe = _exigir_pre_requisitos(boiler or terceiros.caminho_boiler(), jogo_aberto, conta)
    exigir_conta_para_baixar(conta, contas or contas_do_config)
    dados = chamar_boiler(exe, pasta_tmp or PASTA_DEMOS / ".tmp", numeros, rodar=rodar, dormir=dormir)
    try:
        partidas = gc_protobuf.ler_lista_de_partidas(dados)
    except ValueError as e:
        raise ErroDownload(f"Não consegui ler a resposta do Game Coordinator ({e}).") from e
    if not partidas:
        raise ErroDownload("O Game Coordinator não devolveu essa partida.", expirou=True)
    p = partidas[0]
    p.setdefault("reservationid", numeros[1])
    p["token"] = p.get("token") if p.get("token") is not None else numeros[2]
    return p


# ── download e descompactação ─────────────────────────────────────────────────────────────────────

def _baixar_bz2(url: str, destino: Path, abrir=None, progresso: Callable[[int, int], None] | None = None,
                tentativas: int = 3, dormir=time.sleep) -> None:
    """Baixa em partes para `destino` (.parcial), com repetição e espera crescente. 404/410 = a Valve apagou a demo (não repete)."""
    abrir = urllib.request.urlopen if abrir is None else abrir   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    ultimo = ""
    for k in range(1, tentativas + 1):
        parcial = destino.with_name(destino.name + ".parcial")
        try:
            with abrir(urllib.request.Request(url, headers={"User-Agent": "BisturiClips"}), timeout=60) as r, open(parcial, "wb") as f:
                total = int(r.headers.get("Content-Length", 0) or 0) if hasattr(r, "headers") else 0
                if total > LIMITE_COMPRIMIDO:
                    raise ErroDownload("A demo é grande demais; não baixei.")
                baixado = 0
                while True:
                    bloco = r.read(1 << 20)
                    if not bloco:
                        break
                    baixado += len(bloco)
                    if baixado > LIMITE_COMPRIMIDO:
                        raise ErroDownload("A demo passou do limite de tamanho; parei.")
                    f.write(bloco)
                    if progresso:
                        progresso(baixado, total)
                if total and baixado != total:
                    raise OSError(f"transferência incompleta ({baixado}/{total})")
            substituir_arquivo(parcial, destino)
            return
        except urllib.error.HTTPError as e:
            parcial.unlink(missing_ok=True)
            if e.code in (404, 410):
                raise ErroDownload("A Valve já apagou essa demo (passou de ~30 dias).", expirou=True) from e
            ultimo = f"HTTP {e.code}"
        except ErroDownload:
            parcial.unlink(missing_ok=True)
            raise
        except OSError as e:
            parcial.unlink(missing_ok=True)
            ultimo = str(e)
        if k < tentativas:
            dormir(2.0 * (2 ** (k - 1)))
    raise ErroDownload(f"Não consegui baixar a demo ({ultimo}).")


def descompactar(bz2_path: Path, destino: Path, limite: int = LIMITE_DESCOMPACTADO) -> Path:
    """Descompacta em fluxo para `destino`, com limite de tamanho (bomba) e conferindo o cabeçalho `PBDEMS2`. Falha = nada fica no destino."""
    parcial = destino.with_name(destino.name + ".parcial")
    total = 0
    primeiro = True
    d = bz2.BZ2Decompressor()
    try:
        with open(bz2_path, "rb") as origem, open(parcial, "wb") as saida:
            for bloco in iter(lambda: origem.read(1 << 20), b""):
                dados_in = bloco
                while dados_in:
                    if d.eof:                                            # fluxo bz2 seguinte (arquivo com vários fluxos)
                        d = bz2.BZ2Decompressor()
                    try:
                        dados = d.decompress(dados_in)
                    except (OSError, ValueError) as e:
                        raise ErroDownload(f"O arquivo da demo está corrompido ({e}).") from e
                    if primeiro and dados:
                        if not dados.startswith(CABECALHO_DEMO):
                            raise ErroDownload("O arquivo baixado não é uma demo do CS2 (cabeçalho diferente de PBDEMS2).")
                        primeiro = False
                    total += len(dados)
                    if total > limite:
                        raise ErroDownload("A demo descompactada passou do limite de tamanho (possível arquivo malicioso); apaguei.")
                    saida.write(dados)
                    dados_in = d.unused_data if d.eof else b""
        if primeiro:
            raise ErroDownload("O arquivo da demo veio vazio.")
        if not d.eof:
            raise ErroDownload("O arquivo da demo está incompleto (o fluxo bz2 não terminou).")
        substituir_arquivo(parcial, destino)
        return destino
    finally:
        parcial.unlink(missing_ok=True)


def baixar_partida(p: dict, pasta: Path | None = None, abrir=None, progresso=None, dormir=time.sleep) -> Path:
    """Baixa e descompacta a demo de uma partida (um item de `listar_ultimas` ou `resolver_por_codigo`). Já existe = devolve o arquivo."""
    abrir = urllib.request.urlopen if abrir is None else abrir   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    pasta = Path(pasta) if pasta else PASTA_DEMOS
    pasta.mkdir(parents=True, exist_ok=True)
    if not p.get("url"):
        raise ErroDownload("Essa partida não tem demo disponível (a Valve não a guardou).", expirou=True)
    url = validar_url(p["url"])
    nome = sharecode.nome_do_arquivo(p["matchid"], p["reservationid"], p["token"])
    destino = pasta / nome
    if destino.is_file() and destino.stat().st_size > 1_000_000:
        return destino
    bz = pasta / (nome + ".bz2.parcial")
    try:
        _baixar_bz2(url, bz, abrir=abrir, progresso=progresso, dormir=dormir)
        descompactar(bz, destino)
    finally:
        bz.unlink(missing_ok=True)
    info = {"fonte": "bisturi", "matchid": int(p["matchid"]), "matchtime": p.get("matchtime"), "url": url, "baixada_em": time.strftime("%Y-%m-%d %H:%M:%S")}
    gravar_atomico(pasta / (nome + ".info"), json.dumps(info, ensure_ascii=False))      # G-012: interrompido no meio, não deixa o .info pela metade
    return destino


def _data(matchtime) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(matchtime)) if isinstance(matchtime, int) and matchtime > 1_500_000_000 else "?"


def main(argv: list[str] | None = None) -> int:
    """`python -m cs2clip.baixar_demo ultimas` | `ultimas --baixar N` | `codigo CSGO-xxxxx-... [--baixar]`. Steam aberto e logado, CS2 FECHADO."""
    import argparse
    import sys
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="cs2clip.baixar_demo", description=main.__doc__)
    sub = ap.add_subparsers(dest="acao", required=True)
    u = sub.add_parser("ultimas", help="lista as 8 últimas partidas da conta logada no Steam")
    u.add_argument("--baixar", type=int, default=0, help="baixa a partida de número N da lista (1 = a mais recente)")
    c = sub.add_parser("codigo", help="resolve um código de partilha (de qualquer conta)")
    c.add_argument("codigo")
    c.add_argument("--baixar", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.acao == "ultimas":
            partidas = listar_ultimas()
            print(f"{len(partidas)} partida(s) na conta logada (conta {steam_local.conta_logada()}):")
            for i, p in enumerate(partidas, 1):
                print(f"  {i}. {_data(p.get('matchtime'))} | {p.get('modo')} | {'demo disponível' if p.get('url') else 'sem demo'} | matchid {p.get('matchid')}")
            if a.baixar:
                if not 1 <= a.baixar <= len(partidas):
                    print("Número fora da lista.")
                    return 1
                alvo = partidas[a.baixar - 1]
                print(f"Baixando a partida {a.baixar}...")
                arq = baixar_partida(alvo, progresso=lambda b, t: print(f"\r  {b / 1e6:.0f} MB" + (f" de {t / 1e6:.0f} MB" if t else ""), end="", flush=True))
                print(f"\nPronta: {arq}")
        else:
            p = resolver_por_codigo(a.codigo)
            print(f"Partida {p['matchid']} | {_data(p.get('matchtime'))} | {p.get('modo')} | {'demo disponível' if p.get('url') else 'sem demo'}")
            if a.baixar:
                arq = baixar_partida(p, progresso=lambda b, t: print(f"\r  {b / 1e6:.0f} MB" + (f" de {t / 1e6:.0f} MB" if t else ""), end="", flush=True))
                print(f"\nPronta: {arq}")
    except ErroDownload as e:
        print(f"\n[parei] {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
