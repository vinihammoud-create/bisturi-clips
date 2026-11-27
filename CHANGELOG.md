# Changelog

Aqui fica o que muda no Bisturi Clips a cada versão, do ponto de vista de quem usa.

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Cada versão tem um número no formato `maior.menor.correção`.

## [1.1.0] - 2026-11-27

### Adicionado

**Instalação**

- Instalador `BisturiClips-Setup-1.1.0.exe`. Ele instala sem pedir permissão de administrador e traz o Python e o FFmpeg do próprio programa.
- Pasta de dados separada do programa. Os seus vídeos, kits e configurações ficam nela e sobrevivem à atualização e à desinstalação.
- O instalador procura uma placa NVIDIA. Sem ela, prepara o programa para o processador, com um modelo de voz menor.
- As músicas livres podem ser baixadas já na instalação.
- Atualização por cima da versão anterior, sem perder dados.
- O desinstalador pergunta se você quer apagar os dados. A resposta padrão é não.
- Cartão do modelo de voz na tela Início: baixa, mostra o andamento, cancela e continua de onde parou.
- O número da versão aparece no rodapé e na tela Ajuda.

**Entrada e trabalhos**

- Uma caixa só na tela Início. Cole um link de VOD da Twitch, um link do YouTube ou um código de partida, ou escolha um arquivo. O programa reconhece o que é e mostra o plano com o tempo estimado.
- Tela Trabalhos. Cada trabalho mostra as etapas, avisa quando precisa de você e pode ser cancelado e retomado.
- Vídeo do YouTube pelo YouTube Studio. Você baixa pelo botão oficial, o programa acha o arquivo na pasta Downloads e pergunta se é aquele antes de seguir.
- Botão "Última partida do CS2" e aviso quando aparece uma partida nova na pasta do jogo.
- Trabalhos e vídeos com nome legível: mapa, placar e dia.

**Demo junto do vídeo**

- Opção "Procurar a demo da partida no CS2". O programa acha a demo da gravação na pasta do jogo, confere pelos abates e descobre quem é você.

**Gravar pela demo (experimental)**

- O programa regrava os seus melhores rounds pelo próprio CS2, em imagem limpa, e monta cortes, shorts e montagem com essa imagem.
- Download da demo pelo código de partilha da partida.
- Câmera em você ou em outro jogador da demo.
- Vídeo da live junto da demo: a sua voz e a sua webcam da live entram por cima da imagem limpa.
- Escolha entre gravar só os rounds bons ou a partida inteira.
- Botão "Refazer a edição", que refaz cortes, shorts e montagem sem abrir o jogo de novo.
- Grupo "Contas do Steam" na tela Configurar, com o botão "Usar a conta aberta agora no Steam".
- Cartão na tela Início que diz o que falta preparar para gravar.
- Tudo isso exige uma segunda conta Steam. O programa recusa trabalhar com a sua conta principal.

**Músicas e montagem**

- Tela Músicas, com o catálogo de músicas livres. Você liga e desliga faixas, ouve o ponto alto e adiciona músicas novas, sempre com a licença.
- O primeiro tiro da montagem cai no ponto alto da música.
- A montagem não repete a mesma música em vídeos seguidos.
- Quando a licença pede crédito, ele vem pronto no texto para postar.
- Botão "Trocar a música", que solta a música só daquele vídeo.
- Opção para gerar também a montagem com a sua voz e sem música.
- Faixas separadas do OBS para o jogo, o microfone e o Discord. O Discord nunca entra no áudio que o programa monta.

**Shorts, capas e cortes**

- Resenha: momento engraçado com mais de um sinal junto (várias vozes, risadas, você gritando) vira short.
- Opção para valorizar a jogada que começou bem e terminou mal, com a sua reação.
- Opção de short com a webcam inteira em cima e o jogo embaixo.
- Capa com letras grandes, capa discreta e capa chamativa, à sua escolha.
- Capas alternativas de jogadas diferentes, com um arquivo que explica por que cada uma foi escolhida.
- Quando você morre logo depois do último abate, o corte vai até a morte.
- Efeitos opcionais no corte, todos desligados de início: acelerar o caminho entre abates, congelar o headshot decisivo e cartão da jogada no começo.

**Revisão e qualidade**

