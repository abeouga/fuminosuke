$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot 'backend'
$frontendDir = Join-Path $projectRoot 'frontend'
$venvPython = Join-Path $backendDir '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    python -m venv (Join-Path $backendDir '.venv')
}

& $venvPython -m pip install --requirement (Join-Path $backendDir 'requirements.lock')
& $venvPython -m pip install --no-deps --editable $backendDir
if (-not (Test-Path -LiteralPath (Join-Path $backendDir '.env'))) {
    Copy-Item -LiteralPath (Join-Path $backendDir '.env.example') -Destination (Join-Path $backendDir '.env')
}
Push-Location $backendDir
try { & $venvPython -m alembic upgrade head } finally { Pop-Location }
Push-Location $frontendDir
try { npm.cmd ci } finally { Pop-Location }
Write-Host 'Setup completed. Run scripts\dev.ps1 to start the application.'
