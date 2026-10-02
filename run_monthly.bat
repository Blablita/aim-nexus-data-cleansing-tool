@echo off
REM ============================================================
REM  MONTHLY RUN: inbox\<month>  ->  deliverables\<month>
REM  1. Create a folder inbox\YYYY-MM (e.g. inbox\2026-10)
REM  2. Put the month's raw files in it
REM  3. Double-click this file (it processes the latest month)
REM ============================================================
cd /d "%~dp0"

set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\anaconda3\python.exe" set "PY=%LOCALAPPDATA%\anaconda3\python.exe"
if not defined PY if exist "C:\ProgramData\anaconda3\python.exe" set "PY=C:\ProgramData\anaconda3\python.exe"
if not defined PY if exist "%USERPROFILE%\miniconda3\python.exe" set "PY=%USERPROFILE%\miniconda3\python.exe"
if not defined PY (
  echo Python was not found. Open "Anaconda Prompt", go to this folder and run:
  echo   python run_monthly.py
  pause
  exit /b 1
)
if "%PY%"=="py -3" (set "PYQ=py -3") else (set PYQ="%PY%")
echo Using Python: %PY%

echo Checking required packages...
%PYQ% -m pip install -r requirements.txt --quiet --disable-pip-version-check

%PYQ% run_monthly.py %*

echo.
echo Client file: deliverables folder.   Internal QA: qa folder.
pause
