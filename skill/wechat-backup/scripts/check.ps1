$ErrorActionPreference = 'Stop'
$Repo = if ($env:WECHATBACK_REPO) { $env:WECHATBACK_REPO } else { Split-Path -Parent $PSScriptRoot }
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    $Py = (Get-Command python -ErrorAction Stop).Source
}

Push-Location $Repo
try {
    & $Py -m wechatback.cli verify --no-write
    Write-Host ''
    Write-Host "work vault : $($env:WECHATBACK_VAULT)"
    Write-Host "backup root: $($env:WECHATBACK_BACKUP)"
    Write-Host "system dir : $($env:WECHATBACK_SYSTEM_DIR)"
}
finally {
    Pop-Location
}
