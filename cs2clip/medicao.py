"""Mede o acerto do programa contra o GABARITO do usuário (E7): precisão e recall de cada emoção e acerto dos
abates. O gabarito vem da página `revisao.html` (botão "Exportar CSV") ou é preenchido à mão
(`tools/gabarito_momentos.template.csv`). Veja docs/engineering/GABARITO.md.

Como cada linha do gabarito é casada com um momento do programa: o que está dentro de [inicio, fim] do momento e
mais perto do `foco`; a página de revisão exporta o `foco` truncado em segundos (tolerância de 1,5 s).

Limite honesto: a página de revisão só mostra momentos que o programa JÁ achou. Por isso o recall de emoção só vale
para os momentos revisados; jogadas que o programa deixou passar só aparecem se o usuário as anotar à mão."""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# emoção do programa -> categoria do gabarito
EMOCOES = {"RAIVA": "raiva", "HYPE": "alegria", "RISADA": "risada"}
CATEGORIAS = {"raiva", "alegria", "risada", "abate", "clutch", "nenhuma"}
SINONIMOS = {"hype": "alegria", "alegria/hype": "alegria", "nada": "nenhuma", "nenhum": "nenhuma", "kill": "abate"}
TOL_FOCO = 1.5


@dataclass
class Linha:
    vod: str
    t: float
    categorias: set[str]
    abate_certo: str = ""           # "sim" | "nao" | "sem" | ""
    obs: str = ""


@dataclass
class Metrica:
    categoria: str
    vp: int = 0          # o programa disse e o usuário confirmou
    fp: int = 0          # o programa disse, o usuário não confirmou
    fn: int = 0          # o usuário marcou, o programa não disse
    exemplos_fp: list[str] = field(default_factory=list)
    exemplos_fn: list[str] = field(default_factory=list)

    @property
    def precisao(self) -> float | None:
        return self.vp / (self.vp + self.fp) if self.vp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.vp / (self.vp + self.fn) if self.vp + self.fn else None


@dataclass
class Relatorio:
    linhas: int = 0
    sem_momento: int = 0                                  # linhas que não casaram com nenhum momento
    metricas: dict[str, Metrica] = field(default_factory=dict)
    abate_sim: int = 0
    abate_nao: int = 0
    avisos: list[str] = field(default_factory=list)


def _tempo(txt: str) -> float:
    txt = str(txt).strip().replace(",", ".")
    if ":" in txt:
        total = 0.0
        for p in txt.split(":"):
            total = total * 60 + float(p)
        return total
    return float(txt)


def _categorias(txt: str) -> set[str]:
    achadas = set()
    for p in re.split(r"[+,;/|\s]+", str(txt).strip().lower()):
        p = SINONIMOS.get(p, p)
        if p in CATEGORIAS:
            achadas.add(p)
    return achadas


