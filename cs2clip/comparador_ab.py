"""Página de comparação CEGA A/B (C6, Plano V7 integrado): para cada jogada, o corte normal contra cada versão com um
efeito, na MESMA jogada, com uma variável de cada vez. A ordem é sorteada (de forma fixa por jogada/efeito, para a
página não mudar a cada abertura) e a tela não diz qual é o efeito: você só vota. O voto fica no navegador
(localStorage) e o botão "Exportar votos" baixa um CSV com a resposta REAL (qual era o efeito), para guardar.

Arquivos esperados em cada pasta de jogada (os mesmos que `tools/comparar_efeitos.py` gera): A_normal.mp4 e as
variantes. Só entram na página os pares em que as duas versões existem."""
from __future__ import annotations

import hashlib
import html
from pathlib import Path

from .revisao import json_para_script

# arquivo da variante -> (nome da opção na tela Configurar, pergunta)
VARIANTES = {
    "B_acelerar_entre_abates.mp4": "Acelerar o caminho entre abates",
    "C_congelar_headshot.mp4": "Congelar o headshot decisivo",
    "D_cartao_da_jogada.mp4": "Cartão da jogada no início",
}
PARES_SHORT = {"E_short_normal.mp4": ("F_short_camera_balanca.mp4", "Câmera balança no abate")}


def _sorteio(chave: str) -> bool:
    """True = a versão normal aparece como 1. Fixo para a mesma jogada e o mesmo efeito."""
    return hashlib.sha1(chave.encode("utf-8")).digest()[0] % 2 == 0


def pares_da_pasta(pasta: Path) -> list[dict]:
    """Os pares (normal, com efeito) que existem nesta pasta de jogada, já com a ordem cega decidida."""
    pares = []
    candidatos = [("A_normal.mp4", arq, nome) for arq, nome in VARIANTES.items()]
    candidatos += [(normal, outro, nome) for normal, (outro, nome) in PARES_SHORT.items()]
    for normal, efeito, nome in candidatos:
        if not ((pasta / normal).is_file() and (pasta / efeito).is_file()):
            continue
        normal_primeiro = _sorteio(f"{pasta.name}|{efeito}")
        um, dois = (normal, efeito) if normal_primeiro else (efeito, normal)
        pares.append({"id": f"{pasta.name}|{efeito}", "jogada": pasta.name, "efeito": nome, "um": um, "dois": dois,
                      "normal_e": 1 if normal_primeiro else 2})
    return pares


def gerar_pagina(raiz: Path, pastas: list[Path]) -> Path | None:
    """Escreve `comparar.html` em `raiz`. Devolve o caminho (ou None se não houver nenhum par)."""
    todos = [p for pasta in pastas for p in pares_da_pasta(pasta)]
    if not todos:
        return None
    cartoes = []
    for i, p in enumerate(todos):
        base = html.escape(p["jogada"]) + "/"
        cartoes.append(
            f'<section class="par" data-id="{html.escape(p["id"])}"><h2>Jogada: {html.escape(p["jogada"])} · comparação {i + 1}</h2>'
            f'<div class="videos"><figure><figcaption>Versão 1</figcaption><video src="{base}{p["um"]}" controls preload="metadata"></video></figure>'
            f'<figure><figcaption>Versão 2</figcaption><video src="{base}{p["dois"]}" controls preload="metadata"></video></figure></div>'
            f'<div class="votos"><button data-v="1">Eu publicaria a 1</button><button data-v="2">Eu publicaria a 2</button>'
            f'<button data-v="0">Tanto faz</button><span class="escolha"></span></div></section>')
    dados = json_para_script({p["id"]: {"jogada": p["jogada"], "efeito": p["efeito"], "normal_e": p["normal_e"]} for p in todos})
    pagina = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Comparação cega A/B</title><style>
:root{{--fundo:#0b1220;--txt:#e7eefc;--mut:#9aa7b8;--cor:#ff7a1a}}
body{{background:var(--fundo);color:var(--txt);font-family:system-ui,Segoe UI,sans-serif;margin:0;padding:16px;max-width:1200px;margin-inline:auto}}
h1{{font-size:20px}} h2{{font-size:15px;color:var(--mut)}} .par{{border:1px solid #223;border-radius:10px;padding:12px;margin:14px 0}}
.videos{{display:grid;grid-template-columns:1fr 1fr;gap:10px}} video{{width:100%;background:#000;border-radius:8px}}
figcaption{{font-weight:700;margin-bottom:4px}} .votos{{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
button{{background:#16233a;color:var(--txt);border:1px solid #2c3d5c;border-radius:8px;padding:8px 12px;cursor:pointer}}
button.on{{background:var(--cor);color:#1d0f03;border-color:var(--cor)}} .escolha{{color:var(--mut)}}
@media (max-width:700px){{.videos{{grid-template-columns:1fr}}}}
</style></head><body>
<h1>Comparação cega A/B</h1>
<p>Assista às duas versões de cada jogada e vote em qual você <b>publicaria</b>. Você não sabe qual tem o efeito: é de propósito.
Seus votos ficam salvos neste navegador. No fim, clique em <b>Exportar votos</b>.</p>
<button id="exportar">Exportar votos (CSV)</button>
{''.join(cartoes)}
<script>
const INFO = {dados};
// chave própria desta página: votos de outro vídeo/rodada não se misturam com estes (e, se sobrarem ids antigos, são ignorados)
const chave = 'bisturi_ab_votos:' + Object.keys(INFO).sort().join('|');
let votos = {{}};
try {{ votos = JSON.parse(localStorage.getItem(chave) || '{{}}'); }} catch (e) {{ votos = {{}}; }}
Object.keys(votos).forEach(id => {{ if (!Object.prototype.hasOwnProperty.call(INFO, id)) delete votos[id]; }});
function pintar(sec) {{
  const id = sec.dataset.id, v = votos[id];
  sec.querySelectorAll('button').forEach(b => b.classList.toggle('on', v !== undefined && String(v) === b.dataset.v));
  sec.querySelector('.escolha').textContent = v === undefined ? '' : (v === 0 ? 'Você marcou: tanto faz' : 'Você marcou: versão ' + v);
}}
document.querySelectorAll('.par').forEach(sec => {{
  pintar(sec);
  sec.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {{
    votos[sec.dataset.id] = Number(b.dataset.v);
    try {{ localStorage.setItem(chave, JSON.stringify(votos)); }} catch (e) {{}}
    pintar(sec);
  }}));
}});
document.getElementById('exportar').addEventListener('click', () => {{
  const linhas = ['jogada,efeito,voto,resposta'];
  Object.keys(votos).filter(id => Object.prototype.hasOwnProperty.call(INFO, id)).forEach(id => {{
    const i = INFO[id], v = votos[id];
    const resp = v === 0 ? 'tanto_faz' : (v === i.normal_e ? 'normal' : 'com_efeito');
    linhas.push([i.jogada, i.efeito, v, resp].map(x => '"' + String(x).replace(/"/g, '""') + '"').join(','));
  }});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([linhas.join('\\n')], {{type: 'text/csv'}}));
  a.download = 'votos_ab.csv'; document.body.appendChild(a); a.click(); a.remove();
}});
</script></body></html>"""
    destino = Path(raiz) / "comparar.html"
    destino.write_text(pagina, encoding="utf-8")
    return destino
