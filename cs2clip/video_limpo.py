"""D7 (Plano D): o vídeo LIMPO gravado pela demo vira a fonte da edição.

Os rounds gravados pelo `gravador_demo` (`gravacao_demo/<demo>/rounds/round_NN.mp4`) são juntados num vídeo só (`PARTIDA_LIMPA.mp4`) e os momentos
da demo (que estão em tempo de demo) são mapeados para o tempo desse vídeo. Assim o `render_all` de sempre (cortes, shorts, montagem, capa,
qualidade) roda sem nenhuma mudança: ele só enxerga "um vídeo e os momentos dele".

Também junta, se pedido, a VOZ da live (o microfone da gravação do OBS) por cima do áudio do jogo, alinhada pelo deslocamento demo↔vídeo que o
`sincronizar` descobriu quando a live foi analisada junto da demo.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .moments import Event, Moment
from .tools import FFMPEG, FFPROBE
from .tools import substituir_arquivo
from .tools import sem_janela

TICKRATE = 64.0


class ErroVideoLimpo(Exception):
    """Falha ao preparar o vídeo limpo (mensagem pronta, em português)."""


@dataclass
class RoundGravado:
    round: int
    arquivo: Path
    t0: float                 # tempo da demo (s) em que o arquivo começa
    t1: float                 # tempo da demo (s) em que termina
    duracao: float            # duração real do arquivo (s)


def faixas_de_audio(arq: Path) -> int:
    """Quantas faixas de áudio o arquivo tem (0 se não abrir). VOD da Twitch e vídeo do YouTube têm 1; a gravação do OBS, até 6."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(arq)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    return len([x for x in r.stdout.splitlines() if x.strip()]) if r.returncode == 0 else 0


