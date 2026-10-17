"""Índice de todas as lives já analisadas: `saida/indice.json` (para a futura interface) e
`saida/indice.txt` (para ler). Cada análise atualiza só a linha do próprio vídeo; as outras ficam."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .moments import Moment
from .partes import Parte


def _mmss(t: float) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def ler_indice(out_root: Path) -> list[dict]:
    try:
        dados = json.loads((out_root / "indice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return dados if isinstance(dados, list) else []


def atualizar_indice(out_root: Path, video_id: str, arquivo: str, duracao: float, moments: list[Moment],
                     partes: list[Parte], processado_em: str, caminho: str = "") -> list[dict]:
    linha = {
        "id": video_id, "arquivo": arquivo, "caminho": caminho, "duracao": round(duracao, 1), "processado_em": processado_em,
        "momentos": len(moments), "abates": sum(len(m.kills) for m in moments),
        "emocoes": dict(Counter(m.emocao for m in moments if m.emocao)),
        "partidas": [{"titulo": p.titulo, "placar": p.placar, "rounds": len(p.rounds),
                      "inicio": round(p.inicio, 1), "fim": round(p.fim, 1)} for p in partes if p.tipo == "partida"],
    }
    indice = [x for x in ler_indice(out_root) if x.get("id") != video_id] + [linha]
    indice.sort(key=lambda x: x.get("processado_em", ""), reverse=True)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "indice.json").write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_root / "indice.txt").write_text(texto_indice(indice), encoding="utf-8")
    return indice


def texto_indice(indice: list[dict]) -> str:
    linhas = [f"Lives analisadas ({len(indice)})", ""]
    for x in indice:
        partidas = ", ".join(f"{p['titulo']} {_mmss(p['inicio'])}–{_mmss(p['fim'])}" for p in x.get("partidas", [])) \
            or "nenhuma partida detectada"
        emo = ", ".join(f"{n} {c}" for c, n in x.get("emocoes", {}).items()) or "sem emoção"
        linhas.append(f"{x['id']}  {_mmss(x['duracao'])}  {x['momentos']} momentos · {x['abates']} abates · {emo}")
        linhas.append(f"    {partidas}  (analisado em {x['processado_em']})")
    return "\n".join(linhas) + "\n"
