# Créditos

**Bisturi Clips** foi criado por **Vinicius Hammoud** ([twitch.tv/drd0llar](https://twitch.tv/drd0llar)) e é distribuído sob a [licença MIT](LICENSE).

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
| [demoparser2](https://github.com/LaihoE/demoparser) | ler demos `.dem` do CS2 | MIT |
| [FastAPI](https://fastapi.tiangolo.com), [Uvicorn](https://www.uvicorn.org), [python-multipart](https://github.com/Kludex/python-multipart) | a interface no navegador | MIT / BSD-3-Clause / Apache-2.0 |
| nvidia-cublas-cu12, nvidia-cudnn-cu12 | rodar o Whisper na placa de vídeo NVIDIA | licença da NVIDIA (redistribuição permitida) |

## Arquivos incluídos no projeto
| Arquivo | Origem | Licença |
|---|---|---|
| `cs2clip/assets/fontes/Anton-Regular.ttf` | fonte Anton, de The Anton Project Authors | SIL Open Font License 1.1 (`OFL-Anton.txt`) |
| `cs2clip/assets/models/face_detection_yunet_2023mar.onnx` | YuNet, do OpenCV Zoo | MIT |
| `bin/TwitchDownloaderCLI` (baixado à parte) | [TwitchDownloader](https://github.com/lay295/TwitchDownloader) | MIT (avisos em `bin/`) |
| `cs2clip/assets/icons/*.png` | recortes minúsculos do HUD do Counter-Strike 2, usados só como referência para reconhecer o ícone na tela | © Valve Corporation |

Counter-Strike 2 é marca da Valve Corporation. O Bisturi Clips é um projeto independente, sem ligação com a Valve, e não altera o jogo: só lê o vídeo da sua partida (e, se você quiser, a demo).
