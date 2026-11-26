# 注册「立即运行 + 登录后续跑」计划任务（推荐用这个让调度器持续运行）。
# 安装并立即启动：powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\install_resume_task.ps1 -Install -Start
# 卸载：          powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\install_resume_task.ps1 -Remove
# 查看：          powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\install_resume_task.ps1 -Status
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status,
    [switch]$Start,
    [double]$Hours = 7
)

$ErrorActionPreference = 'Stop'
$taskName = 'OAS-DualAgentResume'
$runner = Join-Path $PSScriptRoot 'run_forever.ps1'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if ($Status) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        Write-Host "已安装: $taskName  状态=$($task.State) 上次运行=$($info.LastRunTime) 结果=$($info.LastTaskResult)"
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'status.py') 2>$null
    } else {
        Write-Host "未安装: $taskName"
    }
    exit 0
}

if ($Remove) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已移除计划任务 $taskName"
    exit 0
}

if (-not $Install) {
    Write-Host '用法: -Install [-Start] | -Remove | -Status'
    exit 1
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Hours {1}' -f $runner, $Hours) `
    -WorkingDirectory $repo
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn
$triggerNow = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(30)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 10)
Register-ScheduledTask -TaskName $taskName -Action $action `
    -Trigger @($triggerLogon, $triggerNow) -Settings $settings `
    -Description 'OAS 双 Agent 自动开发：立即运行并在登录后续跑' -Force | Out-Null
Write-Host "已安装计划任务 $taskName（登录后 + 立即运行 run_forever.ps1 -Hours $Hours）"

if ($Start) {
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 8
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Host "已触发启动: 状态=$((Get-ScheduledTask -TaskName $taskName).State) 上次结果=$($info.LastTaskResult)"
}
