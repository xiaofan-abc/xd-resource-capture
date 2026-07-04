@echo off
setlocal

cd /d "%~dp0"
set "HOST=127.0.0.1"
set "PORT=8000"

if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found in %CD%
    pause
    exit /b 1
)

py -3 --version >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON_CMD=python"
    ) else (
        echo [ERROR] Python was not found in PATH.
        echo Please install Python 3.10+ and try again.
        pause
        exit /b 1
    )
)

echo Installing Python requirements...
%PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo Installing Playwright Chromium...
%PYTHON_CMD% -m playwright install chromium
if errorlevel 1 goto :fail

echo.
echo Starting project at http://%HOST%:%PORT%/
echo Press Ctrl+C to stop the server.
%PYTHON_CMD% -m uvicorn web_app.app:app --host %HOST% --port %PORT%
if errorlevel 1 goto :fail

exit /b 0

:fail
echo.
echo Installation failed. Please review the messages above.
pause
exit /b 1
