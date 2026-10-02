@echo off
REM ============================================================
REM  Run the data cleaning toolkit on the NOBO files
REM  Double-click this file. It looks for the input files in the
REM  folder ABOVE this one (the "Data cleaning" folder).
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
  echo   python clean.py --input "..\*.csv" "..\*.xls" --config config_nobo.yaml --output output_nobo --verbose
  pause
  exit /b 1
)
if "%PY%"=="py -3" (set "PYQ=py -3") else (set PYQ="%PY%")
echo Using Python: %PY%

echo Checking required packages...
%PYQ% -m pip install -r requirements.txt --quiet --disable-pip-version-check

%PYQ% clean.py --input "..\*.csv" "..\*.xls" --config config_nobo.yaml --output output_nobo --verbose

echo.
echo Outputs are in the output_nobo folder.
pause
