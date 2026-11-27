"""C4: baixar o VOD de uma live da Twitch pelo link, para a pasta `entrada\\` (usa o TwitchDownloaderCLI que já está em `bin\\`).

Só VOD público do próprio dono: o programa NUNCA usa a opção `--oauth` (token de assinante) e nunca guarda login.
YouTube não entra aqui de propósito: baixe o vídeo pelo YouTube Studio (oficial) e escolha o arquivo.
O nome do arquivo começa com o número do VOD (`1234567890-twitch.mp4`): assim o chat da Twitch é achado sozinho (cs2clip/chat.py).
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
from pathlib import Path

from .tools import FFMPEG, ROOT, sem_janela

CLI = ROOT / "bin" / "TwitchDownloaderCLI.exe"
# `twitch.tv/videos/<id>` (o formato de sempre) e as formas com o canal no caminho que o site ainda usa: `twitch.tv/<canal>/v/<id>` e `twitch.tv/<canal>/video/<id>`.
_LINK = re.compile(r"^(?:https?://)?(?:www\.|m\.)?twitch\.tv/(?:videos/|[A-Za-z0-9_]{3,25}/(?:v|video)/)([0-9]{9,11})(?:[/?#].*)?$", re.IGNORECASE)
_PERCENTUAL = re.compile(r"(\d{1,3})\s*%")


def vod_id_de_link(texto: str) -> str | None:
    """Número do VOD se `texto` é um link `twitch.tv/videos/<número>` (com ou sem https://); qualquer outra coisa (clipe, canal, outro site, número solto) = None."""
    return (m.group(1) if (m := _LINK.match((texto or "").strip())) else None)


def arquivo_de_destino(vod_id: str, pasta: Path) -> Path:
    return Path(pasta) / f"{vod_id}-twitch.mp4"


def comando_download(vod_id: str, destino: Path, temporaria: Path, qualidade: str = "") -> list[str]:
    """Linha de comando do TwitchDownloaderCLI (`videodownload`). Sem `--oauth`; se o arquivo já existe, não sobrescreve (Rename)."""
    if not re.fullmatch(r"[0-9]{9,11}", str(vod_id)):         # só 0-9 (o `\d` aceitaria dígitos árabes e de largura total)
        raise ValueError("número de VOD inválido")
    cmd = [str(CLI), "videodownload", "--id", str(vod_id), "-o", str(destino), "--collision", "Rename",
           "--ffmpeg-path", str(FFMPEG), "--temp-path", str(temporaria), "--banner", "false"]
    if qualidade.strip():
        cmd += ["--quality", qualidade.strip()]
    return cmd


_MARCA_DE_ERRO = re.compile(r"exception|\[ERROR\]|\berror\b", re.IGNORECASE)
_SO_SEPARADOR = re.compile(r"[-=_*\s]*")


# rastro de pilha do .NET: `at Namespace.Tipo.Metodo(args)` (com parêntese) ou `at … in caminho:line N`; e `--- End of … ---`.
# Uma mensagem legítima que só COMEÇA com `at ` ou `---` (`at least 10 GB [ERROR]`, `--- Error: algo ---`) não casa e fica.
_RASTRO_DE_PILHA = re.compile(r"at\s+\S+\(|at\s+\S.* in .+:line\s+\d+\s*$")
_FIM_DE_RASTRO = re.compile(r"---\s*End of\b.*---\s*$", re.IGNORECASE)


def _sem_rastro(linha: str) -> str:
    """A linha limpa, ou "" se é só rastro de exceção: `at Ns.Tipo.Metodo(…)`, `--- End of inner exception stack trace ---`, `--->` sem texto e separadores.
    `---> System.X: texto` (a exceção de dentro) vale: devolve sem o `--->`; o que vem depois do `--->` passa pelo mesmo filtro."""
    x = (linha or "").strip()
    if x.startswith("--->"):
        return _sem_rastro(x[4:])
    if _RASTRO_DE_PILHA.match(x) or _FIM_DE_RASTRO.match(x) or _SO_SEPARADOR.fullmatch(x):
        return ""
    return x


def linha_de_erro(linha: str) -> bool:
    """A linha explica uma falha (exceção ou `[ERROR]`) e não é rastro? Quem guarda só as últimas linhas (ui/vods.py) usa isto para não perder a primeira."""
    x = _sem_rastro(linha)
    return bool(x) and _MARCA_DE_ERRO.search(x) is not None


def mensagem_de_erro(linhas: list[str], codigo: int) -> str:
    """O que mostrar quando o baixador falha: a linha que EXPLICA (a primeira exceção ou `[ERROR]`), nunca o rastro (`at …Program.cs:line 31`,
    `--- End of inner exception stack trace ---`, `--->` vazio). VOD apagado/expirado/privado vira uma frase em português.
    `linhas` = as linhas que ele escreveu (as últimas e a primeira de erro). Sem nenhuma linha útil: o código de saída."""
    uteis = [x for x in (_sem_rastro(l) for l in linhas) if x]
    marcadas = [x for x in uteis if _MARCA_DE_ERRO.search(x)]
    escolhida = marcadas[0] if marcadas else (uteis[-1] if uteis else "")
    if any("invalid vod" in x.lower() for x in uteis):
        return "esse VOD não existe mais na Twitch (apagado, expirado ou privado)"
    if "Only MP4 and M4A" in escolhida:
        return "o baixador recusou o formato do arquivo de saída (só aceita MP4 ou M4A): Only MP4 and M4A audio files are supported"
    return (escolhida or f"o baixador terminou com o código {codigo}")[:300]


def percentual_da_linha(linha: str) -> int | None:
    """Porcentagem (0 a 100) escrita pelo baixador numa linha de status, ou None."""
    achado = _PERCENTUAL.findall(linha or "")
    if not achado:
        return None
    return max(0, min(100, int(achado[-1])))


def info_do_vod(vod_id: str, cli: Path | None = None, prazo_s: float = 20.0, executar=None) -> dict | None:
    """M3: a hora REAL da live e a duração de um VOD, perguntadas à Twitch (`TwitchDownloaderCLI info --id`, que só consulta, não baixa vídeo).
    Devolve `{"inicio_utc": "2026-11-14T00:35:15+00:00", "duracao_s": 6148}` ou None. NUNCA levanta erro: sem internet, sem o baixador, VOD apagado,
    resposta estranha ou prazo estourado = None (quem chama cai no comportamento de antes). Só o número do VOD sai do computador; sem `--oauth`."""
    if not re.fullmatch(r"[0-9]{9,11}", str(vod_id or "")):          # só 0-9 (o `\d` aceitaria dígitos de largura total)
        return None
    exe = Path(cli) if cli else CLI
    if not exe.is_file():
        return None
    executar = executar or subprocess.run          # lido na hora (não preso na definição): os testes e o conftest podem trocar
    cmd = [str(exe), "info", "--id", str(vod_id), "--format", "Raw", "--banner", "false"]
    try:
        r = executar(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=prazo_s, **sem_janela())     # sem janela de console piscando
        if r.returncode != 0:
            return None
        for linha in (r.stdout or "").splitlines():
            if not linha.lstrip().startswith("{"):
                continue
            video = ((json.loads(linha) or {}).get("data") or {}).get("video")
            if not isinstance(video, dict):
                return None
            inicio = datetime.datetime.fromisoformat(str(video["createdAt"]).replace("Z", "+00:00"))
            if inicio.tzinfo is None or not 2010 <= inicio.year <= 2100:
                return None
            duracao = int(video["lengthSeconds"])
            if duracao <= 0:
                return None
            return {"inicio_utc": inicio.astimezone(datetime.timezone.utc).isoformat(), "duracao_s": duracao}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        return None
    return None