def ler_gabarito(arquivo: Path) -> tuple[list[Linha], list[str]]:
    """Aceita BOM, separador `,` ou `;`, tempo em mm:ss, h:mm:ss ou segundos. Linhas ruins viram avisos."""
    texto = Path(arquivo).read_text(encoding="utf-8-sig")
    amostra = texto.splitlines()[0] if texto.strip() else ""
    delim = ";" if amostra.count(";") > amostra.count(",") else ","
    linhas: list[Linha] = []
    avisos: list[str] = []
    for n, row in enumerate(csv.DictReader(io.StringIO(texto), delimiter=delim), 2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not any(row.values()):
            continue
        try:
            t = _tempo(row["tempo"])
            cats = _categorias(row.get("categoria", ""))
            vod = row["vod_id"]
        except (KeyError, ValueError):
            avisos.append(f"linha {n} ignorada (faltou vod_id/tempo, ou o tempo não é mm:ss): {row}")
            continue
        abate = row.get("abate_certo", "").lower()
        # a página de revisão exporta também linhas só com o abate (ou só com o voto): a emoção é opcional
        tem_abate = abate in ("sim", "nao")
        if not cats and row.get("categoria"):
            avisos.append(f"linha {n}: categoria desconhecida {row.get('categoria')!r}"
                          + (" (só o abate conta)" if tem_abate else " (linha ignorada)"))
        if not cats and not tem_abate:
            continue
        linhas.append(Linha(vod, t, cats, abate, row.get("observacao", "")))
    return linhas, avisos


def _casar(t: float, momentos: list[dict]) -> dict | None:
    cand = [m for m in momentos if m.get("inicio", 1e9) - 0.5 <= t <= m.get("fim", -1e9) + 0.5
            or abs(m.get("foco", 1e9) - t) <= TOL_FOCO]
    return min(cand, key=lambda m: abs(m.get("foco", 0) - t)) if cand else None


def medir(linhas: list[Linha], momentos_por_vod: dict[str, list[dict]]) -> Relatorio:
    rel = Relatorio(linhas=len(linhas))
    for cat in EMOCOES.values():
        rel.metricas[cat] = Metrica(cat)
    for ln in linhas:
        m = _casar(ln.t, momentos_por_vod.get(ln.vod, []))
        rotulo = f"{ln.vod} {int(ln.t // 60):02d}:{int(ln.t % 60):02d}"
        if m is None:
            rel.sem_momento += 1
            for cat in ln.categorias & set(EMOCOES.values()):
                rel.metricas[cat].fn += 1
                rel.metricas[cat].exemplos_fn.append(rotulo + " (o programa nem achou esse momento)")
            continue
        prog = EMOCOES.get(m.get("emocao") or "")
        respondeu = ln.categorias & (set(EMOCOES.values()) | {"nenhuma"})
        for cat in (EMOCOES.values() if respondeu else ()):   # sem resposta de emoção: só o abate conta
            met = rel.metricas[cat]
            quer = cat in ln.categorias
            if quer and prog == cat:
                met.vp += 1
            elif prog == cat and not quer:
                met.fp += 1
                met.exemplos_fp.append(f"{rotulo} (disse {m['emocao']}, você marcou {'+'.join(sorted(ln.categorias))})")
            elif quer and prog != cat:
                met.fn += 1
                met.exemplos_fn.append(f"{rotulo} (você marcou {cat}, o programa disse {m.get('emocao') or 'nada'})")
        if m.get("abates") and ln.abate_certo in ("sim", "nao"):
            if ln.abate_certo == "sim":
                rel.abate_sim += 1
            else:
                rel.abate_nao += 1
    return rel


def carregar_momentos(saida: Path, vods: set[str]) -> tuple[dict[str, list[dict]], list[str]]:
    dados, avisos = {}, []
    for vod in sorted(vods):
        arq = Path(saida) / vod / "momentos.json"
        try:
            dados[vod] = json.loads(arq.read_text(encoding="utf-8")).get("momentos", [])
        except (OSError, ValueError):
            avisos.append(f"não achei/consegui ler {arq}: as linhas de '{vod}' não puderam ser conferidas")
    return dados, avisos


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.0f}%"


def texto(rel: Relatorio) -> str:
    out = [f"Medição contra o gabarito ({rel.linhas} linhas)", ""]
    if rel.sem_momento:
        out.append(f"  {rel.sem_momento} linha(s) não casaram com nenhum momento do programa.")
    out.append("")
    out.append(f"  {'emoção':10} {'precisão':>9} {'recall':>8}   acertos / falsos / perdidos")
    for cat, m in rel.metricas.items():
        out.append(f"  {cat:10} {_pct(m.precisao):>9} {_pct(m.recall):>8}   {m.vp} / {m.fp} / {m.fn}")
    tot = rel.abate_sim + rel.abate_nao
    out.append("")
    out.append(f"  Abates: {rel.abate_sim} de {tot} estavam certos ({_pct(rel.abate_sim / tot if tot else None)})")
    out.append("")
    out.append("  Como ler: precisão = das vezes que o programa disse a emoção, quantas você confirmou. "
               "Recall = das que você marcou, quantas o programa achou (só vale para o que ele mostrou na revisão).")
    for cat, m in rel.metricas.items():
        for titulo, ex in (("falsos", m.exemplos_fp), ("perdidos", m.exemplos_fn)):
            if ex:
                out.append(f"\n  {cat}: {titulo}")
                out += [f"    - {e}" for e in ex[:8]]
    for a in rel.avisos:
        out.append(f"\n  [aviso] {a}")
    return "\n".join(out)
