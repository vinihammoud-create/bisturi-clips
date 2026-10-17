# Bisturi Clips — interface local

Interface em português do Brasil para usar o `cs2clip` sem abrir o terminal. O servidor escuta somente em `127.0.0.1`, escolhe uma porta livre de 8765 a 8775 e não chama serviços externos.

## Instalar e abrir

1. A pasta `ui/` já fica na raiz do projeto, ao lado de `cs2clip/`, `entrada/`, `saida/` e `config.toml`.
2. No Windows 11, abra `ui\instalar_ui.bat` uma vez.
3. Abra `ui\iniciar.bat`. Ele inicia o servidor local e abre o Edge em modo aplicativo; se o Edge não estiver instalado, usa o navegador padrão. Mantenha a janela do terminal aberta; pressione **Ctrl+C** nela para encerrar o servidor.
4. Para criar o atalho na Área de Trabalho, clique com o botão direito em `ui\criar_atalho.ps1` e escolha executar com PowerShell, ou execute-o em uma janela do PowerShell.

O programa usa o Python do projeto: `.venv\Scripts\python.exe`. Não usa Node, npm, React, Vite, fontes externas, CDN, analytics nem telemetria. A fonte dos títulos (Anton, licença OFL) fica em `ui/static/fontes/`; logo, emblema e ícone em `ui/static/` (gerados de `docs/img/logo_original.png`).

`python -m ui --sem-navegador` só sobe o servidor, sem abrir a janela (útil para testes e screenshots).

Na primeira abertura aparece uma tela de boas-vindas que pede o @ do canal (vai para `[shorts] marca`). Depois que a pessoa clica em Começar ou Pular, a marca `dados/boas_vindas.json` impede que ela apareça de novo; apague esse arquivo para ver de novo.

## O que a interface faz

- Envia arquivos em blocos para `entrada/`, ou abre o seletor nativo do Windows para selecionar arquivos sem copiá-los.
- Processa um arquivo por vez com `python -m cs2clip`, por uma lista de argumentos, com diretório de trabalho na raiz e `PYTHONIOENCODING=utf-8`.
- Lê `saida/indice.json`, `momentos.json`, `partes.json`, `demo.json` e `progresso.json`; exibe os últimos detalhes e linhas de `saida/log.txt` quando necessário.
- Permite mudar exclusivamente `aprovado` e `excluido` em `momentos.json` e somente as opções da lista fechada do briefing em `config.toml`.
- Serve arquivos de mídia somente dentro de `saida/`, com suporte a `Range` para vídeos e bloqueio de travessia de caminho.

## Rotas locais

| Método | Rota | Uso |
|---|---|---|
| GET | `/` | Interface HTML e token da sessão |
| GET | `/health` | Verificação local de disponibilidade |
| GET | `/api/indice` | Biblioteca de vídeos (com a miniatura `capa` de cada um) |
| GET | `/api/boas-vindas` | Se a tela de primeira abertura deve aparecer, e o @ atual |
| POST | `/api/boas-vindas` | Grava o @ do canal (opcional) e marca que já viu |
| GET | `/api/entrada` | Lista de nomes e tamanhos na pasta de entrada |
| GET | `/api/videos/{id}` | Momentos, partes, demo, links disponíveis e `publicar` (shorts prontos para postar) |
| GET | `/api/progresso` | Andamento e fila |
| GET | `/api/fila` | Estado dos itens enfileirados |
| GET | `/api/log` | Últimas linhas de `saida/log.txt` |
| GET | `/api/config` | Opções permitidas e valores atuais/padrão |
| POST | `/api/escolher-arquivo` | Abre o seletor nativo do Windows |
| POST | `/api/upload` | Envia arquivos em blocos para `entrada/` |
| POST | `/api/fila` | Adiciona arquivos à fila |
| POST | `/api/fila/cancelar-tudo` | Cancela a fila e o processo ativo |
| POST | `/api/fila/{id}/cancelar` | Cancela um item |
| POST | `/api/fila/{id}/jogador` | Informa o nome para tentar a demo de novo |
| POST | `/api/videos/{id}/momento` | Aprova, descarta ou restaura um momento |
| POST | `/api/config` | Salva opções permitidas |
| POST | `/api/config/padroes` | Remove opções explícitas para voltar aos padrões do núcleo |
| POST | `/api/abrir-pasta` | Abre `saida/` ou uma pasta de vídeo no Explorer |
| GET | `/media/{id}/{caminho}` | Serve mídia dentro da pasta do vídeo |
| GET | `/media-geral/{caminho}` | Serve mídia permitida diretamente dentro de `saida/` ou subpastas |

Todas as rotas POST exigem o cabeçalho `X-Token` da sessão. O servidor valida `Host` e `Origin`, não habilita CORS aberto e usa cabeçalhos de segurança. Documentação OpenAPI é desativada.

## Dependências diretas adicionadas

As versões estão fixadas em `requirements-ui.txt`. As licenças declaradas para as dependências diretas são permissivas:

| Dependência | Versão | Licença |
|---|---:|---|
| FastAPI | 0.128.2 | MIT |
| Uvicorn | 0.48.0 | BSD-3-Clause |
| python-multipart | 0.0.29 | Apache-2.0 |
| HTTPX (testes/TestClient) | 0.28.1 | BSD-3-Clause |
| pytest (testes) | 9.0.2 | MIT |

## Testes

Na raiz do projeto, execute:

```bat
.venv\Scripts\python.exe -m pytest tests/test_ui_*.py -q
```

Os testes usam pastas `saida/` falsas, arquivos pequenos e um processo simulado. Não exigem GPU nem vídeos reais.

## Limites conhecidos

- **Revisado e testado com o programa real em 17/10/2026** (fila com processo de verdade, cancelamento, leitura do andamento, edição do `momentos.json` real, página de revisão aberta no Edge). A versão original foi escrita sem acesso ao programa; a revisão corrigiu: página `revisao.html` quebrada pela política de segurança, detecção de processo vivo errada no Windows (`os.kill(pid, 0)`), cancelamento sem plano B, e acrescentou o modo prévia, `--forcar-gpu` e o botão "Verificar meu computador".
- Fechar a janela do app **não** encerra o servidor: feche também a janela preta do `iniciar.bat` (ou Ctrl+C nela).
- O envio de arquivos grandes pelo navegador passa por uma cópia temporária do servidor antes de ir para `entrada/` (usa o dobro do espaço por um instante). Para vídeos grandes prefira **Escolher no computador**, que não copia nada.
- A ação "Gostei" só liga a marca; não há botão para desmarcar.
- Abrir o Explorer e o Edge são funções do Windows: valide à mão.
