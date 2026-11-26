# start_oas_stack.ps1 -- bring up both MuMu players then both OAS instances.
# Safe to re-run: skips anything already running.
# Does NOT need an elevated shell.
#
#   pwsh -NoProfile -File dev_tools\start_oas_stack.ps1
#
# What it does:
#   1. MuMuManager control -v 0 / -v 1 launch   (player 0 -> adb 16384, player 1 -> adb 16416)
#   2. waits until adb lists both serials
#   3. starts server.py --run oas1 / --run oas3 in hidden windows
#   4. prints a short health summary (adb devices + today's log mtimes + webui 22267)
#   5. 拉起平安志（PAZ）：主人只启 OAS 即可，资产数据由它的读取任务提供；
#      -SkipPaz 跳过，-PazOnly 只做这一步（自检用）

param(
    [switch]$SkipPaz,
    [switch]$PazOnly
)

$ErrorActionPreference = 'Continue'
$root = 'D:\3D\YYS2\OnmyojiAutoScript-easy-install-1'
$mumu = 'E:\MuMuPlayer\nx_main\MuMuManager.exe'
$adb = 'E:\MuMuPlayer\nx_main\adb.exe'
$py = Join-Path $root 'toolkit\python.exe'
$serials = @('127.0.0.1:16384', '127.0.0.1:16416')

function Invoke-PazEnsure {
    $script = Join-Path $root 'tasks\Workbench\paz_refresh.py'
    if (-not (Test-Path $script)) { Write-Host 'PAZ: paz_refresh.py missing, skip'; return }
    Write-Host '=== PAZ: 确保平安志在跑（带 CDP 的构建）==='
    try {
        $out = & $py $script --ensure-only 2>&1 | Out-String
        if ($out -match '"ok":\s*true') {
            $started = if ($out -match '"started":\s*true') { 'auto-started' } else { 'already running' }
            Write-Host ("PAZ: ok ({0})" -f $started)
        }
        else {
            Write-Host 'PAZ: not available (资产数据会退回界面采集)'
            Write-Host $out.Trim()
        }
    }
    catch { Write-Host ("PAZ: ensure failed: {0}" -f $_.Exception.Message) }
}

if ($PazOnly) { Invoke-PazEnsure; Write-Host 'done (PazOnly).'; exit 0 }

function Get-AdbSerials {
    $out = & $adb devices 2>$null
    $list = @()
    foreach ($line in $out) {
        if ($line -match '^(127\.0\.0\.1:\d+)\s+device') { $list += $Matches[1] }
    }
    return $list
}

Write-Host '=== 1. MuMu players ==='
$info = & $mumu info -v all 2>$null | Out-String
foreach ($idx in 0, 1) {
    $started = $false
    if ($info -match ('"' + $idx + '"\s*:\s*\{[^}]*"is_android_started"\s*:\s*(true|false)')) {
        $started = ($Matches[1] -eq 'true')
    }
    if ($started) {
        Write-Host ("player {0}: already running" -f $idx)
    }
    else {
        Write-Host ("player {0}: launching..." -f $idx)
        & $mumu control -v $idx launch 2>&1 | Out-Null
    }
}

Write-Host '=== 2. wait for adb ==='
$deadline = (Get-Date).AddSeconds(180)
while ((Get-Date) -lt $deadline) {
    $now = Get-AdbSerials
    if (($serials | Where-Object { $now -contains $_ }).Count -eq 2) { break }
    Start-Sleep -Seconds 5
}
$found = Get-AdbSerials
Write-Host ("adb devices: {0}" -f (($found -join ', ')))

Write-Host '=== 3. OAS instances ==='
$existing = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*server.py*' })
if ($existing) {
    Write-Host ("already running: {0}" -f (($existing | ForEach-Object ProcessId) -join ','))
}
else {
    # 两个实例默认都绑定 deploy.yaml 的 WebuiPort(22267)，后起的会 bind 失败直接退出；
    # 给第二个实例单独分配面板端口。
    $ports = @{ 'oas1' = 22267; 'oas3' = 22268 }
    foreach ($cfg in 'oas1', 'oas3') {
        $argList = @('--run', $cfg, '--port', [string]$ports[$cfg])
        Start-Process -FilePath $py -ArgumentList $argList -WorkingDirectory $root -WindowStyle Hidden
        Write-Host ("started {0} (webui {1})" -f $cfg, $ports[$cfg])
        Start-Sleep -Seconds 3
    }
}

if (-not $SkipPaz) { Invoke-PazEnsure }

Write-Host '=== 4. health ==='
Start-Sleep -Seconds 20
$day = (Get-Date).ToString('yyyy-MM-dd')
foreach ($cfg in 'oas1', 'oas3') {
    $f = Join-Path $root ("log\" + $day + "_" + $cfg + ".txt")
    if (Test-Path $f) {
        $it = Get-Item $f
        Write-Host ("{0}: last write {1}" -f $cfg, $it.LastWriteTime)
    }
    else {
        Write-Host ("{0}: no log today" -f $cfg)
    }
}
$web = Get-NetTCPConnection -State Listen -LocalPort 22267 -ErrorAction SilentlyContinue
Write-Host ("webui 22267 listening: {0}" -f [bool]$web)
Write-Host 'done.'
