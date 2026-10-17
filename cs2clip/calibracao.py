"""Calibração da nota pelos seus votos (pesquisa P7, R4; método descrito pela Opus: escolher o limiar em que pelo
menos 70% dos cortes acima dele foram aprovados, com ~50 cortes avaliados).

Votos vêm de dois lugares: a coluna `voto` do CSV da página de revisão (👍 postaria / 👎 não) e, na interface,
`aprovado: true` (👍 Gostei) e `excluido: true` (🗑 Descartar) no momentos.json. Com poucos votos o resultado é só
uma sugestão; nada é mudado no config sem você aceitar (a revisão cética lembrou do risco de ajustar com poucos
dados, então aqui só se calibra o LIMIAR, não os pesos)."""
from __future__ import annotations

from dataclasses import dataclass

APROVACAO_MIN = 0.70
VOTOS_MIN = 30          # abaixo disso: "poucos votos", sem sugestão


@dataclass
class Sugestao:
    votos: int
    aprovados: int
    limiar: float | None          # nota mínima sugerida (None = sem dados suficientes ou nenhum limiar atinge 70%)
    aprovacao_acima: float | None # fração aprovada acima do limiar sugerido
    mensagem: str


def sugerir_limiar(votos: list[tuple[float, bool]], minimo: float = APROVACAO_MIN, votos_min: int = VOTOS_MIN) -> Sugestao:
    """`votos`: (nota do corte, aprovado?). Escolhe a MENOR nota a partir da qual >= `minimo` dos cortes foram
    aprovados (a menor, para não jogar fora cortes bons) e que ainda deixa ao menos 5 cortes acima."""
    n, ap = len(votos), sum(1 for _, a in votos if a)
    if n < votos_min:
        return Sugestao(n, ap, None, None, f"Só {n} votos: preciso de pelo menos {votos_min} para sugerir um limiar.")
    for limiar in sorted({nota for nota, _ in votos}):
        acima = [a for nota, a in votos if nota >= limiar]
        if len(acima) >= 5 and sum(acima) / len(acima) >= minimo:
            taxa = sum(acima) / len(acima)
            return Sugestao(n, ap, float(limiar), taxa,
                            f"A partir da nota {limiar:.0f}, {taxa:.0%} dos {len(acima)} cortes foram aprovados.")
    return Sugestao(n, ap, None, None, f"Nenhuma nota separa bem: menos de {minimo:.0%} de aprovação em qualquer limiar.")
