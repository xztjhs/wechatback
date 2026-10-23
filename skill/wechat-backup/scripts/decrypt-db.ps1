param(
    [string]$DataRoot = $env:WECHATBACK_DATA_ROOT,
    [string]$SystemDir = $env:WECHATBACK_SYSTEM_DIR,
    [string]$Tool,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Repo = if ($env:WECHATBACK_REPO) { $env:WECHATBACK_REPO } else { Split-Path -Parent $PSScriptRoot }
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    $Py = (Get-Command python -ErrorAction Stop).Source
}

if ($DataRoot) { $env:WECHATBACK_DATA_ROOT = $DataRoot }
if (-not $SystemDir) { $SystemDir = Join-Path $env:USERPROFILE '.wechatback' }

$dbStorage = & $Py -c "from wechatback.config import load_config; c=load_config(); print(c.db_storage or '')"
$dbStorage = $dbStorage.Trim()
if (-not $dbStorage) { throw 'Cannot locate db_storage. Check WECHATBACK_DATA_ROOT and config.toml.' }
if (-not (Test-Path $dbStorage)) { throw "db_storage not found: $dbStorage" }

if (-not $Tool) { $Tool = Join-Path $Repo 'tools\wcdb-key-tool\wcdb_key_tool_windows.py' }
if (-not (Test-Path $Tool)) {
    throw "wcdb-key-tool not found: $Tool. Download it from https://github.com/TANGandXUE/wcdb-key-tool and keep the MIT license."
}

$work = Join-Path $SystemDir 'work'
$keys = Join-Path $SystemDir 'secrets\db_keys.json'
New-Item -ItemType Directory -Force -Path $work, (Split-Path $keys) | Out-Null

Write-Host "db_storage : $dbStorage"
Write-Host "decrypted  : $(Join-Path $work 'decrypted')"
Write-Host "keys file  : $keys"

if ($DryRun) {
    Write-Host "dry run: $Py `"$Tool`" extract --db-dir `"$dbStorage`" --output `"$keys`" --decrypt"
    exit 0
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning 'Administrator rights are usually required to read the WeChat process. Re-run from an elevated PowerShell if extraction returns 0 keys.'
}

Push-Location $work
try {
    & $Py $Tool extract --db-dir $dbStorage --output $keys --decrypt
    if ($LASTEXITCODE -ne 0) { throw "wcdb-key-tool failed: $LASTEXITCODE" }
}
finally {
    Pop-Location
}

$count = (Get-ChildItem (Join-Path $work 'decrypted') -Recurse -Filter *.db -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "decrypted databases: $count"
if ($count -eq 0) { throw 'No decrypted database was produced. Check the tool output and WeChat version.' }
