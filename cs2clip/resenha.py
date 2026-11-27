"""A4 (dono, 08/11/2026): RESENHA = momento engraçado que vale um short mesmo sem 3 abates.

O dono disse: "apenas se tiver uma resenha ou coisa engraçada dentro do Discord junto, como altos gritos, aumento de som
ou muita gente falando ao mesmo tempo". Quatro sinais, e só vale quando DOIS OU MAIS aparecem juntos (a até 3 s um do outro):

  * Discord alto: o volume da faixa do Discord sobe 8 dB ou mais acima da fala típica dela (e fica no percentil 95) por 1,5 s ou mais;
  * várias vozes: o seu microfone e o Discord falam ao mesmo tempo por 2 s ou mais;
  * você gritando: a curva de voz do microfone passa do limiar de grito;
  * risadas: duas ou mais risadas (kkkk, haha) nas falas, a até 6 s uma da outra.

Opção `resenha.discord_basta`: um sinal do DISCORD sozinho (alto ou várias vozes) já basta; o seu grito sozinho nunca basta
(grito depois de morrer quase sempre é chamada de jogada, não graça). Um sinal sozinho continua sendo uma reação comum (e a reação comum não entra na montagem nem no short pela regra do dono).
Sem a faixa do Discord (audio.faixa_comms) só sobram "gritando" e "risadas": o programa avisa e ainda acha resenha quando os dois aparecem.
Só LÊ o áudio. A curva de grito, as falas e o áudio do Discord vêm da análise; nada aqui toca no jogo.
"""
from __future__ import annotations

import numpy as np

from .audio import HOP
from .moments import Moment
from .sequencia import sobreposicao
from .transcribe import Word, hype_events

LIMIAR_GRITO = 0.6          # mesmo valor de cortes.limiar_reacao
GRITO_MIN_S = 0.5
DISCORD_ALTO_PERCENTIL = 95
DISCORD_ALTO_MIN_S = 1.5
DISCORD_PISO_DB = 8.0       # dB acima do chão de ruído da faixa para contar como "alguém falando"
DISCORD_ALTO_MARGEM_DB = 8.0    # o "alto" precisa ficar pelo menos isto acima da fala típica: grito de verdade, não só o 5% mais forte de uma conversa normal
JUNTAS_MIN_S = 2.0
RISADAS_MIN = 2
RISADAS_JANELA_S = 6.0
JUNTA_SINAIS_S = 3.0        # sinais a até isto um do outro são o mesmo momento
ANTES, DEPOIS = 5.0, 4.0
DURACAO_MAX = 40.0
MUDA_DB = -70.0             # faixa com volume médio abaixo disto está muda

NOMES = {"discord_alto": "Discord alto", "varias_vozes": "várias vozes ao mesmo tempo",
         "gritando": "você gritando", "risadas": "risadas", "risadas_amigos": "risadas dos amigos"}
SINAIS_DO_DISCORD = {"discord_alto", "varias_vozes", "risadas_amigos"}

# 17/11/2026 (medido nas gravações do OBS de 02/11, 04/11 e 13/11): a faixa 4 ("Discord") sai MUDA (-91 dB) e a voz dos amigos
# vai para a faixa 3 ("jogo"), junto com o som do jogo (a faixa 1 é exatamente microfone + faixa 3). Nesse caso a resenha usa a
# FALA da faixa do jogo (detector de voz, não volume: tiro e granada são altos e não são conversa).
FALA_AMIGOS_MIN_S = 30.0       # menos fala que isto na gravação inteira: não há conversa de amigos nessa faixa
RISADAS_AMIGOS_MIN = 1         # o Whisper quase nunca escreve risada: uma já conta (e a resenha ainda exige 2 tipos de sinal)
TRECHO_MAX_S = 30.0            # a fala dos amigos é transcrita em trechos de até isto (só os trechos com fala, não a gravação inteira)


def trechos(ligado: np.ndarray, minimo_s: float, hop: float = HOP) -> list[tuple[float, float]]:
    """(início, fim) em segundos de cada sequência de True com pelo menos `minimo_s`."""
    saida: list[tuple[float, float]] = []
    n, i = len(ligado), 0
    while i < n:
        if ligado[i]:
            j = i
            while j < n and ligado[j]:
                j += 1
            if (j - i) * hop >= minimo_s:
                saida.append((i * hop, j * hop))
            i = j
        else:
            i += 1
    return saida


