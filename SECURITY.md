# Segurança

## Como relatar uma vulnerabilidade

**Não abra uma Issue pública** para falha de segurança: qualquer pessoa leria os detalhes antes de haver correção.

Use o relato **em particular** do próprio GitHub:

1. Abra a aba **Security** deste repositório.
2. Clique em **Report a vulnerability** (relatar uma vulnerabilidade).
3. Descreva o que acontece, como reproduzir e o que uma pessoa mal-intencionada conseguiria fazer. A versão do programa aparece no rodapé da interface.

Só quem mantém o projeto vê esse relato. Não há e-mail para isso: o canal é só esse.

Se a aba **Security** não mostrar o botão, abra uma Issue **sem detalhes** (só "quero relatar um problema de segurança, como faço?") e o contato em particular é combinado ali.

## O que vale como vulnerabilidade

- O programa mandar dados seus (vídeos, caminhos, contas) para a internet sem você pedir.
- Um arquivo, link ou nome de vídeo malicioso fazer o programa rodar um comando, ler ou gravar fora das pastas do projeto.
- A interface (que roda só neste computador, em `127.0.0.1`) aceitar pedidos de outras páginas ou de outros computadores.
- Um componente baixado (FFmpeg, modelo de voz, ferramentas da Twitch) ser aceito sem a conferência de hash.

## O que fica fora

- Problemas que exigem que a pessoa já tenha controle do seu computador.
- Alerta do SmartScreen ("O Windows protegeu o computador") no instalador: ele aparece porque o instalador ainda não é assinado. O `SHA256SUMS.txt` de cada versão serve para conferir o arquivo.

## Versões que recebem correção

Só a versão mais nova publicada em **Releases**. Atualize rodando o instalador novo por cima; seus dados ficam.

## Prazo

Projeto de uma pessoa só: a resposta pode levar alguns dias. Você será avisado quando houver correção, e ela é descrita nas notas da versão (`CHANGELOG.md`) sem expor detalhes que facilitem o abuso.
