@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\.."
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Não encontrei .venv\Scripts\python.exe.
  echo Rode instalar.bat na pasta do programa: ele cria o ambiente e instala tudo, inclusive a interface.
  pause
  exit /b 1
)
if not exist "requirements-ui.txt" (
  echo Não encontrei requirements-ui.txt na pasta do projeto.
  pause
  exit /b 1
)
echo Instalando as dependências da interface...
"%PYTHON%" -m pip install -r requirements-ui.txt
if errorlevel 1 (
  echo A instalação falhou. Confira a mensagem acima e a conexão com a internet para instalar as dependencias.
  pause
  exit /b 1
)
echo Instalação concluída.
echo A interface funciona localmente depois que as dependencias sao instaladas.
pause
