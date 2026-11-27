"""D5 (Plano D): código de partilha do CS2 (`CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx`) <-> (matchId, outcomeId, token).

O algoritmo (alfabeto de 57 letras, 18 bytes) segue o decodificador do projeto aberto `cs2-demo-fetcher` (licença Unlicense, domínio público),
e o nome do arquivo segue o padrão do CS Demo Manager (MIT), reescritos aqui. Verificado com o vetor de teste do próprio projeto
(`CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2xK` -> 3230642215713767580, 3230647599455273103, 55788).
"""
from __future__ import annotations

ALFABETO = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789"
BASE = len(ALFABETO)        # 57


class CodigoInvalido(ValueError):
    """O código de partilha não está no formato certo."""


def decodificar(codigo: str) -> tuple[int, int, int]:
    """(matchId, outcomeId, token). Levanta `CodigoInvalido` com a explicação em português."""
    if not isinstance(codigo, str):
        raise CodigoInvalido("o código de partilha precisa ser texto")
    limpo = codigo.strip().replace("CSGO-", "").replace("-", "")
    if len(limpo) != 25:
        raise CodigoInvalido(f"o código tem {len(limpo)} letras e números; o certo é 25 (ex.: CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx)")
    valor = 0
    for c in reversed(limpo):
        i = ALFABETO.find(c)
        if i < 0:
            raise CodigoInvalido(f"caractere inválido no código: {c!r}")
        valor = valor * BASE + i
    if valor >= 1 << 144:
        raise CodigoInvalido("o código está fora do intervalo possível")
    bruto = valor.to_bytes(18, byteorder="big")
    return (int.from_bytes(bruto[0:8], "little"), int.from_bytes(bruto[8:16], "little"), int.from_bytes(bruto[16:18], "little"))


def codificar(match_id: int, outcome_id: int, token: int) -> str:
    bruto = match_id.to_bytes(8, "little") + outcome_id.to_bytes(8, "little") + (token & 0xFFFF).to_bytes(2, "little")
    valor = int.from_bytes(bruto, "big")
    letras = []
    for _ in range(25):
        valor, resto = divmod(valor, BASE)
        letras.append(ALFABETO[resto])
    s = "".join(letras)
    return f"CSGO-{s[0:5]}-{s[5:10]}-{s[10:15]}-{s[15:20]}-{s[20:25]}"


def exato(codigo) -> bool:
    """O formato EXATO `CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx` (5 grupos de 5 letras do alfabeto), sem hífens soltos nem partes faltando. `valido` é mais frouxo de propósito (aceita o
    código sem o `CSGO-`); quem passa o texto adiante como ARGUMENTO de um processo (a rota da gravação manual) usa este, para um valor como `-----abc…` nunca virar opção."""
    import re
    return isinstance(codigo, str) and re.fullmatch(r"CSGO(?:-[%s]{5}){5}" % ALFABETO, codigo.strip()) is not None and valido(codigo)


def valido(codigo) -> bool:
    try:
        decodificar(codigo)
        return True
    except CodigoInvalido:
        return False


def nome_do_arquivo(match_id: int, outcome_id: int, token: int) -> str:
    """`match730_<matchId 21 dígitos>_<outcomeId 10 dígitos>_<token>.dem` (o padrão do CS2 e do CS Demo Manager). Só números: nada de texto livre."""
    return f"match730_{int(match_id):021d}_{int(outcome_id) & 0xFFFFFFFF:010d}_{int(token)}.dem"
