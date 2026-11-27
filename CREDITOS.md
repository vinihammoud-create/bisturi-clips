# Créditos

**Bisturi Clips** foi criado por **Vinicius Hammoud** ([twitch.tv/drd0llar](https://twitch.tv/drd0llar)) e é distribuído sob a [licença MIT](LICENSE).

Desenvolvido com a ajuda do Claude (Anthropic), assistente de programação usado em todo o projeto.

O programa usa estas peças de terceiros. Cada uma mantém a própria licença.

## Bibliotecas (instaladas pelo `instalar.bat`)
| Projeto | Para quê | Licença |
|---|---|---|
| [FFmpeg](https://ffmpeg.org) | ler e gravar os vídeos (instalado à parte pelo winget, não vem junto) | LGPL/GPL |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) e [CTranslate2](https://github.com/OpenNMT/CTranslate2) | transcrever a voz (legendas e reações) | MIT |
| Modelo [Whisper large-v3-turbo](https://github.com/openai/whisper) | reconhecimento de fala | MIT |
| [OpenCV](https://opencv.org) | ler o kill feed e achar a webcam | Apache-2.0 |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) (modelos do PaddleOCR) | ler o banner de fim de round e o painel da morte | Apache-2.0 |
| [ONNX Runtime](https://onnxruntime.ai) | rodar o OCR | MIT |
| [NumPy](https://numpy.org) | contas com áudio e imagem | BSD-3-Clause |
| [pandas](https://pandas.pydata.org) | tabelas de eventos da demo | BSD-3-Clause |
| [pydantic](https://github.com/pydantic/pydantic) | conferir o plano de edição (`EditPlan`) antes de renderizar | MIT |
| [PyAV](https://github.com/PyAV-Org/PyAV) | quadros-chave da varredura do vídeo | BSD-3-Clause (o pacote instalado traz bibliotecas do FFmpeg, LGPL; a licença completa vem dentro dele) |
| [demoparser2](https://github.com/LaihoE/demoparser) | ler demos `.dem` do CS2 | MIT |
| [FastAPI](https://fastapi.tiangolo.com), [Starlette](https://www.starlette.io), [Uvicorn](https://www.uvicorn.org) | a interface no navegador | MIT / BSD-3-Clause / BSD-3-Clause |
| [Pillow](https://python-pillow.org) | imagens das capas e da interface | MIT-CMU (HPND) |
| [huggingface-hub](https://github.com/huggingface/huggingface_hub), [filelock](https://github.com/tox-dev/filelock) e [certifi](https://github.com/certifi/python-certifi) | baixar o modelo de voz com HTTPS (vêm junto do faster-whisper) | Apache-2.0 / MIT / MPL-2.0 |
| nvidia-cublas-cu12 | rodar o Whisper na placa de vídeo NVIDIA (o instalador baixa do PyPI, só em PC com NVIDIA; não vem dentro do instalador) | licença da NVIDIA (redistribuição permitida) |

## Componentes do instalador (`BisturiClips-Setup-x.y.z.exe`)
O instalador só **organiza** a instalação: ele leva o código do programa e baixa o resto de quem o distribui, sempre conferindo o SHA-256 gravado em `instalador/componentes.json`.

| Componente | Para quê | Licença |
|---|---|---|
| [Inno Setup](https://jrsoftware.org/isinfo.php) | montar o instalador `.exe` (o compilador não vai dentro dele) | licença própria do Inno Setup, que permite uso, inclusive comercial, mantendo os avisos de copyright |
| [uv](https://github.com/astral-sh/uv) (Astral) | criar o ambiente e instalar as bibliotecas Python com versões e hashes fixos | Apache-2.0 ou MIT |
| [Python](https://www.python.org) 3.12 | rodar o programa; baixado pelo `uv` | PSF License |
| [python-build-standalone](https://github.com/astral-sh/python-build-standalone) | o pacote do Python que o `uv` baixa | MPL-2.0 (o Python dentro dele segue a PSF License) |
| [FFmpeg](https://www.gyan.dev/ffmpeg/builds/) 9.1 (build `essentials`, de gyan.dev) | ler e gravar os vídeos. **Baixado direto do distribuidor** na instalação; este projeto não o hospeda nem o redistribui | GPLv3 (o distribuidor publica o código-fonte de cada build) |
| nvidia-cublas-cu12 (PyPI) | cálculo na placa NVIDIA; **baixado do PyPI** só quando há placa NVIDIA | licença da NVIDIA |
| Modelo de voz Whisper (Hugging Face) | entender a fala; baixado na primeira vez, com hash conferido | MIT |

Só para quem desenvolve (não são instalados pelo `instalar.bat`): [pytest](https://pytest.org), pytest-xdist, [HTTPX](https://www.python-httpx.org) (o `TestClient` da interface), [Ruff](https://docs.astral.sh/ruff/) e basedpyright, todos MIT ou BSD-3-Clause.

**Separação de voz por IA (Demucs): não faz parte desta cópia.** O Bisturi pode usar o Demucs se ele já estiver instalado à parte no computador; sem ele (o normal), a separação do jogo
e da voz usa um filtro do FFmpeg. Esta cópia não traz o instalador do Demucs nem os pesos do modelo, porque a licença dos pesos não é clara para quem monetiza o canal.

## Arquivos incluídos no projeto
| Arquivo | Origem | Licença |
|---|---|---|
| `cs2clip/assets/fontes/Anton-Regular.ttf` | fonte Anton, de The Anton Project Authors | SIL Open Font License 1.1 (`OFL-Anton.txt`) |
| `ui/static/fontes/Barlow-*.ttf` e `BarlowCondensed-*.ttf` | fontes Barlow e Barlow Condensed (letras da interface), de The Barlow Project Authors | SIL Open Font License 1.1 (`OFL-Barlow.txt`, `OFL-BarlowCondensed.txt`) |
| `cs2clip/assets/models/face_detection_yunet_2023mar.onnx` | YuNet, do OpenCV Zoo | MIT |
| `bin/TwitchDownloaderCLI.exe` (baixado à parte pelo `instalar.bat`, com versão e SHA-256 fixos em `cs2clip/terceiros.py`) | [TwitchDownloader](https://github.com/lay295/TwitchDownloader) 1.56.5 | MIT (avisos em `bin/`) |
| `cs2clip/assets/icons/*.png` | recortes do HUD do Counter-Strike 2. **Não são cobertos pela licença MIT deste projeto.** Usados só como referência para reconhecer o ícone na tela; serão trocados por desenhos próprios. O aviso também está em `cs2clip/assets/icons/AVISO.txt` | © Valve Corporation |
| `cs2clip/assets/sfx/*.wav` | efeitos sonoros sintetizados matematicamente pelo próprio projeto (`cs2clip/sfx.py`) | CC0 1.0 (Domínio Público) |
| `musicas/*.mp3` (não vêm no git; baixados à parte) | 32 faixas: 31 da coleção [SoundSafari/CC0-1.0-Music](https://github.com/SoundSafari/CC0-1.0-Music) e 1 do Pixabay. Autor, link e licença de cada uma em `musicas/catalogo.json`; o que cada fonte exige em `docs/MUSICAS_FONTES.md` | por faixa: CC0 1.0 ou Pixabay Content License |
| `bin/hlae/` e `bin/boiler/` (baixados à parte pelo próprio programa, com SHA-256 fixo; só para gravar pela demo) | [HLAE](https://github.com/advancedfx/advancedfx) 2.193.0 e [boiler-writter](https://github.com/akiver/boiler-writter) 1.7.0 | MIT |

Counter-Strike 2 é marca da Valve Corporation. O Bisturi Clips é um projeto independente, sem ligação com a Valve, e não altera o jogo: só lê o vídeo da sua partida (e, se você quiser, a demo).
