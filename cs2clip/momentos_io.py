"""`momentos.json`: os dados de cada jogada/reação de um vídeo, para o resto do programa e para a futura
interface. Traz a versão do formato e do detector, o nome dos arquivos gerados e a revisão do usuário
(`aprovado` / `excluido`), que SOBREVIVE a uma nova análise do mesmo vídeo.

Formato 1:
    {"formato": 1, "detector": "3", "video": "<id>", "duracao": 3069.4,
     "momentos": [{"n": 1, "tipo": "abate", "rotulo": "DOUBLE KILL", "nota": 28.0, "inicio": ..., "fim": ...,
                   "foco": 111.5, "abates": [...], "parte": "...", "round": 4, "emocao": "RAIVA", ...,
                   "arquivos": {"corte": "cortes/..mp4", "short": ..., "miniatura": ...} | null,
                   "aprovado": true | false | null, "excluido": false}, ...]}
`aprovado` null = ainda não revisado. O mesmo momento é reconhecido entre análises pelo `foco`
(instante do último abate ou do pico da reação), que não muda quando se ajusta antes/depois do corte.
"""
from __future__ import annotations

import json
from pathlib import Path

from .moments import Moment
from .tools import substituir_arquivo

FORMATO = 1


def _chave(foco: float) -> str:
    return f"{foco:.1f}"


def ler_revisao(arquivo: Path) -> dict[str, dict]:
    """{foco: {"aprovado": ..., "excluido": ...}} do momentos.json que já existe (vazio se não existir
    ou estiver ilegível; aceita também o formato antigo, que era só uma lista sem revisão)."""
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    lista = dados.get("momentos", []) if isinstance(dados, dict) else []
    return {_chave(m["foco"]): {"aprovado": m.get("aprovado"), "excluido": bool(m.get("excluido", False)),
                                "arquivos": m.get("arquivos"), "motivo_rejeicao": m.get("motivo_rejeicao"),
                                "avaliacao": m.get("avaliacao"), "ajuste": m.get("ajuste")}
            for m in lista if isinstance(m, dict) and "foco" in m}


def ajustes_dos_momentos(arquivo: Path) -> dict[str, tuple[float, float]]:
    """C10: {foco: (ajuste do início, ajuste do fim)} em segundos, do que o dono acertou na tela de revisão."""
    saida = {}
    for chave, v in ler_revisao(arquivo).items():
        aj = v.get("ajuste")
        if isinstance(aj, dict):
            try:
                saida[chave] = (float(aj.get("inicio", 0.0)), float(aj.get("fim", 0.0)))
            except (TypeError, ValueError):
                continue
    return saida


def chaves_excluidas(arquivo: Path) -> set[str]:
    """Chaves (`foco`) dos momentos que o usuário descartou na revisão: não viram corte, short nem montagem."""
    return {k for k, v in ler_revisao(arquivo).items() if v.get("excluido")}


def chave_do_momento(m: Moment) -> str:
    return _chave(m.focus)


def gravar_momentos(out: Path, moments: list[Moment], video: str, duracao: float, detector: str,
                    arquivos: dict[int, dict] | None = None,
                    bases: dict[int, tuple[float, float, list]] | None = None) -> None:
    """`arquivos`: {nº do momento: {"corte": ..., "short": ..., "miniatura": ...}} (caminhos relativos a
    `out`), depois do render; None na análise.
    `bases`: {nº do momento: (início, fim, trechos)} ANTES do ajuste do dono. O render mexe nos tempos dos momentos
    em memória (`aplicar_ajustes`); o arquivo precisa guardar os tempos da análise, porque o `ajuste` já vai junto
    e somaria de novo na próxima renderização (achado G-G02 do Gemini)."""
    arq = out / "momentos.json"
    revisao = ler_revisao(arq)
    lista = []
    for m in moments:
        rev = revisao.get(_chave(m.focus), {})
        ini, fim, segs = (bases or {}).get(m.idx, (m.start, m.end, m.segmentos))
        if arquivos is not None:
            arqs = arquivos.get(m.idx)
        else:  # análise sem render: mantém o vínculo com os arquivos que ainda existem
            antigos = rev.get("arquivos")
            arqs = ({k: (v if v and (out / v).exists() else None) for k, v in antigos.items()}
                    if isinstance(antigos, dict) else None)
        lista.append({
            "n": m.idx, "tipo": m.kind, "rotulo": m.label, "nota": round(m.score, 1),
            "inicio": round(ini, 2), "fim": round(fim, 2), "foco": round(m.focus, 1),
            "segmentos": [[round(a, 2), round(b, 2)] for a, b in segs], "faixa": m.faixa or None,
            "abates": [{"t": e.t, "icones": e.icons} for e in m.kills],
            "assistencias": [a.t for a in m.assists], "motivos": m.reasons, "falas": m.hype_words,
            "parte": m.parte, "parte_tipo": m.parte_tipo, "parte_classe": m.parte_classe or None,
            "parte_confianca": round(m.parte_confianca, 2) if m.parte_classe else None, "parte_confirmar": bool(m.parte_confirmar),
            "parte_evidencias": list(m.parte_evidencias), "categoria": m.categoria or None, "round": m.round_n, "round_resultado": m.round_resultado,
            "emocao": m.emocao, "confianca": m.confianca or None, "emocao_motivos": m.emocao_motivos,
            "clutch": {"inimigos": m.clutch[0], "ganhou": m.clutch[1]} if m.clutch else None,
            "morreu_no_fim": bool(m.morreu_no_fim), "reacao_na_morte": m.reacao_na_morte or None,
            "arquivos": arqs,
            "aprovado": rev.get("aprovado"), "excluido": rev.get("excluido", False),
            "motivo_rejeicao": rev.get("motivo_rejeicao"), "avaliacao": rev.get("avaliacao"),
            "ajuste": rev.get("ajuste"),
        })
    out.mkdir(parents=True, exist_ok=True)
    dados = {"formato": FORMATO, "detector": detector, "video": video, "duracao": round(duracao, 2),
             "momentos": lista}
    tmp = arq.with_name(arq.name + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    substituir_arquivo(tmp, arq)      # a interface pode estar lendo/editando: o arquivo nunca fica pela metade
