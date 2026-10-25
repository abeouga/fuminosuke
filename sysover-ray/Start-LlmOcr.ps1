param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$taskProjectRoot = Split-Path -Parent $PSScriptRoot
$taskSettings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'app\overlay-settings.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $taskProjectRoot $taskSettings.ProjectRoot))
$taskBackendUri = [uri]$taskSettings.BackendHealthUrl
$taskFrontendUri = [uri]$taskSettings.FrontendUrl
if (-not $taskBackendUri.IsLoopback -or -not $taskFrontendUri.IsLoopback) {
    throw 'SysOverRay starts local loopback services only.'
}

# Overrides apply only to this launcher and its children; backend/.env is retained.
$taskOrigins = @("http://localhost:$($taskFrontendUri.Port)", "http://127.0.0.1:$($taskFrontendUri.Port)")
# Phones on the same private network open http://<this PC>:<port>; only the frontend listens there.
$taskLanAddresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -match '^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)' } |
    ForEach-Object { $_.IPAddress })
foreach ($address in $taskLanAddresses) {
    $taskOrigins += "http://$($address):$($taskFrontendUri.Port)"
}
$env:FRONTEND_ORIGINS = $taskOrigins -join ','
if ($taskLanAddresses.Count -gt 0) {
    $env:LLM_OCR_FRONTEND_HOST = '0.0.0.0'
    foreach ($address in $taskLanAddresses) { Write-Host "Phone: http://$($address):$($taskFrontendUri.Port)" }
}

& (Join-Path $taskProjectRoot 'scripts\dev.ps1') -NoBrowser:$NoBrowser -BackendPort $taskBackendUri.Port -FrontendPort $taskFrontendUri.Port
