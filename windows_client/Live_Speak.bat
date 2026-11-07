@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tts.ps1" live "%~1"
pause
