"""Legendas animadas estilo TikTok/Shorts (ASS) + selo da jogada (DOUBLE KILL, ACE...).

Palavra-por-palavra: blocos curtos (até 3 palavras), a palavra falada fica amarela
e levemente maior; o bloco entra com um "pop". Renderizado pelo libass do ffmpeg.
"""
from __future__ import annotations

import re
from pathlib import Path

from .moments import LABELS, Moment
from .transcribe import Word

YELLOW = "&H0000D7FF&"   # ASS = &HBBGGRR
WHITE = "&H00FFFFFF&"


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(w: str) -> str:
    w = re.sub(r"[^\w\s!?áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ'-]", "", w)
    return w.upper()


# Palavras que não podem fechar um bloco de legenda ("vai DE", "pega O"...): regra de legendagem profissional
# (guia Netflix pt-BR) — o bloco segue até a palavra seguinte.
_NAO_FECHA = {"O", "A", "OS", "AS", "UM", "UMA", "DE", "DA", "DO", "DAS", "DOS", "EM", "NO", "NA", "NOS", "NAS",
              "PRA", "PRO", "PARA", "POR", "COM", "E", "QUE", "NÃO", "NAO", "SE", "MEU", "MINHA", "ESSE", "ESSA"}
# Termos de CS2 que ganham destaque maior quando aparecem na fala (supersize, como Captions/Submagic)
DESTAQUE = {"ACE", "CLUTCH", "HS", "HEADSHOT", "AWP", "DEAGLE", "USP", "AK", "M4", "FACA", "ZEUS", "BALA", "TRIPLE",
            "QUADRA", "DOUBLE", "CAPA", "VAMO", "VAMOS", "CARALHO", "NOSSA", "INSANO", "CLIPA"}


def chunk_words(words: list[Word], max_words: int = 3, max_chars: int = 14, max_gap: float = 0.35) -> list[list[Word]]:
    """Blocos de 1-3 palavras numa linha só (até `max_chars`, que cabe na zona segura de 768 px), quebrando nas
    pausas. Um bloco não termina em artigo/preposição se a próxima palavra vem colada."""
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        txt = _clean(w.text)
        if not txt:
            continue
        w = Word(w.start, w.end, txt)
        too_long = len(" ".join(x.text for x in cur + [w])) > max_chars
        gap = cur and w.start - cur[-1].end > max_gap
        preso = cur and cur[-1].text in _NAO_FECHA and not gap and len(" ".join(x.text for x in cur + [w])) <= max_chars + 6
        if cur and (len(cur) >= max_words or too_long or gap) and not preso:
            chunks.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[!?.]$", w.text):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(path: Path, words: list[Word], m: Moment, clip_start: float, clip_end: float,
              width: int, height: int, cam_h: int, captions: bool, badge: bool, marca: str = "", topo: int = 0,
              gancho: bool | None = None) -> None:
    """`cam_h`: onde termina a câmera (já somada a faixa do topo); `marca`: texto discreto na faixa do topo.
    `gancho`: mostra o nome da jogada no começo. None = só no 1º trecho de uma sequência (`render_por_janelas`
    marca cada trecho em `m.trecho`); nos seguintes ele se repetia e contradizia o selo do abate seguinte."""
    if gancho is None:
        gancho = getattr(m, "trecho", 0) == 0
    marca = (marca or "").strip()
    if not marca.lstrip("@").strip():   # só "@" ou espaços: sem faixa de texto
        marca = ""
    # legendas na emenda câmera/gameplay; selo acima da mira (fora da área da UI do app, embaixo)
    cap_y = cam_h + 110 if cam_h else int(height * 0.30)
    badge_y = cam_h + 330 if cam_h else int(height * 0.42)
    fs_cap = round(width * 0.092)      # Anton é estreita: maior que a Arial Black antiga, mesma largura
    fs_badge = round(width * 0.13)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,Anton,{fs_cap},{WHITE},{WHITE},&H00000000&,&H80000000&,0,0,0,0,100,100,1,0,1,7,3,5,40,40,0,1",
        f"Style: Badge,Anton,{fs_badge},{YELLOW},{WHITE},&H00000000&,&H90000000&,0,0,0,0,100,100,3,0,1,9,5,5,40,40,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    dur = clip_end - clip_start
    if marca and topo >= 60:
        lines.insert(lines.index("[Events]") - 1,
                     f"Style: Marca,Anton,{round(topo * 0.38)},&H00C8C8C8&,&H00C8C8C8&,&H00000000&,&H00000000&,0,0,0,0,"
                     "100,100,2,0,1,2,0,5,40,40,0,1")
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(dur)},Marca,,0,0,0,,{{\\an5\\pos({width // 2},{topo // 2})}}{marca}")
    if captions:
        for ch in chunk_words([w for w in words if clip_start <= w.start < clip_end]):
            c_end = min(ch[-1].end + 0.25, dur + clip_start)
            for i, w in enumerate(ch):
                a = w.start
                b = ch[i + 1].start if i + 1 < len(ch) else c_end
                parts = []
                for j, x in enumerate(ch):
                    if j == i:  # palavra falada: amarela + "pop" 100→115→100% (termo de CS: 130%)
                        pico = 130 if x.text.strip("!?.,") in DESTAQUE else 115
                        parts.append(f"{{\\c{YELLOW}\\t(0,60,\\fscx{pico}\\fscy{pico})\\t(60,140,\\fscx100\\fscy100)}}"
                                     f"{x.text}{{\\r}}")
                    else:
                        parts.append(x.text)
                pop = "\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)" if i == 0 else ""
                txt = f"{{\\an5\\pos({width // 2},{cap_y}){pop}}}" + " ".join(parts)
                lines.append(f"Dialogue: 1,{_ts(a - clip_start)},{_ts(b - clip_start)},Cap,,0,0,0,,{txt}")
    if badge and gancho and len(m.kills) >= 2:
        # gancho: o nome da jogada já no primeiro segundo e meio (a ação vem logo depois, ~2 s)
        anim = f"{{\\an5\\pos({width // 2},{badge_y})\\fscx60\\fscy60\\t(0,120,\\fscx100\\fscy100)\\fad(0,200)}}"
        primeiro = m.kills[0].t - clip_start
        lines.append(f"Dialogue: 2,{_ts(0)},{_ts(max(0.4, min(1.5, primeiro - 0.1)))},Badge,,0,0,0,,{anim}{m.label}")
    if badge and m.kills:
        for k, e in enumerate(m.kills, 1):
            if k == 1 and len(m.kills) > 1:
                continue  # o selo aparece a partir do 2º abate (DOUBLE, TRIPLE...)
            if not clip_start <= e.t < clip_end:
                continue  # abate de outro trecho: o selo dele não pode aparecer no começo deste
            label = LABELS[min(k, 5)] if k > 1 else ("HEADSHOT" if e.hs else "")
            if not label:
                continue
            a = e.t - clip_start + 0.05
            nxt = m.kills[k].t - clip_start if k < len(m.kills) else a + 1.6
            b = min(a + 1.6, nxt, dur)
            anim = ("\\fscx40\\fscy40\\t(0,110,\\fscx118\\fscy118)\\t(110,210,\\fscx100\\fscy100)"
                    "\\fad(0,220)")
            lines.append(f"Dialogue: 2,{_ts(a)},{_ts(b)},Badge,,0,0,0,,{{\\an5\\pos({width // 2},{badge_y}){anim}}}{label}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
