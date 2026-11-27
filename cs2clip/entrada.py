"""E1 (Plano E): descobre o QUE o dono colou ou arrastou na caixa única da tela Início.

Entradas aceitas: demo (.dem), código de partilha da Valve (inclusive dentro de um link `steam://…`), VOD da Twitch, vídeo do YouTube, arquivo de
vídeo e pasta de vídeos. Nada aqui usa a rede ou abre arquivo grande: só olha o texto, a extensão e os 8 primeiros bytes de uma `.dem`.
O que NÃO dá (clipe, canal, playlist, link de outro site, demo do CS:GO antigo, partida com mais de 30 dias) volta como `recusado`, com a frase do porquê.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import sharecode
from .twitch_vod import vod_id_de_link

# Mesma lista de `cs2clip/__main__.py` (um teste confere que continuam iguais; não importamos o __main__ aqui para não puxar o pipeline inteiro)
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".mts", ".m2ts", ".3gp", ".ogv"}
CABECALHO_CS2 = b"PBDEMS2\x00"
CABECALHO_CSGO_ANTIGO = b"HL2DEMO\x00"
NOME_DEMO = re.compile(r"^match730_\d{21}_\d{10}_\d{1,5}\.dem$", re.IGNORECASE)
_CODIGO = re.compile(r"CSGO(?:-[A-Za-z0-9]{5}){5}")
_DISCO = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

AJUDA = ("Aceito: link de VOD da Twitch (twitch.tv/videos/…), link de vídeo do YouTube, código de partilha (CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx) "
         "ou, pelo botão 'Escolher arquivo', uma demo (.dem) ou um vídeo (.mp4, .mkv…).")

TIPOS_ACEITOS = ("demo", "codigo_partilha", "twitch_vod", "youtube", "video", "pasta")


@dataclass(frozen=True)
class Entrada:
    tipo: str                       # um de TIPOS_ACEITOS, ou "recusado" / "desconhecido"
    valor: str = ""                 # caminho normalizado, código, número do VOD ou link normalizado
    mensagem: str = ""              # para recusado/desconhecido: o que fazer. Para os aceitos: avisos (pode ser vazio)
    detalhes: dict = field(default_factory=dict)

    @property
    def aceita(self) -> bool:
        return self.tipo in TIPOS_ACEITOS


def _recusar(mensagem: str, **detalhes) -> Entrada:
    return Entrada("recusado", "", mensagem, detalhes)


def _limpar(texto: str) -> str:
    """Tira espaços, aspas de arrastar-e-soltar e o prefixo `file:///`; várias linhas coladas: fica a primeira que tem conteúdo."""
    linhas = [x.strip() for x in str(texto or "").replace("\r", "\n").split("\n") if x.strip()]
    t = linhas[0] if linhas else ""
    for aspa in ('"', "'", "“", "”"):
        t = t.strip(aspa).strip()
    if t.lower().startswith("file:"):
        u = urllib.parse.urlparse(t)
        caminho = urllib.parse.unquote(u.path)
        if u.netloc and u.netloc.lower() != "localhost":        # file://servidor/pasta/a.dem é caminho de REDE: vira \\servidor\pasta\a.dem e é recusado adiante
            return "\\\\" + u.netloc + caminho.replace("/", "\\")
        t = caminho[1:] if re.match(r"^/[A-Za-z]:", caminho) else caminho
    return t


def _com_esquema(t: str) -> str:
    return t if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", t) else f"https://{t}"


def _codigo_de_partilha(texto: str) -> Entrada | None:
    """O código pode vir cru ou dentro de um link `steam://rungame/730/…/+csgo_download_match%20CSGO-…`. Achou o formato mas está errado = recusado."""
    achado = _CODIGO.search(urllib.parse.unquote(texto))
    if not achado:
        return None
    codigo = achado.group(0)
    if not sharecode.valido(codigo):
        return _recusar("Esse código de partilha não está certo (algum caractere errado). Copie de novo no CS2: Assistir → Suas partidas → copiar código.",
                        codigo=codigo)
    numeros = sharecode.decodificar(codigo)
    return Entrada("codigo_partilha", codigo, "", {"matchid": numeros[0], "outcomeid": numeros[1], "token": numeros[2]})


def _youtube(texto: str) -> Entrada | None:
    u = urllib.parse.urlparse(_com_esquema(texto))
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.").removeprefix("music.")
    if host not in ("youtube.com", "youtu.be", "youtube-nocookie.com"):
        return None
    caminho = [p for p in u.path.split("/") if p]
    consulta = urllib.parse.parse_qs(u.query)
    video = None
    if host == "youtu.be" and caminho:
        video = caminho[0]
    elif caminho[:1] in (["shorts"], ["live"], ["embed"], ["v"]) and len(caminho) >= 2:
        video = caminho[1]
    elif caminho[:1] == ["watch"] and consulta.get("v"):
        video = consulta["v"][0]
    if video is None:
        if caminho[:1] == ["playlist"] or (consulta.get("list") and not consulta.get("v")):
            return _recusar("Esse link é de uma playlist. Cole o link de UM vídeo (o da barra de endereço quando o vídeo está aberto).")
        if caminho[:1] in (["channel"], ["c"], ["user"]) or (caminho and caminho[0].startswith("@")):
            return _recusar("Esse link é de um canal. Cole o link de UM vídeo.")
        return _recusar("Não achei o vídeo nesse link do YouTube. Cole o link de um vídeo (youtube.com/watch?v=… ou youtu.be/…).")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        return _recusar("O endereço desse vídeo do YouTube parece incompleto (o código do vídeo tem 11 caracteres).")
    aviso = "A playlist do link foi ignorada: só o vídeo é usado." if consulta.get("list") else ""
    return Entrada("youtube", f"https://www.youtube.com/watch?v={video}", aviso, {"id": video})


def _twitch(texto: str) -> Entrada | None:
    u = urllib.parse.urlparse(_com_esquema(texto))
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "clips.twitch.tv" or (host == "twitch.tv" and "/clip/" in u.path.lower()):
        return _recusar("Esse link é de um CLIPE da Twitch. O Bisturi precisa do VOD inteiro: twitch.tv/videos/123456789.")
    if host != "twitch.tv":
        return None
    vod = vod_id_de_link(texto if "://" in texto else f"https://{texto}")
    if vod:
        return Entrada("twitch_vod", vod, "", {"link": f"https://www.twitch.tv/videos/{vod}"})
    if "/videos/" in u.path:
        return _recusar("O número desse VOD da Twitch não parece certo (são 9 a 11 números depois de /videos/).")
    return _recusar("Esse link é de um canal ou página da Twitch. Cole o link do VOD (twitch.tv/videos/123456789), na aba Vídeos do canal.")


def _e_caminho_de_rede(texto: str) -> bool:
    """Caminho de rede em qualquer grafia: `\\\\srv\\x`, `//srv/x` e as variantes que o Windows normaliza para rede (`/\\srv\\x`, `\\/srv/x`, `\\\\?\\UNC\\srv\\x`).
    Olha só o texto: nunca toca no disco (revisão de segurança de 13/11: o filtro antigo só via os dois primeiros caracteres)."""
    return texto.replace("/", "\\").startswith(("\\\\", "\\??\\")) or Path(texto).drive.startswith(("\\\\", "//"))   # `\??\UNC\srv` = prefixo de objeto do Windows (G-009)


def _existe_no_disco(texto: str) -> bool:
    """O texto é um arquivo ou pasta que existe de verdade? Nunca olha caminho de rede e nunca levanta erro (texto estranho = não existe)."""
    if _e_caminho_de_rede(texto) or not ("/" in texto or "\\" in texto or _DISCO.match(texto)):
        return False
    try:
        return Path(texto).exists()
    except (OSError, ValueError):
        return False


def _arquivo_ou_pasta(texto: str, procurar_em: list[Path]) -> Entrada | None:
    if _e_caminho_de_rede(texto):                              # \\servidor\pasta: nem olhar (abrir caminho de rede pode vazar login do Windows)
        return _recusar("Caminhos de rede (\\\\servidor\\pasta) não são aceitos. Copie o arquivo para este computador e use o botão 'Escolher arquivo'.")
    pelo_menos_caminho = bool(_DISCO.match(texto)) or "/" in texto or "\\" in texto
    caminho = Path(texto)
    achado: Path | None = caminho if (pelo_menos_caminho and caminho.exists()) else None
    if achado is None and not pelo_menos_caminho:
        for pasta in procurar_em:                                    # só o nome (ex.: match730_….dem): procura nas pastas conhecidas
            if (Path(pasta) / texto).is_file():
                achado = Path(pasta) / texto
                break
    ext = caminho.suffix.lower()
    if achado is None:
        if pelo_menos_caminho or ext in VIDEO_EXT or ext in (".dem", ".bz2"):
            return _recusar(f"Não achei esse arquivo: {texto}. Confira o caminho ou use o botão 'Escolher arquivo'.")
        return None
    if achado.is_dir():
        try:
            videos = [p for p in achado.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT]
        except OSError:        # ex.: ". /" (o Windows lê ". " como a pasta atual, mas não consegue listá-la): não é uma pasta que dê para usar
            return None
        if not videos:
            return _recusar("Essa pasta não tem nenhum vídeo (.mp4, .mkv…).")
        return Entrada("pasta", str(achado.resolve()), "", {"videos": len(videos)})
    nome = achado.name.lower()
    if nome.endswith(".dem.info"):
        return _recusar("Esse é o arquivo de dados da demo (.dem.info). Use o arquivo .dem do mesmo nome.")
    if nome.endswith(".dem.bz2"):
        return _recusar("Essa demo está compactada (.dem.bz2). Descompacte para .dem antes, ou use o código de partilha da partida.")
    try:
        tamanho = achado.stat().st_size
    except OSError:
        return _recusar(f"Não consegui ler esse arquivo: {achado.name}.")
    if tamanho == 0:
        return _recusar(f"O arquivo {achado.name} está vazio.")
    if achado.suffix.lower() == ".dem":
        try:
            with open(achado, "rb") as f:
                cab = f.read(8)
        except OSError:        # arquivo em uso ou bloqueado: uma recusa com o nome, não uma exceção que derruba a listagem (G-019)
            return _recusar(f"Não consegui abrir {achado.name} (outro programa pode estar usando). Feche-o e tente de novo.")
        if cab == CABECALHO_CSGO_ANTIGO:
            return _recusar("Essa demo é do CS:GO antigo (antes do CS2). O Bisturi só entende demos do CS2.")
        if cab != CABECALHO_CS2:
            return _recusar(f"O arquivo {achado.name} não parece uma demo do CS2 (cabeçalho diferente). Baixe de novo no CS2: Assistir → Suas partidas.")
        return Entrada("demo", str(achado.resolve()), "", {"nome": achado.name, "mb": round(tamanho / 1e6, 1), "padrao_cs2": bool(NOME_DEMO.match(achado.name))})
    if achado.suffix.lower() in VIDEO_EXT:
        return Entrada("video", str(achado.resolve()), "", {"nome": achado.name, "mb": round(tamanho / 1e6, 1)})
    return _recusar(f"Não sei usar arquivos {achado.suffix or 'sem extensão'}. {AJUDA}")


def reconhecer(texto: str, procurar_em: list[Path] | None = None) -> Entrada:
    """O que o dono colou/arrastou. `procurar_em`: pastas onde procurar uma demo dada só pelo nome (replays do CS2, `demos/`)."""
    try:
        return _reconhecer(texto, procurar_em)
    except ValueError:      # ex.: "a[b" ou "http://[abc" (urlparse: "Invalid IPv6 URL"): um texto que não dá para ler é só um texto não reconhecido
        return Entrada("desconhecido", "", f"Não entendi o que é isso. {AJUDA}")


def _reconhecer(texto: str, procurar_em: list[Path] | None = None) -> Entrada:
    t = _limpar(texto)
    if not t:
        return Entrada("desconhecido", "", f"Cole um link ou um código de partilha, ou use o botão 'Escolher arquivo'. {AJUDA}")
    cod = _codigo_de_partilha(t)
    if cod is not None:
        return cod
    if re.match(r"^steam://", t, re.I):
        return _recusar("Esse link da Steam não traz o código de uma partida. No CS2: Assistir → Suas partidas → copiar código.")
    ilegivel_como_link = False
    for funcao in (_youtube, _twitch):      # o link ganha primeiro, como sempre foi (mesmo que exista um arquivo de mesmo nome na pasta atual)
        try:
            r = funcao(t)
        except ValueError:                  # nome com "[" (yt-dlp: `Título [id].mp4`) faz o urlparse estourar: não é link
            ilegivel_como_link, r = True, None
        if r is not None:
            return r
    if _existe_no_disco(t):                 # arquivo/pasta de verdade, antes de chamar de "site que o Bisturi não usa"
        if (r := _arquivo_ou_pasta(t, list(procurar_em or []))) is not None:
            return r
    if ilegivel_como_link:
        raise ValueError("texto que não dá para ler como link nem existe no disco")    # `reconhecer` devolve "Não entendi"
    if "\\" not in t and not _DISCO.match(t) and (re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", t)
                                            or re.match(r"^(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/", t)):
        return _recusar("Esse link é de um site que o Bisturi não usa. " + AJUDA)
    if (r := _arquivo_ou_pasta(t, list(procurar_em or []))) is not None:
        return r
    return Entrada("desconhecido", "", f"Não entendi o que é isso. {AJUDA}")
