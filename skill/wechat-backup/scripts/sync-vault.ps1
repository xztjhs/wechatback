param(
    [string]$Local = $env:WECHATBACK_VAULT,
    [string]$Backup = $env:WECHATBACK_BACKUP,
    [switch]$Mirror
)

$ErrorActionPreference = 'Stop'

if (-not $Local) { $Local = Join-Path $env:USERPROFILE 'WeChatBackVault' }
if (-not $Backup) {
    throw 'Backup path not set. Set WECHATBACK_BACKUP or pass -Backup <path>.'
}

$log = Join-Path $env:USERPROFILE '.wechatback\logs\vault-sync.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

if (-not (Test-Path $Local)) { throw "Local vault not found: $Local" }
if (-not (Test-Path $Backup)) { New-Item -ItemType Directory -Force -Path $Backup | Out-Null }

$mode = if ($Mirror) { '/MIR' } else { '/E' }
Write-Host "sync: $Local -> $Backup ($mode)"
& robocopy.exe $Local $Backup $mode /MT:32 /R:1 /W:1 /NFL /NDL /NP /XD .obsidian "/LOG+:$log"

$code = $LASTEXITCODE
# Robocopy: 0-7 is success (1 means files were copied), 8+ is an error.
if ($code -ge 8) {
    throw "robocopy failed with exit code $code; see $log"
}
Write-Host "sync ok (robocopy exit $code); log: $log"
