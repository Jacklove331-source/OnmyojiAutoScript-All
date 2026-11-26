# 双 Agent 自动开发调度器：启动脚本
# 用法：
#   powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\start.ps1 -Hours 7
#   powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\start.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\start.ps1 -Status
[CmdletBinding()]
param(
    [double]$Hours = 0,
    [switch]$DryRun,
    [switch]$Status,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$python = Join-Path $repo 'toolkit\python.exe'
$stateDir = Join-Path $PSScriptRoot 'state'
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $stateDir, $logDir | Out-Null

# Claude Code 需要 anthropic 变量；有些只在用户级环境里，这里补进当前进程
foreach ($name in @('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_MODEL',
                    'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
                    'ANTHROPIC_DEFAULT_HAIKU_MODEL')) {
    if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
        $value = [Environment]::GetEnvironmentVariable($name, 'User')
        if ($value) { Set-Item -Path ('Env:' + $name) -Value $value }
    }
}

if ($Status) {
    & $python (Join-Path $PSScriptRoot 'status.py')
    exit $LASTEXITCODE
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$logPath = Join-Path $logDir ('scheduler_' + $stamp + '.log')
$schedArgs = @((Join-Path $PSScriptRoot 'scheduler.py'))
if ($DryRun) { $schedArgs += '--dry-run' }
if ($Hours -gt 0) { $schedArgs += @('--hours', $Hours) }

if ($Foreground) {
    & $python @schedArgs 2>&1 | Tee-Object -FilePath $logPath
    exit $LASTEXITCODE
}

# 后台脱离启动：关掉终端也继续跑
$cmdLine = '"{0}" {1} > "{2}" 2>&1' -f $python, ($schedArgs -join ' '), $logPath
$process = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmdLine -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 3
Write-Host "scheduler started pid=$($process.Id)"
Write-Host "log: $logPath"
Write-Host "state: $(Join-Path $stateDir 'state.db')"
Write-Host '查看状态: powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\start.ps1 -Status'
