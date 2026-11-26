@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
)
if not exist ".venv\Scripts\python.exe" goto fail
".venv\Scripts\python.exe" -c "import PIL, imageio_ffmpeg" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" "scripts\setup_telugu_windows.py"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" studio.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo Setup or startup failed. See START_HERE.md and the error above.
pause
exit /b 1
