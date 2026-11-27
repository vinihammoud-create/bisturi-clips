"""Divide o vídeo em PARTES: partidas (com os rounds lidos pelos banners), Deathmatch/aquecimento
e trechos fora de partida. Serve para o usuário achar "a partida de 10x6" numa live de horas e
para só dar contexto de round (ganho/perdido) a quem está dentro de uma partida.

Sem demo e sem OCR do placar (o HUD do streamer muda de sessão para sessão), tudo vem dos banners
"DERROTA"/"VITÓRIA" (`rounds.py`):
  * rounds com no máximo `gap_max` s entre si são da mesma partida; menos de `min_rounds` rounds
    não é partida (um banner solto é leitura errada);
  * o placar é a contagem de vitórias x derrotas lidas: APROXIMADO, porque a varredura por
    quadros-chave pode perder um banner curto (`rounds_faltando` estima quantos);
  * o que sobra entre as partidas é Deathmatch/aquecimento se tiver abates seus, senão "fora".
"""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .rounds import RoundTimelineEntry
from .tools import gravar_atomico

GAP_MAX = 240.0       # s entre dois banners para ainda ser a mesma partida (round ~2 min + intervalo)
MIN_ROUNDS = 3        # menos que isso não é partida
DM_MIN_ABATES = 3     # abates seus num trecho fora de partida para chamar de Deathmatch/aquecimento
TRECHO_MIN = 30.0     # s: trechos fora de partida menores que isso são ignorados
FIM_BANNER = 12.0     # s depois do último banner (banner + tela final) em que a partida termina
ROUND_MIN, ROUND_MAX = 60.0, 120.0   # s: duração típica de um round, para estimar o início da partida
JANELA_ROUND = 1.4    # x a duração típica do round: um momento só herda o resultado do próximo banner se ele
                      # vier a menos disso (senão o banner do round do momento foi perdido e o resultado é desconhecido)
FIM_ESTENDIDO = 1.5   # x a duração típica: se o vídeo acaba a menos disso depois do último banner, o resto é
                      # (quase certamente) o(s) último(s) round(s) com banner não lido: continua dentro da partida
BANNER_PERDIDO = 1.5  # intervalo entre banners > 1,5 round típico: deve haver um banner não lido no meio
ROUND_LONGO = 150.0   # s: ou > 150 s (compra + 1:55 + bomba ~175 no máximo; com banners perdidos a média infla)


@dataclass
class Parte:
    tipo: str                     # "partida" | "deathmatch" | "fora"
    inicio: float
    fim: float
    n: int = 0                    # número dentro do tipo (Partida 1, 2...; Deathmatch 1...)
    rounds: list[RoundTimelineEntry] = field(default_factory=list)
    vitorias: int = 0
    derrotas: int = 0
    rounds_faltando: int = 0      # estimativa de banners que a varredura não viu
    abates: int = 0
    dur_round: float = 100.0      # duração típica de um round nesta partida (mediana entre banners)
    numeros: list[int] = field(default_factory=list)   # nº real estimado de cada banner (conta os perdidos)
    exata: bool = False           # rounds, placar e limites vêm da demo da partida (cs2clip/sincronizar.py)
    classificacao: str = ""       # B7: COMPETITIVE | DEATHMATCH | UNKNOWN ("" = JSON antigo, sem classificação)
    confianca_tipo: float = 0.0   # 0–1: quanto o programa confia nessa classificação
    evidencias: list[str] = field(default_factory=list)   # por que (em português, uma linha cada)
    precisa_confirmar: bool = False   # incerto: o dono deve conferir (nada é descartado em silêncio)

    @property
    def placar(self) -> str:
        return f"{self.vitorias}x{self.derrotas}"

    @property
    def titulo(self) -> str:
        if self.tipo == "partida":
            return f"Partida {self.n} ({self.placar})"
        if self.tipo == "deathmatch":
            return "Deathmatch/aquecimento" if self.n <= 1 else f"Deathmatch/aquecimento {self.n}"
        return "Fora de partida"


