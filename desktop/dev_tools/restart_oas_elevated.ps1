# 需要管理员权限运行：停止 OAS GUI 进程树并重新拉起 gui.py
# 由 dev_tools/restart_oas.py --elevate 通过 UAC 提权调用；结果写到 log/restart_elevated.log
$ErrorActionPreference = 'Continue'
$root = 'D:\3D\YYS2\OnmyojiAutoScript-easy-install-1'
$logFile = Join-Path $root 'log\restart_elevated.log'

function Write-Log([string]$text) {
    $line = ('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $text)
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
    Write-Host $line
}

Set-Content -LiteralPath $logFile -Value '' -Encoding UTF8
Write-Log "elevated start, pid=$PID"

# 1) 找出 GUI（有 python 子进程的那个 python.exe）
$all = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Select-Object ProcessId, ParentProcessId
$parents = $all | Select-Object -ExpandProperty ParentProcessId -Unique
$guis = $all | Where-Object { $parents -contains $_.ProcessId }
if (-not $guis) {
    Write-Log 'no GUI process found'
}
foreach ($gui in $guis) {
    Write-Log "stop GUI tree pid=$($gui.ProcessId)"
    & taskkill.exe /PID $gui.ProcessId /T /F 2>&1 | ForEach-Object { Write-Log $_ }
}

Start-Sleep -Seconds 4

# 2) 确认已停
$alive = Get-Process -Name python -ErrorAction SilentlyContinue
Write-Log ('remaining python pids: ' + (($alive | Select-Object -ExpandProperty Id) -join ','))

# 3) 重新拉起 GUI
$python = Join-Path $root 'toolkit\python.exe'
Write-Log "relaunch GUI: $python gui.py"
Start-Process -FilePath $python -ArgumentList 'gui.py' -WorkingDirectory $root

Start-Sleep -Seconds 10
$after = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Select-Object ProcessId, ParentProcessId
Write-Log ('after relaunch: ' + (($after | ForEach-Object { "$($_.ProcessId)<-$($_.ParentProcessId)" }) -join ', '))
Write-Log 'done'
