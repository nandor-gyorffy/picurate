@echo off
REM Picurate setup script for Windows 10/11
REM Run once from the picurate project folder to install dependencies.

cd /d "%~dp0"

echo === Picurate Setup ===
echo.

REM Check Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python not found. Download Python 3.12+ from https://python.org
    echo        Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo Found Python %PY_VER%

REM Create virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if %ERRORLEVEL% NEQ 0 ( echo ERROR: Failed to create venv & pause & exit /b 1 )
    echo Virtual environment created.
) else (
    echo Virtual environment already exists.
)

REM Install dependencies
echo Installing dependencies (this may take a few minutes)...
.venv\Scripts\pip install --upgrade pip --quiet
.venv\Scripts\pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 ( echo ERROR: pip install failed & pause & exit /b 1 )
echo Dependencies installed.

REM Create desktop shortcut
echo.
set /p CREATE_SHORTCUT=Create desktop shortcut? [Y/n]:
if /i "%CREATE_SHORTCUT%"=="" set CREATE_SHORTCUT=Y
if /i "%CREATE_SHORTCUT%"=="Y" (
    set SCRIPT_DIR=%~dp0
    set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%
    set VBS_TMP=%TEMP%\picurate_shortcut.vbs
    echo Set oWS = WScript.CreateObject("WScript.Shell") > "%VBS_TMP%"
    echo sLinkFile = oWS.SpecialFolders("Desktop") ^& "\Picurate.lnk" >> "%VBS_TMP%"
    echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%VBS_TMP%"
    echo oLink.TargetPath = "%SCRIPT_DIR%\run.bat" >> "%VBS_TMP%"
    echo oLink.WorkingDirectory = "%SCRIPT_DIR%" >> "%VBS_TMP%"
    echo oLink.IconLocation = "%SCRIPT_DIR%\assets\icon\picurate.ico" >> "%VBS_TMP%"
    echo oLink.Description = "Picurate Photo Organizer" >> "%VBS_TMP%"
    echo oLink.Save >> "%VBS_TMP%"
    cscript //nologo "%VBS_TMP%"
    del "%VBS_TMP%"
    echo Desktop shortcut created: Picurate.lnk
)

echo.
echo === Setup complete! ===
echo.
echo To launch Picurate: double-click run.bat  (or the desktop shortcut)
echo.
pause