def _grupos(rounds: list[RoundTimelineEntry], gap_max: float) -> list[list[RoundTimelineEntry]]:
    out: list[list[RoundTimelineEntry]] = []
    for r in sorted(rounds, key=lambda r: r.t):
        if out and r.t - out[-1][-1].t <= gap_max:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def _partida(g: list[RoundTimelineEntry], duration: float, completa: bool = False) -> Parte:
    """`completa`: os rounds vêm da demo (todos, sem banner perdido): a numeração é 1, 2, 3... e nada é estimado."""
    gaps = [b.t - a.t for a, b in zip(g, g[1:])]
    med = min(ROUND_MAX, max(ROUND_MIN, statistics.median(gaps))) if gaps else 100.0
    perdidos = [max(0, round(gp / med) - 1) if gp > 1.7 * med and not completa else 0 for gp in gaps]
    numeros = [1 + i + sum(perdidos[:i]) for i in range(len(g))]   # banner perdido desloca os números seguintes
    fim = min(duration, g[-1].t + FIM_BANNER)
    if duration - fim <= FIM_ESTENDIDO * med:
        fim = duration   # o vídeo acaba logo depois: o último round (banner não lido) ainda é da partida
    return Parte("partida", max(0.0, g[0].t - med), fim, 0, list(g),
                 sum(r.resultado == "vitoria" for r in g), sum(r.resultado == "derrota" for r in g),
                 sum(perdidos), dur_round=med, numeros=numeros)


def dividir_partes(rounds: list[RoundTimelineEntry], abates: list[float], duration: float,
                   gap_max: float = GAP_MAX, min_rounds: int = MIN_ROUNDS,
                   exatos: list[tuple[float, float]] | None = None) -> list[Parte]:
    """`abates`: instantes (s) dos abates do próprio jogador. Devolve as partes em ordem de tempo,
    cobrindo o vídeo todo (trechos fora de partida < TRECHO_MIN s são descartados).
    `exatos`: (início, fim) de partidas conhecidas pela demo (cs2clip/sincronizar.py): os rounds dentro de cada
    intervalo formam UMA partida com esses limites, sem estimativa; os demais seguem a regra dos banners."""
    exatos = sorted(exatos or [])
    livres = [r for r in rounds if not any(a <= r.t <= b for a, b in exatos)]
    partidas = [_partida(g, duration) for g in _grupos(livres, gap_max) if len(g) >= min_rounds]
    for a, b in exatos:
        g = sorted((r for r in rounds if a <= r.t <= b), key=lambda r: r.t)
        if len(g) >= min_rounds:
            p = _partida(g, duration, completa=True)
            p.inicio, p.fim = a, b
            p.exata = True
            partidas.append(p)
    partidas.sort(key=lambda p: p.inicio)
    partes: list[Parte] = []
    cursor = 0.0
    ha_partida = bool(partidas)
    for p in partidas:
        partes += _fora(cursor, p.inicio, abates, ha_partida)
        partes.append(p)
        cursor = p.fim
    partes += _fora(cursor, duration, abates, ha_partida)
    cont = {"partida": 0, "deathmatch": 0, "fora": 0}
    for p in partes:
        cont[p.tipo] += 1
        p.n = cont[p.tipo]
        if p.tipo == "partida":
            p.abates = sum(p.inicio <= t <= p.fim for t in abates)
    for p in partes:
        avaliar(p, ha_partida)
    return partes


def _fora(a: float, b: float, abates: list[float], ha_partida: bool = True) -> list[Parte]:
    """Trecho sem partida. Só é "Deathmatch/aquecimento" se o vídeo tem uma partida de verdade (banners
    lidos) para contrastar: sem nenhum banner não dá para saber se é Deathmatch ou só um clipe de
    uma partida competitiva, então fica "fora de partida"."""
    if b - a < TRECHO_MIN:
        return []
    n = sum(a <= t < b for t in abates)
    return [Parte("deathmatch" if ha_partida and n >= DM_MIN_ABATES else "fora", a, b, abates=n)]


