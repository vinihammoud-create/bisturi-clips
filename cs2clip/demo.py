"""Entrada por demo (`.dem`): lê os eventos exatos do jogo com o `demoparser2` (MIT) e devolve as jogadas do
jogador no mesmo formato `Moment` que o resto do programa usa.

A demo dá o que o vídeo só consegue estimar: instante certo de cada abate (e de cada morte), headshot,
parede (wallbang), sem mira (noscope), fumaça, cegado, round, placar e clutch 1vX. Não traz voz nem reação:
isso continua vindo do vídeo. Os tempos saem em segundos DA DEMO (tick ÷ 64); para casar com um vídeo,
some o deslocamento (`deslocamento`) que a sincronização descobrir.

Só LÊ a demo. Não abre o CS2 nem mexe em nada do jogo.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from pathlib import Path

from .moments import Event, Moment, _label
from .nota import pontuar
from .tools import gravar_atomico

TICKRATE = 64.0            # CS2 em servidor oficial; o cabeçalho da demo não informa
# Dois `round_end` para o mesmo início de round, a menos de 20 s um do outro = o mesmo fim, repetido. Um round de verdade não cabe nesse
# intervalo: entre o fim de um e o fim do seguinte há a pausa de fim de round (7 s) e o tempo de compra (15 s) antes de qualquer jogada.
REPETICAO_ROUND_END_TICKS = int(20 * TICKRATE)
STEAMID64_BASE = 76561197960265728   # steamid64 - isto = accountid (o número de 32 bits que o registro do Steam e os comandos do jogo usam)
CLUTCH_MIN_INIMIGOS = 2    # 1v2 ou mais conta como clutch (1v1 é só um duelo)
ERRO_ILEGIVEL = "Demo ilegível ou não é uma demo de CS2"


# Maior patch do CS2 que o leitor (demoparser2 0.42.2) leu em demo real: as 9 demos de 04/10 a 14/11/2026 (patches 14188 a 14197). O 0.42.0 parou no
# patch 14197 (11/11/2026): o cabeçalho abria e todo evento dava `EntityNotFound`. Só serve para dar a mensagem certa quando uma demo NÃO abre.
PATCH_MAXIMO_PROVADO = 14197


class DemoInvalida(Exception):
    """Arquivo que não abre como demo (mensagem pronta para o usuário)."""


class DemoNovaDemais(DemoInvalida):
    """A demo é de um patch do CS2 mais NOVO do que o leitor conhece: não está corrompida, o Bisturi é que precisa de atualização."""
    def __init__(self, mensagem: str, patch: int):
        super().__init__(mensagem)
        self.patch = patch


_PATCH_SEM_PONTOS = re.compile(r"[0-9]{5}")
_PATCH_COM_PONTOS = re.compile(r"([0-9])\.([0-9]{2})\.([0-9])\.([0-9])")


def numero_do_patch(texto) -> int | None:
    """`14197` -> 14197 (o formato real do cabeçalho da demo) e `1.41.9.7` -> 14197 (o formato do Steam). QUALQUER outro formato (`1.41.9.10`, `14197.1`, mais de
    5 dígitos, texto, dígito que não é 0-9 como `²`) = None: melhor dizer "ilegível" do que acusar "versão nova" por uma leitura errada."""
    s = str(texto if texto is not None else "").strip()
    if _PATCH_SEM_PONTOS.fullmatch(s):
        return int(s)
    achado = _PATCH_COM_PONTOS.fullmatch(s)
    return int("".join(achado.groups())) if achado else None


def _erro_ilegivel(caminho: Path, cabecalho: dict | None = None, parser=None) -> DemoInvalida:
    """O erro certo para uma demo que o leitor não conseguiu ler. Cabeçalho legível com patch MAIOR que `PATCH_MAXIMO_PROVADO` = demo nova demais
    (`DemoNovaDemais`); qualquer outro caso (truncada, corrompida, não é demo) = o texto de sempre. `cabecalho` vem de quem já o leu; senão tenta `parser`."""
    nome = Path(caminho).name
    if cabecalho is None and parser is not None:
        try:
            cabecalho = parser.parse_header()
        except BaseException as e:  # noqa: BLE001
            if not e_falha_do_parser(e):
                raise
            cabecalho = None
    patch = numero_do_patch(cabecalho.get("patch_version")) if isinstance(cabecalho, dict) else None
    if patch is not None and patch > PATCH_MAXIMO_PROVADO:
        return DemoNovaDemais(f"A demo {nome} foi gravada numa versão do CS2 mais nova (patch {patch}) do que o leitor de demos do Bisturi conhece "
                              f"(até o patch {PATCH_MAXIMO_PROVADO}). Atualize o Bisturi (ou a demo está incompleta).", patch)
    return DemoInvalida(f"{ERRO_ILEGIVEL}: {nome}")


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
    abertura: bool = False        # 1º abate do round inteiro (entry kill)
    tick: int = 0                 # D1: tick (da demo) do abate = `t` x 64

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
    tick_inicio: int = 0          # D1: tick (da demo) em que o tempo de compra acaba = `inicio` x 64
    tick_fim: int = 0             # D1: tick do fim do round = `fim` x 64


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
    # D1: identidade do jogador para o gravador (vazios/None quando a demo não traz ou o leitor falha)
    accountid: int | None = None       # steamid64 - STEAMID64_BASE
    user_id: int | None = None         # "userid" do jogador na demo (o HLAE/CS2 usam user_id + 1 como índice do controlador)
    entity_id: int | None = None       # índice da entidade lida pelo parser (pode ser a do corpo, não a do controlador)
    patch_version: str = ""            # versão do jogo em que a demo foi gravada (cabeçalho)

    @property
    def titulo(self) -> str:
        return f"{self.mapa} {self.placar[0]}x{self.placar[1]}"


def _tick_s(tick: float) -> float:
    return round(float(tick) / TICKRATE, 3)


def e_falha_do_parser(e: BaseException) -> bool:
    """O parser de demos (Rust) falha de dois jeitos: `Exception` comum e `PanicException`, que herda de BaseException e ESCAPA de `except Exception`
    (visto em 08/11 com uma demo truncada que tinha o cabeçalho certo). Ctrl+C e saída do programa nunca contam como falha do parser."""
    return isinstance(e, Exception) or type(e).__name__ == "PanicException"


def _abrir(caminho: Path):
    try:
        from demoparser2 import DemoParser
    except ImportError as e:  # pragma: no cover
        from .checagem import _reparar
        raise DemoInvalida("Falta o leitor de demos (demoparser2 0.42.2). " + _reparar("o que falta")) from e
    try:
        return DemoParser(str(caminho))
    except BaseException as e:
        if not e_falha_do_parser(e):
            raise
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
    except BaseException as e:
        if not e_falha_do_parser(e):
            raise
        raise _erro_ilegivel(Path(caminho), parser=p) from e
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
    cabecalho = None
    try:
        cabecalho = p.parse_header()
        mapa = str(cabecalho.get("map_name", "") or "mapa?")
        mortes = p.parse_event("player_death", player=["team_num"])
        fim_compra = p.parse_event("round_freeze_end")
        fim_round = p.parse_event("round_end")
    except BaseException as e:
        if not e_falha_do_parser(e):
            raise
        raise _erro_ilegivel(caminho, cabecalho, p) from e
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
    inicios_usados: set[int] = set()
    for _, r in fins.iterrows():
        ini = next((t for t in reversed(inicios) if t < int(r["tick"])), None)
        if ini is None:
            continue   # round_end antes do 1º fim de compra (aquecimento): não é round e não conta no número (R-023)
        if ini in inicios_usados:
            fim_anterior = rounds[-1].tick_fim
            if int(r.get("tick") or 0) - fim_anterior <= REPETICAO_ROUND_END_TICKS:
                continue   # A-008: round_end REPETIDO logo depois do anterior não é outro round; fica o 1º (o fim de verdade)
            # Longe do anterior, o que faltou foi o `round_freeze_end` DESTE round (evento perdido): o round existe e conta no número;
            # o começo dele é o fim do anterior (revisão final de 26/11: descartá-lo andava os números seguintes 1 para trás).
            ini = fim_anterior
        inicios_usados.add(ini)
        rounds.append(RoundDemo(len(rounds) + 1, _tick_s(ini), _tick_s(int(r.get("tick") or 0)), str(r["winner"]), str(r.get("reason", "")),
                                tick_inicio=int(ini), tick_fim=int(r["tick"])))

    def round_do(t: float) -> RoundDemo | None:
        for rd in reversed(rounds):
            if t >= rd.inicio - 0.01:
                return rd if t <= rd.fim + 8 else None   # até 8 s depois: morte logo após o fim do round
        return None

    sid = _escolher(jogadores, abates_por, jogador)
    partida = PartidaDemo(mapa, jogadores, sid, jogadores.get(sid, "") if sid else "", rounds)
    partida.patch_version = str(cabecalho.get("patch_version", "") or "")
    if sid is None:
        return partida
    partida.accountid = int(sid) - STEAMID64_BASE if int(sid) > STEAMID64_BASE else None

    # elenco de cada round (time de cada jogador no começo da ação; os times trocam de lado no intervalo)
    vivos: dict[int, dict[str, set[int]]] = {}
    meu_time: dict[int, str] = {}
    ini_por_tick = {int(round(rd.inicio * TICKRATE)): rd for rd in rounds}
    try:
        elenco = p.parse_ticks(["team_num"], ticks=sorted(ini_por_tick))
    except BaseException as e:
        if not e_falha_do_parser(e):
            raise
        raise _erro_ilegivel(caminho, cabecalho, p) from e
    for _, r in elenco.iterrows():
        tick_ini = _inteiro(r["tick"])
        rd = ini_por_tick.get(tick_ini) if tick_ini is not None else None
        equipe, quem = _inteiro(r["team_num"]), _inteiro(r["steamid"])
        if rd is None or equipe not in (2, 3) or quem is None:       # sem time ou sem steamid (espectador, bot): ignora a linha, não derruba a leitura
            continue
        lado = "T" if equipe == 2 else "CT"
        vivos.setdefault(rd.n, {"T": set(), "CT": set()})[lado].add(quem)
        if quem == sid:
            meu_time[rd.n] = lado

    partida.user_id, partida.entity_id = _identidade(p, int(sid), min(ini_por_tick) if ini_por_tick else 0)

    primeiro_tick_round: dict[int, int] = {}
    for _, r in mortes.iterrows():
        tk = int(r["tick"])
        rd0 = round_do(_tick_s(tk))
        atk0, vit0 = _inteiro(r.get("attacker_steamid")), _inteiro(r.get("user_steamid"))      # vazio (None/NaN/texto) = sem id: a morte é pulada (F6), não derruba a leitura
        if rd0 and atk0 and vit0 is not None and atk0 != vit0:
            if r.get("user_team_num") != r.get("attacker_team_num"):
                if rd0.n not in primeiro_tick_round or tk < primeiro_tick_round[rd0.n]:
                    primeiro_tick_round[rd0.n] = tk

    for _, r in mortes.iterrows():
        t = _tick_s(r["tick"])
        rd = round_do(t)
        if rd is None:
            continue
        atk, vit = _inteiro(r.get("attacker_steamid")), _inteiro(r.get("user_steamid"))
        if atk and atk == sid and vit is not None and atk != vit:        # vítima sem steamid (F6): só ESTE abate é pulado
            tm_vit = r.get("user_team_num")
            tm_atk = r.get("attacker_team_num")
            if tm_vit == tm_atk:         # fogo amigo não conta como abate
                continue
            partida.abates.append(Abate(
                t, rd.n, str(r.get("user_name")), str(r.get("weapon")), bool(r.get("headshot")),
                bool((_inteiro(r.get("penetrated")) or 0) > 0), bool(r.get("noscope")),
                bool(r.get("thrusmoke")), bool(r.get("attackerblind")), bool(r.get("attackerinair")),
                float(r.get("distance") or 0.0),
                int(r["tick"]) == primeiro_tick_round.get(rd.n, -1), int(r["tick"])))
        if vit and vit == sid:
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


def _inteiro(valor) -> int | None:
    """int(valor), ou None se vier vazio (None/NaN/texto): o parser deixa NaN em jogador sem time ou sem steamid (ex.: espectador, bot, desconectado)."""
    try:
        if valor is None or valor != valor:
            return None
        return int(valor)
    except (TypeError, ValueError, OverflowError):
        return None


def _identidade(p, sid: int, tick: int) -> tuple[int | None, int | None]:
    """(user_id, entity_id) do jogador no `tick`, lidos do parser. Qualquer falha (demo sem a coluna, parser de teste) = (None, None):
    o gravador então cai nos outros caminhos de câmera."""
    saida: dict[str, int | None] = {"user_id": None, "entity_id": None}
    for coluna in saida:
        try:
            df = p.parse_ticks([coluna], ticks=[int(tick)])
            linha = df[df["steamid"].astype("int64") == int(sid)]
            if len(linha) and coluna in linha.columns:
                saida[coluna] = int(linha.iloc[0][coluna])
        except BaseException as e:  # noqa: BLE001
            if not e_falha_do_parser(e):
                raise
            saida[coluna] = None
    return saida["user_id"], saida["entity_id"]


@dataclass
class TrechoGravacao:
    """Um pedaço da demo a gravar (D1): em ticks da demo, com a folga já somada."""
    round: int
    tick_ini: int
    tick_fim: int
    abates: int                   # abates do jogador neste round
    ticks_abates: list[int] = field(default_factory=list)

    def duracao_s(self, tickrate: float = TICKRATE) -> float:
        return (self.tick_fim - self.tick_ini) / tickrate


TICKS_MAX_S = 600.0     # um trecho por ticks tem no máximo 10 minutos (o jogo de verdade grava ~1,5x o tempo real)


def trechos_por_ticks(partida: PartidaDemo, texto: str, tickrate: float = TICKRATE) -> list[TrechoGravacao]:
    """Gravar por ticks (23/11/2026): `INI-FIM[,INI-FIM...]` em ticks da demo vira trechos exatos para gravar, sem as margens do round. O round de cada
    trecho é o que contém o tick de início (até 30 s antes da ação e 10 s depois do fim dele, como a conta do `round_do` da leitura da demo); os abates
    são os do jogador escolhido dentro do intervalo. ValueError com a explicação quando o texto ou o intervalo não servem."""
    pedaços = [p.strip() for p in str(texto or "").split(",") if p.strip()]
    if not pedaços:
        raise ValueError("Diga os ticks no formato INICIO-FIM (ex.: 106500-110100), separados por vírgula.")
    saida: list[TrechoGravacao] = []
    vistos: set[int] = set()
    for p in pedaços:
        m = re.fullmatch(r"(\d{1,9})\s*-\s*(\d{1,9})", p)
        if not m:
            raise ValueError(f"Os ticks \"{p}\" não estão no formato INICIO-FIM (ex.: 106500-110100).")
        ini, fim = int(m.group(1)), int(m.group(2))
        if fim <= ini:
            raise ValueError(f"Nos ticks {ini}-{fim} o fim vem antes do começo.")
        if (fim - ini) / tickrate > TICKS_MAX_S:
            raise ValueError(f"Os ticks {ini}-{fim} duram mais de 10 minutos de jogo: grave por partes.")
        cobre = [(min(fim, r.tick_fim) - max(ini, r.tick_inicio), r) for r in partida.rounds]
        melhor = max(cobre, key=lambda x: x[0], default=(0, None))
        rd = melhor[1] if melhor[0] > 0 else next((r for r in partida.rounds if r.tick_inicio - 30 * tickrate <= ini <= r.tick_fim + 10 * tickrate), None)
        if rd is None:
            raise ValueError(f"O tick {ini} não cai em nenhum round desta demo (a demo tem {len(partida.rounds)} rounds, "
                             f"até o tick {max((r.tick_fim for r in partida.rounds), default=0)}).")
        if rd.n in vistos:
            raise ValueError(f"Dois trechos caem no mesmo round ({rd.n}): grave um de cada vez (cada round vira um arquivo).")
        vistos.add(rd.n)
        ticks = sorted(a.tick for a in partida.abates if ini <= a.tick <= fim)
        saida.append(TrechoGravacao(rd.n, ini, fim, len(ticks), ticks))
    return saida


def pasta_da_partida(stem: str, steamid: int | str | None = None) -> str:
    """Nome da pasta da gravação (`gravacao_demo/<isto>`) e do kit (`saida/<isto>_limpo`). Câmera em outro jogador (`--jogador`): uma pasta
    por jogador, para nunca misturar rounds gravados com câmeras diferentes (revisão de 17/11, A2). Sem jogador escolhido: o nome de sempre."""
    return f"{stem}__{int(steamid)}" if steamid not in (None, "") else stem


POS_ABATE_S = 5.0      # a gravação segue ao menos isto depois do último abate do dono (o corte usa até ~2,5 s + banner)


def rounds_para_gravar(partida: PartidaDemo, so_rounds_bons: bool = True, min_abates: int = 3,
                       margem_antes_s: float = 2.0, margem_depois_s: float = 3.0,
                       tickrate: float = TICKRATE) -> list[TrechoGravacao]:
    """Os rounds a gravar, em ordem. `so_rounds_bons`: só os rounds em que o jogador tem `min_abates` abates ou mais (a regra do dono);
    False = todos os rounds da partida. Cada trecho vai do fim do tempo de compra (menos `margem_antes_s`) ao fim do round (mais
    `margem_depois_s`, que cobre o banner). Rounds sem os ticks (demo lida por versão antiga do leitor) são ignorados."""
    por_round: dict[int, list[Abate]] = {}
    for a in partida.abates:
        por_round.setdefault(a.round, []).append(a)
    saida: list[TrechoGravacao] = []
    for rd in partida.rounds:
        if not rd.tick_inicio or not rd.tick_fim or rd.tick_fim <= rd.tick_inicio:
            continue
        abates = sorted(por_round.get(rd.n, []), key=lambda a: a.tick)
        if so_rounds_bons and len(abates) < max(1, min_abates):
            continue
        ini = max(0, rd.tick_inicio - int(round(margem_antes_s * tickrate)))
        fim_round = rd.tick_fim
        if abates:                                                    # prova real 10: abate 2,4 s depois do "fim" do round ficou sem o pós-abate
            fim_round = max(fim_round, abates[-1].tick + int(round(POS_ABATE_S * tickrate)))
        fim = fim_round + int(round(margem_depois_s * tickrate))
        saida.append(TrechoGravacao(rd.n, ini, fim, len(abates), [a.tick for a in abates]))
    return saida


def _clutches(mortes, rounds, round_do, sid, meu_time, vivos, abates) -> list[Clutch]:
    """Clutch = ficou como último vivo do time com ≥ CLUTCH_MIN_INIMIGOS adversários vivos."""
    por_round: dict[int, list[tuple[float, int, int]]] = {}
    for _, r in mortes.iterrows():
        t = _tick_s(r["tick"])
        rd = round_do(t)
        vit = r.get("user_steamid")
        equipe_vit = _inteiro(r.get("user_team_num"))
        if rd is not None and vit == vit and vit and equipe_vit is not None:
            por_round.setdefault(rd.n, []).append((t, int(vit), equipe_vit))
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

def momentos_da_demo(partida: PartidaDemo, antes: float = 4.0, depois: float = 2.5, *, depois_de_cada_abate: float = 6.0,
                     antes_do_proximo: float = 6.0, pulo_minimo: float = 2.0, deslocamento: float = 0.0) -> list[Moment]:
    """Uma jogada por round com abates do jogador (mesma regra do vídeo): todos os abates do round num vídeo só.
    O começo é `antes` s antes do 1º abate e o fim `depois` s depois do último; no meio vale a regra do pulo
    (`sequencia.trechos_da_jogada`: `depois_de_cada_abate` depois de cada abate, `antes_do_proximo` antes do seguinte,
    só pula se sobrar `pulo_minimo` s ou mais). Como a demo sabe a morte, nenhum trecho passa de 1 s depois dela."""
    from .sequencia import MORTE_NO_FIM_S, trechos_da_jogada
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
        # R3 (dono, 17/11): a morte logo depois entra no corte. Aqui vai até 1 s depois da morte EXATA (tick da demo); na análise do vídeo são 1,5 s
        # depois do painel "matou você", que já aparece um pouco depois da morte (`sequencia.DEPOIS_DA_MORTE_S`)
        morreu_no_fim = morte is not None and morte - g[-1].t <= MORTE_NO_FIM_S
        fim_jogada = fim_max if morreu_no_fim else min(fim_max, g[-1].t + depois)
        trechos = trechos_da_jogada([a.t for a in g], max(0.0, g[0].t - antes), fim_jogada,
                                    depois_de_cada_abate, antes_do_proximo, pulo_minimo)
        trechos = [(a + deslocamento, b + deslocamento) for a, b in trechos]
        eventos = [Event(round(a.t + deslocamento, 3), "kill", a.icones) for a in g]
        m = Moment(trechos[0][0], trechos[-1][1], "abate", kills=eventos, label=_label(eventos))
        m.segmentos = trechos if len(trechos) > 1 else []
        c = clutches.get(n_round)
        if c and any(a.t >= c.t for a in g):
            m.clutch = (c.inimigos, c.ganhou)   # a nota (cs2clip/nota.py) pesa o clutch
            if c.ganhou and len(g) < 5:
                m.label = f"CLUTCH 1v{c.inimigos}"
        m.abertura = any(a.abertura for a in g)
        m.morreu_no_fim = morreu_no_fim
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
    from .sequencia import regra_do_corte
    c = cfg.cortes
    momentos = momentos_da_demo(partida, c.antes, c.depois, **regra_do_corte(c))
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
    gravar_atomico(out / "demo.json", json.dumps(resumo, ensure_ascii=False, indent=1))   # G-012: interrompido no meio, o antigo fica inteiro
    return partida, momentos, out
