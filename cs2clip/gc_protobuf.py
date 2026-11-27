"""D5 (Plano D): leitor mínimo, sem dependências, do que o `boiler-writter` grava: `CMsgGCCStrike15_v2_MatchList`.

Estrutura usada (conferida com o arquivo real de 8 partidas do projeto `cs2-demo-fetcher`, Unlicense, que serve de teste):
  campo 4 (repetido) = CDataGCCStrike15_v2_MatchInfo:
      1 matchid (varint) · 2 matchtime (varint, segundos desde 1970) · 3 watchablematchinfo (2: tv_port)
      4 roundstats_legacy · 5 (repetido) roundstatsall; o ÚLTIMO traz: 1 reservationid, 2 reservation (1: contas, 2: game_type), 3 URL da demo.
O parser só LÊ bytes: não executa nada do que vem dentro. Dado malformado levanta `ValueError`.
"""
from __future__ import annotations

from collections.abc import Iterator

MODOS_PELO_NUMERO_DE_JOGADORES = {10: "5x5", 4: "Wingman", 6: "Rush"}


def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        if i >= len(b):
            raise ValueError("varint cortado")
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i
        if s > 70:
            raise ValueError("varint longo demais")


def _campos(b: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """(número do campo, tipo, valor): inteiro ou bytes."""
    i = 0
    while i < len(b):
        chave, i = _varint(b, i)
        f, w = chave >> 3, chave & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 1:
            if i + 8 > len(b):
                raise ValueError("campo cortado")
            v = b[i:i + 8]
            i += 8
        elif w == 5:
            if i + 4 > len(b):
                raise ValueError("campo cortado")
            v = b[i:i + 4]
            i += 4
        elif w == 2:
            n, i = _varint(b, i)
            if i + n > len(b):
                raise ValueError("campo cortado")
            v = b[i:i + n]
            i += n
        else:
            raise ValueError(f"tipo de campo não suportado: {w}")
        yield f, w, v


def ler_lista_de_partidas(dados: bytes | bytearray | memoryview) -> list[dict]:
    """Uma entrada por partida: matchid, matchtime, reservationid (outcomeId), tv_port (token = tv_port & 0xFFFF), url (ou None), jogadores, modo."""
    partidas: list[dict] = []
    dados = bytes(dados)        # bytearray/memoryview viram bytes: os `isinstance(..., bytes)` abaixo descartariam os campos em silêncio
    for f, w, v in _campos(dados):
        if f != 4 or w != 2 or not isinstance(v, bytes):   # tipo 2 é sempre bytes (o isinstance só informa os tipos)
            continue
        m: dict = {"matchid": None, "matchtime": None, "reservationid": None, "tv_port": None, "url": None, "jogadores": 0, "game_type": None}
        rodadas: list[bytes] = []
        legado = None
        for ff, ww, vv in _campos(v):
            if ff == 1 and ww == 0:
                m["matchid"] = vv
            elif ff == 2 and ww == 0:
                m["matchtime"] = vv
            elif ff == 3 and ww == 2 and isinstance(vv, bytes):
                for a, wa, x in _campos(vv):
                    if a == 2 and wa == 0 and isinstance(x, int):
                        m["tv_port"] = x
            elif ff == 4 and ww == 2 and isinstance(vv, bytes):
                legado = vv
            elif ff == 5 and ww == 2 and isinstance(vv, bytes):
                rodadas.append(vv)
        ultimo = legado if legado is not None else (rodadas[-1] if rodadas else None)
        if ultimo is not None:
            for a, wa, x in _campos(ultimo):
                if a == 1 and wa == 0:
                    m["reservationid"] = x
                elif a == 3 and wa == 2 and isinstance(x, bytes):
                    m["url"] = bytes(x).decode("utf-8", errors="replace")
                elif a == 2 and wa == 2 and isinstance(x, bytes):
                    for r, wr, y in _campos(x):
                        if r == 1 and wr == 0:
                            m["jogadores"] += 1
                        elif r == 1 and wr == 2 and isinstance(y, bytes):   # codificação empacotada
                            j = 0
                            while j < len(y):
                                _, j = _varint(y, j)
                                m["jogadores"] += 1
                        elif r == 2 and wr == 0:
                            m["game_type"] = y
        m["modo"] = MODOS_PELO_NUMERO_DE_JOGADORES.get(m["jogadores"], "?")
        m["token"] = (m["tv_port"] & 0xFFFF) if m["tv_port"] is not None else None
        partidas.append(m)
    return partidas
