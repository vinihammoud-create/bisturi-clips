@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Instalar Bisturi Clips
echo.
echo   Instalando o Bisturi Clips
echo   --------------------------
echo   Pode deixar rodando: na primeira vez demora (baixa uns 3 GB entre pacotes e o modelo de voz).
echo   Cada passo e conferido. So aparece "Pronto" no fim se a checagem passar.
echo.

rem ---- 1. Python 3.11 ou mais novo ----
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYOK set "PY=%%P" & set "PYOK=1"
set "PY_BOM=0"
if exist "%PY%" "%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY_BOM=1"
if "%PY_BOM%"=="0" (
  echo [1/6] Python 3.11+ nao encontrado. Instalando o Python 3.12 pelo winget...
  where winget >nul 2>nul
  if errorlevel 1 (
    echo ERRO: o winget nao existe neste Windows. Instale o Python 3.12 em python.org e rode este arquivo de novo.
    goto :falhou
  )
  winget install --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
  set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not exist "%PY%" (
  echo ERRO: o Python nao ficou disponivel. Feche esta janela, abra de novo e rode instalar.bat outra vez.
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
".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-ui.txt
if errorlevel 1 (
  echo ERRO: a instalacao dos pacotes falhou ^(mensagem acima^). Veja a internet e tente de novo.
  goto :falhou
)
if not exist "entrada" mkdir entrada

rem ---- 4. Modelo de voz (legendas e reacoes) ----
echo [4/6] Baixando o modelo de voz ^(uns 1,6 GB, so na primeira vez^)...
".venv\Scripts\python.exe" -c "from cs2clip.config import load_config; from faster_whisper import download_model; download_model(load_config().transcricao.modelo)"
if errorlevel 1 (
  echo ERRO: nao consegui baixar o modelo de voz. Veja a internet e rode instalar.bat de novo.
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
