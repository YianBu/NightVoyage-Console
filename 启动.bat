@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe main.py
) else (
    start "" python.exe main.py
)

