# De onde vêm as músicas (e o que cada licença exige)

Leitura das páginas oficiais em **09/11/2026**. Antes de confiar em outra fonte, leia a página dela: blogs de terceiros divergem (um deles dizia que o
StreamBeats não vale para YouTube monetizado; o FAQ oficial diz o contrário, e o catálogo segue o oficial).

O Bisturi **só usa músicas do catálogo (`musicas/catalogo.json`) que tenham licença escrita e não estejam desligadas** (tela **Músicas**). Arquivo solto na pasta
nunca é sorteado. Os detalhes de cada faixa (autor, licença, link, aviso, crédito) ficam no catálogo.

| Fonte | Combina com CS2? | O que a página oficial diz | O que o Bisturi faz |
|---|---|---|---|
| **CC0** (domínio público) — [creativecommons.org/publicdomain/zero/1.0](https://creativecommons.org/publicdomain/zero/1.0/) | depende da faixa | Pode copiar, modificar e usar, inclusive comercial, **sem pedir permissão nem dar crédito**. Não afeta direitos de terceiros (marca, imagem, privacidade). | sem crédito |
| **NCS – NoCopyrightSounds** — [ncs.io/usage-policy](https://ncs.io/usage-policy) | **muito** (EDM, trap, phonk) | Criador independente **pode monetizar**, copiando a **seção de crédito da descrição do vídeo da faixa** no YouTube da NCS (sem seção: link para o vídeo). Em live (Twitch ou YouTube): "Music provided by http://spoti.fi/NCS" na descrição da live. Para jogo ou marca: autorização da NCS. Diz que quem segue a política não recebe reclamação nem strike. | **crédito obrigatório** pronto no `publicar.txt` (cole o texto oficial ao adicionar a faixa) |
| **Pixabay** — [pixabay.com/service/license-summary](https://pixabay.com/service/license-summary/) | bom ("gaming/action") | Uso grátis, sem dar crédito, pode modificar. **Não** pode vender/distribuir a faixa sozinha; não usar de forma enganosa. O resumo não fala de Content ID: só diz que "é sua responsabilidade checar se precisa de licença de terceiros". | sem crédito, com **aviso de Content ID** (algumas faixas recebem reclamação automática: guarde o certificado da página da faixa para contestar) |
| **Biblioteca de Áudio do YouTube** — [support.google.com/youtube/answer/3376882](https://support.google.com/youtube/answer/3376882) | médio | "Copyright-safe": liberada para **monetizar** (membros do Programa de Parcerias) e **não é reclamada pelo Content ID**. Faixas com licença Creative Commons pedem **crédito na descrição** (o Studio gera o texto). A página **não cobre** uso fora do YouTube (Twitch). | o dono diz se a faixa pede crédito; vale só para vídeos do YouTube |
| **StreamBeats (Harris Heller)** — [streambeats.com/faq](https://www.streambeats.com/faq) | bom para live | "Grátis em streams, vídeos e podcasts para sempre"; **"pode monetizar seus vídeos livremente"** no YouTube; crédito "apreciado, nunca obrigatório". A empresa trabalha com Content ID e Audible Magic, mas diz que **"nenhum sistema é perfeito"**. | sem crédito, com aviso de Content ID |
| **CC BY** (Creative Commons Atribuição) — [creativecommons.org/licenses/by/4.0](https://creativecommons.org/licenses/by/4.0/) | depende | Livre, **com crédito obrigatório** (autor, título, licença, link). | crédito obrigatório |
| **Músicas comerciais** (artistas, Spotify, etc.) | — | — | **nunca** entram |

## O que está no catálogo hoje (32 faixas, 24 com drop)
- 15 faixas do pacote inicial (FreePD e outras), mais **17** do lote de 09/11 (Komiku, Loyalty Freak Music, Monplaisir, Dancefloor is Lava, Lloyd Rodgers, Soft and
  Furious, Frederic Lardon, Planeteer Reaction do FreePD).
- Todas vieram do repositório [SoundSafari/CC0-1.0-Music](https://github.com/SoundSafari/CC0-1.0-Music), que as **classifica como CC0 / domínio público**. Limites que você
  precisa saber:
  - O repositório **não verifica arquivo por arquivo** e pede para abrir uma issue se algo estiver errado.
  - O **FreePD fechou** (o site foi desativado), então a licença de origem não dá mais para reverificar lá.
  - Por isso essas faixas têm o aviso "licença CC0 conforme a classificação do repositório" no catálogo. CC0 não exige crédito.
- **`black-box-legendary-9509`** vem da pasta do Pixabay do repositório. Estava rotulada CC0, mas o Pixabay **não usa CC0**: ela está agora como **Pixabay Content License**, com
  aviso de Content ID.
- As faixas dos lotes foram escolhidas **pelo nome e pela medição** (tem drop? energia? emoção?), sem ouvir. **Ouça e desligue** na tela Músicas as que não combinarem com CS.

## Como a montagem escolhe
- Só faixas **com drop** que **caibam** na montagem sem repetir o começo; vence a de maior emoção; empate real (até 0,10) é sorteado.
- O **primeiro tiro da montagem cai no drop**.
- **Rodízio:** o mesmo vídeo mantém a faixa (refazer não troca); um vídeo novo evita as **2 últimas** faixas dadas a outros vídeos.
- O crédito (quando a licença exige) e o aviso de Content ID saem no `publicar.txt` da montagem.

## Se o YouTube ou a Twitch reclamar
1. Veja de qual faixa é (o `publicar.txt` e `saida/<vídeo>/benchmark.json` dizem a música da montagem).
2. Conteste citando a licença da faixa (certificado do Pixabay, política da NCS, FAQ do StreamBeats, CC0 do repositório).
3. Se persistir, desligue a faixa na tela **Músicas** (ela nunca mais é sorteada).

## Pedido de revisão
Se você achar alguma faixa com licença errada, desligue na tela Músicas e me diga: o Bisturi não tenta adivinhar licença.
