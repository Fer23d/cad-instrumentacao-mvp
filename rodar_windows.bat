@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py app.py --port 8000
  goto fim
)

where python >nul 2>nul
if %errorlevel%==0 (
  python app.py --port 8000
  goto fim
)

echo Python nao foi encontrado.
echo Instale o Python, depois rode novamente este arquivo.
pause

:fim
endlocal
