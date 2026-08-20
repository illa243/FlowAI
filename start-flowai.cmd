@echo off
setlocal
set "FLOWAI_ROOT=%~dp0"
set "FLOWAI_PYTHON=%FLOWAI_ROOT%.venv\Scripts\python.exe"

if not exist "%FLOWAI_PYTHON%" (
  echo FlowAI ще не встановлено. Запускаю install.ps1...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%FLOWAI_ROOT%install.ps1"
  if errorlevel 1 pause & exit /b 1
)

cd /d "%FLOWAI_ROOT%"
"%FLOWAI_PYTHON%" -m flowai
if errorlevel 1 pause
