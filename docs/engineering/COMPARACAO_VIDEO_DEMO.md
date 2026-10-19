# Comparação vídeo x demo (18/10/2026)

Material: 1 vídeo de live de 1h13 (1080p60) com **duas partidas seguidas** e as duas demos `.dem` dessas partidas
(de_cache 10x13, de_dust2 6x13). A demo é a verdade; o vídeo é o que o programa consegue ler da tela.
A pergunta: o que o programa lê certo, o que erra e o que dá para aprender com isso.

## Resultado

| | Vídeo sozinho (antes) | Vídeo sozinho (depois das correções) | Demo (verdade) |
|---|---|---|---|
| Abates | 39 | 39 | 22 + 17 = 39 |
| Abates que casam 1 a 1 com a demo | 38 de 39 (a 2 s) | 39 de 39 (a 4 s) | |
| Mortes | 27 | 27 | 15 + 14 = 29 |
| Rounds lidos | 35 de 42 | **42 de 42** | 23 + 19 = 42 |
| Placar da partida | 4x11 e 5x13 | **10x13 e 6x13** | 10x13 e 6x13 |
| Resultado de round errado | 0 | 0 | |

Com a demo junto (`--jogador "nome"`), o programa passa a usar os fatos exatos: ícones certos em cada abate
(headshot, parede, noscope, fumaça, cego), instante exato do abate e da morte, os 42 rounds com número e vencedor,
início e fim de cada partida, e clutch 1vX. Nos dois mapas a demo mostra 8 situações de clutch (1v2 ou mais) que o vídeo sozinho não sabe dizer.

## O que a comparação ensinou

1. **A VITÓRIA do lado CT não era lida.** O pré-filtro do leitor de banner só aceitava vermelho ou dourado; o
   painel da vitória do CT é **azul**. Os 7 rounds perdidos eram todos vitórias de CT (6 em cache, 1 em dust2).
   Isso já valia para qualquer live: a partida aparecia com placar errado e rounds sem número. Corrigido com a
   medida `_azul_banner` (limiar 0,01): lê 20 de 20 quadros de banner azul e deixa passar ~8% dos quadros comuns
   (um OCR de ~50 ms a mais por quadro: uns 10 s numa live de 73 min). Cache do detector subiu para v7, então
   quem já analisou uma live refaz sozinho.
2. **O deslocamento demo→vídeo é fixo na partida inteira.** cache +193,2 s e dust2 +2656,7 s, com resíduo mediano
   de 0,05 s em 38 min: a sincronização por abates é exata. Dá para casar sem relógio nem marcador.
3. **Abate com o atirador cego é lido atrasado** (+2,4 s no único caso): a tela branca do flash esconde o kill
   feed. Com a demo o instante vem dela; sozinho, é um limite conhecido (o corte ainda pega o abate: 4 s antes).
4. **O banner aparece 1,0 a 3,0 s depois do fim do round** (mediana 1,4 a 1,6 s). A linha do tempo de rounds
   vinda da demo usa esse atraso medido no próprio vídeo.
5. **O painel "matou você" atrasa 0,9 a 3,4 s** (mediana ~1,4 s) em relação à morte, e **não aparece** em 2 casos:
   morte junto com o fim do round (o banner de DERROTA cobre) e morte no último round da partida (entra o
   placar final). São as 2 mortes que faltam; só a demo cobre.
6. **Duas partidas coladas** (a 2ª começa ~220 s depois da 1ª) ficam a poucos segundos do limite que separa
   partidas pelos banners (240 s). Com a demo os limites são exatos e não depende disso.

## Como reproduzir

```
.venv\Scripts\python.exe -m cs2clip "video.mp4" --jogador "seu nome no jogo"
```
Com `--jogador` (ou `[demo] jogador` no `config.toml`), as `.dem` que estiverem na mesma pasta do vídeo são lidas e
cruzadas sozinhas; também dá para listar as demos na linha de comando. Demo que não é do vídeo (poucos abates em
comum) é ignorada, e o motivo fica em `saida/<video>/sincronia.json`.

## Limites

- Só vale para vídeo corrido (sem corte nem pausa editados): o deslocamento é um só por partida.
- A demo precisa ser da mesma partida e do mesmo jogador; sem o nome do jogador a demo não é usada.
- Reação (raiva, hype, risada) continua vindo só do vídeo.
