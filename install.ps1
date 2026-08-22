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

Write-Host "Створення ярлика FlowAI..."
$pythonwPath = Join-Path $venvPath "Scripts\pythonw.exe"
$iconDirectory = Join-Path $env:LOCALAPPDATA "FlowAI\assets"
$iconPath = Join-Path $iconDirectory "FlowAI.ico"
New-Item -ItemType Directory -Force -Path $iconDirectory | Out-Null
& $pythonPath -m flowai.ui.branding $iconPath
$programs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Force -Path $programs | Out-Null
$targets = @(
    (Join-Path $flowaiRoot "FlowAI.lnk"),
    (Join-Path $programs "FlowAI.lnk")
)
$shell = New-Object -ComObject WScript.Shell
foreach ($shortcutPath in $targets) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $pythonwPath
    $shortcut.Arguments = "-m flowai"
    $shortcut.WorkingDirectory = $flowaiRoot
    $shortcut.WindowStyle = 7
    $shortcut.Description = "FlowAI"
    $shortcut.IconLocation = "$iconPath,0"
    $shortcut.Save()
}

$startMenuShortcut = Join-Path $programs "FlowAI.lnk"
& $pythonPath -m flowai.ui.branding --set-app-id $startMenuShortcut "FlowAI.Desktop"

Write-Host ""
Write-Host "FlowAI встановлено. Запустіть FlowAI.lnk або start-flowai.cmd"
