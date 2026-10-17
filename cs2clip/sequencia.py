"""Sequências: todos os abates do MESMO round viram um vídeo só, sem o tempo parado do meio.

Decidido com o usuário em 17/10/2026 depois de pesquisar como o CS2 (modo destaques da demo: 6 s antes e 2 s
depois de cada abate, pulando o resto com um fade de 0,25 s), o Outplayed (4 abates em 30 s = 1 clipe) e os
geradores abertos fazem (docs/editing/REGRAS_DE_CORTE.md):

  * 2 ou mais abates no mesmo round de uma partida = UM vídeo ("1 kill, pausa, depois mais 3" não vira 2 vídeos);
  * cada abate fica com o seu trecho (antes/depois + a reação do streamer); trechos a menos de
    `cortes.pular_parado` s um do outro ficam contínuos; o tempo parado maior que isso é CORTADO, e os trechos
    são emendados com fade curto (quem faz é `render.render_por_janelas`);
  * o vídeo termina logo depois do último abate e da reação (não segue até o fim do round);
  * o short usa a versão compacta (`shorts.antes`/`shorts.depois` por abate) para caber em `shorts.duracao_maxima`.

Limite honesto: a própria morte não é detectada no vídeo; com a demo da mesma partida ela vira exata.
"""
from __future__ import annotations

import dataclasses

from .moments import BASE, Moment, _label
from .partes import Parte, ajustar_pos_round, intervalo_do_round, round_de

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


def _definir(m: Moment, trechos: list[tuple[float, float]]) -> Moment:
    """Aplica os trechos ao momento (start/end = extremos; `segmentos` só quando há mais de um)."""
    m.start, m.end = trechos[0][0], trechos[-1][1]
    m.segmentos = list(trechos) if len(trechos) > 1 else []
    return m


def _limitar(m: Moment, maximo: float) -> None:
    """Jogada maior que o máximo: fica o FINAL (onde ela termina), cortando trechos do começo."""
    if maximo <= 0 or m.duration <= maximo:
        return
    resto, novos = maximo, []
    for a, b in reversed(m.janelas):
        if b - a <= resto:
            novos.append((a, b))
            resto -= b - a
        else:
            novos.append((b - resto, b))
            resto = 0.0
        if resto <= 0:
            break
    novos.reverse()
    _definir(m, novos)
    dentro = [k for k in m.kills if any(a <= k.t <= b for a, b in novos)]
    if m.kills and len(dentro) != len(m.kills):   # abate que ficou fora do vídeo não conta no rótulo
        m.kills = dentro
        m.label = _label(dentro) if dentro else m.label


def _unir(grupo: list[Moment], cortes) -> Moment:
    if len(grupo) == 1:
        return grupo[0]
    kills = sorted((k for m in grupo for k in m.kills), key=lambda k: k.t)
    vistos, assists = set(), []
    for a in sorted((a for m in grupo for a in m.assists), key=lambda a: a.t):
        if a.t not in vistos:
            vistos.add(a.t)
            assists.append(a)
    topo = max(grupo, key=lambda m: m.score)
    # cada jogada original já tem antes/depois, a reação estendida e o ajuste às falas: viram os trechos
    trechos = juntar_trechos([(m.start, m.end) for m in grupo], cortes.pular_parado)
    m = Moment(trechos[0][0], trechos[-1][1], "abate", kills=kills, assists=assists, label=_label(kills))
    _definir(m, trechos)
    m.score, m.reasons = topo.score, list(topo.reasons)
    n, n_topo = len(kills), max(len(x.kills) for x in grupo)
    m.add(BASE[min(n, 5)] - BASE[min(n_topo, 5)], f"{n} abates no mesmo round")
    m.add(3 * max(0, sum(k.hs for k in kills) - sum(k.hs for k in topo.kills)), "headshots da sequência")
    m.add(8, "jogada do round inteira")
    m.voice, m.chat, m.talk = (max(getattr(x, c) for x in grupo) for c in ("voice", "chat", "talk"))
    _limitar(m, cortes.sequencia_max)
    return m


def unir_sequencias(moments: list[Moment], partes: list[Parte], cortes, words=None, duracao: float = 0.0,
                    mortes: list[float] = ()) -> list[Moment]:
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
        limite = cortes.juntar_no_round
        intervalo = intervalo_do_round(t_ref, partes)
        if intervalo and intervalo[2]:
            limite = min(limite, VAO_SUSPEITO)
        morreu_no_meio = grupos and any(grupos[-1][-1].kills[-1].t < k < m.kills[0].t for k in mortes)
        if grupos and chave is not None and chave == chave_atual and vao <= limite and not morreu_no_meio:
            grupos[-1].append(m)
        else:
            grupos.append([m])
        chave_atual = chave
    unidos = [_unir(g, cortes) for g in grupos]
    todos = sorted(unidos + [m for m in moments if not (m.kind == "abate" and m.kills)], key=lambda m: m.start)
    for i, m in enumerate(todos, 1):
        m.idx = i
    return todos


def compactar(m: Moment, maximo: float, antes: float, depois: float, pular: float) -> Moment:
    """Versão curta de uma jogada (para o short): só `antes`/`depois` em volta de cada abate, dentro da janela
    original. Se mesmo assim passar de `maximo`, fica o final. Devolve uma cópia; o momento original não muda."""
    if maximo <= 0 or m.duration <= maximo or not m.kills:
        return m
    inicio, fim = m.start, m.end
    dentro = [k for k in m.kills if any(a <= k.t <= b for a, b in m.janelas)]
    trechos = [(a, b) for a, b in juntar_trechos([(max(inicio, k.t - antes), min(fim, k.t + depois)) for k in dentro],
                                                 pular) if b > a]
    if not trechos:
        return m
    novo = dataclasses.replace(m, kills=list(m.kills), assists=list(m.assists), reasons=list(m.reasons),
                               segmentos=[], emocao_motivos=list(m.emocao_motivos))
    _definir(novo, trechos)
    _limitar(novo, maximo)
    return novo


def sobreposicao(a: Moment, b: Moment) -> float:
    """Fração do menor dos dois momentos que fica dentro do outro (0 = nada em comum; 1 = um contém o outro)."""
    comum = sum(max(0.0, min(x1, y1) - max(x0, y0)) for x0, x1 in a.janelas for y0, y1 in b.janelas)
    menor = min(a.duration, b.duration)
    return comum / menor if menor > 0 else 0.0


def remover_repetidos(moments: list[Moment], limite: float = 0.5, pular: float = 6.0,
                      maximo: float = 0.0) -> list[Moment]:
    """Dois cortes que mostram mais da metade do mesmo trecho viram um só. Abate com abate: FUNDE (os abates dos
    dois ficam, senão sumiriam de todos os vídeos — revisão 17/10/2026). Abate com reação: fica o abate (a reação
    já aparece dentro dele). Medido em 17/10/2026: nenhum par nos 3 VODs; é proteção para outras regras.
    `maximo` (cortes.sequencia_max): a fusão também respeita o limite que `_unir` garante (0 = sem limite)."""
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
            _definir(igual, juntar_trechos(igual.janelas + m.janelas, pular))
            igual.label = _label(igual.kills)
            igual.score = max(igual.score, m.score)
            _limitar(igual, maximo)
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
