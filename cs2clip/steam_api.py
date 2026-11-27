"""D8 (Plano D, opcional): avisar de partidas novas SEM abrir o CS2, pela API oficial do Steam (`GetNextMatchSharingCode`). Só leitura.

Precisa de 3 coisas que SÓ o dono cria (ver `docs/PASSO_A_PASSO_GRAVAR_PELA_DEMO.md`, seção D8): a chave da API do Steam, o código de autenticação de jogo do CS2 e UM código
de partilha recente da conta (o ponto de partida da cadeia). Ficam em `dados\\segredos_steam.json` (pasta fora do git), com permissão só do usuário,
e NUNCA aparecem no log, nas mensagens de erro nem no terminal depois de digitados.

Cada partida nova vira um código de partilha; o `baixar_demo` resolve e baixa cada um.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import sharecode
from .caminhos import PASTA_DADOS
from .tools import substituir_arquivo
from .tools import sem_janela

ARQUIVO = PASTA_DADOS / "dados" / "segredos_steam.json"
URL = "https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1"
LIMITE_CADEIA = 20


class ErroSteamApi(Exception):
    """Falha ao falar com a API do Steam (mensagem pronta, em português, sem a chave)."""


def _so_o_usuario(arq: Path) -> None:
    """Melhor esforço: tira a herança e deixa só o usuário atual com acesso ao arquivo (Windows) ou 600 (outros)."""
    try:
        if sys.platform == "win32":
            subprocess.run(["icacls", str(arq), "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:F"], capture_output=True, timeout=30, **sem_janela())
        else:
            os.chmod(arq, 0o600)
    except (OSError, subprocess.SubprocessError):
        pass


def _gravar_segredo(arq: Path, texto: str) -> None:
    """Grava o arquivo de segredos SEM janela de exposição (G-012/G-019): o `.tmp` nasce vazio, recebe a permissão só do
    usuário e SÓ ENTÃO o conteúdo; a troca para o nome final leva a permissão junto. Antes, o arquivo era escrito com a
    permissão herdada da pasta e só depois restringido. Com `fsync`: segredo não se perde numa queda de energia."""
    tmp = arq.with_name(arq.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # fora do Windows já nasce 600
    os.close(fd)
    try:
        _so_o_usuario(tmp)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        substituir_arquivo(tmp, arq)
    except BaseException:
        tmp.unlink(missing_ok=True)      # nunca deixa segredo solto num .tmp
        raise


def gravar_segredos(api_key: str, steamid: str, codigo_autenticacao: str, ultimo_codigo: str, arquivo: Path | None = None) -> Path:
    """Confere o formato de cada valor e grava. A chave tem 32 letras/números; o SteamID64, 17 números; o código de autenticação segue AAAA-AAAAA-AAAA."""
    if not re.fullmatch(r"[0-9A-Fa-f]{32}", api_key.strip()):
        raise ErroSteamApi("A chave da API do Steam tem 32 letras e números (de 0 a 9 e A a F).")
    if not re.fullmatch(r"\d{17}", steamid.strip()):
        raise ErroSteamApi("O SteamID64 tem 17 números.")
    if not re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{5}-[A-Z0-9]{4}", codigo_autenticacao.strip().upper()):
        raise ErroSteamApi("O código de autenticação de jogo tem o formato AAAA-AAAAA-AAAA.")
    if not sharecode.valido(ultimo_codigo):
        raise ErroSteamApi("O código de partilha inicial não está no formato certo (CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx).")
    arq = Path(arquivo) if arquivo else ARQUIVO
    arq.parent.mkdir(parents=True, exist_ok=True)
    _gravar_segredo(arq, json.dumps({"api_key": api_key.strip(), "steamid": steamid.strip(), "codigo_autenticacao": codigo_autenticacao.strip().upper(),
                                     "ultimo_codigo": ultimo_codigo.strip()}))
    return arq


def ler_segredos(arquivo: Path | None = None) -> dict:
    arq = Path(arquivo) if arquivo else ARQUIVO
    try:
        d = json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ErroSteamApi("Ainda não configurei a busca de partidas novas (rode: python -m cs2clip.steam_api configurar).") from e
    for k in ("api_key", "steamid", "codigo_autenticacao", "ultimo_codigo"):
        if not d.get(k):
            raise ErroSteamApi(f"O arquivo de segredos está sem '{k}': configure de novo.")
    return d


def _limpar(texto: str, segredos: dict) -> str:
    """Tira qualquer segredo de um texto antes de ele ir para mensagem ou log."""
    for k in ("api_key", "codigo_autenticacao"):
        if segredos.get(k):
            texto = texto.replace(str(segredos[k]), "***")
    return re.sub(r"(key|steamidkey)=[^&\s]+", r"\1=***", texto)


def proximo_codigo(segredos: dict, conhecido: str, abrir=None) -> tuple[str | None, str]:
    """(próximo código ou None, situação). Situações: 'novo', 'sem_novo' (a Valve devolveu 202/'n/a': ainda não há partida mais nova),
    'conhecido_invalido' (412: o código de partida não é aceito). Outras respostas levantam `ErroSteamApi`."""
    abrir = urllib.request.urlopen if abrir is None else abrir   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    consulta = urllib.parse.urlencode({"key": segredos["api_key"], "steamid": segredos["steamid"], "steamidkey": segredos["codigo_autenticacao"],
                                       "knowncode": conhecido})
    try:
        with abrir(urllib.request.Request(f"{URL}?{consulta}", headers={"User-Agent": "BisturiClips"}), timeout=30) as r:
            status = getattr(r, "status", 200)
            corpo = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 412:
            return None, "conhecido_invalido"
        if e.code == 202:
            return None, "sem_novo"
        if e.code in (401, 403):
            raise ErroSteamApi("O Steam recusou a chave ou o código de autenticação (confira os dois).") from None
        raise ErroSteamApi(f"O Steam respondeu com erro {e.code}.") from None
    except OSError as e:
        raise ErroSteamApi(_limpar(f"Não consegui falar com o Steam ({e}).", segredos)) from None
    if status == 202:
        return None, "sem_novo"
    try:
        codigo = str(json.loads(corpo).get("result", {}).get("nextcode", ""))
    except ValueError as e:
        raise ErroSteamApi("A resposta do Steam não veio no formato esperado.") from e
    if not codigo or codigo.lower() == "n/a":
        return None, "sem_novo"
    if not sharecode.valido(codigo):
        raise ErroSteamApi("O Steam devolveu um código de partilha em formato desconhecido.")
    return codigo, "novo"


def novas_partidas(segredos: dict, abrir=None, limite: int = LIMITE_CADEIA) -> tuple[list[str], str]:
    """Anda na cadeia a partir do último código conhecido. Devolve (códigos novos em ordem, situação final)."""
    abrir = urllib.request.urlopen if abrir is None else abrir   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    conhecido, novos, situacao = segredos["ultimo_codigo"], [], "sem_novo"
    for _ in range(limite):
        codigo, situacao = proximo_codigo(segredos, conhecido, abrir)
        if codigo is None:
            break
        novos.append(codigo)
        conhecido = codigo
    return novos, situacao


def procurar(arquivo: Path | None = None, abrir=None, imprimir=print) -> list[str]:
    """Procura partidas novas e guarda o último código como novo ponto de partida (só se achou algo)."""
    abrir = urllib.request.urlopen if abrir is None else abrir   # lido na hora da chamada (B2, 14/11): um teste que troca o original troca de verdade
    seg = ler_segredos(arquivo)
    novos, situacao = novas_partidas(seg, abrir)
    if situacao == "conhecido_invalido":
        raise ErroSteamApi("O Steam não aceitou o código de partida inicial: pegue outro no CS2 (Assistir, Suas partidas, copiar código) e configure de novo.")
    if novos:
        seg["ultimo_codigo"] = novos[-1]
        arq = Path(arquivo) if arquivo else ARQUIVO
        _gravar_segredo(arq, json.dumps(seg))
        imprimir(f"{len(novos)} partida(s) nova(s) (da mais antiga para a mais nova). Para baixar cada uma: python -m cs2clip.baixar_demo codigo <código> --baixar")
        for c in novos:
            imprimir(f"  {c}")
    else:
        imprimir("Nenhuma partida nova desde a última busca.")
    return novos


def main(argv: list[str] | None = None) -> int:
    """`python -m cs2clip.steam_api configurar` (pergunta os 4 valores) | `procurar` (lista as partidas novas)."""
    import argparse
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="cs2clip.steam_api", description=main.__doc__)
    ap.add_argument("acao", choices=["configurar", "procurar"])
    a = ap.parse_args(argv)
    try:
        if a.acao == "configurar":
            print("Cole cada valor e tecle Enter (a chave e o código de autenticação não aparecem na tela enquanto você digita).")
            chave = getpass.getpass("Chave da API do Steam (32 letras e números): ")
            steamid = input("SteamID64 da conta que joga (17 números): ")
            auth = getpass.getpass("Código de autenticação de jogo (AAAA-AAAAA-AAAA): ")
            codigo = input("UM código de partilha recente (CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx): ")
            arq = gravar_segredos(chave, steamid, auth, codigo)
            print(f"Guardado em {arq} (fora do git, só o seu usuário lê).")
        else:
            procurar()
    except ErroSteamApi as e:
        print(f"[parei] {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
