@echo off
REM ============================================================
REM  AIM Nexus Data Cleansing Tool - local web app
REM  Double-click this file. The app opens in your browser.
REM  Close this window to stop the app.
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
  echo   python -m pip install -r requirements_app.txt
  echo   python -m streamlit run app.py
  pause
  exit /b 1
)
if "%PY%"=="py -3" (set "PYQ=py -3") else (set PYQ="%PY%")
echo Using Python: %PY%

echo Checking required packages (the first time this can take a few minutes)...
%PYQ% -m pip install -r requirements_app.txt --quiet --disable-pip-version-check
if errorlevel 1 (
  echo Could not install the packages. Check the internet connection and try again.
  pause
  exit /b 1
)

echo.
echo Starting the app at http://localhost:8501
echo Keep this window open while you use the app. Close it to stop the app.
%PYQ% -m streamlit run app.py
pause
