"""Cartão tático estilo broadcast (BLAST / HLTV) para cortes 16:9 e montagem (V4).

Gera um arquivo de legenda `.ass` minimalista no canto inferior esquerdo (1920x1080) nos primeiros `1,8 s`
da jogada, com entrada e saída suaves (`\fad(150,250)`), sem cobrir a mira nem o kill feed:
  Ex.: `ROUND 12  ·  CLUTCH 1v4` ou `ROUND 5  ·  TRIPLE KILL (3 HS)`
"""
from __future__ import annotations

from pathlib import Path

from .moments import Moment


def texto_cartao(m: Moment) -> str:
    """Monta a linha curta de contexto tático para o cartão da jogada."""
    partes: list[str] = []
    if m.round_n:
        partes.append(f"ROUND {m.round_n}")
    titulo = (m.label or "").strip()
    if m.clutch and m.clutch[1]:
        titulo = f"CLUTCH 1v{m.clutch[0]}"
    if titulo:
        n_hs = sum(k.hs for k in m.kills)
        if n_hs >= 2 and "HS" not in titulo:
            titulo += f" ({n_hs} HS)"
        partes.append(titulo)
    if getattr(m, "abertura", False) and "ENTRY" not in " ".join(partes):
        partes.append("ENTRY")
    return "  ·  ".join(partes)


def titulo_grande(m: Moment) -> str:
    """Só o nome da jogada, para a capa de letras grandes ("DOUBLE KILL", "CLUTCH 1v4"): sem o round, que a miniatura não comporta."""
    titulo = (m.label or "").strip()
    if m.clutch and m.clutch[1]:
        titulo = f"CLUTCH 1v{m.clutch[0]}"
    return titulo


def gerar_ass_cartao(destino: Path, m: Moment, largura: int = 1920, altura: int = 1080,
                     duracao: float = 1.8, estatico: bool = False, grande: bool = False) -> Path | None:
    """Escreve o arquivo `.ass` do cartão tático em `destino`. Devolve `None` se o momento não tiver rótulo.
    `estatico=True` (capa/foto de 1 quadro): o texto já está inteiro em 0,00 s, sem fade — o quadro único de uma
    foto tem tempo 0 e, com o início em 0,10 s e fade-in, o título saía invisível (achado G-G01 do Gemini)."""
    linha = titulo_grande(m) if grande else texto_cartao(m)
    if not linha:
        return None
    # capa grande (D-G3 B): Anton ~17% da altura, branco com contorno preto grosso, no terço de baixo à esquerda
    estilo = ("Style: Cartao,Anton,120,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,"
              "0,0,0,0,100,100,2,0,1,9,4,1,60,60,56,1\n\n") if grande else (
              "Style: Cartao,Anton,42,&H00FFFFFF,&H0000FFFF,&H00141414,&H80000000,"
              "0,0,0,0,100,100,1.5,0,3,14,0,1,64,64,72,1\n\n")
    inicio = "0:00:00.00" if estatico else "0:00:00.10"
    efeito = "" if estatico else "{\\fad(150,250)}"
    fim_cs = int(round(duracao * 100))
    fim_str = f"0:00:{fim_cs // 100:02d}.{fim_cs % 100:02d}"
    conteudo = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {largura}\n"
        f"PlayResY: {altura}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + estilo +
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,{inicio},{fim_str},Cartao,,0,0,0,,{efeito}{linha}\n"
    )
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(conteudo, encoding="utf-8")
    return destino

