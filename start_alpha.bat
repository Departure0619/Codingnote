@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -X utf8 -m alpha.launch
) else (
  python -X utf8 -m alpha.launch
)
if errorlevel 1 pause