def avaliar(p: Parte, ha_partida: bool) -> None:
    """B7: preenche `classificacao`, `confianca_tipo`, `evidencias` e `precisa_confirmar` de uma parte. Regras simples
    e explicáveis: partida da demo = certeza; partida lida pelos banners ganha confiança com o nº de rounds; Deathmatch
    é inferido pela AUSÊNCIA de rounds (por isso sempre pede confirmação); trecho sem nada é desconhecido."""
    if p.tipo == "partida":
        p.classificacao = "COMPETITIVE"
        if p.exata:
            p.confianca_tipo, p.evidencias = 0.97, ["rounds, placar e limites vêm da demo da partida"]
        else:
            n = len(p.rounds)
            p.confianca_tipo = 0.9 if n >= 8 else 0.8 if n >= 5 else 0.6
            p.evidencias = [f"{n} rounds lidos pelos banners de vitória/derrota", f"placar lido {p.placar}"]
            if p.rounds_faltando:
                p.evidencias.append(f"estimados {p.rounds_faltando} banner(s) não lidos")
                p.confianca_tipo = max(0.0, round(p.confianca_tipo - 0.05, 2))
    elif p.tipo == "deathmatch":
        p.classificacao = "DEATHMATCH"
        p.confianca_tipo = round(min(0.75, 0.55 + 0.02 * max(0, p.abates - DM_MIN_ABATES)), 2)
        p.evidencias = [f"{p.abates} abates seus fora de qualquer round lido"]
        if ha_partida:
            p.evidencias.append("o vídeo tem partida com rounds lidos para contrastar")
    else:
        p.classificacao, p.confianca_tipo = "UNKNOWN", 0.3 if p.abates else 0.0
        p.evidencias = ["nenhum round lido neste trecho" + (f", mas há {p.abates} abate(s) seus" if p.abates else "")]
    p.precisa_confirmar = (p.tipo == "partida" and p.confianca_tipo < 0.7) or p.tipo == "deathmatch"         or (p.tipo == "fora" and p.abates > 0)


def parte_de(t: float, partes: list[Parte]) -> Parte | None:
    for p in partes:
        if p.inicio <= t < p.fim:
            return p
    return None


def round_de(t: float, partes: list[Parte]) -> tuple[Parte | None, int | None, str | None]:
    """(parte, nº do round dentro da partida, resultado do round) para um instante.
    O round de um momento é o que TERMINA logo depois dele (o próximo banner da mesma partida).
    Se esse banner está longe demais (> JANELA_ROUND x a duração típica do round), o banner do round do
    momento foi perdido na leitura: o resultado NÃO é herdado do round seguinte (None). Fora de partida
    ou depois do último banner, nº e resultado vêm None. O nº conta os banners perdidos. Com a parte EXATA (rounds da demo)
    não há banner perdido: o limite de distância não vale (G-001: round longo de 155 s não perde o nº dos primeiros abates)."""
    p = parte_de(t, partes)
    if p is None or p.tipo != "partida":
        return p, None, None
    for i, r in enumerate(p.rounds):
        if r.t >= t:
            if not p.exata and r.t - t > JANELA_ROUND * p.dur_round:   # com a demo (exata) não há banner perdido (G-001)
                return p, None, None
            numero = p.numeros[i] if i < len(p.numeros) else i + 1
            return p, numero, r.resultado
    return p, None, None


def banner_do_round(t: float, partes: list[Parte]) -> float | None:
    """O instante do banner de fim do round de um momento (o mesmo banner que `round_de` usa), ou None."""
    p = parte_de(t, partes)
    if p is None or p.tipo != "partida":
        return None
    for r in p.rounds:
        if r.t >= t:
            return None if not p.exata and r.t - t > JANELA_ROUND * p.dur_round else r.t
    return None


POS_ROUND = 7.0   # s: um abate até isto depois de um banner é "pós-round" (exit frag) do round que acabou


def ajustar_pos_round(t: float, partes: list[Parte]) -> float:
    """Abate logo DEPOIS de um banner de fim de round pertence ao round que acabou (o jogo ainda deixa matar por
    alguns segundos). Devolve um instante dentro desse round, para `round_de` achar o round certo. Revisão
    17/10/2026: um abate 1 s depois do banner foi unido aos abates do round seguinte, 96 s depois."""
    p = parte_de(t, partes)
    if p is None or p.tipo != "partida":
        return t
    for r in p.rounds:
        if 0 <= t - r.t <= POS_ROUND:
            return r.t - 0.01
    return t


def intervalo_do_round(t: float, partes: list[Parte]) -> tuple[float, float, bool] | None:
    """(banner anterior, banner seguinte, suspeito) do round em que `t` está. `suspeito` = o intervalo entre os
    dois banners é bem maior que um round típico: provavelmente um banner foi perdido ali e há mais de um round
    nesse intervalo. Com a parte EXATA (rounds vindos da demo) não há banner perdido: `suspeito` é sempre False,
    para todo consumidor concordar com a regra de ouro "nunca dividir os abates do mesmo round" (N4, 15/11/2026;
    mirage de 14/11, round 10 de 160 s). Sem demo a desconfiança continua igual. None fora de partida."""
    p = parte_de(t, partes)
    if p is None or p.tipo != "partida":
        return None
    for i, r in enumerate(p.rounds):
        if r.t >= t:
            anterior = p.rounds[i - 1].t if i > 0 else p.inicio
            gap = r.t - anterior
            return anterior, r.t, not p.exata and (gap > BANNER_PERDIDO * p.dur_round or gap > ROUND_LONGO)
    return None


