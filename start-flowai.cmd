@echo off
setlocal
set "FLOWAI_ROOT=%~dp0"
set "FLOWAI_PYTHONW=%FLOWAI_ROOT%.venv\Scripts\pythonw.exe"

if not exist "%FLOWAI_PYTHONW%" (
  echo FlowAI ще не встановлено. Запускаю install.ps1...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%FLOWAI_ROOT%install.ps1"
  if errorlevel 1 pause & exit /b 1
)

cd /d "%FLOWAI_ROOT%"
rem start від'єднує процес, тому вікно cmd закривається одразу,
rem а pythonw.exe не створює консолі взагалі.
start "" "%FLOWAI_PYTHONW%" -m flowai
exit /b 0
