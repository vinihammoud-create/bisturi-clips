"""Índice de todas as lives já analisadas: `saida/indice.json` (para a futura interface) e
`saida/indice.txt` (para ler). Cada análise atualiza só a linha do próprio vídeo; as outras ficam."""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from .moments import Moment
from .partes import Parte
from .tools import gravar_atomico


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


def _guardar_se_ilegivel(arq: Path) -> None:
    """indice.json ilegível NÃO é regravado por cima (isso apagaria a biblioteca inteira): fica guardado como
    `indice.json.corrompido` para dar para recuperar, e a biblioteca recomeça."""
    try:
        if isinstance(json.loads(arq.read_text(encoding="utf-8")), list):
            return
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        pass
    copia = arq.with_name(arq.name + ".corrompido")
    os.replace(arq, copia)
    print(f"    [aviso] {arq.name} estava ilegível: guardado em {copia.name} e a biblioteca recomeça deste vídeo")


def atualizar_indice(out_root: Path, video_id: str, arquivo: str, duracao: float, moments: list[Moment],
                     partes: list[Parte], processado_em: str, caminho: str = "") -> list[dict]:
    linha = {
        "id": video_id, "arquivo": arquivo, "caminho": caminho, "duracao": round(duracao, 1), "processado_em": processado_em,
        "momentos": len(moments), "abates": sum(len(m.kills) for m in moments),
        "emocoes": dict(Counter(m.emocao for m in moments if m.emocao)),
        "partidas": [{"titulo": p.titulo, "placar": p.placar, "rounds": len(p.rounds),
                      "inicio": round(p.inicio, 1), "fim": round(p.fim, 1)} for p in partes if p.tipo == "partida"],
    }
    _guardar_se_ilegivel(out_root / "indice.json")
    indice = [x for x in ler_indice(out_root) if x.get("id") != video_id] + [linha]
    indice.sort(key=lambda x: x.get("processado_em", ""), reverse=True)
    out_root.mkdir(parents=True, exist_ok=True)
    gravar_atomico(out_root / "indice.json", json.dumps(indice, ensure_ascii=False, indent=1))
    gravar_atomico(out_root / "indice.txt", texto_indice(indice))
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
