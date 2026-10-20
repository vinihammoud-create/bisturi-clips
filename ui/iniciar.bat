@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\.."
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Não encontrei .venv\Scripts\python.exe. Confira a pasta do projeto.
  pause
  exit /b 1
)
"%PYTHON%" -c "import fastapi, uvicorn, multipart" >nul 2>&1
if errorlevel 1 (
  echo As dependências da interface estao faltando.
  echo Execute instalar.bat ^(na pasta do programa^) e abra esta janela novamente.
  pause
  exit /b 1
)
echo Iniciando o Bisturi Clips... ^(versao com console, para diagnostico: o atalho da Area de Trabalho abre sem esta janela^)
echo O navegador sera aberto em uma janela de aplicativo do Edge, se ele estiver instalado.
echo Mantenha esta janela aberta. Para parar o servidor, pressione Ctrl+C aqui.
"%PYTHON%" -m ui
if errorlevel 1 (
  echo A interface não conseguiu iniciar. Confira as mensagens acima.
  pause
  exit /b 1
)
endlocal
