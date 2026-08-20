$ErrorActionPreference = "Stop"

$flowaiRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPath = Join-Path $flowaiRoot ".venv"
$pythonPath = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host "Створення ізольованого Python-середовища..."
    python -m venv $venvPath
}

Write-Host "Встановлення FlowAI та залежностей..."
& $pythonPath -m pip install --upgrade pip
& $pythonPath -m pip install -e $flowaiRoot

Write-Host ""
Write-Host "FlowAI встановлено. Запустіть start-flowai.cmd"
