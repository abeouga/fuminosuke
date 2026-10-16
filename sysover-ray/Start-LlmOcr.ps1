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
$env:FRONTEND_ORIGINS = "http://localhost:$($taskFrontendUri.Port),http://127.0.0.1:$($taskFrontendUri.Port)"

& (Join-Path $taskProjectRoot 'scripts\dev.ps1') -NoBrowser:$NoBrowser -BackendPort $taskBackendUri.Port -FrontendPort $taskFrontendUri.Port
