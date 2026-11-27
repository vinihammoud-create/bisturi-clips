"""Índice de todas as lives já analisadas: `saida/indice.json` (para a futura interface) e
`saida/indice.txt` (para ler). Cada análise atualiza só a linha do próprio vídeo; as outras ficam."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .moments import Moment
from .partes import Parte
from .tools import gravar_atomico, substituir_arquivo


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
    substituir_arquivo(arq, copia)
    print(f"    [aviso] {arq.name} estava ilegível: guardado em {copia.name} e a biblioteca recomeça deste vídeo")


def definir_titulo(out_root: Path, video_id: str, titulo: str) -> bool:
    """Põe (ou troca) o título legível de um vídeo já no índice, sem mexer no resto da linha. False se o vídeo não está no índice."""
    indice = ler_indice(out_root)
    achou = False
    for x in indice:
        if x.get("id") == video_id:
            x["titulo"] = titulo
            achou = True
    if achou:
        gravar_atomico(out_root / "indice.json", json.dumps(indice, ensure_ascii=False, indent=1))
        gravar_atomico(out_root / "indice.txt", texto_indice(indice))
    return achou


def remover_do_indice(out_root: Path, ids: list[str]) -> list[str]:
    """Tira do índice os kits apagados (as outras linhas ficam). Devolve os ids que estavam lá.
    Índice ilegível não é regravado (fica como está)."""
    arq = out_root / "indice.json"
    try:
        dados = json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(dados, list):
        return []
    tirar = set(ids)
    tirados = [x.get("id") for x in dados if x.get("id") in tirar]
    if tirados:
        resto = [x for x in dados if x.get("id") not in tirar]
        gravar_atomico(arq, json.dumps(resto, ensure_ascii=False, indent=1))
        gravar_atomico(out_root / "indice.txt", texto_indice(resto))
    return tirados


def atualizar_indice(out_root: Path, video_id: str, arquivo: str, duracao: float, moments: list[Moment],
                     partes: list[Parte], processado_em: str, caminho: str = "", titulo: str = "") -> list[dict]:
    linha = {
        "id": video_id, "arquivo": arquivo, "caminho": caminho, "duracao": round(duracao, 1), "processado_em": processado_em,
        "momentos": len(moments), "abates": sum(len(m.kills) for m in moments),
        "emocoes": dict(Counter(m.emocao for m in moments if m.emocao)),
        "partidas": [{"titulo": p.titulo, "placar": p.placar, "rounds": len(p.rounds),
                      "inicio": round(p.inicio, 1), "fim": round(p.fim, 1)} for p in partes if p.tipo == "partida"],
    }
    if titulo:
        linha["titulo"] = titulo           # nome legível para a Biblioteca (kit de imagem limpa: "Mirage 5x13 · 20/10 (imagem limpa)")
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
        # .get em tudo: uma linha velha ou incompleta não pode derrubar o índice inteiro (nem o "Apagar kit")
        partidas = ", ".join(f"{p.get('titulo', '')} {_mmss(p.get('inicio', 0))}–{_mmss(p.get('fim', 0))}"
                             for p in x.get("partidas") or []) or "nenhuma partida detectada"
        emo = ", ".join(f"{n} {c}" for c, n in (x.get("emocoes") or {}).items()) or "sem emoção"
        linhas.append(f"{x.get('id', '?')}  {_mmss(x.get('duracao') or 0)}  {x.get('momentos', 0)} momentos · {x.get('abates', 0)} abates · {emo}")
        linhas.append(f"    {partidas}  (analisado em {x.get('processado_em', '?')})")
    return "\n".join(linhas) + "\n"
