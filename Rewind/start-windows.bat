@echo off
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  echo First start: setting up...
  %PY% -m venv .venv || (echo Install Python from https://www.python.org/downloads/windows/ and tick "Add to PATH". & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt 2>nul || echo (optional helpers not installed)
python -m rewind %*
pause
