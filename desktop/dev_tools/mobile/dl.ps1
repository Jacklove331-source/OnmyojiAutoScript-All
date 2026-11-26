param(
  [Parameter(Mandatory=$true)][string]$Url,
  [Parameter(Mandatory=$true)][string]$Dest,
  [int]$MaxRounds = 30
)
$ErrorActionPreference = 'Continue'
$dir = Split-Path -Parent $Dest
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }

function Get-RemoteLength([string]$u) {
    $head = curl.exe -sIL --max-time 30 $u 2>$null
    $line = ($head | Select-String '^content-length:' -CaseSensitive:$false | Select-Object -Last 1).Line
    if (-not $line) { return 0 }
    return [int64](($line -split ':')[1].Trim())
}

$remote = Get-RemoteLength $Url
Write-Host ("remote size: {0:N1} MB -> {1}" -f ($remote/1MB), $Dest)

for ($i = 1; $i -le $MaxRounds; $i++) {
    $local = 0
    if (Test-Path $Dest) { $local = (Get-Item $Dest).Length }
    if ($remote -gt 0 -and $local -ge $remote) {
        Write-Host ("DONE {0:N1} MB after {1} round(s)" -f ($local/1MB), $i)
        exit 0
    }
    Write-Host ("round {0}: local={1:N1}MB / {2:N1}MB" -f $i, ($local/1MB), ($remote/1MB))
    curl.exe -L -C - --retry 2 --retry-delay 2 --speed-time 30 --speed-limit 1024 -o $Dest $Url 2>&1 | Out-Null
    Start-Sleep -Seconds 2
}
$final = (Get-Item $Dest).Length
Write-Host ("GAVE UP: {0:N1} MB / {1:N1} MB" -f ($final/1MB), ($remote/1MB))
exit 1
