[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $PSCommandPath
Set-Location $ProjectDir
$Python = Join-Path $ProjectDir '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = Join-Path $ProjectDir 'venv\Scripts\python.exe' }
if (-not (Test-Path $Python)) { throw 'Python virtual environment missing. Run .\setup_windows.ps1 first.' }
if (-not (Test-Path '.env')) { throw 'Missing .env. Configure local Telegram credentials before launch.' }

New-Item -ItemType Directory -Force -Path runtime | Out-Null
& $Python -B -m py_compile native_mt5_executor.py telegram_listener.py telegram_control_bot.py

$services = @(
    @{ Name = 'native_mt5_executor'; Script = 'native_mt5_executor.py' },
    @{ Name = 'telegram_listener'; Script = 'telegram_listener.py' },
    @{ Name = 'telegram_control_bot'; Script = 'telegram_control_bot.py' }
)
$processes = foreach ($service in $services) {
    $log = Join-Path $ProjectDir "runtime\$($service.Name).log"
    $process = Start-Process -FilePath $Python -ArgumentList @('-u', '-B', $service.Script) -WorkingDirectory $ProjectDir -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru
    Write-Host "Started $($service.Name) (log: runtime\$($service.Name).log)"
    $process
}

try {
    Write-Host 'Signal Sniper is running. Press Ctrl-C to stop all services.'
    while ($true) {
        if ($processes | Where-Object { $_.HasExited }) { throw 'A Signal Sniper service exited. Check runtime logs.' }
        Start-Sleep -Seconds 1
    }
} finally {
    $processes | Where-Object { -not $_.HasExited } | Stop-Process -ErrorAction SilentlyContinue
}
