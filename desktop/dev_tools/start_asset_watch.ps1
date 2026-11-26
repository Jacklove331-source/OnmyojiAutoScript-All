# start_asset_watch.ps1 -- 幂等拉起「资产监控」三件套：页采集守护 + 实时帧守护 + 平安志（PAZ）。
# 由 OAS 服务启动时自动调用（见 module/server/workbench_router.py 的 startup 钩子），
# 也可以手动跑： powershell -NoProfile -ExecutionPolicy Bypass -File dev_tools\start_asset_watch.ps1
#
# 为什么需要它：采集/实时帧两个守护以前是我手工 Start-Process 起的，重启机器后就没了；
# 而「刷新」按钮只是往 asset_request.json 写请求，没有守护在跑就没人处理。

param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'   # 控制台是 GBK，角色名/中文 JSON 会 UnicodeEncodeError
$root = 'D:\3D\YYS2\OnmyojiAutoScript-easy-install-1'
$py = Join-Path $root 'toolkit\python.exe'
$log = Join-Path $root 'log\asset_watch_boot.log'

function Write-Log([string]$msg) {
    $line = ('{0} {1}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $msg)
    if (-not $Quiet) { Write-Host $line }
    Add-Content -Path $log -Value $line -Encoding UTF8
}

function Test-Running([string]$pattern) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like $pattern }
    return ($null -ne $procs -and @($procs).Count -gt 0)
}

if (-not (Test-Path $py)) { Write-Log "python missing: $py"; exit 1 }

# 1) 页采集守护（30 分钟一轮 / 请求唤醒；快照超 1500s 先让平安志重采）
if (Test-Running '*asset_collect_daemon.py*') {
    Write-Log 'collect daemon: already running'
}
else {
    Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Hidden -ArgumentList @(
        (Join-Path $root 'dev_tools\asset_collect_daemon.py'), '--interval', '300', '--paz-max-age', '1500')
    Write-Log 'collect daemon: started'
}

# 2) 实时帧守护（10s 读 OAS 自己写的 log/live/<config>.jpg）
if (Test-Running '*asset_live_watch.py*') {
    Write-Log 'live watch: already running'
}
else {
    Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Hidden -ArgumentList @(
        (Join-Path $root 'dev_tools\asset_live_watch.py'), '--tick', '10')
    Write-Log 'live watch: started'
}

# 3) 平安志（PAZ）：采集/刷新都靠它的读取任务；没开就拉起带 CDP 的那份
$refresh = Join-Path $root 'tasks\Workbench\paz_refresh.py'
if (Test-Path $refresh) {
    try {
        $out = & $py $refresh --ensure-only 2>&1 | Out-String
        if ($out -match '"ok":\s*true') {
            $how = if ($out -match '"started":\s*true') { 'auto-started' } else { 'already running' }
            Write-Log "paz: ok ($how)"
        }
        else {
            Write-Log ('paz: not available -> ' + ($out -replace '\s+', ' ').Trim())
        }
    }
    catch { Write-Log ('paz: ensure failed: ' + $_.Exception.Message) }
}

Write-Log 'done.'
exit 0
