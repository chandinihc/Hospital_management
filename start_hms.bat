@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
if exist ".venv\Scripts\python.exe" set "PYTHON_CMD=.venv\Scripts\python.exe"
if not defined PYTHON_CMD if exist "%USERPROFILE%\anaconda3\python.exe" set "PYTHON_CMD=%USERPROFILE%\anaconda3\python.exe"
if not defined PYTHON_CMD (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
    python --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo Python 3.10 or later was not found. Install Python or Anaconda, then try again.
    pause
    exit /b 1
)

%PYTHON_CMD% -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo Installing HMS dependencies...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
)

echo.
echo CarePoint HMS is starting at http://127.0.0.1:5000
echo Keep this window open while using the application. Press Ctrl+C to stop.
echo.
%PYTHON_CMD% app.py
pause
