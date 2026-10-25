# The app's own PostgreSQL cluster in backend/data/postgres (127.0.0.1:5434 only).
#   -Init   create the cluster once and write DATABASE_URL/DATABASE_SCHEMA into backend/.env
#   -Start  start it if it is not running (scripts/dev.ps1 calls this)
#   -Stop   stop it
# Uses an installed PostgreSQL (default: C:\Program Files\PostgreSQL\18\bin, or LLM_OCR_PG_BIN).
# The Windows service of that installation is not used or changed.
param([switch]$Init, [switch]$Start, [switch]$Stop)
$ErrorActionPreference = 'Stop'

$backendDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
$pgData = Join-Path $backendDir 'data\postgres'
$pgLog = Join-Path $backendDir 'data\logs\postgres.log'
$port = 5434
$bin = if ($env:LLM_OCR_PG_BIN) { $env:LLM_OCR_PG_BIN } else { 'C:\Program Files\PostgreSQL\18\bin' }
foreach ($tool in 'initdb.exe', 'pg_ctl.exe', 'pg_isready.exe', 'psql.exe') {
    if (-not (Test-Path -LiteralPath (Join-Path $bin $tool))) { throw "PostgreSQL tool not found: $bin\$tool" }
}

function Test-Ready {
    & (Join-Path $bin 'pg_isready.exe') -h 127.0.0.1 -p $port -q
    return $LASTEXITCODE -eq 0
}

function Start-Cluster {
    if (Test-Ready) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pgLog) | Out-Null
    # pg_ctl keeps the console handles of its caller; run it detached and poll instead.
    Start-Process -FilePath (Join-Path $bin 'pg_ctl.exe') -ArgumentList 'start', '-D', "`"$pgData`"", '-l', "`"$pgLog`"" -WindowStyle Hidden | Out-Null
    foreach ($attempt in 1..60) {
        if (Test-Ready) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Local PostgreSQL did not become ready. See $pgLog"
}

if ($Init) {
    if (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION')) { throw "$pgData already exists" }
    $chars = [char[]]'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $password = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
    $pwFile = Join-Path $env:TEMP ("llmocr-pg-" + [guid]::NewGuid() + '.txt')
    try {
        [IO.File]::WriteAllText($pwFile, $password)
        & (Join-Path $bin 'initdb.exe') -D $pgData -U llmocr --auth=scram-sha-256 --pwfile=$pwFile -E UTF8 --locale=C | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'initdb failed.' }
    }
    finally { Remove-Item -LiteralPath $pwFile -Force -ErrorAction SilentlyContinue }
    Add-Content -LiteralPath (Join-Path $pgData 'postgresql.conf') "`nlisten_addresses = '127.0.0.1'`nport = $port`ntimezone = 'UTC'"
    Start-Cluster
    $env:PGPASSWORD = $password
    try {
        & (Join-Path $bin 'psql.exe') -h 127.0.0.1 -p $port -U llmocr -d postgres -qtAc 'CREATE DATABASE llmocr'
        if ($LASTEXITCODE -ne 0) { throw 'CREATE DATABASE failed.' }
    }
    finally { Remove-Item Env:PGPASSWORD }
    $envFile = Join-Path $backendDir '.env'
    $lines = @(Get-Content -LiteralPath $envFile -Encoding UTF8 | Where-Object { $_ -notmatch '^(DATABASE_URL|DATABASE_SCHEMA)=' })
    $lines += "DATABASE_URL=postgresql+psycopg://llmocr:$password@127.0.0.1:$port/llmocr"
    $lines += 'DATABASE_SCHEMA=app'
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [IO.File]::WriteAllLines($envFile, $lines, $utf8)
    Write-Host "Local PostgreSQL created at $pgData (127.0.0.1:$port). backend/.env now points at it."
}
elseif ($Start) { Start-Cluster }
elseif ($Stop) {
    if (Test-Ready) { & (Join-Path $bin 'pg_ctl.exe') stop -D $pgData -m fast | Out-Null }
}
else { throw 'Use -Init, -Start or -Stop.' }
exit 0
