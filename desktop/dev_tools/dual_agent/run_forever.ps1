# 守护循环：调度器异常退出时自动重启；进入终态则退出。
# 用法：
#   powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\run_forever.ps1 -Hours 7
[CmdletBinding()]
param(
    [double]$Hours = 7,
    [int]$MaxRestarts = 30,
    [int]$RestartDelaySec = 30
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$python = Join-Path $repo 'toolkit\python.exe'
$stateDir = Join-Path $PSScriptRoot 'state'
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $stateDir, $logDir | Out-Null
$watchLog = Join-Path $logDir ('watch_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')

function Write-Watch([string]$text) {
    $line = '[' + (Get-Date -Format 'HH:mm:ss') + '] ' + $text
    Add-Content -Path $watchLog -Value $line -Encoding UTF8
}

foreach ($name in @('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL',
                    'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
                    'ANTHROPIC_DEFAULT_HAIKU_MODEL')) {
    if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
        $value = [Environment]::GetEnvironmentVariable($name, 'User')
        if ($value) { Set-Item -Path ('Env:' + $name) -Value $value }
    }
}

$restarts = 0
Write-Watch "watchdog start (hours=$Hours max_restarts=$MaxRestarts)"
while ($restarts -le $MaxRestarts) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $runLog = Join-Path $logDir ('scheduler_' + $stamp + '.log')
    Write-Watch "launch scheduler -> $runLog"
    & $python (Join-Path $PSScriptRoot 'scheduler.py') --hours $Hours *>&1 |
        Tee-Object -FilePath $runLog | Out-Host
    $code = $LASTEXITCODE
    Write-Watch "scheduler exited code=$code"

    $status = 'unknown'
    $db = Join-Path $stateDir 'state.db'
    if (Test-Path $db) {
        $status = (& $python -c "import sqlite3,sys;c=sqlite3.connect(r'$db');r=c.execute(\"select value from meta where key='status'\").fetchone();print(r[0] if r else '')") 2>$null
        if ($status) { $status = $status.Trim('"').Trim() }
    }
    Write-Watch "status=$status"
    if ($status -in @('completed', 'paused_budget', 'blocked', 'paused_limit', 'paused_manual')) {
        Write-Watch "terminal state ($status) -> watchdog stop"
        break
    }
    $restarts += 1
    if ($restarts -gt $MaxRestarts) { Write-Watch "restart budget exhausted"; break }
    Write-Watch "restart #$restarts in $RestartDelaySec s"
    Start-Sleep -Seconds $RestartDelaySec
}
Write-Watch 'watchdog end'
