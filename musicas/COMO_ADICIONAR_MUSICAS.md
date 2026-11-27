# Como pôr músicas novas na montagem

O Bisturi só sorteia sozinho as músicas que estão no `catalogo.json` **com a licença escrita**. Arquivo solto na pasta nunca é usado
(para não cair em música com direito autoral no YouTube ou na Twitch).

## Num clone novo (do GitHub) não há arquivos de música
O `catalogo.json` vem no git, os `.mp3` não. Para baixar as faixas livres que o catálogo lista (CC0 e a do Pixabay), rode:
`.venv\Scripts\python.exe tools\baixar_musicas_cc0.py`. Ele não apaga nem muda faixa do catálogo, não rotula nada como CC0 por conta própria e gera o `licencas.txt` a partir do catálogo.
Músicas suas, de outras fontes, entram pela tela (abaixo), sempre com a licença.

## Onde fica o que você muda
O `catalogo.json` é o catálogo **de fábrica** (vem com o programa; o Bisturi não o regrava). O que você faz na tela (adicionar faixa, ligar e
desligar) vai para o `catalogo_usuario.json`: nesta mesma pasta quando você roda da pasta do projeto, ou em `musicas\` da pasta de dados no
programa instalado. Faixa de fábrica que você liga ou desliga fica anotada em `ajustes`; voltar ao estado de fábrica apaga a anotação.

## Passo a passo (pela tela, a forma mais simples)
1. Abra a tela **Músicas**, clique em **Escolher arquivo de música**, escolha a licença, o autor, o título e (se a licença pedir) o link da página da faixa.
2. Clique em **Adicionar ao catálogo**. O Bisturi copia o arquivo para `musicas/`, mede BPM, drop e energia e grava a licença. **Sem licença a música não entra.**
3. Use **▶ Ouvir o drop** para escutar e a chave da esquerda para ligar ou desligar a faixa.
4. De onde pegar e o que cada licença exige: `docs/MUSICAS_FONTES.md`.

## Passo a passo (à mão, para quem prefere)
1. Copie o `.mp3` para esta pasta (`musicas/`).
2. Abra o `catalogo_usuario.json` (se não existir, crie com `{"faixas": []}`) e ponha uma linha em `faixas`, no formato das do `catalogo.json`.
   Preencha `arquivo`, `autor`, `licenca` (nome e endereço da página) e `duracao_s`. Nome repetido com uma faixa de fábrica: vale a sua.
3. Rode a medição: `python -m tools.medir_musicas --catalogo musicas\catalogo_usuario.json` (grava `energia`, `drop_s`, `impacto_db`, `emocao`;
   `--so-ver` só mostra a tabela).

## O que o Bisturi faz com isso (decisão do dono, 08/11/2026)
- Entre as músicas com o BPM parecido com o ritmo da montagem, prefere a de maior `emocao` (energia + tamanho do drop).
- O **primeiro tiro da montagem cai no drop** da música (`montagem.tiro_no_drop = true`): a música pula o começo até o drop chegar no tiro.
  Música sem drop (`null`) toca desde o início, como antes.
- Os cortes continuam na batida.

## Estado da biblioteca (09/11/2026)
32 músicas, 24 com drop. A lista com licença, drop e emoção de cada uma está na tela **Músicas**. A montagem não repete a mesma faixa em vídeos seguidos.
