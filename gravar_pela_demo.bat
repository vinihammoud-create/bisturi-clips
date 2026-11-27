@echo off
REM Grava a partida pelo CS2 a partir da demo (Parte D do plano). Rode com duplo clique numa sessao NORMAL (nao como administrador).
REM Exemplos:
REM   gravar_pela_demo.bat --demo auto --so-checar          (mostra o plano e o que falta, sem abrir nada)
REM   gravar_pela_demo.bat --demo auto --teste-sem-hlae     (primeira prova: abre o CS2 so para ver se a demo abre)
REM   gravar_pela_demo.bat --demo auto --rounds 12          (grava o round 12)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m cs2clip.gravador_demo %*
echo.
pause