def _fechar(ligado: np.ndarray, buraco_s: float, hop: float = HOP) -> np.ndarray:
    """Une trechos ligados separados por um buraco menor que `buraco_s` (fala tem pausa entre as palavras)."""
    k = max(1, int(round(buraco_s / hop)))
    kern = np.ones(2 * k + 1)
    dilatado = np.convolve(ligado.astype(float), kern, mode="same") > 0
    erodido = np.convolve(dilatado.astype(float), kern, mode="same") >= len(kern)
    return erodido | ligado


def nivel_medio_db(audio: np.ndarray, bloco: int = 262_144) -> float:
    """Volume médio (dBFS, RMS) da faixa, lido em blocos: a faixa de uma live de horas não ganha uma cópia inteira em ponto flutuante."""
    if audio is None or not len(audio):
        return -120.0
    soma = 0.0
    for i in range(0, len(audio), bloco):
        parte = audio[i:i + bloco].astype(np.float64) / 32768.0
        soma += float(np.dot(parte, parte))
    return float(20.0 * np.log10(np.sqrt(soma / len(audio)) + 1e-9))


def curva_do_discord(audio: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """(ativo, alto, nivel_db) por passo de `HOP` s da faixa do Discord, ou None se ela está muda ou quase sem fala."""
    from .audio import _band_db
    if audio is None or len(audio) < sr:
        return None
    if nivel_medio_db(audio) < MUDA_DB:
        return None
    db, _ = _band_db(audio, sr)
    if not len(db):
        return None
    piso = float(np.percentile(db, 20))
    ativo = db > piso + DISCORD_PISO_DB
    if int(ativo.sum()) * HOP < 5.0:          # menos de 5 s de fala na gravação inteira: não dá para saber o que é "alto"
        return None
    tipico = float(np.median(db[ativo]))
    corte = float(np.percentile(db[ativo], DISCORD_ALTO_PERCENTIL))
    alto = ativo & (db >= max(corte, tipico + DISCORD_ALTO_MARGEM_DB))
    return ativo, _fechar(alto, 0.5), db


def _segmentos_para_curva(segs: list[tuple[float, float]], n: int) -> np.ndarray:
    ativo = np.zeros(n, bool)
    for a, b in segs:
        ativo[int(a / HOP):min(n, int(b / HOP) + 1)] = True
    return ativo


def _grupos_de_risada(falas: list[Word], minimo: int) -> list[tuple[float, float, float]]:
    """(início, fim, força) de cada grupo de `minimo` ou mais risadas a até RISADAS_JANELA_S uma da outra."""
    risos = sorted(s for s, _, _, termo in hype_events(falas) if termo == "risada")
    saida: list[tuple[float, float, float]] = []
    i = 0
    while i < len(risos):
        grupo = [r for r in risos[i:] if r - risos[i] <= RISADAS_JANELA_S]
        if len(grupo) >= minimo:
            saida.append((grupo[0], grupo[-1] + 1.0, min(1.0, len(grupo) / 4)))
        i += max(1, len(grupo))
    return saida


def sinais(voz: np.ndarray | None, segs_mic: list[tuple[float, float]], discord: tuple | None,
           falas: list[Word], falas_amigos: list[Word] | None = None) -> list[tuple[str, float, float, float]]:
    """Todos os sinais da gravação: (nome, início, fim, força 0-1)."""
    saida: list[tuple[str, float, float, float]] = []
    if voz is not None and len(voz):
        for a, b in trechos(voz >= LIMIAR_GRITO, GRITO_MIN_S):
            i, j = int(a / HOP), max(int(a / HOP) + 1, int(b / HOP))
            saida.append(("gritando", a, b, float(voz[i:j].max())))
    if discord is not None:
        ativo, alto, _ = discord
        for a, b in trechos(alto, DISCORD_ALTO_MIN_S):
            saida.append(("discord_alto", a, b, 1.0))
        n = len(ativo)
        mic = _segmentos_para_curva(segs_mic, n)
        for a, b in trechos(mic & ativo, JUNTAS_MIN_S):
            saida.append(("varias_vozes", a, b, 1.0))
    for a, b, forca in _grupos_de_risada(falas, RISADAS_MIN):
        saida.append(("risadas", a, b, forca))
    for a, b, forca in _grupos_de_risada(falas_amigos or [], RISADAS_AMIGOS_MIN):
        saida.append(("risadas_amigos", a, b, forca))
    return sorted(saida, key=lambda s: s[1])


def _agrupar(todos: list[tuple[str, float, float, float]]) -> list[list[tuple[str, float, float, float]]]:
    grupos: list[list[tuple[str, float, float, float]]] = []
    fim_atual = -1e9
    for s in todos:
        if grupos and s[1] <= fim_atual + JUNTA_SINAIS_S:
            grupos[-1].append(s)
            fim_atual = max(fim_atual, s[2])
        else:
            grupos.append([s])
            fim_atual = s[2]
    return grupos


def achar_resenhas(voz: np.ndarray | None, segs_mic: list[tuple[float, float]], discord: tuple | None, falas: list[Word],
                   duracao: float, existentes: list[Moment], quantidade: int = 6,
                   min_sinais: int = 2, discord_basta: bool = False,
                   falas_amigos: list[Word] | None = None) -> tuple[list[Moment], list[Moment]]:
    """(novas resenhas, reações comuns que elas substituem). Reação comum é qualquer momento sem abate que a resenha cobre;
    resenha em cima de uma jogada com abate não é criada (a jogada já tem o seu lugar).
    `falas_amigos`: a transcrição da fala dos amigos (faixa do Discord, ou a do jogo quando o Discord vai para ela)."""
    novas: list[Moment] = []
    for grupo in _agrupar(sinais(voz, segs_mic, discord, falas, falas_amigos)):
        tipos = {s[0] for s in grupo}
        so_discord = bool(tipos & SINAIS_DO_DISCORD)
        if len(tipos) < max(2, min_sinais) and not (discord_basta and so_discord):
            continue
        ini, fim = min(s[1] for s in grupo), max(s[2] for s in grupo)
        forca = max(s[3] for s in grupo)
        pico_s = max(grupo, key=lambda s: (s[3], s[2] - s[1]))
        a = max(0.0, ini - ANTES)
        b = min(duracao, fim + DEPOIS)
        if b - a > DURACAO_MAX:       # resenha longa demais: fica a parte mais forte
            centro = (pico_s[1] + pico_s[2]) / 2
            a, b = max(0.0, centro - DURACAO_MAX / 2), min(duracao, centro + DURACAO_MAX / 2)
        if b - a < 3.0:
            continue
        m = Moment(a, b, "reacao", label="RESENHA", pico=(pico_s[1] + pico_s[2]) / 2)
        m.categoria = "resenha"
        m.voice = float(forca) if "gritando" in tipos else 0.0
        m.reasons = ["resenha: " + " + ".join(NOMES[t] for t in sorted(tipos))]
        m.score = float(min(60.0, 24.0 + 8.0 * max(0, len(tipos) - 2) + 10.0 * forca + 4.0 * min(2, len(grupo) - len(tipos) + 1)))
        novas.append(m)
    novas.sort(key=lambda m: -m.score)
    ficam: list[Moment] = []
    remover: list[Moment] = []
    for m in novas:
        if len(ficam) >= quantidade:
            break
        if any(o.kind == "abate" and sobreposicao(m, o) > 0.3 for o in existentes):
            continue
        if any(sobreposicao(m, o) > 0.3 for o in ficam):
            continue
        ficam.append(m)
        remover += [o for o in existentes if o.kind == "reacao" and o.categoria != "resenha" and sobreposicao(m, o) > 0.3]
    return sorted(ficam, key=lambda m: m.start), remover


def carregar_discord(src: str, faixa_comms: int) -> tuple[tuple | None, str]:
    """(curva do Discord, aviso). `faixa_comms` é o número da faixa do OBS (1 a 6; 0 = não tenho)."""
    from . import audio as aud
    from .montagem import n_faixas_audio
    if faixa_comms <= 1:
        return None, "sem audio.faixa_comms: a resenha usa só gritos e risadas (marque a faixa do Discord para achar Discord alto e várias vozes)"
    if n_faixas_audio(src) < faixa_comms:
        return None, f"o vídeo não tem a faixa {faixa_comms} (audio.faixa_comms): a resenha usa só gritos e risadas"
    curva = curva_do_discord(aud.load_audio(src, faixa=faixa_comms - 1), aud.SR)
    if curva is None:
        return None, f"a faixa {faixa_comms} (Discord) está muda ou quase sem fala neste vídeo: a resenha usa só gritos e risadas"
    return curva, ""


def _trechos_para_transcrever(segs: list[tuple[float, float]], maximo: float = TRECHO_MAX_S) -> list[tuple[float, float]]:
    """Junta trechos de fala vizinhos (até 1 s de pausa) em blocos de até `maximo` s, com 0,3 s de folga em cada ponta."""
    blocos: list[list[float]] = []
    for a, b in sorted(segs):
        a, b = max(0.0, a - 0.3), b + 0.3
        if blocos and a - blocos[-1][1] <= 1.0 and b - blocos[-1][0] <= maximo:
            blocos[-1][1] = max(blocos[-1][1], b)
        else:
            blocos.append([a, b])
    saida: list[tuple[float, float]] = []
    for a, b in blocos:              # um trecho único mais longo que o máximo é fatiado (revisão de 17/11, B5)
        while b - a > maximo:
            saida.append((a, a + maximo))
            a += maximo
        saida.append((a, b))
    return saida


def transcrever_trechos(pcm: np.ndarray, sr: int, segs: list[tuple[float, float]], transcrever) -> list[Word]:
    """Transcreve só os trechos com fala (`transcrever(áudio float32, início_s) -> [Word]`), não a gravação inteira."""
    falas: list[Word] = []
    for a, b in _trechos_para_transcrever(segs):
        parte = pcm[int(a * sr):int(b * sr)]
        if len(parte) >= sr // 2:
            falas += transcrever(parte.astype(np.float32) / 32768.0, a)
    return falas


MIC_NA_FAIXA_MAX = 0.6        # mais que isto da SUA fala também aparece como fala na faixa do jogo: ela é cópia da mistura (não só dos amigos)


def _fracao_coberta(segs: list[tuple[float, float]], outros: list[tuple[float, float]]) -> float:
    total = sum(b - a for a, b in segs)
    if total <= 0:
        return 0.0
    return sum(max(0.0, min(b, d) - max(a, c)) for a, b in segs for c, d in outros) / total


def carregar_amigos(src: str, faixa_comms: int, faixa_jogo: int, transcrever=None,
                    usar_faixa_do_jogo: bool = False, faixa_voz: int = 0,
                    segs_mic: list[tuple[float, float]] | None = None) -> tuple[tuple | None, list[Word], str]:
    """(curva do Discord, falas dos amigos, aviso).

    1. A faixa do Discord (`faixa_comms`) com fala: curva de volume, como antes, e a fala dela transcrita.
    2. Discord mudo, mas há conversa na faixa do jogo (`faixa_jogo`): a curva "alguém falando" vem do detector de voz dessa faixa
       (sem "Discord alto": tiro também é alto) e a fala dela é transcrita para achar as risadas dos amigos.
    `transcrever` None = não transcreve (sem risadas dos amigos). O caminho 2 só vale com `usar_faixa_do_jogo` (desligado por padrão), com a
    faixa do Discord marcada (`faixa_comms` >= 2) e com a faixa do microfone (`faixa_voz`) no vídeo; e é recusado quando a sua própria fala
    (`segs_mic`) também aparece na faixa do jogo (cópia da mistura: qualquer grito viraria "várias vozes"; revisão de 17/11, M4)."""
    from . import audio as aud
    from .montagem import n_faixas_audio
    curva, aviso = carregar_discord(src, faixa_comms)
    faixa_fala = faixa_comms if curva is not None else 0
    n_faixas = None
    if curva is None and usar_faixa_do_jogo and faixa_comms >= 2 and faixa_jogo > 1 and faixa_jogo != faixa_comms and faixa_voz >= 2:
        n_faixas = n_faixas_audio(src)
    if curva is None and n_faixas is not None and n_faixas >= max(faixa_jogo, faixa_voz):
        pcm = aud.load_audio(src, faixa=faixa_jogo - 1)
        segs = aud.speech_segments(pcm)
        fala_s = sum(b - a for a, b in segs)
        if segs_mic and sum(b - a for a, b in segs_mic) >= 10.0 and _fracao_coberta(segs_mic, segs) >= MIC_NA_FAIXA_MAX:
            return None, [], (f"a faixa {faixa_jogo} (jogo) também tem a sua voz (parece cópia da mistura): a resenha não usa a fala dela. "
                              f"No OBS, deixe o microfone fora da faixa {faixa_jogo}")
        if fala_s >= FALA_AMIGOS_MIN_S:
            n = int(len(pcm) / aud.SR / HOP) + 1
            ativo = _segmentos_para_curva(segs, n)
            curva = (ativo, np.zeros(n, bool), None)
            faixa_fala = faixa_jogo
            aviso = (f"a faixa {faixa_comms} (Discord) está muda e a conversa dos amigos está na faixa {faixa_jogo} (jogo), {fala_s:.0f} s de fala: "
                     f"a resenha usa essa fala. Para separar, no OBS mande o Discord só para a faixa {faixa_comms} "
                     f"(enquanto isso, a voz dos amigos também vai para o som do jogo da montagem)")
            return curva, (transcrever_trechos(pcm, aud.SR, segs, transcrever) if transcrever else []), aviso
    if curva is None or not transcrever or not faixa_fala:
        return curva, [], aviso
    pcm = aud.load_audio(src, faixa=faixa_fala - 1)
    return curva, transcrever_trechos(pcm, aud.SR, aud.speech_segments(pcm), transcrever), aviso
