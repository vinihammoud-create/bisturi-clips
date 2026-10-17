"""Detecta a webcam sobreposta no gameplay (para o layout vertical).

1. Amostra ~48 frames espalhados pelo vídeo.
2. Detecta rostos (Haar do OpenCV) e pega o agrupamento mais estável.
3. Delimita o retângulo da câmera: colunas/linhas ao redor do rosto cuja variação
   temporal é baixa (sala do streamer) em contraste com o jogo (muda o tempo todo).
Resultado em coordenadas relativas (0–1); pode ser sobrescrito no config.
"""
from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .tools import FFMPEG, imwrite

SW, SH = 960, 540  # escala de análise


@dataclass
class Layout:
    cam: tuple[float, float, float, float] | None  # x, y, w, h relativos
    face: tuple[float, float, float, float] | None
    confidence: float


def sample_frames(path: str, duration: float, n: int = 40) -> list[np.ndarray]:
    def grab(k: int) -> np.ndarray | None:
        t = duration * (k + 0.5) / n
        cmd = [FFMPEG, "-v", "error", "-nostdin", "-skip_frame", "nokey", "-ss", f"{t:.2f}", "-i", path,
               "-frames:v", "1", "-vf", f"scale={SW}:{SH}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        raw = subprocess.run(cmd, capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(SH, SW, 3) if len(raw) == SW * SH * 3 else None

    with ThreadPoolExecutor(6) as ex:
        return [f for f in ex.map(grab, range(n)) if f is not None]


YUNET = Path(__file__).resolve().parent / "assets" / "models" / "face_detection_yunet_2023mar.onnx"


def _faces(frames: list[np.ndarray]) -> list[tuple[int, int, int, int]]:
    # carregado da memória: o OpenCV não abre caminhos com acento no Windows
    det = cv2.FaceDetectorYN.create("onnx", np.fromfile(str(YUNET), np.uint8), np.array([], np.uint8),
                                    (SW, SH), 0.75, 0.3, 50)
    out = []
    for f in frames:
        _, faces = det.detect(f)
        for row in faces if faces is not None else []:
            x, y, w, h = row[:4]
            if 14 <= w <= 260:
                out.append((int(x), int(y), int(w), int(h)))
    return out


def _cluster(boxes, n_frames):
    if not boxes:
        return None, 0.0
    arr = np.array(boxes, np.float32)
    centers = arr[:, :2] + arr[:, 2:] / 2
    best, best_n = None, 0
    for c in centers:
        d = np.linalg.norm(centers - c, axis=1)
        idx = d < 30
        if idx.sum() > best_n:
            best_n, best = idx.sum(), idx
    sel = arr[best]
    return tuple(np.median(sel, axis=0).tolist()), best_n / max(1, n_frames)


def detect_layout(path: str, duration: float) -> Layout:
    frames = sample_frames(path, duration)
    if len(frames) < 8:
        return Layout(None, None, 0.0)
    face, conf = _cluster(_faces(frames), len(frames))
    if face is None or conf < 0.2:
        return Layout(None, None, conf)
    fx, fy, fw, fh = face
    cx, cy = int(fx + fw / 2), int(fy + fh / 2)

    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]).astype(np.float32)
    std = cv2.GaussianBlur(gray.std(axis=0), (3, 3), 0)

    # Borda da câmera = onde a variação temporal salta de baixa (sala) para alta (jogo).
    # Bordas internas estáticas (ex.: quadro na parede) têm os dois lados estáveis e perdem.
    def border(axis_prof_in, axis_prof_out, lo, hi):
        best, best_s = None, -1e9
        for p in range(lo, hi):
            s = axis_prof_out(p) - axis_prof_in(p)
            if s > best_s:
                best, best_s = p, s
        return best, best_s

    rb0, rb1 = max(0, int(fy)), min(SH, int(fy + 1.6 * fh))          # faixa de linhas (rosto/tronco)
    cb0, cb1 = max(0, int(fx - 0.8 * fw)), min(SW, int(fx + 1.8 * fw))  # faixa de colunas
    col = lambda a, b: std[rb0:rb1, max(0, a):max(0, min(SW, b))].mean() if b > a and a < SW else 0.0  # noqa: E731
    row = lambda a, b: std[max(0, a):max(0, min(SH, b)), cb0:cb1].mean() if b > a and a < SH else 0.0  # noqa: E731
    x0, _ = border(lambda p: col(p + 2, p + 9), lambda p: col(p - 9, p - 2),
                   max(9, int(cx - 6 * fw)), max(10, int(cx - 1.4 * fw)))
    x1, _ = border(lambda p: col(p - 9, p - 2), lambda p: col(p + 2, p + 9),
                   min(SW - 10, int(cx + 1.4 * fw)), min(SW - 9, int(cx + 6 * fw)))
    y0, _ = border(lambda p: row(p + 2, p + 9), lambda p: row(p - 9, p - 2),
                   max(9, int(cy - 4 * fh)), max(10, int(cy - 0.9 * fh)))
    y1, _ = border(lambda p: row(p - 9, p - 2), lambda p: row(p + 2, p + 9),
                   min(SH - 10, int(cy + 0.9 * fh)), min(SH - 9, int(cy + 4 * fh)))
    if None in (x0, x1, y0, y1):
        x0 = x1 = y0 = y1 = 0

    w, h = x1 - x0, y1 - y0
    ok = (2.2 * fw <= w <= 9 * fw) and (1.8 * fh <= h <= 7 * fh)
    if not ok:  # fallback: caixa proporcional ao rosto
        w, h = 4.6 * fw, 2.9 * fh
        x0, y0 = cx - w / 2, cy - h * 0.42
        x0, y0 = max(0, x0), max(0, y0)
        w, h = min(w, SW - x0), min(h, SH - y0)
        conf *= 0.6
    rel = lambda a, b, c, d: (a / SW, b / SH, c / SW, d / SH)  # noqa: E731
    return Layout(rel(x0, y0, w, h), rel(fx, fy, fw, fh), float(conf))


