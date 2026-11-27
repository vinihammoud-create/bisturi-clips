"""Mede a nota do programa contra os votos do dono (B6, Plano V7 integrado). Os votos vêm do `momentos.json`:
`aprovado` (Gostei), `excluido` (Descartar), `motivo_rejeicao` e `avaliacao` (0–3 por critério).

O que entra e o que não entra:
  * só momentos JÁ REVISADOS entram (aprovado = True, ou descartado). Sem voto = sem informação, fica de fora;
  * relevância: descartado = 0; Gostei = 2; Gostei com avaliação média >= 2,5 = 3;
  * a lista é ordenada pela NOTA do programa e medimos Precision@K e NDCG@K sobre os revisados;
  * Recall@K NÃO é calculado: só faria sentido se você tivesse marcado TODAS as jogadas boas da partida;
  * com poucos votos o número oscila muito: o relatório mostra quantos votos há e avisa quando são poucos."""
from __future__ import annotations

import json
import math
from pathlib import Path

POUCOS_VOTOS = 20


def relevancia(m: dict) -> int | None:
    """None = ainda sem voto (fica de fora da medida)."""
    if m.get("excluido") or m.get("aprovado") is False:
        return 0
    if m.get("aprovado") is True:
        av = m.get("avaliacao")
        if isinstance(av, dict) and av:
            notas = [v for v in av.values() if isinstance(v, int)]
            if notas and sum(notas) / len(notas) >= 2.5:
                return 3
        return 2
    return None


def _ndcg(rels: list[int], k: int) -> float | None:
    def dcg(lista: list[int]) -> float:
        return sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(lista[:k]))
    ideal = dcg(sorted(rels, reverse=True))
    return round(dcg(rels) / ideal, 3) if ideal > 0 else None


def medir(momentos: list[dict], ks: tuple[int, ...] = (3, 5, 10)) -> dict:
    revisados = [(m, relevancia(m)) for m in momentos]
    revisados = [(m, r) for m, r in revisados if r is not None]
    revisados.sort(key=lambda x: -float(x[0].get("nota") or 0))
    rels = [r for _, r in revisados]
    n = len(revisados)
    rej = [m for m, r in revisados if r == 0]
    motivos: dict[str, int] = {}
    for m in rej:
        chave = m.get("motivo_rejeicao") or "sem_motivo"
        motivos[chave] = motivos.get(chave, 0) + 1
    rel = {"momentos": len(momentos), "revisados": n, "aprovados": sum(r > 0 for r in rels),
           "taxa_de_aprovacao": round(sum(r > 0 for r in rels) / n, 3) if n else None,
           "precisao_em_k": {}, "ndcg_em_k": {}, "motivos_de_rejeicao": motivos,
           "rejeitados_por_falta_de_contexto": round(motivos.get("sem_contexto", 0) / len(rej), 3) if rej else None,
           "poucos_votos": n < POUCOS_VOTOS}
    for k in ks:
        if n >= k:
            rel["precisao_em_k"][str(k)] = round(sum(r > 0 for r in rels[:k]) / k, 3)
            rel["ndcg_em_k"][str(k)] = _ndcg(rels, k)
    topo = [m for m, _ in revisados[:max(ks)]]
    rotulos = {m.get("rotulo") for m in topo}
    rel["diversidade_de_rotulos_no_topo"] = round(len(rotulos) / len(topo), 3) if topo else None
    rounds_ap = {m.get("round") for m, r in revisados if r > 0 and m.get("round") is not None}
    rounds_rev = {m.get("round") for m, _ in revisados if m.get("round") is not None}
    rel["cobertura_de_rounds"] = round(len(rounds_ap) / len(rounds_rev), 3) if rounds_rev else None
    return rel


def carregar(pasta_saida: Path) -> list[dict]:
    """Todos os momentos de todos os vídeos de uma pasta `saida` (ou de uma pasta de um vídeo só)."""
    arqs = [pasta_saida / "momentos.json"] if (pasta_saida / "momentos.json").is_file() else sorted(pasta_saida.glob("*/momentos.json"))
    saida: list[dict] = []
    for a in arqs:
        try:
            saida += [m for m in json.loads(a.read_text(encoding="utf-8")).get("momentos", []) if isinstance(m, dict)]
        except (OSError, ValueError):
            continue
    return saida


def texto(rel: dict) -> str:
    linhas = [f"Momentos: {rel['momentos']} · revisados por você: {rel['revisados']} · aprovados: {rel['aprovados']}"]
    if not rel["revisados"]:
        return "\n".join(linhas + ["Ainda não há votos: use Gostei/Descartar na tela de revisão e rode de novo."])
    linhas.append(f"Taxa de aprovação: {rel['taxa_de_aprovacao']:.0%}")
    for k, v in rel["precisao_em_k"].items():
        linhas.append(f"Precision@{k}: {v:.0%} dos {k} primeiros pela nota do programa você aprovou · NDCG@{k}: {rel['ndcg_em_k'][k]}")
    if not rel["precisao_em_k"]:
        linhas.append("Poucos revisados para calcular Precision@K (precisa de pelo menos 3).")
    if rel["motivos_de_rejeicao"]:
        linhas.append("Motivos de rejeição: " + ", ".join(f"{k} ×{v}" for k, v in sorted(rel["motivos_de_rejeicao"].items())))
    if rel["diversidade_de_rotulos_no_topo"] is not None:
        linhas.append(f"Diversidade de jogadas no topo: {rel['diversidade_de_rotulos_no_topo']:.0%} · rounds cobertos: "
                      + (f"{rel['cobertura_de_rounds']:.0%}" if rel["cobertura_de_rounds"] is not None else "—"))
    if rel["poucos_votos"]:
        linhas.append(f"Atenção: só {rel['revisados']} voto(s) — com menos de {POUCOS_VOTOS} estes números oscilam muito.")
    linhas.append("Recall@K não é calculado: exigiria que você marcasse TODAS as jogadas boas da partida.")
    return "\n".join(linhas)
