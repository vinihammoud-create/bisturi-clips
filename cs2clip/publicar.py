"""Títulos, hashtags e descrição prontos para postar (pesquisa P7, R13: títulos por estilo do Allstar, hashtags do
StreamLadder). Tudo por MODELOS fixos em português, montados a partir do que o programa mediu (rótulo da jogada,
headshots, match point, pistol, velocidade, emoção): nada de texto livre de IA. Saída: `publicar.txt` (para ler e
copiar) e `publicar.json` (para a interface), na pasta do vídeo.
"""
from __future__ import annotations

import json
from pathlib import Path

from .moments import Moment

BASE = {1: "Que tiro", 2: "DOUBLE KILL", 3: "TRIPLE KILL", 4: "QUADRA KILL", 5: "ACE"}
HASHTAGS_FIXAS = ["#CS2", "#CounterStrike2"]   # sem #Premier: o programa não sabe o modo (Faceit, Competitivo...)
FAIXA_TXT = {"DESTAQUE": "Destaque", "PADRAO": "Padrão", "RELATORIO": "Fraco"}   # o json guarda o valor interno


def _contexto(m: Moment) -> str:
    """O detalhe mais forte da jogada, em português (vazio se não houver)."""
    motivos = " ".join(m.reasons).lower()
    if "match point" in motivos:
        return "no match point"
    if "12x12" in motivos:
        return "no 12x12"
    if "pistol" in motivos:
        return "no pistol"
    if len(m.kills) >= 2 and all(k.hs for k in m.kills):
        return "só de HS"
    if len(m.kills) >= 3 and m.kills[-1].t - m.kills[0].t <= 5.0:
        d = m.kills[-1].t - m.kills[0].t
        # abaixo de 2 s, uma casa decimal: "em 0 segundos"/"em 1 segundos" ficavam errados
        return f"em {d:.1f} s".replace(".", ",") if d < 2 else f"em {round(d)} segundos"
    if any("smoke" in k.icons for k in m.kills):
        return "pela fumaça"
    return ""


def titulos(m: Moment) -> list[str]:
    """3 opções de título (até ~60 caracteres)."""
    if m.kind == "fail" or (m.emocao == "RISADA" and not m.kills):
        return ["O pior jeito de morrer no CS2 😂", "Não aguentei kkkk", "Isso não pode acontecer no CS2"]
    if m.emocao == "RAIVA" and not m.kills:
        return ["Não acredito nisso 😡", "Perdi a cabeça nesse round", "CS2 me tirando do sério"]
    n = min(len(m.kills), 5)
    if n == 0:
        return ["Que momento na live 🔥", "Olha isso no CS2", "Isso aconteceu na live de hoje"]
    base = BASE[n] if n > 1 or not m.kills[0].hs else "Que HS"
    if m.clutch and m.clutch[1]:   # clutch ganho (só com a demo): é o que a jogada tem de mais forte
        base = f"CLUTCH 1v{m.clutch[0]}"
    ctx = _contexto(m)
    fogo = " 🔥" if m.faixa == "DESTAQUE" else ""
    return [f"{base} {ctx}".strip() + fogo,
            f"Olha esse {base if n > 1 else 'tiro'} {ctx}".strip(),
            f"{base} {ctx} | CS2".replace("  ", " ")]


def _nome(marca: str) -> str:
    """O @ do canal sem o '@' e sem espaços ("" = sem marca: nada de '#' sozinho nem 'twitch.tv/')."""
    return (marca or "").strip().lstrip("@").strip()


def hashtags(m: Moment, marca: str, short: bool = True) -> list[str]:
    tags = list(HASHTAGS_FIXAS)
    if _nome(marca):
        tags.append("#" + _nome(marca).replace(" ", ""))
    n = len(m.kills)
    if n >= 5:
        tags.append("#ace")
    if n >= 2:
        tags.append("#multikill")
    if m.clutch and m.clutch[1]:
        tags.append("#clutch")
    if any(k.hs for k in m.kills):
        tags.append("#headshot")
    if "match point" in " ".join(m.reasons).lower():
        tags.append("#matchpoint")
    if m.emocao == "RAIVA":
        tags.append("#rage")
    if m.kind == "fail":
        tags += ["#fail", "#funny"]
    elif m.emocao == "RISADA":
        tags.append("#funny")
    if short:
        tags.append("#shorts")
    vistos: list[str] = []
    for t in tags:
        if t not in vistos:
            vistos.append(t)
    return vistos


def gerar_publicar(out: Path, video: str, shorts: list[Moment], arquivos: dict[int, dict], marca: str) -> Path | None:
    """Escreve publicar.txt e publicar.json para os shorts que foram gerados. Devolve o .txt (ou None)."""
    itens = []
    for m in shorts:
        arq = (arquivos.get(m.idx) or {})
        if not arq.get("short"):
            continue
        tit = titulos(m)
        tags = hashtags(m, marca)
        canal = f"\n\nLive completa: twitch.tv/{_nome(marca)}" if _nome(marca) else ""   # só com marca
        itens.append({"n": m.idx, "short": arq.get("short"), "short_efeitos": arq.get("short_efeitos"),
                      "capa": arq.get("miniatura"), "titulos": tit, "hashtags": tags,
                      "descricao": f"{tit[0]}{canal}\n\n{' '.join(tags)}", "nota": round(m.score), "faixa": m.faixa})
    if not itens:   # sem shorts: não deixa textos de uma análise anterior para trás
        for velho in (out / "publicar.json", out / "publicar.txt"):
            velho.unlink(missing_ok=True)
        return None
    (out / "publicar.json").write_text(json.dumps({"video": video, "itens": itens}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    linhas = [f"Para postar — {video}", "(3 opções de título; copie a descrição inteira com as hashtags)", ""]
    for i in itens:
        linhas += [f"■ {i['short']}" + (f"   (com efeitos: {i['short_efeitos']})" if i["short_efeitos"] else ""),
                   f"  nota {i['nota']} · {FAIXA_TXT.get(i['faixa'], i['faixa'])} · capa sugerida: {i['capa'] or 'nenhuma'}",
                   *[f"  Título {k}: {t}" for k, t in enumerate(i["titulos"], 1)],
                   "  Descrição:", *[f"    {l}" for l in i["descricao"].splitlines()], ""]
    txt = out / "publicar.txt"
    txt.write_text("\n".join(linhas), encoding="utf-8")
    return txt