def cam_crop(lay: Layout, src_w: int, src_h: int, aspect: float, zoom: float = 5.4) -> tuple[int, int, int, int] | None:
    """Recorte (px) centrado no rosto, com a proporção do painel da câmera e dentro do retângulo dela."""
    if lay.face is None:
        return None
    fx, fy, fw, fh = lay.face
    fx, fw = fx * src_w, fw * src_w
    fy, fh = fy * src_h, fh * src_h
    if lay.cam:
        rx, ry, rw, rh = lay.cam
        rx, rw, ry, rh = rx * src_w, rw * src_w, ry * src_h, rh * src_h
    else:
        rx, ry, rw, rh = 0.0, 0.0, float(src_w), float(src_h)
    w = min(zoom * fw, rw)
    h = w / aspect
    if h > rh:
        h = rh
        w = h * aspect
    cx = fx + fw / 2
    cy = fy + fh / 2 + 0.10 * h  # rosto um pouco acima do centro (respiro de cabeça)
    x = min(max(cx - w / 2, rx), rx + rw - w)
    y = min(max(cy - h / 2, ry), ry + rh - h)
    ev = lambda v: int(round(v / 2) * 2)  # noqa: E731  (dimensões pares p/ yuv420)
    return ev(x), ev(y), ev(w), ev(h)


# ── área do jogo (barras pretas) ────────────────────────────────────────────────────────────────
# Outros players gravam o 4:3 com barras pretas dos lados numa live 16:9 (ou ultrawide com faixa em cima/embaixo).
# Kill feed, banner, painel da morte e mira do short são frações da ÁREA DO JOGO, não do quadro. No CS2 o 4:3 com
# barras e o 4:3 esticado desenham a MESMA imagem (só o monitor estica): a área do jogo levada às regiões de sempre
# reproduz o caso do usuário. Sem barras, a área é None e tudo segue idêntico ao de antes.
# Como: o pixel de barra é preto em quase todos os quadros amostrados; o jogo (mesmo um canto escuro do mapa) não.
# A borda é procurada do MEIO para fora nesse "preto que fica": webcam ou chat dentro da barra (sem encostar no
# jogo) não encurtam a barra, e uma tela fora do jogo ("já volto") em poucos quadros não atrapalha.
# NÃO resolvido (fica None = quadro inteiro, como antes): barras assimétricas, barra que não é preta (imagem/fundo
# animado), webcam/chat encostados na borda do jogo, jogo fora do centro do quadro, telas fora do jogo em mais de
# 30% dos quadros amostrados. Ultrawide (21:9) é detectado, mas o HUD dele não foi medido com dados reais: painel da
# morte e banner ficam na proporção da própria área (`como_esticado`), sem a regra do 4:3 esticado.
AREA_VERSAO = "1"
ESCURO = 28            # cinza <= isto é preto de barra (preto de vídeo = 16; folga para o ruído da compressão)
FIXO = 0.7             # pixel de barra: escuro em >= 70% dos quadros que votam
FRACAO_ESCURA = 0.6    # coluna/linha com >= 60% de "preto que fica" é barra (webcam/chat de até 40% da altura passa)
BARRA_MIN = 0.02       # barra < 2% do lado é borda de captura: ignora (quadro inteiro, como antes)
QUADRO_ESCURO = 0.85   # quadro quase todo preto (fade, carregamento) não vota
PROPORCOES = (4 / 3, 5 / 4, 16 / 10, 3 / 2, 16 / 9, 21 / 9, 64 / 27)   # para acertar a borda borrada pela escala

