param(
    [string]$SystemDir = $env:WECHATBACK_SYSTEM_DIR,
    [string]$Dest = $env:WECHATBACK_BACKUP,
    [string]$Python,
    [switch]$IncludeSecrets
)

$ErrorActionPreference = 'Stop'

if (-not $SystemDir) { $SystemDir = Join-Path $env:USERPROFILE '.wechatback' }
if (-not $Dest) {
    throw 'Backup path not set. Set WECHATBACK_BACKUP or pass -Dest <backup root>.'
}
$SystemDest = Join-Path $Dest '90_System'

if (-not $Python) {
    $repo = Split-Path -Parent $PSScriptRoot
    $candidate = Join-Path $repo '.venv\Scripts\python.exe'
    if (Test-Path $candidate) {
        $Python = $candidate
    } else {
        $Python = (Get-Command python -ErrorAction Stop).Source
    }
}

foreach ($d in $SystemDest, (Join-Path $SystemDest 'db'), (Join-Path $SystemDest 'secrets'), (Join-Path $SystemDest 'logs')) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$snapshot = Join-Path $SystemDir 'work\archive-snapshot.db'
if (Test-Path $snapshot) { Remove-Item $snapshot -Force }

$pyCode = @'
import sqlite3, sys, pathlib
src, dst = sys.argv[1], sys.argv[2]
pathlib.Path(dst).unlink(missing_ok=True)
con = sqlite3.connect(src)
con.execute('VACUUM INTO ?', (dst,))
con.close()
'@
& $Python -c $pyCode (Join-Path $SystemDir 'archive.db') $snapshot
if ($LASTEXITCODE -ne 0) { throw "archive snapshot failed: $LASTEXITCODE" }

Copy-Item $snapshot (Join-Path $SystemDest 'db\archive.db') -Force
foreach ($name in 'state.db', 'source_snapshot.json', 'config.toml') {
    $src = Join-Path $SystemDir $name
    if (Test-Path $src) { Copy-Item $src (Join-Path $SystemDest $name) -Force }
}

if ($IncludeSecrets) {
    $secrets = Join-Path $SystemDir 'secrets'
    if (Test-Path $secrets) {
        Copy-Item (Join-Path $secrets '*.json') (Join-Path $SystemDest 'secrets') -Force -ErrorAction SilentlyContinue
    }
}

$logs = Join-Path $SystemDir 'logs'
if (Test-Path $logs) {
    Copy-Item (Join-Path $logs '*.log') (Join-Path $SystemDest 'logs') -Force -ErrorAction SilentlyContinue
}

$readme = @'
# 90_System (sensitive)

This folder contains:

- config.toml : local paths
- db/archive.db : archive fact layer (contains chat content)
- db/state.db : incremental watermarks
- secrets/ : database and image keys (only with -IncludeSecrets)
- logs/ : run logs (counts, no chat text)

Do NOT share, commit to Git, or upload to cloud storage.
Restrict the SMB/NAS share to the owner, or encrypt the folder.
Obsidian excludes this folder from search/graph via userIgnoreFilters.
'@
$readme | Out-File -FilePath (Join-Path $SystemDest 'README.md') -Encoding utf8

Write-Host "system snapshot -> $SystemDest"
