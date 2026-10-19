"""Página de revisão (`revisao.html`): o usuário assiste cada corte e responde, com cliques, o que
aconteceu de verdade. Isso gera o GABARITO que calibra a emoção e mede a precisão dos abates
(docs/engineering/GABARITO.md) sem ele ter que garimpar o VOD à mão.

Para cada momento a página mostra o corte, o que o programa achou (jogada, round, emoção e por quê) e
pergunta: (1) o abate que o programa disse está no corte? (2) que emoção você teve? As respostas ficam
salvas no navegador e o botão "Exportar CSV" baixa `gabarito_momentos.csv` no formato do gabarito.
Tudo local, sem servidor: abre direto do arquivo (`saida\\<id>\\revisao.html`)."""
from __future__ import annotations

import json
from pathlib import Path

from .moments import Moment


def _mmss(t: float) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def itens_revisao(moments: list[Moment], arquivos: dict[int, dict]) -> list[dict]:
    """Só os momentos que têm corte renderizado (o usuário precisa poder assistir)."""
    out = []
    for m in moments:
        arq = arquivos.get(m.idx) or {}
        if not arq.get("corte"):
            continue
        out.append({
            "n": m.idx, "tempo": _mmss(m.focus), "foco": round(m.focus, 1), "corte": arq["corte"],
            "miniatura": arq.get("miniatura"), "rotulo": m.label, "tipo": m.kind, "nota": round(m.score),
            "abates": len(m.kills), "parte": m.parte, "round": m.round_n, "round_resultado": m.round_resultado,
            "emocao": m.emocao, "confianca": m.confianca or None,
            "motivos": list(m.emocao_motivos) + [r for r in m.reasons if "empolgação" in r],
        })
    return out