- Em cada momento: "Por que esta nota?", avaliação da jogada e ajuste do começo e do fim do corte.
- Selo de qualidade no resultado, com a conferência de imagem e de volume de cada arquivo final.
- Botão "Apagar kit", que apaga os cortes de um vídeo sem apagar o vídeo original.
- O pico de som dos arquivos finais fica dentro do padrão do YouTube e do TikTok.

### Modificado

- Short e montagem agora só levam round com 3 ou mais abates seus. Round com 1 ou 2 abates continua virando corte. Quando não há nenhum round assim, o programa avisa "Nada para postar".
- O selo de multi-kill do short começa no TRIPLE KILL. Não aparece mais "DOUBLE KILL".
- O estilo de fábrica passou a ser o esports limpo: corte seco, sem título de abertura no short e sem a versão do short com efeitos. Dá para mudar em Configurar.
- Numa jogada com vários abates, o vídeo fica 6 segundos depois de cada abate e volta 6 segundos antes do seguinte. Só pula quando sobra tempo parado.
- Com a demo, todos os abates do mesmo round saem no mesmo vídeo, mesmo num round longo.
- A capa de letras grandes passou a ser a principal.
- Visual novo, com as cores da logo e texto mais fácil de ler.
- O modo de arrastar arquivos e processar em 3 passos continua existindo, recolhido como "Modo manual".
- Fechar a janela com um trabalho rodando: o trabalho termina e só depois o programa sai.
- "Voltar aos padrões" mantém as contas do Steam e o seu nome no jogo.
- O instalador `.exe` não depende do winget. A instalação por ZIP continua existindo para desenvolvedores.

### Corrigido

- O mesmo abate lido duas vezes não vira mais TRIPLE KILL.
- Vídeo que começa depois do início da partida não gera mais corte com duração negativa.
- A numeração dos rounds bate com o placar nas demos de campeonato.
- Na prorrogação, o match point e o empate são reconhecidos nos placares certos.
- A reação do chat da Twitch não entra mais 4 segundos adiantada.
- Os marcadores de abate para o DaVinci Resolve caem no instante certo depois de um pulo.
- As cores dos vídeos gerados foram corrigidas.
- Janelas pretas não piscam mais na tela durante a edição.
- Cancelar um trabalho não derruba mais o download de outro.
- Cancelar o download de um VOD para o download inteiro.
- Um registro antigo de processamento não trava mais a fila.
- O botão de encerrar e o fim do programa param tudo o que estava rodando.
- O lembrete para voltar à conta principal aparece também quando a gravação dá erro.
- O ajuste do corte e a avaliação da jogada não dizem mais que salvaram quando não salvaram.
- A tela Músicas avisa quando faltam os arquivos de música e diz como consegui-los.
- Um arquivo de configuração com valor impossível dá erro claro, com o nome do campo.

### Segurança

- O console do CS2, usado na gravação pela demo, só aceita os comandos de que o gravador precisa.
- O download da demo por código recusa trabalhar sem a segunda conta configurada ou com a conta principal logada.
- O baixador da Twitch, o HLAE, o boiler-writter, o FFmpeg e os pacotes do programa têm versão fixa e impressão digital conferida.
- A tela do programa exige a chave da sessão em todos os pedidos, inclusive nas leituras.
- Nome de vídeo com código malicioso não quebra mais as páginas geradas.
- Caminhos de rede disfarçados são recusados.

## [1.0.0] - 2026-10-20

Primeira versão.

### Adicionado

- Leitura do kill feed do vídeo: o programa acha os seus abates, os headshots e as sequências, do double ao ACE.
- Leitura dos rounds, do placar e da sua morte pelo próprio vídeo.
- O round inteiro num vídeo só, com o tempo parado cortado.
- Cortes só de partidas competitivas. Aquecimento e Deathmatch ficam de fora.
- Reações pela voz e pelas falas: raiva, hype e risada.
- Nota de 0 a 99 para cada momento.
- Shorts verticais, com a webcam em cima, o jogo embaixo e o selo da jogada.
- Montagem com as melhores jogadas.
- Compilação de FAILS.
- Títulos e hashtags prontos para cada short.
- Demo (`.dem`) da partida: sozinha, gera a tabela de abates; junto do vídeo, deixa a análise mais exata.
- Vídeos em 4:3 esticado, 4:3 com barras e 16:9.
- Interface no navegador, que roda só no seu computador.
- O programa abre sem janela preta e encerra sozinho quando você fecha a janela.
- Instalação por ZIP, com o `instalar.bat`.
