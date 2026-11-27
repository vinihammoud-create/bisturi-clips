"""Nome legível de uma partida ("Mirage 11x13 · 08/11"): mapa, placar do dono e dia. Módulo leve (sem o leitor de demos), usado pela interface e pela edição."""
from __future__ import annotations

import datetime
import re
from pathlib import Path

NOMES_DE_MAPAS = {"de_dust2": "Dust2", "de_mirage": "Mirage", "de_inferno": "Inferno", "de_nuke": "Nuke", "de_overpass": "Overpass", "de_vertigo": "Vertigo",
                  "de_ancient": "Ancient", "de_anubis": "Anubis", "de_train": "Train", "de_cache": "Cache", "de_cbble": "Cobblestone", "de_grail": "Grail",
                  "de_jura": "Jura", "de_basalt": "Basalt", "de_edin": "Edin", "de_palais": "Palais"}


def nome_do_mapa(mapa: str) -> str:
    """`de_mirage` → "Mirage"; mapa que não conhecemos: tira o prefixo (`de_`, `cs_`, `ar_`) e põe maiúscula (`de_novo_mapa` → "Novo Mapa")."""
    chave = str(mapa or "").strip().lower()
    if chave in NOMES_DE_MAPAS:
        return NOMES_DE_MAPAS[chave]
    return re.sub(r"^(?:de|cs|ar|gd)_", "", chave).replace("_", " ").title()


def data_da_demo(caminho: str | Path) -> datetime.datetime | None:
    """A data da partida (do `.dem.info` do lado) ou, sem ele, a data do arquivo (quando foi baixado). None se nem isso der."""
    from . import demos_cs2
    caminho = Path(caminho)
    quando = demos_cs2.ler_matchtime(caminho.with_name(caminho.name + ".info"))
    if quando is not None:
        return quando
    try:
        return datetime.datetime.fromtimestamp(caminho.stat().st_mtime)
    except (OSError, ValueError, OverflowError):
        return None


def titulo_da_partida(mapa: str, placar: tuple[int, int] | None, quando: datetime.datetime | None, sem_nada: str = "") -> str:
    """"Mirage 11x13 · 08/11". Sem placar: "Mirage · 08/11". Sem mapa: "Partida". `sem_nada` é devolvido se não houver mapa nem data."""
    if not mapa and quando is None:
        return sem_nada
    texto = nome_do_mapa(mapa) if mapa else "Partida"
    if placar:
        texto += f" {placar[0]}x{placar[1]}"
    return texto + (f" · {quando:%d/%m}" if quando is not None else "")
