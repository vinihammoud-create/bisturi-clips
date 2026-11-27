"""Sequências: todos os abates do MESMO round viram um vídeo só, sem o tempo parado do meio.

Decidido com o usuário em 17/10/2026 depois de pesquisar como o CS2 (modo destaques da demo: 6 s antes e 2 s
depois de cada abate, pulando o resto com um fade de 0,25 s), o Outplayed (4 abates em 30 s = 1 clipe) e os
geradores abertos fazem (docs/editing/REGRAS_DE_CORTE.md):

  * 2 ou mais abates no mesmo round de uma partida = UM vídeo ("1 kill, pausa, depois mais 3" não vira 2 vídeos);
  * a regra do pulo (dono, 15/11/2026; `trechos_da_jogada`): o vídeo fica `cortes.depois_de_cada_abate` (6 s)
    depois de cada abate e volta `cortes.antes_do_proximo` (6 s) antes do seguinte; só pula se sobrar
    `cortes.pulo_minimo` (2 s) ou mais para cortar (abates a 14 s ou mais um do outro), senão fica contínuo. O tempo
    parado cortado é emendado com fade curto (quem faz é `render.render_por_janelas`). O começo (`cortes.antes`) e o fim
    (`cortes.depois` + a reação) não mudam; no vídeo (com palavras), o fim de um trecho do meio que cairia no meio de uma palavra
    é estendido até o fim dela (no máximo 1,5 s; `_fechar_falas`);
  * REGRA DE OURO do dono: nunca dividir nem tirar abates de um round: nenhum limite (`sequencia_max`, short compacto) corta
    abate; quando a jogada não cabe, só a folga (antes/depois) encolhe (`_caber`). Com a demo (parte exata) todos os abates do
    mesmo round vão num vídeo só, sem limite de vão nem a trava do "intervalo suspeito";
  * o vídeo termina logo depois do último abate e da reação (não segue até o fim do round);
  * o short usa a versão compacta (`shorts.antes`/`shorts.depois` por abate) para caber em `shorts.duracao_maxima`.

Limite honesto: a própria morte não é detectada no vídeo; com a demo da mesma partida ela vira exata.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import TypedDict

from .config import Cortes
from .moments import BASE, Moment, _label
from .partes import Parte, ajustar_pos_round, intervalo_do_round, parte_de, round_de

VAO_SUSPEITO = 30.0   # s: entre banners suspeitos (um foi perdido), só junta abates bem próximos


def juntar_trechos(trechos: list[tuple[float, float]], pular: float) -> list[tuple[float, float]]:
    """Une trechos que se sobrepõem ou cujo vão é de até `pular` s. Entrada em qualquer ordem."""
    saida: list[tuple[float, float]] = []
    for a, b in sorted(trechos):
        if saida and a - saida[-1][1] <= pular:
            saida[-1] = (saida[-1][0], max(saida[-1][1], b))
        else:
            saida.append((a, b))
    return saida


class RegraDoCorte(TypedDict):
    depois_de_cada_abate: float
    antes_do_proximo: float
    pulo_minimo: float


def regra_do_corte(cortes) -> RegraDoCorte:
    """Os 3 números da regra do pulo, tirados de `Config.cortes` (um lugar só, para todos os caminhos)."""
    return {"depois_de_cada_abate": cortes.depois_de_cada_abate, "antes_do_proximo": cortes.antes_do_proximo,
            "pulo_minimo": cortes.pulo_minimo}


def _emendar(trechos: list[tuple[float, float]], pulo_minimo: float) -> list[tuple[float, float]]:
    """Ordena e une trechos que se tocam ou cujo vão é MENOR que `pulo_minimo` (vão igual ao mínimo pula). O vão é arredondado
    a 1 microssegundo: 13 s entre abates tem de dar exatamente 2 s de sobra mesmo com o ruído do ponto flutuante."""
    saida: list[tuple[float, float]] = []
    for a, b in sorted(trechos):
        if b <= a:
            continue
        if saida:
            vao = round(a - saida[-1][1], 6)
            if vao <= 0 or vao < pulo_minimo:
                saida[-1] = (saida[-1][0], max(saida[-1][1], b))
                continue
        saida.append((a, b))
    return saida


def trechos_da_jogada(abates: Sequence[float], inicio: float, fim: float, depois_de_cada_abate: float,
                      antes_do_proximo: float, pulo_minimo: float,
                      minimos: Sequence[tuple[float, float]] = ()) -> list[tuple[float, float]]:
    """A regra do pulo (dono, 15/11/2026), pura: os trechos de uma jogada com vários abates.

    `abates`: instantes dos abates (qualquer ordem). `inicio`: o começo da jogada (1º abate - cortes.antes). `fim`: o fim (último
    abate + cortes.depois + a reação; pela demo, no máximo a morte + 1 s). O 1º trecho começa em `inicio`; cada abate que não é o
    último fica `depois_de_cada_abate` s (nunca além de `fim`); o trecho de cada abate seguinte começa `antes_do_proximo` s antes
    dele; o último vai até `fim`. Se o que sobra para cortar entre dois trechos é menor que `pulo_minimo`, não há pulo.
    `minimos`: os trechos que o corte já tinha (reação estendida, ajuste às falas): a regra só acrescenta tempo, nunca encolhe um."""
    ordem = sorted(abates)
    candidatos: list[tuple[float, float]] = [(a, b) for a, b in minimos]
    if not ordem:
        candidatos.append((inicio, fim))
    for i, t in enumerate(ordem):
        ini = inicio if i == 0 else max(inicio, t - antes_do_proximo)
        fi = fim if i == len(ordem) - 1 else min(fim, t + depois_de_cada_abate)
        candidatos.append((ini, fi))
    return _emendar(candidatos, pulo_minimo)


def _definir(m: Moment, trechos: list[tuple[float, float]]) -> Moment:
    """Aplica os trechos ao momento (start/end = extremos; `segmentos` só quando há mais de um)."""
    m.start, m.end = trechos[0][0], trechos[-1][1]
    m.segmentos = list(trechos) if len(trechos) > 1 else []
    return m


MARGEM_MIN = 0.5   # s: quando a jogada não cabe no máximo, cada abate ainda fica com pelo menos isto antes e depois


def _folga(x: float, f: float) -> float:
    """A folga `x` (s) multiplicada por `f` (0 a 1), nunca abaixo de MARGEM_MIN e nunca acima de `x` (G-018: com menos de MARGEM_MIN de
    espaço, o mínimo cede ao espaço que existe, senão o corte começava antes de 0 s)."""
    return min(x, max(min(x, MARGEM_MIN), x * f))


def _soma(trechos: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in trechos)


def _caber(construir, maximo: float) -> list[tuple[float, float]]:
    """REGRA DE OURO do dono (15/11/2026): abate nunca sai de uma jogada por causa de limite. `construir(f)` devolve os trechos com a
    FOLGA (antes/depois) multiplicada por `f` (1 = normal); aqui vale o maior `f` que cabe em `maximo`. Se nem com a folga mínima
    couber, ficam todos os abates mesmo assim (passa do máximo): o limite cede, o abate não."""
    cheio = construir(1.0)
    if maximo <= 0 or _soma(cheio) <= maximo:
        return cheio
    baixo, alto = 0.0, 1.0
    for _ in range(40):
        meio = (baixo + alto) / 2
        if _soma(construir(meio)) <= maximo:
            baixo = meio
        else:
            alto = meio
    return construir(baixo)


def _limitar(m: Moment, maximo: float, regra: RegraDoCorte) -> None:
    """Jogada maior que o máximo (`cortes.sequencia_max`): TODOS os abates ficam; só a folga em volta deles encolhe (começo, fim, tempo
    depois de cada abate e antes do seguinte), por igual, até caber. Sem abates (reação) o limite não se aplica."""
    if maximo <= 0 or m.duration <= maximo or not m.kills:
        return
    atuais = m.janelas
    ks = [k.t for k in m.kills]
    ini0, fim0 = atuais[0][0], atuais[-1][1]

    def construir(f: float) -> list[tuple[float, float]]:
        if f >= 1.0:
            return list(atuais)
        return trechos_da_jogada(ks, min(ks) - _folga(min(ks) - ini0, f), max(ks) + _folga(fim0 - max(ks), f),
                                 _folga(regra["depois_de_cada_abate"], f), _folga(regra["antes_do_proximo"], f), regra["pulo_minimo"])

    _definir(m, _caber(construir, maximo))


FALA_EXTRA_MAX = 1.5   # s: no máximo isto a mais para não cortar uma palavra ao meio no fim de um trecho do meio


def _fechar_falas(trechos: list[tuple[float, float]], words, duracao: float, pulo_minimo: float) -> list[tuple[float, float]]:
    """N2b: o mesmo cuidado de `moments.snap_to_speech` (não cortar no meio de palavra) para os trechos do MEIO de uma jogada, que
    a regra do pulo faz terminar em `abate + depois_de_cada_abate` sem olhar as falas (só o último trecho recebe esse ajuste lá no
    `build_moments`). Se o fim de um trecho que não é o último cai no meio de uma palavra (início < fim < término), estende o fim até
    o término da palavra, no máximo `FALA_EXTRA_MAX` s (mais que isso fica como está) e nunca além de `duracao` (se informada). Só
    ESTENDE: nenhum trecho encolhe. Depois refaz o vão com o seguinte (menor que `pulo_minimo`: os trechos se juntam)."""
    if not words or len(trechos) < 2:
        return trechos
    novos = []
    for i, (a, b) in enumerate(trechos):
        if i < len(trechos) - 1:
            termino = max((w.end for w in words if w.start < b < w.end), default=None)
            if termino is not None:
                if duracao > 0:
                    termino = min(termino, duracao)
                if b < termino <= b + FALA_EXTRA_MAX + 1e-9:
                    b = termino
        novos.append((a, b))
    return _emendar(novos, pulo_minimo)


def _unir(grupo: list[Moment], cortes, words=None, duracao: float = 0.0) -> Moment:
    if len(grupo) == 1:
        return grupo[0]
    kills = sorted((k for m in grupo for k in m.kills), key=lambda k: k.t)
    vistos, assists = set(), []
    for a in sorted((a for m in grupo for a in m.assists), key=lambda a: a.t):
        if a.t not in vistos:
            vistos.add(a.t)
            assists.append(a)
    topo = max(grupo, key=lambda m: m.score)
    # cada jogada original já tem antes/depois, a reação estendida e o ajuste às falas: são o mínimo de cada trecho; a regra dos
    # pulo (`trechos_da_jogada`) acrescenta o tempo depois de cada abate e antes do seguinte
    antigos = [j for m in grupo for j in m.janelas]
    trechos = trechos_da_jogada([k.t for k in kills], min(a for a, _ in antigos), max(b for _, b in antigos),
                                **regra_do_corte(cortes), minimos=antigos)
    trechos = _fechar_falas(trechos, words, duracao, cortes.pulo_minimo)   # só no vídeo (a demo não tem palavras)
    m = Moment(trechos[0][0], trechos[-1][1], "abate", kills=kills, assists=assists, label=_label(kills))
    _definir(m, trechos)
    m.score, m.reasons = topo.score, list(topo.reasons)
    n, n_topo = len(kills), max(len(x.kills) for x in grupo)
    m.add(BASE[min(n, 5)] - BASE[min(n_topo, 5)], f"{n} abates no mesmo round")
    m.add(3 * max(0, sum(k.hs for k in kills) - sum(k.hs for k in topo.kills)), "headshots da sequência")
    m.add(8, "jogada do round inteira")
    m.voice, m.chat, m.talk = (max(getattr(x, c) for x in grupo) for c in ("voice", "chat", "talk"))
    _limitar(m, cortes.sequencia_max, regra_do_corte(cortes))
    return m


def unir_sequencias(moments: list[Moment], partes: list[Parte], cortes, words=None, duracao: float = 0.0,
                    mortes: Sequence[float] = ()) -> list[Moment]:
    """Devolve os momentos com as jogadas do mesmo round unidas, em ordem de tempo e renumerados.
    Reações e momentos fora de partida (ou sem round lido) passam sem mudança.
    Proteções (17/10/2026: um TRIPLE + 1 abate do round seguinte viraram "QUADRA KILL" porque o banner entre eles
    não foi lido): uma morte sua entre dois abates separa (não se mata depois de morrer no mesmo round); e se o
    intervalo entre os banners é suspeito de esconder um round, só junta abates a até VAO_SUSPEITO s."""
    if cortes.juntar_no_round <= 0:
        return moments
    grupos: list[list[Moment]] = []
    chave_atual = None
    for m in sorted((m for m in moments if m.kind == "abate" and m.kills), key=lambda m: m.kills[0].t):
        t_ref = ajustar_pos_round(m.kills[-1].t, partes)
        parte, numero, _ = round_de(t_ref, partes)
        chave = (parte.n, numero) if parte is not None and parte.tipo == "partida" and numero is not None else None
        vao = m.kills[0].t - grupos[-1][-1].kills[-1].t if grupos else 0.0
        exata = bool(parte is not None and parte.exata)   # round certo (da demo): sem limite de vão (e `intervalo_do_round` nunca o chama de suspeito)
        limite = float("inf") if exata else cortes.juntar_no_round
        intervalo = intervalo_do_round(t_ref, partes)
        if intervalo and intervalo[2]:
            limite = min(limite, VAO_SUSPEITO)
        morreu_no_meio = grupos and any(grupos[-1][-1].kills[-1].t < k < m.kills[0].t for k in mortes)
        # banner do round perdido (abates longe do próximo banner ficam sem round): na MESMA partida, só junta se o
        # vão for curto (VAO_SUSPEITO), já que não dá para saber se um round terminou no meio
        p_ant = parte_de(grupos[-1][-1].kills[-1].t, partes) if grupos else None
        sem_chave_na_partida = bool(grupos and chave is None and chave_atual is None
                                    and parte is not None and parte.tipo == "partida" and p_ant is parte)
        mesma = ((chave is not None and chave == chave_atual and vao <= limite)
                 or (sem_chave_na_partida and vao <= min(limite, VAO_SUSPEITO)))
        if grupos and mesma and not morreu_no_meio:
            grupos[-1].append(m)
        else:
            grupos.append([m])
        chave_atual = chave
    unidos = [_unir(g, cortes, words, duracao) for g in grupos]
    todos = sorted(unidos + [m for m in moments if not (m.kind == "abate" and m.kills)], key=lambda m: m.start)
    for i, m in enumerate(todos, 1):
        m.idx = i
    return todos


MORTE_NO_FIM_S = 4.0       # R3: a sua morte até isto depois do último abate é "o fim do lance"
DEPOIS_DA_MORTE_S = 1.5    # o corte vai até isto depois da morte (o painel "matou você" aparece)


def marcar_morte_no_fim(moments: list[Moment], mortes: Sequence[float], duracao: float = 0.0,
                        ate: float = MORTE_NO_FIM_S, depois: float = DEPOIS_DA_MORTE_S) -> int:
    """R3 (dono, 17/11/2026: "fez 2 kills e morreu para uma AWP", e o kit só dizia "round ganho"): numa jogada com abate, a sua morte até
    `ate` s depois do último abate marca `morreu_no_fim` e o fim do corte vai até `depois` s depois dela (só cresce; o começo e os pulos
    não mudam). Devolve quantas jogadas foram marcadas."""
    n = 0
    for m in moments:
        if m.kind != "abate" or not m.kills:
            continue
        ultimo = max(k.t for k in m.kills)
        morte = next((d for d in sorted(mortes) if ultimo < d <= ultimo + ate), None)
        if morte is None:
            continue
        m.morreu_no_fim = True
        m.morte_t = morte
        n += 1
        fim = morte + depois
        if duracao > 0:
            fim = min(fim, duracao)
        if m.segmentos:
            a, b = m.segmentos[-1]
            if fim > b:
                m.segmentos = [*m.segmentos[:-1], (a, fim)]
        if fim > m.end:
            m.end = fim
    return n


def compactar(m: Moment, maximo: float, antes: float, depois: float, pular: float) -> Moment:
    """Versão curta de uma jogada (para o short): só `antes`/`depois` em volta de cada abate, dentro da janela original (decisão D4: o
    short compacto continua com `shorts.antes`/`shorts.depois` e com `cortes.pular_parado` para unir trechos). TODOS os abates ficam (regra
    de ouro do dono): se mesmo assim passar de `maximo`, só a folga encolhe. Devolve uma cópia; o momento original não muda."""
    if maximo <= 0 or m.duration <= maximo or not m.kills:
        return m
    inicio, fim = m.start, m.end
    dentro = [k for k in m.kills if any(a <= k.t <= b for a, b in m.janelas)]

    ultimo = max(k.t for k in dentro) if dentro else None
    cauda = m.morreu_no_fim and ultimo is not None and fim > ultimo      # 23/11 (revisão): a morte e a reação depois do último abate ficam no short compacto

    def construir(f: float) -> list[tuple[float, float]]:
        pedacos = [(max(inicio, k.t - _folga(antes, f)), min(fim, k.t + _folga(depois, f))) for k in dentro]
        if cauda and ultimo is not None:
            pedacos.append((max(inicio, ultimo - _folga(antes, f)), fim))
        return [(a, b) for a, b in juntar_trechos(pedacos, pular) if b > a]

    if not dentro or not construir(1.0):
        return m
    novo = dataclasses.replace(m, kills=list(m.kills), assists=list(m.assists), reasons=list(m.reasons),
                               segmentos=[], emocao_motivos=list(m.emocao_motivos))
    _definir(novo, _caber(construir, maximo))
    return novo


def sobreposicao(a: Moment, b: Moment) -> float:
    """Fração do menor dos dois momentos que fica dentro do outro (0 = nada em comum; 1 = um contém o outro)."""
    comum = sum(max(0.0, min(x1, y1) - max(x0, y0)) for x0, x1 in a.janelas for y0, y1 in b.janelas)
    menor = min(a.duration, b.duration)
    return comum / menor if menor > 0 else 0.0


def remover_repetidos(moments: list[Moment], limite: float = 0.5, maximo: float = 0.0, cortes=None) -> list[Moment]:
    """Dois cortes que mostram mais da metade do mesmo trecho viram um só. Abate com abate: FUNDE (os abates dos
    dois ficam, senão sumiriam de todos os vídeos — revisão 17/10/2026). Abate com reação: fica o abate (a reação
    já aparece dentro dele). Medido em 17/10/2026: nenhum par nos 3 VODs; é proteção para outras regras.
    `maximo` (cortes.sequencia_max): a fusão também respeita o limite que `_unir` garante (0 = sem limite).
    `cortes` (Config.cortes; vazio = os padrões): os 3 números da regra do pulo para refazer os trechos do corte fundido
    (o `pular_parado` antigo não decide mais: o pulo dentro da jogada é só da regra)."""
    regra = regra_do_corte(cortes if cortes is not None else Cortes())
    ordem = sorted(moments, key=lambda m: (m.kind != "abate", -m.score))
    ficam: list[Moment] = []
    for m in ordem:
        igual = next((f for f in ficam if sobreposicao(m, f) > limite), None)
        if igual is None:
            ficam.append(m)
        elif m.kind == "abate" and igual.kind == "abate":
            vistos = {round(k.t, 2) for k in igual.kills}
            igual.kills = sorted(igual.kills + [k for k in m.kills if round(k.t, 2) not in vistos], key=lambda k: k.t)
            igual.assists = sorted({round(a.t, 2): a for a in igual.assists + m.assists}.values(), key=lambda a: a.t)
            antigos = igual.janelas + m.janelas
            _definir(igual, trechos_da_jogada([k.t for k in igual.kills], min(a for a, _ in antigos), max(b for _, b in antigos),
                                              **regra, minimos=antigos))
            igual.label = _label(igual.kills)
            igual.score = max(igual.score, m.score)
            _limitar(igual, maximo, regra)
    ficam.sort(key=lambda m: m.start)
    for i, m in enumerate(ficam, 1):
        m.idx = i
    return ficam


def com_gancho(m: Moment, antes: float) -> Moment:
    """Short começa `antes` s antes do 1º abate (o gancho dos primeiros segundos: pesquisa P7, R2). Só corta o
    começo do primeiro trecho; devolve cópia quando muda."""
    if antes <= 0 or not m.kills:
        return m
    inicio = m.kills[0].t - antes
    (a, b), resto = m.janelas[0], m.janelas[1:]
    if inicio <= a or inicio >= b:
        return m
    novo = dataclasses.replace(m, kills=list(m.kills), assists=list(m.assists), reasons=list(m.reasons),
                               emocao_motivos=list(m.emocao_motivos), segmentos=[])
    return _definir(novo, [(inicio, b)] + list(resto))
