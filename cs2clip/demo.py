"""Entrada por demo (`.dem`): lê os eventos exatos do jogo com o `demoparser2` (MIT) e devolve as jogadas do
jogador no mesmo formato `Moment` que o resto do programa usa.

A demo dá o que o vídeo só consegue estimar: instante certo de cada abate (e de cada morte), headshot,
parede (wallbang), sem mira (noscope), fumaça, cegado, round, placar e clutch 1vX. Não traz voz nem reação:
isso continua vindo do vídeo. Os tempos saem em segundos DA DEMO (tick ÷ 64); para casar com um vídeo,
some o deslocamento (`deslocamento`) que a sincronização descobrir.

Só LÊ a demo. Não abre o CS2 nem mexe em nada do jogo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .moments import Event, Moment, _label
from .nota import pontuar

TICKRATE = 64.0            # CS2 em servidor oficial; o cabeçalho da demo não informa
CLUTCH_MIN_INIMIGOS = 2    # 1v2 ou mais conta como clutch (1v1 é só um duelo)
ERRO_ILEGIVEL = "Demo ilegível ou não é uma demo de CS2"


class DemoInvalida(Exception):
    """Arquivo que não abre como demo (mensagem pronta para o usuário)."""


@dataclass
class Abate:
    t: float                      # segundos da demo
    round: int
    vitima: str
    arma: str
    headshot: bool = False
    parede: bool = False
    sem_mira: bool = False
    fumaca: bool = False
    cego: bool = False
    no_ar: bool = False
    distancia: float = 0.0

    @property
    def icones(self) -> list[str]:
        nomes = (("headshot", self.headshot), ("wallbang", self.parede), ("noscope", self.sem_mira),
                 ("smoke", self.fumaca), ("blind", self.cego), ("inair", self.no_ar))
        return [n for n, ligado in nomes if ligado]


@dataclass
class RoundDemo:
    n: int
    inicio: float                 # fim do tempo de compra (começo da ação), s
    fim: float
    vencedor: str                 # "T" | "CT"
    motivo: str = ""


@dataclass
class Clutch:
    round: int
    t: float                      # instante em que ficou sozinho
    inimigos: int                 # quantos adversários vivos nesse instante
    ganhou: bool
    abates: int = 0               # abates feitos depois de ficar sozinho


@dataclass
class PartidaDemo:
    mapa: str
    jogadores: dict[int, str]                      # steamid -> nome
    jogador: int | None = None                     # steamid escolhido
    nome: str = ""
    rounds: list[RoundDemo] = field(default_factory=list)
    abates: list[Abate] = field(default_factory=list)      # só do jogador
    mortes: list[float] = field(default_factory=list)      # instantes das mortes do jogador
    clutches: list[Clutch] = field(default_factory=list)
    placar: tuple[int, int] = (0, 0)               # (rounds do jogador, rounds do adversário)
    lados: dict[int, str] = field(default_factory=dict)   # nº do round -> "T" | "CT" do jogador

    @property
    def titulo(self) -> str:
        return f"{self.mapa} {self.placar[0]}x{self.placar[1]}"


def _tick_s(tick: float) -> float:
    return round(float(tick) / TICKRATE, 3)


def _abrir(caminho: Path):
    try:
        from demoparser2 import DemoParser
    except ImportError as e:  # pragma: no cover
        raise DemoInvalida("Falta instalar o leitor de demos: pip install demoparser2==0.42.0") from e
    try:
        return DemoParser(str(caminho))
    except Exception as e:
        raise DemoInvalida(f"{ERRO_ILEGIVEL}: {Path(caminho).name}") from e


def _escolher(jogadores: dict[int, str], abates_por: dict[int, int], quem: str | int | None) -> int | None:
    """`quem`: nome (parte do nome, sem maiúsculas) ou steamid. Sem `quem`, não adivinha (None)."""
    if quem in (None, ""):
        return None
    q = str(quem).strip().lower()
    for sid, nome in jogadores.items():
        if q == str(sid):
            return sid
    achados = [sid for sid, nome in jogadores.items() if q in nome.lower()]
    if len(achados) == 1:
        return achados[0]
    if len(achados) > 1:   # desempata pelo nome exato, depois por quem mais matou
        exato = [sid for sid in achados if jogadores[sid].lower() == q]
        if len(exato) == 1:
            return exato[0]
        return max(achados, key=lambda s: abates_por.get(s, 0))
    return None


def listar_jogadores(caminho: Path) -> list[tuple[str, int, int]]:
    """[(nome, steamid, abates)] do mais ao menos abates: para o usuário escolher quem é ele."""
    p = _abrir(Path(caminho))
    try:
        d = p.parse_event("player_death")
    except Exception as e:
        raise DemoInvalida(f"{ERRO_ILEGIVEL}: {Path(caminho).name}") from e
    cont: dict[int, tuple[str, int]] = {}
    for _, r in d.iterrows():
        sid, nome = r.get("attacker_steamid"), r.get("attacker_name")
        if sid == sid and sid:        # descarta NaN (morte sem atacante: queda, bomba)
            cont[int(sid)] = (str(nome), cont.get(int(sid), ("", 0))[1] + 1)
    return sorted(((n, s, k) for s, (n, k) in cont.items()), key=lambda x: -x[2])


def ler_demo(caminho: Path, jogador: str | int | None = None) -> PartidaDemo:
    """Lê a demo inteira (1–6 s). `jogador`: nome (ou parte) ou steamid de quem estamos editando."""
    caminho = Path(caminho)
    p = _abrir(caminho)
    try:
        mapa = str(p.parse_header().get("map_name", "") or "mapa?")
        mortes = p.parse_event("player_death", player=["team_num"])
        fim_compra = p.parse_event("round_freeze_end")
        fim_round = p.parse_event("round_end")
    except Exception as e:
        raise DemoInvalida(f"{ERRO_ILEGIVEL}: {caminho.name}") from e
    if not len(fim_round) or not len(fim_compra):
        raise DemoInvalida(f"Esta demo não tem rounds completos: {caminho.name}")

    jogadores: dict[int, str] = {}
    abates_por: dict[int, int] = {}
    for _, r in mortes.iterrows():
        for pre in ("attacker", "user"):
            sid = r.get(f"{pre}_steamid")
            if sid == sid and sid:
                jogadores[int(sid)] = str(r.get(f"{pre}_name"))
        a = r.get("attacker_steamid")
        if a == a and a and a != r.get("user_steamid"):
            abates_por[int(a)] = abates_por.get(int(a), 0) + 1

    # rounds: cada um vai do fim do tempo de compra ao fim do round (pareados em ordem)
    inicios = sorted(int(t) for t in fim_compra["tick"])
    fins = fim_round.sort_values("tick")
    rounds: list[RoundDemo] = []
    for i, (_, r) in enumerate(fins.iterrows()):
        ini = next((t for t in reversed(inicios) if t < int(r["tick"])), None)
        if ini is None:
            continue
        rounds.append(RoundDemo(i + 1, _tick_s(ini), _tick_s(r["tick"]), str(r["winner"]), str(r.get("reason", ""))))

    def round_do(t: float) -> RoundDemo | None:
        for rd in reversed(rounds):
            if t >= rd.inicio - 0.01:
                return rd if t <= rd.fim + 8 else None   # até 8 s depois: morte logo após o fim do round
        return None

    sid = _escolher(jogadores, abates_por, jogador)
    partida = PartidaDemo(mapa, jogadores, sid, jogadores.get(sid, "") if sid else "", rounds)
    if sid is None:
        return partida

    # elenco de cada round (time de cada jogador no começo da ação; os times trocam de lado no intervalo)
    vivos: dict[int, dict[str, set[int]]] = {}
    meu_time: dict[int, str] = {}
    ini_por_tick = {int(round(rd.inicio * TICKRATE)): rd for rd in rounds}
    try:
        elenco = p.parse_ticks(["team_num"], ticks=sorted(ini_por_tick))
    except Exception as e:
        raise DemoInvalida(f"{ERRO_ILEGIVEL}: {caminho.name}") from e
    for _, r in elenco.iterrows():
        rd = ini_por_tick.get(int(r["tick"]))
        if rd is None or int(r["team_num"]) not in (2, 3):
            continue
        lado = "T" if int(r["team_num"]) == 2 else "CT"
        vivos.setdefault(rd.n, {"T": set(), "CT": set()})[lado].add(int(r["steamid"]))
        if int(r["steamid"]) == sid:
            meu_time[rd.n] = lado

    for _, r in mortes.iterrows():
        t = _tick_s(r["tick"])
        rd = round_do(t)
        if rd is None:
            continue
        atk, vit = r.get("attacker_steamid"), r.get("user_steamid")
        if atk == atk and atk and int(atk) == sid and int(atk) != int(vit):
            tm_vit = r.get("user_team_num")
            tm_atk = r.get("attacker_team_num")
            if tm_vit == tm_atk:         # fogo amigo não conta como abate
                continue
            partida.abates.append(Abate(
                t, rd.n, str(r.get("user_name")), str(r.get("weapon")), bool(r.get("headshot")),
                bool(r.get("penetrated") and int(r.get("penetrated")) > 0), bool(r.get("noscope")),
                bool(r.get("thrusmoke")), bool(r.get("attackerblind")), bool(r.get("attackerinair")),
                float(r.get("distance") or 0.0)))
        if vit == vit and vit and int(vit) == sid:
            partida.mortes.append(t)

    venceu = perdeu = 0
    for rd in rounds:
        tm = meu_time.get(rd.n)
        if tm is None:
            continue
        venceu += rd.vencedor == tm
        perdeu += rd.vencedor != tm
    partida.placar = (venceu, perdeu)
    partida.lados = dict(meu_time)
    partida.clutches = _clutches(mortes, rounds, round_do, sid, meu_time, vivos, partida.abates)
    return partida


def _clutches(mortes, rounds, round_do, sid, meu_time, vivos, abates) -> list[Clutch]:
    """Clutch = ficou como último vivo do time com ≥ CLUTCH_MIN_INIMIGOS adversários vivos."""
    por_round: dict[int, list[tuple[float, int, int]]] = {}
    for _, r in mortes.iterrows():
        t = _tick_s(r["tick"])
        rd = round_do(t)
        vit = r.get("user_steamid")
        if rd is not None and vit == vit and vit:
            por_round.setdefault(rd.n, []).append((t, int(vit), int(r.get("user_team_num"))))
    saida: list[Clutch] = []
    for rd in rounds:
        tm = meu_time.get(rd.n)
        if tm is None:
            continue
        meus = set(vivos[rd.n][tm])
        deles = set(vivos[rd.n]["T" if tm == "CT" else "CT"])
        for t, vit, num in sorted(por_round.get(rd.n, [])):
            if vit == sid:
                break
            lado = "T" if num == 2 else "CT"
            (meus if lado == tm else deles).discard(vit)
            if len(meus) == 1 and sid in meus and len(deles) >= CLUTCH_MIN_INIMIGOS:
                feitos = sum(1 for a in abates if a.round == rd.n and a.t >= t)
                saida.append(Clutch(rd.n, t, len(deles), rd.vencedor == tm, feitos))
                break
    return saida


# ── da demo para Momentos ─────────────────────────────────────────────────────────────────────

def momentos_da_demo(partida: PartidaDemo, antes: float = 4.0, depois: float = 2.5, pular: float = 6.0,
                     deslocamento: float = 0.0) -> list[Moment]:
    """Uma jogada por round com abates do jogador (mesma regra do vídeo): todos os abates do round num vídeo só,
    trechos de `antes`/`depois` em volta de cada abate, tempo parado maior que `pular` cortado. Como a demo sabe
    a morte, o último trecho não passa de 1 s depois dela."""
    from .sequencia import juntar_trechos
    por_round: dict[int, list[Abate]] = {}
    for a in sorted(partida.abates, key=lambda a: a.t):
        por_round.setdefault(a.round, []).append(a)
    clutches = {c.round: c for c in partida.clutches}
    duracao = (partida.rounds[-1].fim + 30) if partida.rounds else float("inf")
    saida: list[Moment] = []
    for n_round, g in sorted(por_round.items()):
        rd = next((r for r in partida.rounds if r.n == n_round), None)
        morte = next((t for t in sorted(partida.mortes) if rd and rd.inicio <= t <= rd.fim + 8 and t >= g[-1].t), None)
        fim_max = min(duracao, morte + 1.0) if morte is not None else duracao
        trechos = juntar_trechos([(max(0.0, a.t - antes), min(fim_max, a.t + depois)) for a in g], pular)
        trechos = [(a + deslocamento, b + deslocamento) for a, b in trechos]
        eventos = [Event(round(a.t + deslocamento, 3), "kill", a.icones) for a in g]
        m = Moment(trechos[0][0], trechos[-1][1], "abate", kills=eventos, label=_label(eventos))
        m.segmentos = trechos if len(trechos) > 1 else []
        c = clutches.get(n_round)
        if c and any(a.t >= c.t for a in g):
            m.clutch = (c.inimigos, c.ganhou)   # a nota (cs2clip/nota.py) pesa o clutch
            if c.ganhou and len(g) < 5:
                m.label = f"CLUTCH 1v{c.inimigos}"
        m.round_n = n_round
        lado = partida.lados.get(n_round)
        if rd and lado:
            m.round_resultado = "vitoria" if rd.vencedor == lado else "derrota"
        m.parte, m.parte_tipo = partida.titulo, "partida"
        pontuar(m, [])   # mesma nota 0-99 e faixa dos momentos do vídeo (sem partes: sem bônus de pistol/match point)
        saida.append(m)
    for i, m in enumerate(saida, 1):
        m.idx = i
    return saida


def analisar_demo(caminho: Path, cfg, out_root: Path, jogador: str | int | None = None):
    """Lê a demo e grava `saida/<demo>/momentos.json` + `demo.json` (rounds, placar, clutches).
    Devolve (partida, momentos, pasta). Sem `jogador` não adivinha: levanta `DemoInvalida` com a lista."""
    import json

    from .momentos_io import gravar_momentos
    caminho = Path(caminho)
    quem = jogador or cfg.demo.jogador
    partida = ler_demo(caminho, quem)
    if partida.jogador is None:
        nomes = ", ".join(f"{n} ({k} abates)" for n, _, k in listar_jogadores(caminho)[:10])
        dica = f"Não achei o jogador '{quem}'." if quem else "Diga quem é você na demo."
        raise DemoInvalida(f"{dica} Use --jogador \"nome\" (ou [demo] jogador no config.toml). Jogadores: {nomes}")
    c = cfg.cortes
    momentos = momentos_da_demo(partida, c.antes, c.depois, c.pular_parado)
    out = out_root / caminho.stem[:60]
    duracao = partida.rounds[-1].fim if partida.rounds else 0.0
    gravar_momentos(out, momentos, caminho.stem, duracao, "demo-1")
    resumo = {
        "formato": 1, "mapa": partida.mapa, "jogador": partida.nome, "placar": list(partida.placar),
        "rounds": [{"n": r.n, "inicio": r.inicio, "fim": r.fim, "vencedor": r.vencedor, "motivo": r.motivo,
                    "lado_do_jogador": partida.lados.get(r.n)} for r in partida.rounds],
        "abates": [{"t": a.t, "round": a.round, "vitima": a.vitima, "arma": a.arma, "icones": a.icones,
                    "distancia": round(a.distancia, 1)} for a in partida.abates],
        "mortes": partida.mortes,
        "clutches": [{"round": k.round, "t": k.t, "inimigos": k.inimigos, "ganhou": k.ganhou, "abates": k.abates}
                     for k in partida.clutches],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "demo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    return partida, momentos, out