def _duracao(arq: Path) -> float:
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(arq)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def rounds_gravados(pasta_gravacao: Path) -> list[RoundGravado]:
    """Os rounds que a gravação deixou prontos e legíveis, em ordem. Lê o `registro.json` (resultados + trechos em ticks)."""
    pasta = Path(pasta_gravacao)
    try:
        dados = json.loads((pasta / "registro.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ErroVideoLimpo(f"Não achei o registro da gravação em {pasta} (grave a demo primeiro).") from e
    ticks = {int(t["round"]): (int(t["tick_ini"]), int(t["tick_fim"])) for t in dados.get("trechos", [])}
    saida: list[RoundGravado] = []
    for r in dados.get("resultados", []):
        arq = Path(r.get("arquivo", ""))
        n = int(r.get("round", 0))
        if not arq.is_file() or n not in ticks:
            continue
        dur = _duracao(arq)
        if dur <= 1.0:
            continue
        ini, _fim = ticks[n]
        t0 = ini / TICKRATE - float(r.get("congelado_inicio_s") or 0.0)          # o vídeo começa parado no tick_ini por esses segundos
        saida.append(RoundGravado(n, arq, t0, t0 + dur, dur))
    saida.sort(key=lambda x: x.round)
    if not saida:
        raise ErroVideoLimpo("Nenhum round gravado e legível nessa pasta.")
    return saida


def montar_partida_limpa(rounds: list[RoundGravado], destino: Path) -> tuple[Path, dict[int, float]]:
    """Junta os rounds num arquivo só, na ordem, SEM reencodar (todos saem do mesmo gravador). Devolve (arquivo, {round: deslocamento}) com
    `tempo_no_arquivo = tempo_da_demo + deslocamento[round]`."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    lista = destino.with_suffix(".lista.txt")
    deslocamentos: dict[int, float] = {}
    acumulado = 0.0
    linhas = []
    for r in rounds:
        deslocamentos[r.round] = acumulado - r.t0
        acumulado += r.duracao
        caminho = str(r.arquivo.resolve()).replace("\\", "/").replace("'", "'\\''")
        linhas.append(f"file '{caminho}'")
    lista.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    parcial = destino.with_name(destino.stem + ".parcial.mp4")
    try:
        cmd = [FFMPEG, "-y", "-v", "error", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(lista), "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
               "-movflags", "+faststart", str(parcial)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
        if r.returncode != 0:
            raise ErroVideoLimpo(f"o ffmpeg não conseguiu juntar os rounds: {r.stderr[-300:]}")
        substituir_arquivo(parcial, destino)
    finally:
        lista.unlink(missing_ok=True)
        parcial.unlink(missing_ok=True)
    return destino, deslocamentos


APARAR_MAX_S = 8.0      # quanto um momento pode passar do fim gravado e ainda ser aparado (em vez de descartado)


def _round_pelo_tempo(ini_m: float, fim_m: float, rounds: list[RoundGravado]) -> RoundGravado | None:
    """O round gravado que contém o momento (tempo da demo), ou None. O fim pode passar até `APARAR_MAX_S` do fim gravado, como no
    casamento pelo número: quem decide se apara ou descarta é a mesma regra de baixo (revisão final de 26/11)."""
    return next((r for r in rounds if ini_m >= r.t0 - 0.05 and fim_m <= r.t1 + APARAR_MAX_S), None)


def mapear_momentos(momentos: list[Moment], rounds: list[RoundGravado], deslocamentos: dict[int, float],
                    imprimir=print) -> list[Moment]:
    """Só os momentos de rounds gravados e que cabem inteiros no arquivo do round, com todos os tempos levados para o vídeo limpo.
    A-008: o round é achado pelo número; se o número não casa (pasta gravada antes da correção da numeração, R-023: o round físico 16
    foi salvo como 17), casa pelo TEMPO (o momento inteiro dentro de um round gravado) e avisa."""
    por_round = {r.round: r for r in rounds}
    saida: list[Moment] = []
    pelo_tempo: list[str] = []
    for m in momentos:
        ini_m = min([m.start] + [a for a, _ in m.segmentos]) if m.segmentos else m.start
        fim_m = max([m.end] + [b for _, b in m.segmentos]) if m.segmentos else m.end
        r = por_round.get(m.round_n or -1)
        if r is None or fim_m < r.t0 or ini_m > r.t1:           # sem esse número, ou o número é de outro round gravado (tempo sem relação)
            achado = _round_pelo_tempo(ini_m, fim_m, rounds)
            if achado is None:
                continue
            pelo_tempo.append(f"{m.round_n}->{achado.round}")
            r = achado
        if ini_m < r.t0 - 0.05:
            continue                                                       # começa antes do que foi gravado: fica de fora
        fim_corte = r.t1 - 0.2                                             # até onde dá para cortar com segurança
        if fim_m > r.t1 + 0.05:
            # prova real 10: o pós-abate (2,5 s) passou do fim gravado por poucos segundos. Se TODOS os abates cabem e o excesso é pequeno,
            # o momento é aparado no fim gravado em vez de sumir
            if fim_m - r.t1 > APARAR_MAX_S or any(k.t > fim_corte - 0.5 for k in m.kills) or m.start >= fim_corte:
                continue
            m = dataclasses.replace(
                m, end=min(m.end, fim_corte),
                segmentos=[(a, min(b, fim_corte)) for a, b in m.segmentos if a < fim_corte],
                pico=None if m.pico is None else min(m.pico, fim_corte))
        d = deslocamentos[r.round]
        novo = dataclasses.replace(
            m, start=m.start + d, end=m.end + d,
            kills=[Event(k.t + d, k.kind, list(k.icons), k.crop_file) for k in m.kills],
            assists=[Event(a.t + d, a.kind, list(a.icons), a.crop_file) for a in m.assists],
            segmentos=[(a + d, b + d) for a, b in m.segmentos],
            pico=None if m.pico is None else m.pico + d,
            round_banner_t=None)                                           # o banner do vídeo limpo é lido de novo; aqui não há leitura
        saida.append(novo)
    if pelo_tempo:
        imprimir(f"[aviso] {len(pelo_tempo)} jogada(s) casada(s) com o round gravado pelo TEMPO, não pelo número ({', '.join(pelo_tempo)}): "
                 "a pasta foi gravada com a numeração antiga dos rounds.")
    for i, m in enumerate(saida, 1):
        m.idx = i
    return saida


# ── a voz da live ─────────────────────────────────────────────────────────────────────────────────

def deslocamento_da_analise(pasta_analise: Path, demo_nome: str) -> float:
    """`t_video_da_live = t_demo + deslocamento`, lido do `sincronia.json` que a análise da live (com a demo) gravou."""
    try:
        itens = json.loads((Path(pasta_analise) / "sincronia.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ErroVideoLimpo(f"Não achei o sincronia.json em {pasta_analise}: analise a live junto da demo primeiro.") from e
    for item in itens:
        if str(item.get("demo")) == demo_nome and item.get("usada", True) and item.get("deslocamento") is not None:
            return float(item["deslocamento"])
    raise ErroVideoLimpo(f"A análise da live não usou a demo {demo_nome}: a sincronia não foi confiável.")


def juntar_voz_da_live(clip: Path, live: Path, inicio_na_live: float, faixa_voz: int, destino: Path, ganho_db: float = 0.0) -> Path:
    """Põe a voz do dono (faixa `faixa_voz` da gravação do OBS, 1 = a primeira) por cima do jogo do vídeo limpo. O resultado mantém o formato dos
    cortes: faixa 1 = jogo + voz (para cortes e shorts), faixa 2 = só o jogo (para a música da montagem)."""
    if faixa_voz < 2:
        raise ErroVideoLimpo("A faixa 1 do OBS é a mistura da live (com o Discord): use a faixa só do microfone (audio.faixa_voz de 2 a 6).")
    dur = _duracao(clip)
    if inicio_na_live < 0:
        raise ErroVideoLimpo("O round começa antes do início da live; não dá para pôr a voz.")
    destino = Path(destino)
    parcial = destino.with_name(destino.stem + ".parcial.mp4")
    filtro = (f"[1:a:{faixa_voz - 1}]atrim=duration={dur:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
              f"volume={ganho_db:.1f}dB[voz];[0:a:1]aresample=48000,aformat=channel_layouts=stereo[jogo];"
              "[jogo][voz]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89:level=false[mix]")
    cmd = [FFMPEG, "-y", "-v", "error", "-nostdin", "-i", str(clip), "-ss", f"{inicio_na_live:.3f}", "-i", str(live), "-filter_complex", filtro,
           "-map", "0:v:0", "-map", "[mix]", "-map", "0:a:1", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
           str(parcial)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    if r.returncode != 0:
        parcial.unlink(missing_ok=True)
        raise ErroVideoLimpo(f"o ffmpeg não conseguiu juntar a voz da live: {r.stderr[-300:]}")
    substituir_arquivo(parcial, destino)
    return destino


# ── a webcam da live (S5): painel de cima do short de imagem limpa ───────────────────────────────

def caixa_da_webcam(pasta_analise: Path, live: Path, cfg) -> tuple[int, int, int, int] | None:
    """Onde a webcam está na LIVE, em pixels do arquivo da live (x, y, largura, altura), com a proporção do painel do short e o zoom do dono: o mesmo recorte
    que o short da live faria. Vem do `layout.json` que a análise da live gravou (ou de `shorts.camera_manual`, que o dono pode ter marcado).
    None = a análise não achou webcam, a live não é 16:9 (o recorte relativo não vale) ou não deu para ler."""
    from .layout import Layout, cam_crop
    from .scan import probe
    s = cfg.shorts
    try:
        if s.camera_manual and len(s.camera_manual) == 4:
            x, y, w, h = s.camera_manual
            lay = Layout((x, y, w, h), (x + w * 0.4, y + h * 0.25, w * 0.2, h * 0.35), 1.0)
        else:
            d = json.loads((Path(pasta_analise) / ".cache" / "layout.json").read_text(encoding="utf-8"))
            if not d.get("face"):
                return None
            lay = Layout(tuple(d["cam"]) if d.get("cam") else None, tuple(d["face"]), float(d.get("conf") or 0.0))
        info = probe(str(live))
    except Exception:  # noqa: BLE001  # a webcam é enfeite do short: nunca derruba a edição
        return None
    if info.height <= 0 or abs(info.width / info.height - 16 / 9) > 0.05:
        return None
    return cam_crop(lay, info.width, info.height, s.largura / s.camera_altura, s.zoom_camera)


def assinatura_da_webcam(live: Path, caixa: tuple[int, int, int, int], desloc: float, rounds: list[RoundGravado], largura: int, altura: int) -> str:
    """O que, se mudar, exige refazer `PARTIDA_WEBCAM.mp4`: a live (nome, tamanho, data), a caixa do recorte, o deslocamento demo↔live, o tamanho do painel e os rounds
    gravados (número, início e duração). Não usa a data dos arquivos que a própria edição recria (`_com_voz`), senão nada seria reaproveitado."""
    import hashlib
    try:
        st = Path(live).stat()
        origem = [Path(live).name, st.st_size, st.st_mtime_ns]
    except OSError:
        origem = [Path(live).name, 0, 0]
    dados = {"live": origem, "caixa": list(caixa), "desloc": round(float(desloc), 3), "painel": [largura, altura],
             "rounds": [[r.round, round(r.t0, 3), round(r.duracao, 3)] for r in rounds]}
    return hashlib.sha1(json.dumps(dados, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def webcam_da_live(clip: Path, live: Path, inicio_na_live: float, caixa: tuple[int, int, int, int], destino: Path, largura: int, altura: int, fps: int = 60) -> Path:
    """Recorta a webcam da live (`caixa`, px da live) durante o round `clip` e a deixa do tamanho do painel (`largura` x `altura`), SEM áudio e com a MESMA duração
    do round: o vídeo da webcam fica alinhado quadro a quadro com o vídeo limpo (mesma linha do tempo)."""
    if inicio_na_live < 0:
        raise ErroVideoLimpo("O round começa antes do início da live; não dá para pôr a webcam.")
    dur = _duracao(clip)
    x, y, w, h = caixa
    destino = Path(destino)
    parcial = destino.with_name(destino.stem + ".parcial.mp4")
    filtro = (f"crop={w}:{h}:{x}:{y},scale={largura}:{altura}:flags=lanczos,fps={fps},tpad=stop_mode=clone:stop_duration={dur:.3f},"
              f"trim=duration={dur:.3f},setpts=PTS-STARTPTS,format=yuv420p")
    cmd = [FFMPEG, "-y", "-v", "error", "-nostdin", "-ss", f"{inicio_na_live:.3f}", "-i", str(live), "-vf", filtro, "-an", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "16", "-g", "120", "-movflags", "+faststart", str(parcial)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **sem_janela())
    if r.returncode != 0:
        parcial.unlink(missing_ok=True)
        raise ErroVideoLimpo(f"o ffmpeg não conseguiu recortar a webcam da live: {r.stderr[-300:]}")
    substituir_arquivo(parcial, destino)
    return destino


def montar_webcam(rounds: list[RoundGravado], cams: dict[int, Path], destino: Path, tolerancia_s: float = 0.5) -> Path:
    """Junta as webcams dos rounds, na mesma ordem e com as mesmas durações da partida limpa: `PARTIDA_WEBCAM.mp4` tem a mesma linha do tempo de `PARTIDA_LIMPA.mp4`.
    Levanta `ErroVideoLimpo` se a duração total não bater (aí não dá para usar: o painel ficaria fora de tempo)."""
    cams_rounds = [RoundGravado(r.round, cams[r.round], r.t0, r.t1, _duracao(cams[r.round])) for r in rounds if r.round in cams]
    if len(cams_rounds) != len(rounds):
        raise ErroVideoLimpo("Falta a webcam de algum round.")
    arquivo, _ = montar_partida_limpa(cams_rounds, destino)
    esperado, obtido = sum(r.duracao for r in rounds), _duracao(arquivo)
    if abs(esperado - obtido) > tolerancia_s * max(1, len(rounds)):
        arquivo.unlink(missing_ok=True)
        raise ErroVideoLimpo(f"A webcam saiu com {obtido:.1f} s e a partida limpa tem {esperado:.1f} s: não dá para alinhar.")
    return arquivo
