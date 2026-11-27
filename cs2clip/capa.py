"""Gerador automático de Capa / Thumbnail para YouTube (V9 - `CAPA_<video>.jpg`, 1280x720).

Escolhe a jogada de maior nota do vídeo, amostra 5 quadros ao redor do abate principal (`focus ± 0,2 s`),
mede a nitidez (variância do Laplaciano) para evitar quadro borrado por movimento/flash, aplica contraste limpo
e grava o título de destaque (`ACE`, `CLUTCH 1v4`, `QUADRA KILL`) com a fonte Anton em 1280x720.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from .cartao import gerar_ass_cartao
from .moments import Moment
from .render import _filtro_ass
from .tools import FFMPEG, gravar_atomico
from .tools import sem_janela


BANNER_BRANCO = 0.45    # fração da faixa do banner com pixels quase brancos (>235) acima da qual o quadro é descartado


def banner_estourado(img: np.ndarray) -> bool:
    """True se a faixa central-superior (onde o CS2 mostra o banner de fim de round: y 15–28%, x 35–65%) está em boa
    parte branca — o banner piscando na entrada. O filtro de brilho da tela inteira não pega isso (achado do Gemini:
    a capa_1 saiu com um retângulo branco estourado e ainda ganhava nos pontos de nitidez por causa da borda dura)."""
    h, w = img.shape
    faixa = img[int(h * 0.15):int(h * 0.28), int(w * 0.35):int(w * 0.65)]
    return bool(faixa.size and float(np.mean(faixa > 235.0)) > BANNER_BRANCO)


DESLOCAMENTOS = (-1.2, -0.8, -0.4, -0.2, -0.1, 0.0, 0.1, 0.2)   # s em torno do abate: o começo deixa fugir do banner de fim de round
BANNER_ANTES_CAPA = 1.0   # s: o banner é lido no quadro-chave (até ~2 s depois de aparecer); nenhum candidato fica depois disto


ANTES_DO_ULTIMO_ABATE = 0.6   # s: nunca antes disto do último abate (a nitidez sozinha premiava parede texturizada, sem ação)


def candidatos_da_capa(t_central: float, banner_t: float | None = None, ultimo_abate: float | None = None) -> list[float]:
    """Instantes que a capa testa. Com o instante do banner de fim de round conhecido, os que caem depois de
    `banner_t - BANNER_ANTES_CAPA` ficam de fora (o banner piscando estragou a capa principal da prova de 65 min), e com o
    último abate conhecido, os que caem mais de `ANTES_DO_ULTIMO_ABATE` antes dele também (a ação é o que vende a capa).
    Se as duas regras não deixarem nenhum, vale só a do banner; se nem essa, fica o mais cedo."""
    todos = sorted({round(max(0.0, t_central + dt), 2) for dt in DESLOCAMENTOS})
    if banner_t is None and ultimo_abate is None:
        return todos
    boa = [t for t in todos if (banner_t is None or t < banner_t - BANNER_ANTES_CAPA)
           and (ultimo_abate is None or t >= ultimo_abate - ANTES_DO_ULTIMO_ABATE)]
    if boa:
        return boa
    sem_banner = [t for t in todos if banner_t is None or t < banner_t - BANNER_ANTES_CAPA]
    return sem_banner or todos[:1]


def escolher_instante_com_nitidez(src: str, t_central: float, duracao_video: float = 0.0,
                                  banner_t: float | None = None, ultimo_abate: float | None = None) -> tuple[float, float]:
    """Testa instantes em torno de `t_central` e devolve (o de maior nitidez e sem flash branco, a nitidez dele; -1 se nenhum serviu)."""
    w, h = 320, 180
    candidatos = candidatos_da_capa(t_central, banner_t, ultimo_abate)
    if duracao_video > 0:
        candidatos = [min(max(0.0, duracao_video - 0.1), t) for t in candidatos]
    melhor_t = max(0.0, t_central)
    melhor_nota = -1.0
    for t in sorted(set(round(x, 2) for x in candidatos)):
        r = subprocess.run(
            [FFMPEG, "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1",
             "-vf", f"scale={w}:{h}", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
            capture_output=True, **sem_janela(),
        )
        if len(r.stdout) < w * h:
            continue
        img = np.frombuffer(r.stdout[: w * h], dtype=np.uint8).reshape(h, w).astype(np.float32)
        brilho = float(np.mean(img))
        if brilho > 235.0 or brilho < 12.0:   # tela branca de flashbang ou quadro todo preto
            continue
        if banner_estourado(img):             # faixa do banner de fim de round piscando em branco
            continue
        # Variância do Laplaciano discreto 2D como medida de nitidez
        lap = (
            -4.0 * img[1:-1, 1:-1]
            + img[:-2, 1:-1] + img[2:, 1:-1]
            + img[1:-1, :-2] + img[1:-1, 2:]
        )
        nitidez = float(np.var(lap))
        if nitidez > melhor_nota:
            melhor_nota = nitidez
            melhor_t = t
    return melhor_t, melhor_nota


def _ultimo_abate(m: Moment) -> float | None:
    return max((k.t for k in m.kills), default=None)


def _renderizar_capa(src: str, melhor: Moment, t: float, destino: Path, grande: bool = False) -> Path:
    destino = Path(destino).resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)
    ass_tmp = destino.with_suffix(".capa.ass")
    try:
        gerar_ass_cartao(ass_tmp, melhor, largura=1280, altura=720, duracao=5.0, estatico=True, grande=grande)
        vf = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=contrast=1.08:saturation=1.12"
        if ass_tmp.is_file():
            vf += _filtro_ass(ass_tmp)   # roda na pasta do .ass (cwd): caminho com acento/apóstrofo não quebra
        r = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", str(Path(src).resolve()),
             "-frames:v", "1", "-vf", vf, "-q:v", "2", str(destino)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=ass_tmp.parent if ass_tmp.is_file() else None, **sem_janela(),
        )
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg falhou na capa: {r.stderr[-400:]}")
    finally:
        ass_tmp.unlink(missing_ok=True)
    return destino


ESTILOS = ("grande", "discreta", "chamativa")

# ── 23/11/2026 (dono): capa "chamativa" ──────────────────────────────────────────────────────────
# "uma imagem apenas do jogo ou junto com minha webcam, mas com uma escrita chamativa para criar alerta em quem vê e dar vontade de clicar".
# Regra do texto da capa: até 3 palavras, gíria do CS2, número em vez de nome, reticências valem; e a regra da verdade: só o que o kit sabe.
LARANJA_ASS = "&H001F9AFF"     # #FF9A1F, a cor de destaque da marca (o ASS guarda em BGR)
ALTURA_WEBCAM_CAPA = 300       # px da webcam na capa 1280x720 (canto de baixo à direita)
MARGEM_WEBCAM_CAPA = 36
BORDA_WEBCAM_CAPA = 6


def texto_chamativo(m: Moment) -> tuple[str, str]:
    """(linha branca, linha em laranja) da capa chamativa. Sempre verdade: sai só do que o momento sabe (abates, ícones, clutch, morte no fim, tipo)."""
    if m.categoria == "resenha":
        return "", "RESENHA"
    if m.kind == "fail":
        return ("QUE", "ISSO?") if m.categoria == "morte_com_risada" else ("DEU", "RUIM...")
    n = len(m.kills)
    if m.clutch:
        inimigos, ganhou = m.clutch
        return ("CLUTCH", f"1v{inimigos}") if ganhou else ("QUASE", f"1v{inimigos}...")
    if n >= 5:
        return "", "ACE"
    if n >= 3:                                # revisão final de 23/11: com 3+ kills a contagem é o gancho (o dono aprovou "3 KILLS" na prévia da jogada de 3)
        return str(n), "KILLS"
    if getattr(m, "morreu_no_fim", False) and n >= 1:
        return "COMEÇOU", "BEM..."           # o título que mais rendeu (1.227 views): "Começou bem e terminou..."
    icones = {i for k in m.kills for i in k.icons}
    if "noscope" in icones:
        return "SEM", "MIRA"
    if "smoke" in icones:
        return "PELA", "SMOKE"
    if "wallbang" in icones:
        return "PELA", "PAREDE"
    if n >= 1:
        return str(n), "KILLS" if n > 1 else "KILL"
    return "", (m.label or "").upper()


UMA_LINHA_MAX = 9              # "3 KILLS", "SEM MIRA", "DEU RUIM...": cabe numa linha só (maior); acima disso, 2 linhas coladas


def gerar_ass_chamativa(destino: Path, m: Moment, com_webcam: bool, largura: int = 1280, altura: int = 720) -> Path:
    """Texto grande (Anton) à esquerda, no meio da altura: palavra branca + palavra laranja, contorno preto grosso e sombra. Curto: uma linha só, maior;
    comprido ("COMEÇOU BEM..."): 2 linhas coladas (posição fixa: o espaço entre linhas do ASS separava demais). Com a webcam no canto, a letra é menor."""
    cima, destaque = texto_chamativo(m)
    branco, laranja = "{\\c&H00FFFFFF&}", "{\\c" + LARANJA_ASS + "&}"
    meio = altura // 2
    if len(f"{cima} {destaque}".strip()) <= UMA_LINHA_MAX:
        tamanho = 250 if com_webcam else 330
        eventos = ["{\\an4\\pos(60," + str(meio) + ")}" + (branco + cima + " " if cima else "") + laranja + destaque]
    else:
        tamanho = 205 if com_webcam else 270
        eventos = ["{\\an1\\pos(60," + str(meio + int(tamanho * 0.16)) + ")}" + branco + cima,
                   "{\\an7\\pos(60," + str(meio - int(tamanho * 0.16)) + ")}" + laranja + destaque]
    cabecalho = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {largura}", f"PlayResY: {altura}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Chamativa,Anton,{tamanho},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,2,0,1,11,7,4,56,56,0,1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    conteudo = "\n".join(cabecalho + [f"Dialogue: 0,0:00:00.00,0:00:05.00,Chamativa,,0,0,0,,{e}" for e in eventos]) + "\n"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(conteudo, encoding="utf-8")
    return destino


FUNDO_LIVRE_MIN = 360    # px (escala 1920): o fundo só recorta o centro se sobrar pelo menos isto de cada lado da mira até a webcam (senão, quadro inteiro)


def foco_da_capa(m: Moment, estilo: str) -> float:
    """O instante em volta do qual a capa procura o quadro. Chamativa num FAIL: 1,5 s ANTES da morte (logo depois vem a tela do placar, prévia real de 23/11).
    Nos outros casos, o foco de sempre (o estilo de antes não muda)."""
    if estilo == "chamativa" and m.kind == "fail":
        return m.focus - 1.5
    return m.focus


def _renderizar_capa_chamativa(src: str, m: Moment, t: float, destino: Path, cam_box: tuple[int, int, int, int] | None = None,
                               mostrar_webcam: bool = True) -> Path:
    """Capa 1280x720: o quadro do jogo mais vivo, a metade esquerda um pouco escura (o texto lê em qualquer mapa), o texto chamativo e, se houver,
    a webcam INTEIRA (em pixels de 1920x1080) no canto de baixo à direita com borda laranja."""
    destino = Path(destino).resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)
    ass_tmp = destino.with_suffix(".capa.ass")
    corte = ""
    if cam_box is not None:      # o fundo é o centro do jogo, sem alcançar a webcam da live (senão ela aparece pequena atrás do texto: prévia real de 23/11)
        cx, _, cw, _ = cam_box
        livre = 960 - (cx + cw) if cx + cw <= 960 else (cx - 960 if cx >= 960 else 0)
        if livre >= FUNDO_LIVRE_MIN:
            w = int(2 * livre) // 2 * 2
            h = int(w * 9 / 16) // 2 * 2
            corte = f"scale=1920:1080,crop={w}:{h}:{960 - w // 2}:{540 - h // 2},"
    fundo = (f"{corte}scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=contrast=1.12:saturation=1.3:brightness=-0.02,format=yuv444p,"
             "geq=lum='lum(X,Y)*min(1,0.55+0.45*X/760)':cb='cb(X,Y)':cr='cr(X,Y)',format=yuv420p")   # degradê: mais escuro à esquerda, onde vai o texto
    try:
        gerar_ass_chamativa(ass_tmp, m, cam_box is not None and mostrar_webcam)
        if cam_box is None or not mostrar_webcam:
            args = ["-vf", fundo + _filtro_ass(ass_tmp)]
        else:
            cx, cy, cw, ch = cam_box
            alt = ALTURA_WEBCAM_CAPA
            larg = int(alt * cw / max(1, ch)) // 2 * 2
            b = BORDA_WEBCAM_CAPA
            x = 1280 - MARGEM_WEBCAM_CAPA - larg - b
            y = 720 - MARGEM_WEBCAM_CAPA - alt - b
            grafo = (f"[0:v]scale=1920:1080,split[a][c0];[a]{fundo}[bg];"
                     f"[c0]crop={cw}:{ch}:{cx}:{cy},scale={larg}:{alt}:flags=lanczos,pad={larg + 2 * b}:{alt + 2 * b}:{b}:{b}:color=0xFF9A1F[cam];"
                     f"[bg][cam]overlay=x={x}:y={y},format=yuv420p{_filtro_ass(ass_tmp)}[v]")
            args = ["-filter_complex", grafo, "-map", "[v]"]
        r = subprocess.run([FFMPEG, "-y", "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", str(Path(src).resolve()), "-frames:v", "1",
                            *args, "-q:v", "2", str(destino)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ass_tmp.parent, **sem_janela())
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg falhou na capa chamativa: {r.stderr[-400:]}")
    finally:
        ass_tmp.unlink(missing_ok=True)
    return destino


def _renderizar_par(src: str, m: Moment, t: float, destino: Path, estilo: str, cam_box: tuple[int, int, int, int] | None = None) -> tuple[Path, Path]:
    """A capa principal no estilo escolhido e a outra versão ao lado. Chamativa: com webcam (se houver) e a outra só com o jogo; sem webcam, a outra é a "grande"."""
    destino = Path(destino)
    if estilo == "chamativa":
        principal = _renderizar_capa_chamativa(src, m, t, destino, cam_box)
        if cam_box is not None:
            return principal, _renderizar_capa_chamativa(src, m, t, destino.with_name(destino.stem + "_so_jogo.jpg"), cam_box, mostrar_webcam=False)
        return principal, _renderizar_capa(src, m, t, destino.with_name(destino.stem + "_grande.jpg"), grande=True)
    grande = estilo != "discreta"
    principal = _renderizar_capa(src, m, t, destino, grande=grande)
    return principal, _renderizar_capa(src, m, t, destino.with_name(destino.stem + _nome_da_outra(estilo) + ".jpg"), grande=not grande)


def _nome_da_outra(estilo: str) -> str:
    """Sufixo da capa que NÃO é a principal: com o padrão `grande`, a discreta sai como `*_discreta`; com `discreta`, a grande como `*_grande`."""
    return "_discreta" if estilo == "grande" else "_grande"


def gerar_capa(src: str, momentos: list[Moment], destino: Path, duracao_video: float = 0.0, estilo: str = "grande",
               cam_box: tuple[int, int, int, int] | None = None) -> Path | None:
    """Gera `destino` (1280x720 JPEG) a partir da melhor jogada de `momentos`.
    `estilo` (decidido pelo dono em 08/11/2026): "grande" = o arquivo principal tem as letras grandes e a versão com o
    cartão discreto sai ao lado como `*_discreta.jpg`; "discreta" = o contrário (o comportamento anterior)."""
    if not momentos:
        return None
    melhor = max(momentos, key=lambda m: (m.score, len(m.kills)))
    t, _ = escolher_instante_com_nitidez(src, foco_da_capa(melhor, estilo), duracao_video, melhor.round_banner_t, _ultimo_abate(melhor))
    principal, _ = _renderizar_par(src, melhor, t, Path(destino), estilo, cam_box)     # a outra versão fica ao lado, para comparar
    return principal


DISTANCIA_MINIMA_S = 20.0   # capas candidatas vêm de jogadas diferentes: focos a pelo menos isto de distância


def escolher_distintos(momentos: list[Moment], n: int = 3, distancia: float = DISTANCIA_MINIMA_S) -> list[Moment]:
    """Os `n` momentos de maior nota que não são o mesmo lance (focos a menos de `distancia` s ficam de fora)."""
    escolhidos: list[Moment] = []
    for m in sorted(momentos, key=lambda x: (x.score, len(x.kills)), reverse=True):
        if all(abs(m.focus - o.focus) >= distancia for o in escolhidos):
            escolhidos.append(m)
        if len(escolhidos) >= n:
            break
    return escolhidos


ENTRE_QUADROS_S = 1.5   # alternativas da MESMA jogada: quadros a menos disto um do outro são "a mesma foto"


def _alternativas_da_jogada(src: str, m: Moment, usados: list[float], quantas: int, duracao_video: float) -> list[tuple[float, float, int]]:
    """23/11/2026: quando o kit tem menos jogadas diferentes que capas pedidas (imagem limpa da demo: 1 jogada), as vagas que sobram vão para
    quadros de OUTROS abates da mesma jogada. (instante, nitidez, nº do abate), do mais nítido para o menos, sem quadros colados."""
    achados: list[tuple[float, float, int]] = []
    for i, k in enumerate(sorted(m.kills, key=lambda e: e.t), 1):
        t, nitidez = escolher_instante_com_nitidez(src, k.t, duracao_video, m.round_banner_t, k.t)
        if nitidez >= 0:
            achados.append((t, nitidez, i))
    ficam: list[tuple[float, float, int]] = []
    for t, nitidez, i in sorted(achados, key=lambda x: -x[1]):
        if all(abs(t - u) >= ENTRE_QUADROS_S for u in usados + [f[0] for f in ficam]):
            ficam.append((t, nitidez, i))
        if len(ficam) >= quantas:
            break
    return ficam


def gerar_capas_candidatas(src: str, momentos: list[Moment], pasta: Path, n: int = 3,
                           duracao_video: float = 0.0, estilo: str = "grande", mesma_jogada: bool = False,
                           cam_box: tuple[int, int, int, int] | None = None) -> list[dict]:
    """C10: até `n` capas 1280x720 de jogadas diferentes em `pasta` (capa_1.jpg...), mais `capas.json` com o MOTIVO de
    cada uma (jogada, nota e a nitidez do quadro escolhido). A capa principal `CAPA_<id>.jpg` continua sendo a da
    melhor jogada; estas são alternativas para você escolher. `mesma_jogada`: se há menos jogadas diferentes que `n`, as
    vagas que sobram são de quadros de outros abates da melhor jogada (23/11/2026)."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    for antiga in pasta.glob("capa_*.jpg"):
        antiga.unlink(missing_ok=True)
    saida: list[dict] = []
    distintos = escolher_distintos(momentos, n)
    usados: list[float] = []
    for i, m in enumerate(distintos, 1):
        t, nitidez = escolher_instante_com_nitidez(src, foco_da_capa(m, estilo), duracao_video, m.round_banner_t, _ultimo_abate(m))
        usados.append(t)
        destino, outra = _renderizar_par(src, m, t, pasta / f"capa_{i}.jpg", estilo, cam_box)
        saida.append({"arquivo": destino.name, "estilo": estilo, "arquivo_outra_versao": outra.name, "jogada": m.label, "nota": round(m.score, 1), "instante_s": round(t, 2),
                      "nitidez": round(nitidez, 1) if nitidez >= 0 else None,
                      "motivo": f"{m.label or 'jogada'} (nota {m.score:.0f}); quadro mais nítido entre {len(candidatos_da_capa(m.focus, m.round_banner_t, _ultimo_abate(m)))} testados em torno do abate (antes do banner de fim de round)"
                                + ("" if nitidez >= 0 else " — nenhum quadro passou no filtro de flash/preto, usei o central")})
    if mesma_jogada and distintos and len(distintos) < n:
        melhor = distintos[0]
        for t, nitidez, k in _alternativas_da_jogada(src, melhor, usados, n - len(distintos), duracao_video):
            i = len(saida) + 1
            destino, outra = _renderizar_par(src, melhor, t, pasta / f"capa_{i}.jpg", estilo, cam_box)
            saida.append({"arquivo": destino.name, "estilo": estilo, "arquivo_outra_versao": outra.name,
                          "jogada": melhor.label, "nota": round(melhor.score, 1), "instante_s": round(t, 2), "nitidez": round(nitidez, 1),
                          "mesma_jogada": True,
                          "motivo": f"{melhor.label or 'jogada'}: quadro do abate {k} de {len(melhor.kills)} (mesma jogada; nota {melhor.score:.0f}); o mais nítido em torno desse abate"})
    gravar_atomico(pasta / "capas.json", json.dumps(saida, ensure_ascii=False, indent=1))   # G-012
    return saida
