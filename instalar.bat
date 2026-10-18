@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Instalar Bisturi Clips
rem aberto de dentro do ZIP, o Windows extrai so este arquivo numa pasta temporaria: sem o resto nao ha o que instalar
if not exist "cs2clip\__main__.py" (
  echo.
  echo ERRO: este arquivo foi aberto de dentro do ZIP, sem o resto do programa ao lado.
  echo Extraia o ZIP inteiro numa pasta ^(botao direito no ZIP ^> Extrair tudo^) e de dois cliques no instalar.bat dessa pasta.
  pause
  exit /b 1
)
echo.
echo   Instalando o Bisturi Clips
echo   --------------------------
echo   Pode deixar rodando: na primeira vez demora (baixa uns 3 GB entre pacotes e o modelo de voz).
echo   Cada passo e conferido. So aparece "Pronto" no fim se a checagem passar.
echo.

rem ---- 1. Python 3.11 ou 3.12 (o leitor do banner de round, rapidocr-onnxruntime, nao instala no 3.13+) ----
set "VERSAO_OK=import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 12) else 1)"
set "PY="
call :testar_python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :testar_python "%ProgramFiles%\Python312\python.exe"
call :testar_python "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :testar_python "%ProgramFiles%\Python311\python.exe"
if not defined PY for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do call :testar_python "%%P"
if not defined PY for /f "delims=" %%P in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do call :testar_python "%%P"
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do call :testar_python "%%P"
if not defined PY (
  echo [1/6] Python 3.11 ou 3.12 nao encontrado. Instalando o Python 3.12 pelo winget...
  where winget >nul 2>nul
  if errorlevel 1 (
    echo ERRO: o winget nao existe neste Windows. Instale o Python 3.12 em python.org e rode este arquivo de novo.
    goto :falhou
  )
  winget install --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
  call :testar_python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
  echo ERRO: o Python 3.12 nao ficou disponivel. Feche esta janela, abra de novo e rode instalar.bat outra vez.
  goto :falhou
)
echo [1/6] Python ok: %PY%

rem ---- 2. FFmpeg ----
rem o winget poe o FFmpeg nesta pasta; o PATH novo so vale em janelas novas, entao ela entra aqui tambem
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"
set "TEM_FFMPEG=0"
if exist "bin\ffmpeg.exe" set "TEM_FFMPEG=1"
where ffmpeg >nul 2>nul && set "TEM_FFMPEG=1"
if "%TEM_FFMPEG%"=="0" (
  echo [2/6] FFmpeg nao encontrado. Instalando pelo winget...
  where winget >nul 2>nul
  if errorlevel 1 (
    echo ERRO: o winget nao existe neste Windows. Baixe o FFmpeg em ffmpeg.org, ponha ffmpeg.exe e ffprobe.exe na pasta bin e rode de novo.
    goto :falhou
  )
  winget install --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
  where ffmpeg >nul 2>nul && set "TEM_FFMPEG=1"
)
if "%TEM_FFMPEG%"=="0" (
  echo ERRO: o FFmpeg nao ficou disponivel. Feche esta janela, abra de novo e rode instalar.bat outra vez.
  goto :falhou
)
echo [2/6] FFmpeg ok.

