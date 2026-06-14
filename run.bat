@echo off
REM Picurate — Windows launcher
REM Run from the picurate project folder: double-click this file.

cd /d "%~dp0"

REM Use the bundled venv if it exists, otherwise fall back to system Python
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe main.py
) else (
    python main.py
)