Area = tuple[int, int, int, int]   # x, y, largura, altura em pixels do vídeo


def _ponta(frac: np.ndarray) -> int:
    """Largura da barra numa ponta (`frac[0]` = coluna/linha da borda do quadro, até o meio): vai até a coluna
    escura mais perto do meio."""
    ok = np.nonzero(frac >= FRACAO_ESCURA)[0]
    return int(ok[-1]) + 1 if len(ok) else 0


def _barras(frac: np.ndarray) -> tuple[int, int]:
    """Largura da barra em cada ponta (início, fim) a partir da fração de pixels escuros por coluna (ou linha)."""
    meio = len(frac) // 2
    return _ponta(frac[:meio]), _ponta(frac[::-1][:meio])


def _par(v: float) -> int:
    return int(round(v / 2) * 2)


def area_bruta(frames: list[np.ndarray]) -> Area | None:
    """Área do jogo a partir de quadros em cinza no tamanho original do vídeo. None = quadro inteiro."""
    if not frames:
        return None
    H, W = frames[0].shape[:2]
    conta = np.zeros((H, W), np.uint16)
    votos = 0
    for f in frames:
        if f.shape[:2] != (H, W):
            continue
        e = f <= ESCURO
        if e.mean() < QUADRO_ESCURO:
            conta += e
            votos += 1
    if votos < 4:
        return None
    fixo = conta >= FIXO * votos   # "preto que fica"
    # 1º as laterais (todas as linhas), depois cima/baixo só entre as laterais, e as laterais de novo entre elas
    esq, dir_ = _barras(fixo.mean(axis=0))
    x0, x1 = esq, W - dir_
    cima, baixo = _barras(fixo[:, x0:x1].mean(axis=1))
    y0, y1 = cima, H - baixo
    if y0 or baixo:
        esq, dir_ = _barras(fixo[y0:y1].mean(axis=0))
    # barra fininha = borda de captura (não é layout): conta como sem barra
    esq, dir_ = (0 if v < BARRA_MIN * W else v for v in (esq, dir_))
    cima, baixo = (0 if v < BARRA_MIN * H else v for v in (cima, baixo))
    if not (esq or dir_ or cima or baixo):
        return None
    # só barras simétricas (jogo centralizado): uma barra só pode ser webcam/overlay escuro em cima do jogo
    if abs(esq - dir_) > max(4, W // 100) or abs(cima - baixo) > max(4, H // 100):
        return None
    w, h = W - esq - dir_, H - cima - baixo
    # a escala borra a borda em 1-2 px: perto de uma proporção conhecida, usa a medida exata (centralizada)
    for p in PROPORCOES:
        if abs(w / h / p - 1) <= 0.012:
            if esq:
                w = min(W, _par(h * p))
            else:
                h = min(H, _par(w / p))
            break
    if not 1.0 <= w / h <= 2.6:
        return None
    x, y = _par((W - w) / 2), _par((H - h) / 2)
    w, h = w // 2 * 2, h // 2 * 2
    if (x, y, w, h) == (0, 0, W, H):
        return None
    return x, y, w, h


def _quadros_cinza(path: str, duration: float, largura: int, altura: int, n: int = 16) -> list[np.ndarray]:
    """n quadros-chave espalhados pelo vídeo, em cinza e no tamanho original (a borda da barra é medida em px)."""
    def grab(k: int) -> np.ndarray | None:
        t = duration * (k + 0.5) / n
        cmd = [FFMPEG, "-v", "error", "-nostdin", "-skip_frame", "nokey", "-ss", f"{t:.2f}", "-i", path,
               "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
        raw = subprocess.run(cmd, capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(altura, largura) if len(raw) == largura * altura else None

    with ThreadPoolExecutor(6) as ex:
        return [f for f in ex.map(grab, range(n)) if f is not None]


def detectar_area(path: str, duration: float, largura: int, altura: int) -> Area | None:
    return area_bruta(_quadros_cinza(path, duration, largura, altura))


def ler_area(cache: Path, largura: int, altura: int) -> tuple[bool, Area | None]:
    """(achou_no_cache, área). Cache de outra versão ou de outro tamanho de vídeo não vale."""
    import json
    try:
        d = json.loads((cache / "area_jogo.json").read_text(encoding="utf-8"))
        if d.get("versao") != AREA_VERSAO or list(d.get("tamanho") or []) != [largura, altura]:
            return False, None
        if not d["area"]:
            return True, None
        area = tuple(int(v) for v in d["area"])
        # cache corrompido/editado (3 valores, fora do quadro...): mede de novo em vez de quebrar o crop
        if len(area) != 4:
            return False, None
        x, y, w, h = area
        if not (x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= largura and y + h <= altura):
            return False, None
        return True, area
    except (OSError, ValueError, KeyError, TypeError):
        return False, None


def area_cached(cache: Path, info, refazer: bool = False) -> Area | None:
    """Área do jogo do vídeo (uma vez por vídeo, em `.cache/area_jogo.json`). Falha na leitura = quadro inteiro
    (e não grava cache, para tentar de novo na próxima)."""
    import json
    arq = cache / "area_jogo.json"
    if not refazer:
        achou, area = ler_area(cache, info.width, info.height)
        if achou:
            return area
    quadros = _quadros_cinza(info.path, info.duration, info.width, info.height)
    if len(quadros) < 4:
        # apaga a área velha: o render (ler_area) não pode cortar o short com barras que a varredura não usou
        arq.unlink(missing_ok=True)
        return None
    area = area_bruta(quadros)
    cache.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps({"versao": AREA_VERSAO, "tamanho": [info.width, info.height],
                               "area": list(area) if area else None}), encoding="utf-8")
    return area


PROPORCOES_ESTICADAS = (4 / 3, 5 / 4, 16 / 10)   # áreas que o CS2 desenha como o 4:3 esticado medido


def como_esticado(area: Area | None) -> bool:
    """A área do jogo conta como o 4:3 esticado de sempre (painel da morte e banner levados a 16:9)? Só nas
    proporções 4:3, 5:4 e 16:10. Ultrawide e as outras não foram medidas: ficam na proporção da própria área."""
    if area is None:
        return False
    r = area[2] / area[3]
    return any(abs(r / p - 1) <= 0.02 for p in PROPORCOES_ESTICADAS)


def recortar_area(img: np.ndarray, area: Area | None) -> np.ndarray:
    if area is None:
        return img
    x, y, w, h = area
    return img[y:y + h, x:x + w]


def filtro_area(area: Area | None) -> str:
    """Começo do filtro do ffmpeg que deixa só a área do jogo ('' sem barras: o filtro fica igual ao de antes)."""
    if area is None:
        return ""
    x, y, w, h = area
    return f"crop={w}:{h}:{x}:{y},"


def preview(path: str, duration: float, lay: Layout, out_png: str) -> None:
    frames = sample_frames(path, duration, n=3)
    if not frames:
        return
    img = frames[len(frames) // 2].copy()  # pode vir menos de 3 quadros (vídeo curto com GOP longo)
    if lay.cam:
        x, y, w, h = lay.cam
        cv2.rectangle(img, (int(x * SW), int(y * SH)), (int((x + w) * SW), int((y + h) * SH)), (0, 255, 0), 2)
    if lay.face:
        x, y, w, h = lay.face
        cv2.rectangle(img, (int(x * SW), int(y * SH)), (int((x + w) * SW), int((y + h) * SH)), (0, 200, 255), 1)
    imwrite(out_png, img)
