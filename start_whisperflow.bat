@echo off
title VoiceCraft
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
    "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" app.py
) else (
    python app.py
)