def _mmss(t: float) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def texto_partes(partes: list[Parte], nome: str, duration: float) -> str:
    linhas = [f"Partes do vídeo {nome} ({_mmss(duration)})", ""]
    for p in partes:
        extra = ""
        if p.tipo == "partida":
            extra = (f" · placar exato (da demo), {len(p.rounds)} rounds" if p.exata
                     else f" · placar aproximado, {len(p.rounds)} rounds lidos")
            if p.rounds_faltando and not p.exata:
                extra += f" (~{p.rounds_faltando} podem ter ficado de fora)"
            extra += f" · {p.abates} abates seus"
        elif p.tipo == "deathmatch":
            extra = f" · {p.abates} abates seus"
        linhas.append(f"{_mmss(p.inicio)}–{_mmss(p.fim)}  {p.titulo}{extra}")
    return "\n".join(linhas) + "\n"


def capitulos_youtube(partes: list[Parte], duration: float) -> list[tuple[float, str]]:
    """Capítulos válidos para o YouTube: o 1º em 00:00, no mínimo 3, cada um com 10 s ou mais.
    Devolve [] se não der para montar (o YouTube ignora a lista inteira se for inválida)."""
    caps: list[tuple[float, str]] = []
    for p in partes:
        if caps and p.inicio - caps[-1][0] < 10.0:
            continue
        caps.append((p.inicio, p.titulo))
    if caps and caps[0][0] >= 10.0:
        caps.insert(0, (0.0, "Início"))
    elif caps:
        caps[0] = (0.0, caps[0][1])   # 1º capítulo a menos de 10 s do início: ancora em 00:00
    if len(caps) < 3 or any(b[0] - a[0] < 10.0 for a, b in zip(caps, caps[1:])) or duration - caps[-1][0] < 10.0:
        return []
    return caps


def gravar_partes(out: Path, partes: list[Parte], nome: str, duration: float) -> None:
    """`partes.json` (para o resto do programa) e `partes.txt` (para o usuário ler)."""
    out.mkdir(parents=True, exist_ok=True)
    dados = [{**{k: v for k, v in asdict(p).items() if k != "rounds"}, "titulo": p.titulo, "placar": p.placar,
              "rounds": [asdict(r) for r in p.rounds]} for p in partes]
    gravar_atomico(out / "partes.json", json.dumps(dados, ensure_ascii=False, indent=1))   # G-012: interrompido no meio, o antigo fica inteiro
    (out / "partes.txt").write_text(texto_partes(partes, nome, duration), encoding="utf-8")


def carregar_partes(out: Path) -> list[Parte]:
    """As partes gravadas em `partes.json`. Arquivo ausente, truncado ou de formato errado (G-012: queda no meio da gravação) = lista vazia:
    os capítulos do YouTube caem na lista de jogadas (pipeline) em vez de a análise inteira cair."""
    f = out / "partes.json"
    if not f.exists():
        return []
    try:
        return _ler_partes(f)
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _ler_partes(f: Path) -> list[Parte]:
    campos = {"tipo", "inicio", "fim", "n", "vitorias", "derrotas", "rounds_faltando", "abates"}
    return [Parte(**{k: d[k] for k in campos}, dur_round=d.get("dur_round", 100.0), numeros=d.get("numeros", []),
                  rounds=[RoundTimelineEntry(**r) for r in d["rounds"]], exata=bool(d.get("exata", False)),
                  classificacao=d.get("classificacao", ""), confianca_tipo=float(d.get("confianca_tipo", 0.0)),
                  evidencias=list(d.get("evidencias", [])), precisa_confirmar=bool(d.get("precisa_confirmar", False)))
            for d in json.loads(f.read_text(encoding="utf-8"))]


def linhas_capitulos(caps: list[tuple[float, str]]) -> list[str]:
    """`[(t, título)]` -> linhas "MM:SS título" (ou "H:MM:SS") como o YouTube lê na descrição."""
    return [f"{_mmss(t)} {titulo}" for t, titulo in caps]
