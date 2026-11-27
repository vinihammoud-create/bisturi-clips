"""Exportação de Timeline FCPXML 1.9 para DaVinci Resolve / Premiere com jogadas e marcadores de batida (M5).

Permite abrir a montagem pronta diretamente no DaVinci Resolve (`File -> Import -> Timeline...`) com:
  * cada corte já posicionado na sequência em 1920x1080 60 fps;
  * marcadores nos instantes dos abates e nas batidas da música;
  * trilha de música sincronizada embaixo dos cortes.
"""
from __future__ import annotations

import html
from pathlib import Path

from .moments import Moment


def _seg_fcpxml(seg: float, fps: int = 60) -> str:
    quadros = max(0, int(round(seg * fps)))
    return f"{quadros * 100}/{fps * 100}s"


def _posicao_no_corte(t: float, janelas: list[tuple[float, float]]) -> float:
    """G-011: o instante `t` (tempo do vídeo original) vira a posição dentro do corte já emendado: soma só os trechos que ficaram
    (a regra do pulo tira o tempo parado do meio). Antes do 1º trecho = 0; depois do último (ou num vão cortado) = o começo do trecho seguinte."""
    acumulado = 0.0
    for a, b in janelas:
        if t <= b:
            return acumulado + max(0.0, t - a)
        acumulado += b - a
    return acumulado


def exportar_fcpxml(
    destino: Path,
    momentos: list[Moment],
    arquivos_cortes: list[Path],
    fps: int = 60,
    musica: Path | None = None,
    batidas_s: list[float] | None = None,
) -> Path:
    """Grava um arquivo `.fcpxml` compatível com o DaVinci Resolve em `destino`."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    recursos: list[str] = [
        f'    <format id="r1" name="FFVideoFormat1080p{fps}" frameDuration="100/{fps * 100}s" width="1920" height="1080"/>'
    ]
    clipes_xml: list[str] = []
    pos = 0.0

    for i, (m, arq) in enumerate(zip(momentos, arquivos_cortes), 1):
        rid = f"r{i + 1}"
        uri = arq.resolve().as_uri()
        dur = max(0.5, float(m.duration))
        nome = html.escape(f"{m.idx:02d}_{m.label or m.kind} ({m.score:.0f}pts)")
        recursos.append(f'    <asset id="{rid}" name="{nome}" src="{html.escape(uri)}" start="0s" '
                        f'duration="{_seg_fcpxml(dur, fps)}" hasVideo="1" hasAudio="1" format="r1"/>')
        marcadores: list[str] = []
        for k in m.kills:
            rel = max(0.0, min(dur - 0.05, _posicao_no_corte(k.t, m.janelas)))
            rotulo = "HEADSHOT" if k.hs else "ABATE"
            marcadores.append(
                f'          <marker start="{_seg_fcpxml(rel, fps)}" duration="100/{fps * 100}s" value="{rotulo}"/>'
            )
        bloco_m = ("\n" + "\n".join(marcadores) + "\n        ") if marcadores else ""
        clipes_xml.append(
            f'        <asset-clip name="{nome}" ref="{rid}" offset="{_seg_fcpxml(pos, fps)}" '
            f'start="0s" duration="{_seg_fcpxml(dur, fps)}" format="r1">{bloco_m}</asset-clip>'
        )
        pos += dur

    if musica is not None and musica.is_file() and pos > 0:
        rid_m = f"r{len(arquivos_cortes) + 2}"
        uri_m = musica.resolve().as_uri()
        nome_m = html.escape(musica.name)
        recursos.append(f'    <asset id="{rid_m}" name="{nome_m}" src="{html.escape(uri_m)}" start="0s" '
                        f'duration="{_seg_fcpxml(pos, fps)}" hasVideo="0" hasAudio="1"/>')
        clipes_xml.append(
            f'        <asset-clip name="{nome_m}" ref="{rid_m}" lane="-1" offset="0s" '
            f'start="0s" duration="{_seg_fcpxml(pos, fps)}"/>'
        )

    for b in (batidas_s or [])[:120]:
        if 0.0 <= b <= pos:
            clipes_xml.append(
                f'        <marker start="{_seg_fcpxml(b, fps)}" duration="100/{fps * 100}s" value="BATIDA"/>'
            )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE fcpxml>\n'
        '<fcpxml version="1.9">\n'
        '  <resources>\n'
        + "\n".join(recursos) + "\n"
        '  </resources>\n'
        '  <library>\n'
        '    <event name="Bisturi Clips">\n'
        '      <project name="Montagem CS2">\n'
        f'        <sequence format="r1" duration="{_seg_fcpxml(pos, fps)}" tcStart="0s" tcFormat="NDF">\n'
        '          <spine>\n'
        + "\n".join(clipes_xml) + "\n"
        '          </spine>\n'
        '        </sequence>\n'
        '      </project>\n'
        '    </event>\n'
        '  </library>\n'
        '</fcpxml>\n'
    )
    destino.write_text(xml, encoding="utf-8")
    return destino

