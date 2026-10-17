# Regras de corte das jogadas (decididas em 17/10/2026)

O usuário revisou os 29 trechos do VOD `cs2 twitch.mp4` e apontou o problema: um round com 3 a 4 abates de USP
virou "1 kill e acabou" num vídeo e "mais 2 kills" em outro. Ele quer a jogada inteira num vídeo só. Antes de
mudar, pesquisamos como o próprio CS2, os programas do mercado e os geradores abertos fazem.

## O que a pesquisa mostrou

| Quem | Regra | Fonte |
|---|---|---|
| **CS2 (modo destaques da demo)** | mostra só as kills e mortes do jogador: `demo_highlight_seconds_before` = **6 s**, `demo_highlight_seconds_after` = **2 s**, fade de **0,25 s**; pula o resto | [Memorin](https://memorin.app/blog/cs2-highlights-and-clips-from-demos.html) |
| **cs2-highlights-maker** (aberto) | kills do jogador **no mesmo round** com vão **< 20 s** viram um "momento"; `lead_in` 4 s, `lead_out` 3 s; pontua 3K/4K/ACE, HS, faca/zeus, noscope, fumaça/cego/parede, clutch ganho, round importante | [GitHub](https://github.com/Rovniy/cs2-highlights-maker) |
| **Fragfinder** (comunidade) | multikill "**rápido**" = todas as kills em 6 s; "**espalhado**" = 15 s ou mais entre duas kills | [GitHub](https://github.com/Arad119/Fragfinder-GUI) |
| **Outplayed** | junta 4 kills em 30 s num clipe só (relato de usuário) | [Medal feedback](https://feedback.medal.tv/2112) |
| **Medal** | um clipe por kill; usuários reclamam e desligam | [Medal feedback](https://feedback.medal.tv/2112) |
| **Eklipse** | mantém **o round de ACE inteiro**, inclusive a primeira kill; mantém a voz e a reação; "o espectador quer o momento, não a caminhada até ele" | [Eklipse](https://eklipse.gg/use-case/counter-strike-2-highlights/), [blog](https://blog.eklipse.gg/beginner-guide-2/how-to-make-gaming-montage.html) |
| **CS2.VIDEO** | "round completo em menos de 1 min" (POV limpo) ou edição com música e gancho nos 2 primeiros s | [CS2.VIDEO](https://cs2.video/) |
| **CS Demo Manager** | sequências por kill com 2 s antes / 2 s depois (CLI); não agrupa por round | [docs](https://cs-demo-manager.com/docs/cli) |
| Guias de Shorts/TikTok | ação nos 3 primeiros s; 15–30 s para retenção, 30–60 s com contexto; clipe de streamer: 1–2 s de contexto e **deixar a reação terminar** | [Clypse](https://clypse.ai/blog/best-tiktok-gaming-clip-length-2026), [Eklipse](https://eklipse.gg/help/what-are-the-best-practices-for-editing-stream-highlights/) |

Todas são fontes de fabricantes ou projetos da comunidade; nenhuma traz medição de audiência.

## O que o usuário decidiu (perguntas objetivas, 17/10)

| Pergunta | Resposta |
|---|---|
| Mínimo de abates no round para unir | **2 ou mais** |
| Tempo parado entre kills | **cortar com fade de 0,25 s**; pausas de até 6 s ficam |
| Fim do vídeo quando o round é ganho | **logo depois da última kill** e da reação (não segue até a vitória) |
| Short de jogada > 60 s | **versão compacta** (2 s antes / 1,5 s depois de cada kill) |

## Como ficou no programa

- `cs2clip/sequencia.py`: todos os abates do mesmo round (com vão de até `cortes.juntar_no_round` = 120 s) viram **um** `Moment`. Cada abate mantém o trecho que já tinha (4 s antes, 2,5 s depois, + a reação do streamer, + o ajuste às falas). Trechos a até `cortes.pular_parado` = 6 s um do outro ficam contínuos; o resto é cortado e os trechos ficam em `Moment.segmentos`.
- `cs2clip/render.py::render_por_janelas`: renderiza cada trecho do jeito normal (cada um com seu fade de 0,12 s nas pontas, que somados dão ~0,25 s na emenda) e junta com `concat`.
- Nota: a da melhor jogada do grupo + diferença da base pelo total de abates + 8 por "jogada do round inteira".
- Short: `sequencia.compactar` (só `shorts.antes`/`shorts.depois` em volta de cada abate; se ainda passar de `shorts.duracao_maxima` = 60 s, fica o final).
- Demo (`cs2clip/demo.py`): mesma regra; como a demo sabe a morte, o último trecho não passa de 1 s depois dela.
- `momentos.json` ganhou o campo `segmentos` (lista de `[inicio, fim]`; vazia quando é um trecho só). O EDL tem um evento por trecho.

## Limites
- No vídeo não detectamos a própria morte: o vídeo termina depois da última kill + reação. Com a demo da mesma partida (caminho A da E6) fica exato.
- O round de um momento vem do banner de fim de round; se o banner foi perdido na leitura, os abates daquele round não são unidos (ficam separados, como antes).
