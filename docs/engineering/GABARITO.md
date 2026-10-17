# Gabarito de momentos (para medir o acerto do detector)

**Objetivo:** marcar ~20 momentos reais num VOD seu, para comparar com o que o programa encontra sozinho (R-A7). Sem isso, não dá para saber se o detector de emoção está bom.

## Como fazer (jeito fácil: página de revisão)
1. Rode o vídeo no programa (`processar.bat`). Na pasta `saida\<id do vídeo>\` nasce o **`revisao.html`** (o programa também mostra o caminho no fim).
2. Abra esse arquivo no navegador. Cada momento aparece com o corte para assistir. Você só responde duas perguntas e clica:
   - o abate que o programa achou está no corte? (Sim / Não / Sem abate, só reação);
   - que emoção foi? (Raiva, Alegria/hype, Risada, Nada de especial — pode marcar mais de uma).
3. As respostas ficam salvas no navegador (pode fechar e voltar). A barra no topo mostra quantos faltam.
4. No fim, clique em **Exportar CSV**: baixa o `gabarito_momentos.csv` já no formato certo (mais de uma emoção vem juntada com `+`, ex.: `alegria+risada`). Salve-o em `tools\gabarito_momentos.csv`.
5. Não precisa revisar todos: 15 a 30 já ajudam muito. Eu uso esse arquivo para medir acerto e recall por categoria e calibrar a emoção.
6. Para ver o resultado você mesmo: `.venv\Scripts\python.exe tools\medir_gabarito.py` (mostra precisão e recall de raiva, alegria e risada, e quantos abates estavam certos).

## Jeito manual (se preferir assistir ao VOD direto)
Preencha o `tools/gabarito_momentos.csv` (modelo `gabarito_momentos.template.csv`): 1 linha por momento, tempo em mm:ss, categorias `abate`, `clutch`, `raiva`, `alegria`, `risada` (várias separadas por `+`).

## Definição de cada categoria (sua, 11/10)
- **raiva:** grito/aumento de volume da sua voz, ou vozes falando ao mesmo tempo em tom alto (discussão) — não precisa ter palavrão.
- **alegria/hype:** comemoração depois de um abate bom, round ganho, jogada que deu certo.
- **risada:** risada de verdade (kkkk, gargalhada), sua ou de quem está com você.
- **abate/clutch:** jogada em si (o programa já pega isso razoavelmente bem).

O arquivo fica fora do git (tem timestamps de um vídeo privado), mas o **modelo vazio** (`gabarito_momentos.template.csv`) é versionado.