rem ---- 3. Ambiente e pacotes ----
rem .venv de uma tentativa com Python 3.13+ (ou copiada de outro PC) nao serve: e recriada
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "%VERSAO_OK%" >nul 2>nul
  if errorlevel 1 (
    echo [3/6] O ambiente .venv usa um Python que nao serve ^(precisa 3.11 ou 3.12^). Recriando...
    rmdir /s /q .venv
    if exist ".venv" (
      echo ERRO: nao consegui apagar a pasta .venv. Feche o Bisturi Clips ^(janela preta^) e rode de novo.
      goto :falhou
    )
  )
)
if not exist ".venv\Scripts\python.exe" (
  echo [3/6] Criando o ambiente do programa...
  "%PY%" -m venv .venv
  if errorlevel 1 (
    echo ERRO: nao consegui criar o ambiente .venv.
    goto :falhou
  )
)
echo [3/6] Instalando os pacotes do programa ^(pode demorar varios minutos na primeira vez^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
  echo ERRO: nao consegui atualizar o pip. Veja a internet e tente de novo.
  goto :falhou
)
rem placa NVIDIA (o driver traz o nvidia-smi): leva os pacotes de GPU do Whisper. Sem ela eles seriam ~1,2 GB inuteis.
set "TEM_NVIDIA=0"
where nvidia-smi >nul 2>nul && set "TEM_NVIDIA=1"
set "PACOTES=-r requirements.txt -r requirements-ui.txt"
if "%TEM_NVIDIA%"=="1" set "PACOTES=%PACOTES% -r requirements-gpu.txt"
".venv\Scripts\python.exe" -m pip install %PACOTES%
if errorlevel 1 (
  echo ERRO: a instalacao dos pacotes falhou ^(mensagem acima^). Veja a internet e tente de novo.
  goto :falhou
)
if not exist "entrada" mkdir entrada

rem ---- 4. Modelo de voz (legendas e reacoes) ----
if "%TEM_NVIDIA%"=="0" (
  echo       Placa NVIDIA nao encontrada: a voz sera transcrita no processador. Usando o modelo de voz menor ^(small^),
  echo       bem mais rapido nesse caso. Para trocar, mude "modelo" em config.toml.
  ".venv\Scripts\python.exe" -m cs2clip --modelo-leve
)
echo [4/6] Baixando o modelo de voz ^(a placa NVIDIA usa ~1,6 GB; sem ela e menor, so na primeira vez^)...
".venv\Scripts\python.exe" -m cs2clip --baixar-modelo
if errorlevel 1 (
  echo ERRO: nao consegui baixar o modelo de voz ^(mensagem acima^). Veja a internet e rode instalar.bat de novo.
  goto :falhou
)

rem ---- extra: TwitchDownloaderCLI (chat da Twitch; opcional, MIT, do GitHub oficial lay295/TwitchDownloader) ----
if not exist "bin\TwitchDownloaderCLI.exe" (
  echo       Baixando o leitor do chat da Twitch ^(opcional^)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/lay295/TwitchDownloader/releases/latest'; $a=$r.assets | Where-Object { $_.name -match 'CLI.*Windows-x64\.zip$' } | Select-Object -First 1; $z=Join-Path $env:TEMP 'twitchdl_cli.zip'; Invoke-WebRequest $a.browser_download_url -OutFile $z; Expand-Archive $z -DestinationPath 'bin' -Force; Remove-Item $z"
  if errorlevel 1 echo       AVISO: nao consegui baixar; o programa funciona sem o chat.
)

rem ---- 5. Checagem final ----
echo.
echo [5/6] Conferindo se esta tudo funcionando...
echo.
".venv\Scripts\python.exe" -m cs2clip --checar
if errorlevel 1 (
  echo.
  echo A instalacao terminou, mas a checagem acima achou problema^(s^) grave^(s^). Resolva e rode de novo.
  goto :falhou
)

rem ---- 6. Atalho na Area de Trabalho ----
powershell -NoProfile -ExecutionPolicy Bypass -File "ui\criar_atalho.ps1"
if errorlevel 1 (
  echo AVISO: nao consegui criar o atalho. Abra o programa por ui\iniciar.bat.
) else (
  echo [6/6] Atalho "Bisturi Clips" criado na Area de Trabalho.
)

echo.
echo Pronto! Abra o "Bisturi Clips" pelo atalho na Area de Trabalho.
choice /c SN /n /m "Abrir agora? [S/N] "
if errorlevel 2 exit /b 0
start "" "ui\iniciar.bat"
exit /b 0

:falhou
echo.
echo A instalacao NAO foi concluida.
pause
exit /b 1

rem PY = o caminho recebido, se for um Python 3.11 ou 3.12 que abre (o atalho da Microsoft Store nao abre)
:testar_python
if defined PY exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "%VERSAO_OK%" >nul 2>nul && set "PY=%~1"
exit /b 0
