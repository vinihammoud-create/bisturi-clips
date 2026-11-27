@echo off
chcp 65001 >nul
setlocal
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title Bisturi Clips
echo.
echo   Bisturi Clips - cortes automaticos de CS2
echo   ------------------------------------------
echo   Arraste videos sobre este arquivo, ou coloque-os na pasta "entrada".
echo.
if not exist ".venv\Scripts\python.exe" (
  echo O programa ainda nao foi instalado nesta pasta: de dois cliques em instalar.bat primeiro.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m cs2clip %*
set "CODIGO=%ERRORLEVEL%"
echo.
if "%CODIGO%"=="1" (
  echo Nenhum video foi processado. Veja os detalhes em saida\log.txt
) else if "%CODIGO%"=="2" (
  echo Opcao ou comando invalido. Veja a mensagem acima.
) else (
  if exist "saida\relatorio.html" start "" "saida\relatorio.html"
  if "%CODIGO%"=="3" echo Terminou com problemas. Veja saida\log.txt
)
pause
