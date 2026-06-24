@echo off
REM Picurate uninstaller for Windows 10/11
REM Removes shortcuts and optionally catalog data and the app folder.
REM Does NOT delete your photos.

cd /d "%~dp0"

echo === Picurate Uninstaller ===
echo.

REM ── Remove desktop shortcut ───────────────────────────────────────────────
set SHORTCUT=%USERPROFILE%\Desktop\Picurate.lnk
if exist "%SHORTCUT%" (
    del /f "%SHORTCUT%"
    echo [OK] Desktop shortcut removed.
) else (
    echo  [--] Desktop shortcut not found.
)

REM ── Remove Start Menu entry if it exists ──────────────────────────────────
set STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Picurate.lnk
if exist "%STARTMENU%" (
    del /f "%STARTMENU%"
    echo [OK] Start Menu entry removed.
)

REM ── Optionally remove catalog data ────────────────────────────────────────
set DATADIR=%LOCALAPPDATA%\Picurate\Picurate
echo.
echo Your catalog and thumbnails are stored at:
echo   %DATADIR%
echo.
set /p DEL_DATA=Delete catalog data and thumbnails? (your photos are NOT affected) [y/N]:
if /i "%DEL_DATA%"=="y" (
    if exist "%DATADIR%" (
        rmdir /s /q "%DATADIR%"
        echo [OK] Catalog data removed.
    ) else (
        echo  [--] No catalog data found.
    )
) else (
    echo  Catalog data kept.
)

REM ── Optionally remove the app folder ──────────────────────────────────────
set APPDIR=%~dp0
set APPDIR=%APPDIR:~0,-1%
echo.
set /p DEL_APP=Delete the Picurate application folder (%APPDIR%)? [y/N]:
if /i "%DEL_APP%"=="y" (
    REM Schedule self-deletion via cmd after this script exits
    echo Scheduling folder deletion on next shell exit...
    start /b cmd /c "ping 127.0.0.1 -n 3 > nul && rmdir /s /q ""%APPDIR%"""
    echo [OK] Application folder will be removed momentarily.
) else (
    echo  Application folder kept.
)

echo.
echo === Picurate has been uninstalled. ===
echo.
pause