PAGE = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Revisão de momentos</title>
<style>
:root{--bg:#0b0d10;--surface:#15181d;--surface-2:#1c2028;--line:#262b33;--txt:#eef1f5;--mut:#8b94a1;--acc:#ffb224;
--roxo:#9b6bff;--ok:#35d08a;--ruim:#ff5f6b;--azul:#4da3ff;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;padding-inline:20px;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,Segoe UI,sans-serif}
header{max-width:1100px;margin:0 auto;padding-block:24px 8px}h1{margin:0 0 6px;font-size:26px}
.sub{color:var(--mut);max-width:70ch}.barra{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:16px}
.prog{flex:1;min-width:220px;height:10px;background:var(--surface-2);border-radius:99px;overflow:hidden}
.prog>i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--roxo),var(--acc));transition:width .2s}
button{font:600 13px/1 inherit;color:var(--txt);background:var(--surface-2);border:1px solid var(--line);border-radius:999px;padding:9px 14px;cursor:pointer}
button:hover{border-color:var(--acc)}button.on{border-color:var(--acc);color:var(--acc);background:rgba(255,178,36,.1)}
button.sim.on{border-color:var(--ok);color:var(--ok);background:rgba(53,208,138,.1)}button.nao.on{border-color:var(--ruim);color:var(--ruim);background:rgba(255,95,107,.1)}
.exp{background:linear-gradient(90deg,var(--roxo),#7b4dff);border:0}
main{max-width:1100px;margin:0 auto;padding-block:12px 60px;display:flex;flex-direction:column;gap:18px}
.card{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:18px;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px}
.card.feito{border-color:rgba(53,208,138,.45)}video{width:100%;aspect-ratio:16/9;background:#000;border-radius:10px}
.titulo{font-weight:800;font-size:17px}.meta{color:var(--mut);font-size:13px;margin:2px 0 8px}
.pill{display:inline-block;font-size:11px;font-weight:800;letter-spacing:.04em;padding:2px 9px;border-radius:99px;margin-right:6px;background:var(--surface-2)}
.pill.RAIVA{color:var(--ruim)}.pill.HYPE{color:var(--acc)}.pill.RISADA{color:var(--azul)}.pill.ganho{color:var(--ok)}.pill.perdido{color:var(--ruim)}
.pq{margin:12px 0 6px;font-weight:700;font-size:14px}.linha{display:flex;gap:8px;flex-wrap:wrap}
.motivos{color:var(--mut);font-size:12.5px;margin:6px 0 0;padding-left:16px}
textarea{width:100%;margin-top:8px;background:var(--surface-2);color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px;font:13px system-ui}
@media (max-width:760px){.card{grid-template-columns:1fr}}
</style>
<header>
<h1>Revisão de momentos</h1>
<div class="sub">Assista cada trecho e responda duas perguntas. Isso diz ao programa onde ele acerta e onde erra, e calibra a emoção. Suas respostas ficam salvas neste navegador; no fim, clique em <b>Exportar CSV</b>.</div>
<div class="barra"><div class="prog"><i id="pb"></i></div><span id="pt"></span>
<button class="exp" id="exp">Exportar CSV</button><button id="lim" title="apaga todas as respostas deste vídeo">Limpar respostas</button></div>
</header>
<main id="lista"></main>
<script>
const VIDEO=__VIDEO__, D=__DATA__, CHAVE='cs2rev:'+VIDEO;
const EMO=[['raiva','😡 Raiva'],['alegria','😄 Alegria / hype'],['risada','😂 Risada'],['nenhuma','😐 Nada de especial']];
let R={};try{R=JSON.parse(localStorage.getItem(CHAVE)||'{}')}catch(e){R={}}
D.forEach(m=>m.k=String(m.foco));   // a resposta fica presa ao instante da jogada, não ao número (que muda)
function salvar(){try{localStorage.setItem(CHAVE,JSON.stringify(R))}catch(e){}atualizar()}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function feito(k){const r=R[k];return !!(r&&r.abate&&r.emocao&&r.emocao.length)}
function atualizar(){const f=D.filter(m=>feito(m.k)).length;document.getElementById('pb').style.width=(100*f/Math.max(1,D.length))+'%';
document.getElementById('pt').textContent=f+' de '+D.length+' revisados';document.querySelectorAll('.card').forEach(c=>c.classList.toggle('feito',feito(c.dataset.k)))}
function card(m){const e=document.createElement('section');e.className='card';e.dataset.k=m.k;
const pills=(m.emocao?`<span class="pill ${m.emocao}">${m.emocao}${m.confianca=='media'?'?':''}</span>`:'')+(m.round_resultado=='vitoria'?'<span class="pill ganho">round ganho</span>':m.round_resultado=='derrota'?'<span class="pill perdido">round perdido</span>':'');
e.innerHTML=`<div><video controls preload="none" ${m.miniatura?`poster="${esc(m.miniatura)}"`:''} src="${esc(m.corte)}"></video></div>
<div><div class="titulo">${esc(m.rotulo)} <span style="color:var(--mut);font-weight:500">· ${esc(m.tempo)} no vídeo</span></div>
<div class="meta">${esc(m.parte||'')}${m.round?' · round '+m.round:''} · nota ${m.nota} · ${m.abates} abate(s)</div><div>${pills}</div>
${m.motivos.length?`<ul class="motivos">${m.motivos.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}
<div class="pq">1. O abate que o programa achou está nesse corte?</div><div class="linha" data-q="abate">
<button class="sim" data-v="sim">✅ Sim</button><button class="nao" data-v="nao">❌ Não</button><button data-v="sem">Sem abate (só reação)</button></div>
<div class="pq">2. Que emoção você sentiu / ouve aí? (pode marcar mais de uma)</div><div class="linha" data-q="emocao">${EMO.map(([k,t])=>`<button data-v="${k}">${t}</button>`).join('')}</div>
<div class="pq">3. Você postaria esse corte?</div><div class="linha" data-q="voto">
<button class="sim" data-v="sim">👍 Postaria</button><button class="nao" data-v="nao">👎 Não</button></div>
<textarea rows="1" placeholder="observação (opcional)">${esc((R[m.k]||{}).obs||'')}</textarea></div>`;
const r=R[m.k]||{};
e.querySelectorAll('[data-q=abate] button').forEach(b=>{b.classList.toggle('on',r.abate==b.dataset.v);b.onclick=()=>{R[m.k]=Object.assign(R[m.k]||{},{abate:b.dataset.v});
e.querySelectorAll('[data-q=abate] button').forEach(x=>x.classList.toggle('on',x==b));salvar()}});
e.querySelectorAll('[data-q=emocao] button').forEach(b=>{b.classList.toggle('on',(r.emocao||[]).includes(b.dataset.v));b.onclick=()=>{const x=R[m.k]=R[m.k]||{};let l=x.emocao||[];
if(b.dataset.v=='nenhuma')l=l.includes('nenhuma')?[]:['nenhuma'];else{l=l.filter(v=>v!='nenhuma');l=l.includes(b.dataset.v)?l.filter(v=>v!=b.dataset.v):l.concat(b.dataset.v)}
x.emocao=l;e.querySelectorAll('[data-q=emocao] button').forEach(y=>y.classList.toggle('on',l.includes(y.dataset.v)));salvar()}});
e.querySelectorAll('[data-q=voto] button').forEach(b=>{b.classList.toggle('on',r.voto==b.dataset.v);b.onclick=()=>{R[m.k]=Object.assign(R[m.k]||{},{voto:b.dataset.v});
e.querySelectorAll('[data-q=voto] button').forEach(x=>x.classList.toggle('on',x==b));salvar()}});
e.querySelector('textarea').oninput=ev=>{R[m.k]=Object.assign(R[m.k]||{},{obs:ev.target.value});try{localStorage.setItem(CHAVE,JSON.stringify(R))}catch(e){}};
return e}
const L=document.getElementById('lista');D.forEach(m=>L.appendChild(card(m)));atualizar();
function csv(c){c=String(c).replace(/"/g,'""');return /[",\\n]/.test(c)?'"'+c+'"':c}
document.getElementById('exp').onclick=()=>{
const cab=['vod_id','tempo','categoria','observacao','abate_certo','programa_rotulo','programa_emocao','programa_confianca','round_resultado','voto','programa_nota'];
const lin=D.filter(m=>{const r=R[m.k];return r&&((r.emocao&&r.emocao.length)||r.voto||r.abate)}).map(m=>{const r=R[m.k];
return [VIDEO,m.tempo,(r.emocao||[]).join('+'),r.obs||'',r.abate||'',m.rotulo,m.emocao||'',m.confianca||'',m.round_resultado||'',r.voto||'',m.nota].map(csv).join(',')});
const blob=new Blob(['\\ufeff'+[cab.join(',')].concat(lin).join('\\n')+'\\n'],{type:'text/csv;charset=utf-8'});
const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='gabarito_momentos.csv';document.body.appendChild(a);a.click();a.remove()};
document.getElementById('lim').onclick=()=>{if(confirm('Apagar todas as respostas deste vídeo?')){R={};try{localStorage.removeItem(CHAVE)}catch(e){}location.reload()}};
</script>
"""


def gerar_revisao(out: Path, video: str, itens: list[dict]) -> Path:
    """Escreve `out/revisao.html` com os momentos embutidos (caminhos dos cortes relativos a `out`)."""
    pagina = (PAGE.replace("__VIDEO__", json.dumps(video, ensure_ascii=False))
              .replace("__DATA__", json.dumps(itens, ensure_ascii=False).replace("</", "<\\/")))
    f = out / "revisao.html"
    out.mkdir(parents=True, exist_ok=True)
    f.write_text(pagina, encoding="utf-8")
    return f
