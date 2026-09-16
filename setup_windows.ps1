[CmdletBinding()]
param(
    [switch]$SkipStartupTask
)

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $PSCommandPath
Set-Location $ProjectDir

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 --version
    if (-not (Test-Path '.venv\Scripts\python.exe')) { & py -3 -m venv .venv }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python --version
    if (-not (Test-Path '.venv\Scripts\python.exe')) { & python -m venv .venv }
} else {
    throw 'Python 3 was not found. Install supported Python, then rerun this script.'
}

$VenvPython = Join-Path $ProjectDir '.venv\Scripts\python.exe'
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt
New-Item -ItemType Directory -Force -Path runtime, logs | Out-Null
if (-not (Test-Path 'execution_settings.json')) {
    Copy-Item 'execution_settings.example.json' 'execution_settings.json'
}

$PythonFiles = Get-ChildItem -Path $ProjectDir -Filter '*.py' -File | ForEach-Object { $_.FullName }
if ($PythonFiles.Count -gt 0) {
    & $VenvPython -B -m py_compile $PythonFiles
}
& $VenvPython -B -c "import MetaTrader5; print('Official MetaTrader5 Python package: OK')"

$settings = Get-Content 'execution_settings.json' -Raw | ConvertFrom-Json
if ($settings.terminal_path -and -not (Test-Path $settings.terminal_path)) {
    Write-Warning 'Configured terminal_path does not exist. Update execution_settings.json before enabling execution.'
}

if (-not $SkipStartupTask) {
    $TaskName = 'SignalSniper-NativeMT5Executor'
    $RunLine = '"' + $VenvPython + '" "' + (Join-Path $ProjectDir 'native_mt5_executor.py') + '"'
    schtasks.exe /Create /TN $TaskName /SC ONLOGON /RL LIMITED /TR $RunLine /F | Out-Null
    Write-Host "Startup task '$TaskName' installed or updated."
}

Write-Host 'Windows setup complete.'
Write-Host 'MT5 package check passed. Configure the terminal and protected credentials before enabling execution.'
