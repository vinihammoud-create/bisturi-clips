"""Relatório HTML local: todas as jogadas com miniatura, nota, motivos e player."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .moments import Moment


def _fmt(t: float) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


PAGE = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Highlights CS2</title>
<style>
:root{--bg:#0e1013;--card:#171a1f;--line:#262a31;--txt:#e9edf2;--mut:#9aa3ad;--acc:#ffd23f;--kill:#ff4d4d;--reac:#4dabff;--win:#3ddc84;--lose:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.45 system-ui,Segoe UI,sans-serif}
header{padding:28px 24px 8px;max-width:1280px;margin:auto}h1{margin:0 0 4px;font-size:26px}
.sub{color:var(--mut)}.stats{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.stat b{display:block;font-size:22px}.stat span{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 0}.bar button{background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:999px;padding:6px 12px;cursor:pointer}
.bar button.on{border-color:var(--acc);color:var(--acc)}
main{max-width:1280px;margin:auto;padding:12px 24px 48px;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.c{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.th{position:relative;aspect-ratio:16/9;background:#000;cursor:pointer}.th img,.th video{width:100%;height:100%;object-fit:cover;display:block}
.tag{position:absolute;left:10px;top:10px;background:var(--kill);color:#fff;font-weight:800;font-size:12px;padding:3px 8px;border-radius:6px;letter-spacing:.03em}
.tag.reacao{background:var(--reac)}.sc{position:absolute;right:10px;top:10px;background:rgba(0,0,0,.7);color:var(--acc);font-weight:800;padding:3px 8px;border-radius:6px}
.ctx{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;letter-spacing:.02em}
.ctx.round_ganho{background:rgba(61,220,132,.15);color:var(--win);border:1px solid var(--win)}
.ctx.round_perdido{background:rgba(255,107,107,.15);color:var(--lose);border:1px solid var(--lose)}
.emo{display:inline-block;font-size:11px;font-weight:800;padding:2px 8px;border-radius:999px;letter-spacing:.04em;margin-left:6px}
.emo.RAIVA{background:rgba(255,107,107,.2);color:var(--lose)}.emo.HYPE{background:rgba(255,210,63,.18);color:var(--acc)}.emo.RISADA{background:rgba(77,171,255,.18);color:var(--reac)}
.emo.media{opacity:.7}.pt{font-size:12px;color:var(--mut)}
.bd{padding:12px 14px;display:flex;flex-direction:column;gap:6px;flex:1}.t{color:var(--mut);font-size:13px}
.r{margin:0;padding-left:18px;color:var(--mut);font-size:13px}.lk{display:flex;gap:10px;margin-top:auto;padding-top:6px}
.lk a{color:var(--acc);text-decoration:none;font-size:13px}.lk a:hover{text-decoration:underline}
.vid{font-size:12px;color:var(--mut);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media (max-width:520px){header,main{padding-left:16px;padding-right:16px}}
</style></head><body>
<header><h1>Highlights CS2</h1><div class="sub">__SUB__</div>
<div class="stats">__STATS__</div>
<div class="bar"><button data-f="all" class="on">Todas</button><button data-f="abate">Jogadas</button><button data-f="reacao">Reações</button><button data-f="short">Com short</button><button data-s="score">Ordenar por nota</button><button data-s="time">Ordem do vídeo</button></div>
</header><main id="g"></main>
<script>
const D=__DATA__;let F='all',S='score';
function card(m){const e=document.createElement('div');e.className='c';
e.innerHTML=`<div class="th"><img loading="lazy" src="${m.thumb}"><div class="tag ${m.kind}">${m.label}</div><div class="sc">${m.score}</div></div>
<div class="bd"><div class="vid">${m.video}</div><div class="t">${m.time} · ${m.dur}s · ${m.detail}</div>
${m.contexto=='round_ganho'?'<span class="ctx round_ganho">🟢 round ganho</span>':m.contexto=='round_perdido'?'<span class="ctx round_perdido">🔴 round perdido</span>':''}${m.emocao?`<span class="emo ${m.emocao} ${m.confianca||''}" title="${(m.emocao_motivos||[]).join(' · ')}">${m.emocao}${m.confianca=='media'?'?':''}</span>`:''}
${m.parte?`<div class="pt">${m.parte}${m.round_n?` · round ${m.round_n}`:''}</div>`:''}
<ul class="r">${m.reasons.map(r=>`<li>${r}</li>`).join('')}</ul>
<div class="lk">${m.clip?`<a href="${m.clip}">▶ Corte 16:9</a>`:''}${m.short?`<a href="${m.short}">▶ Short 9:16</a>`:''}</div></div>`;
const th=e.querySelector('.th');th.onclick=()=>{if(!m.clip)return;th.innerHTML=`<video src="${m.clip}" controls autoplay></video>`};return e}
function draw(){const g=document.getElementById('g');g.innerHTML='';let L=D.filter(m=>F=='all'||(F=='short'?m.short:m.kind==F));
L.sort((a,b)=>S=='score'?b.score-a.score:(a.video+a.start).localeCompare(b.video+b.start,undefined,{numeric:true}));L.forEach(m=>g.appendChild(card(m)))}
document.querySelectorAll('.bar button').forEach(b=>b.onclick=()=>{if(b.dataset.f){F=b.dataset.f;document.querySelectorAll('[data-f]').forEach(x=>x.classList.toggle('on',x==b))}
if(b.dataset.s){S=b.dataset.s;document.querySelectorAll('[data-s]').forEach(x=>x.classList.toggle('on',x==b))}draw()});
document.querySelector('[data-s=score]').classList.add('on');draw();
</script></body></html>"""


def write_report(out_html: Path, entries: list[dict], stats: dict[str, str], subtitle: str) -> None:
    st = "".join(f'<div class="stat"><b>{html.escape(v)}</b><span>{html.escape(k)}</span></div>'
                 for k, v in stats.items())
    page = (PAGE.replace("__SUB__", html.escape(subtitle)).replace("__STATS__", st)
            .replace("__DATA__", json.dumps(entries, ensure_ascii=False)))
    out_html.write_text(page, encoding="utf-8")


def entry(m: Moment, video: str, rel_thumb: str, rel_clip: str | None, rel_short: str | None,
          contexto: str | None = None) -> dict:
    if contexto is None and m.round_resultado:
        contexto = "round_ganho" if m.round_resultado == "vitoria" else "round_perdido"
    hs = sum(e.hs for e in m.kills)
    detail = (f"{len(m.kills)} abate(s), {hs} HS" + (f", {len(m.assists)} assist." if m.assists else "")
              if m.kills else "sem abate")
    return {
        "video": video, "start": f"{m.start:09.2f}", "time": f"{_fmt(m.start)}–{_fmt(m.end)}",
        "dur": f"{m.duration:.0f}", "label": m.label, "kind": m.kind, "score": round(m.score),
        "reasons": [html.escape(r) for r in m.reasons + ([f"falas: {', '.join(m.hype_words)}"] if m.hype_words else [])],
        "detail": detail, "thumb": rel_thumb, "clip": rel_clip, "short": rel_short,
        "emocao": m.emocao, "confianca": m.confianca, "emocao_motivos": [html.escape(x) for x in m.emocao_motivos],
        "parte": html.escape(m.parte), "round_n": m.round_n,
        "contexto": contexto,  # "round_ganho" | "round_perdido" | None (cs2clip/emotion.py) — ver docs/STATUS.md
    }
