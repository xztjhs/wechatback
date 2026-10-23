param(
    [ValidateSet('video', 'file', 'all')]
    [string]$Kind = 'video',
    [switch]$IncludeSecrets
)

$ErrorActionPreference = 'Stop'
$Repo = if ($env:WECHATBACK_REPO) { $env:WECHATBACK_REPO } else { Split-Path -Parent $PSScriptRoot }
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    $Py = (Get-Command python -ErrorAction Stop).Source
}

Push-Location $Repo
try {
    & $Py -m wechatback.cli retry-missing --kind $Kind
    if ($LASTEXITCODE -ne 0) { throw 'retry-missing failed' }

    if ($Kind -eq 'all') {
        & $Py -m wechatback.cli media
    } else {
        & $Py -m wechatback.cli media --skip-images
    }
    if ($LASTEXITCODE -ne 0) { throw 'media repair failed' }

    if ($env:WECHATBACK_BACKUP) {
        & powershell -ExecutionPolicy Bypass -File (Join-Path $Repo 'scripts\sync-vault.ps1') -Backup $env:WECHATBACK_BACKUP
        if ($LASTEXITCODE -ge 8) { throw "sync-vault failed: $LASTEXITCODE" }

        $systemArgs = @('-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Repo 'scripts\sync-system.ps1'), '-Dest', $env:WECHATBACK_BACKUP)
        if ($IncludeSecrets) { $systemArgs += '-IncludeSecrets' }
        & powershell @systemArgs
        if ($LASTEXITCODE -ne 0) { throw "sync-system failed: $LASTEXITCODE" }
    } else {
        Write-Host 'WECHATBACK_BACKUP not set; skipped sync.'
    }

    Write-Host "repair done: $Kind"
}
finally {
    Pop-Location
}
