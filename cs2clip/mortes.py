"""A própria morte do jogador, lida no vídeo (sem demo).

Quando o jogador morre, o CS2 mostra no centro da tela o painel "<quem> matou você com: <arma>"
(`roundend.detect_own_death`). Medido em 17/10/2026 (live 2902813681, 15:00-45:00):
  * o painel fica legível ~2,2 s; a varredura de quadros-chave (1 a cada 2 s) perdia 2 de 7 mortes;
  * por isso esta é uma passada própria a 2 quadros/s, decodificando só a região do painel (CPU, ~45x o tempo
    real por processo; em partes paralelas uma live de 1h36 sai em menos de 1 min);
  * o OCR só roda nos quadros em que o filtro de cor do painel passa.
Resultado em cache (`.cache/mortes.json`); `--refazer mortes` lê de novo.
"""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .roundend import PAINEL_MORTE_REL, detect_own_death
from .tools import FFMPEG, gravar_atomico

FPS = 2.0
LARGURA = 563              # largura do recorte do painel num quadro 1280x720 (a que o OCR lê melhor)
MESMA_MORTE = 10.0         # s: leituras mais próximas que isso são a mesma morte
VERSAO = "2"                # v2: painel tirado da área do jogo (vídeo com barras pretas)
VERSOES_SEM_BARRAS = ("1",)  # a v1 continua valendo para vídeo sem barras (lá a leitura é idêntica)


def _trecho(video: str, inicio: float, duracao: float, altura: int, area=None) -> list[float]:
    from .layout import filtro_area
    from .rounds import criar_ocr
    x, y, w, h = PAINEL_MORTE_REL
    filtro = f"fps={FPS},{filtro_area(area)}crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={LARGURA}:{altura}"
    p = subprocess.Popen([FFMPEG, "-v", "error", "-nostdin", "-threads", "2", "-ss", f"{inicio:.3f}", "-i", video,
                          "-t", f"{duracao:.3f}", "-an", "-sn", "-vf", filtro, "-f", "rawvideo", "-pix_fmt", "bgr24",
                          "pipe:1"], stdout=subprocess.PIPE)
    ocr, achados, i, n = None, [], 0, LARGURA * altura * 3
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        painel = np.frombuffer(buf, np.uint8).reshape(altura, LARGURA, 3)
        # detect_own_death espera o quadro inteiro: monta um quadro em que o painel cai na posição certa
        quadro = _quadro_com_painel(painel)
        if ocr is None and _passa_filtro(quadro):
            ocr = criar_ocr()
        if ocr is not None and detect_own_death(quadro, ocr):
            achados.append(inicio + i / FPS)
        i += 1
    p.stdout.close()
    if p.wait() != 0:
        raise RuntimeError(f"ffmpeg falhou lendo as mortes no trecho {inicio:.0f}-{inicio + duracao:.0f} s")
    return achados


def _quadro_com_painel(painel: np.ndarray) -> np.ndarray:
    x, y, w, h = PAINEL_MORTE_REL
    W, H = round(LARGURA / w), round(painel.shape[0] / h)
    quadro = np.zeros((H, W, 3), np.uint8)
    x0, y0 = int(x * W), int(y * H)
    quadro[y0:y0 + painel.shape[0], x0:x0 + painel.shape[1]] = painel[: H - y0, : W - x0]
    return quadro


def _passa_filtro(quadro: np.ndarray) -> bool:
    from .roundend import VERMELHO_PAINEL_MIN, _vermelho_painel
    return _vermelho_painel(quadro) >= VERMELHO_PAINEL_MIN


def juntar(instantes: list[float], janela: float = MESMA_MORTE) -> list[float]:
    """Várias leituras do mesmo painel viram uma morte (vale o primeiro instante)."""
    saida: list[float] = []
    for t in sorted(instantes):
        if not saida or t - saida[-1] > janela:
            saida.append(t)
    return saida


def altura_painel(largura: int, altura: int, area=None) -> int:
    """Altura do recorte do painel levado a LARGURA px. Área 4:3 (ou 5:4, 16:10) com barras conta como 16:9 (o 4:3
    com barras vira o 4:3 esticado medido); outra área (ultrawide, não medida) fica na proporção dela, sem deformar;
    sem barras, a proporção do vídeo, como sempre."""
    from .layout import como_esticado
    _, _, w, h = PAINEL_MORTE_REL
    if como_esticado(area):
        largura, altura = 16, 9
    elif area is not None:
        largura, altura = area[2], area[3]
    return max(8, round(LARGURA * (h * altura) / (w * largura)))


def ler_mortes(video: str, duracao: float, largura: int, altura: int, processos: int = 6,
               area=None) -> list[float]:
    """Instantes (s) em que o painel "matou você" aparece, já juntados.
    `area`: área do jogo (layout.area_cached); o painel é fração dela. None = quadro inteiro."""
    alt = altura_painel(largura, altura, area)
    partes = max(1, processos)
    passo = duracao / partes
    trechos = [(i * passo, min(duracao, (i + 1) * passo + 3.0) - i * passo) for i in range(partes)]
    with ThreadPoolExecutor(partes) as ex:
        listas = list(ex.map(lambda tr: _trecho(video, tr[0], tr[1], alt, area), trechos))
    return juntar([t for lista in listas for t in lista])


def mortes_cached(cache: Path, video: str, info, processos: int, refazer: bool, area=None) -> list[float]:
    arq = cache / "mortes.json"
    if arq.exists() and not refazer:
        try:
            d = json.loads(arq.read_text(encoding="utf-8"))
            mesma_area = (d.get("area") or None) == (list(area) if area else None)   # lida com outra área: lê de novo
            if (d.get("versao") == VERSAO and mesma_area) or (area is None and d.get("versao") in VERSOES_SEM_BARRAS):
                return list(d["mortes"])
        except (OSError, ValueError, KeyError):
            pass
    try:
        mortes = ler_mortes(video, info.duration, info.width, info.height, processos, area)
    except Exception as e:  # noqa: BLE001  (sem mortes o resto funciona; e não grava cache de "nenhuma morte")
        print(f"    [aviso] não consegui ler as mortes ({e}); seguindo sem elas")
        return []
    cache.mkdir(parents=True, exist_ok=True)
    gravar_atomico(arq, json.dumps({"versao": VERSAO, "area": list(area) if area else None, "mortes": mortes}))
    return mortes
