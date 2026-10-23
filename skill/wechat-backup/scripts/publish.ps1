param(
    [switch]$Full,
    [switch]$SkipImages,
    [switch]$SkipFiles,
    [switch]$NoSync,
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
    $cliArgs = @('-m', 'wechatback.cli', 'export')
    if ($Full) { $cliArgs += '--full' } else { $cliArgs += '--incremental' }
    if ($SkipImages) { $cliArgs += '--skip-images' }
    if ($SkipFiles) { $cliArgs += '--skip-files' }

    Write-Host "export: $($cliArgs -join ' ')"
    & $Py @cliArgs
    if ($LASTEXITCODE -ne 0) { throw "export failed: $LASTEXITCODE" }

    if (-not $NoSync) {
        if (-not $env:WECHATBACK_BACKUP) {
            throw 'WECHATBACK_BACKUP is not set. Use -NoSync, or set it to the backup root, e.g. \\NAS\Backup\WeChatBack.'
        }

        Write-Host "sync vault -> $env:WECHATBACK_BACKUP"
        & powershell -ExecutionPolicy Bypass -File (Join-Path $Repo 'scripts\sync-vault.ps1') -Backup $env:WECHATBACK_BACKUP
        if ($LASTEXITCODE -ge 8) { throw "sync-vault failed: $LASTEXITCODE" }

        Write-Host "sync system -> $env:WECHATBACK_BACKUP"
        $systemArgs = @('-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Repo 'scripts\sync-system.ps1'), '-Dest', $env:WECHATBACK_BACKUP)
        if ($IncludeSecrets) { $systemArgs += '-IncludeSecrets' }
        & powershell @systemArgs
        if ($LASTEXITCODE -ne 0) { throw "sync-system failed: $LASTEXITCODE" }
    }
    Write-Host 'publish done'
}
finally {
    Pop-Location
}
