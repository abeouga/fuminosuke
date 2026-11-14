$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot 'backend'
$frontendDir = Join-Path $projectRoot 'frontend'
$venvPython = Join-Path $backendDir '.venv\Scripts\python.exe'
$alembic = Join-Path $backendDir '.venv\Scripts\alembic.exe'

function Get-Python312Path {
    $pythonLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($pythonLauncher) {
        $installedPythons = & $pythonLauncher.Source -0p
        foreach ($entry in $installedPythons) {
            if ([string]$entry -match '^\s*-V:3\.12\s+\*?\s*(.+\\python\.exe)\s*$') {
                return $Matches[1]
            }
        }
    }

    $candidates = @()
    $pythonCommand = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($pythonCommand) { $candidates += $pythonCommand.Source }
    if ($env:USERPROFILE) {
        $candidates += Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = & $candidate -c 'import sys; print(sys.version_info[:2])'
        if ($LASTEXITCODE -eq 0 -and ([string]$version).Trim() -eq '(3, 12)') {
            return $candidate
        }
    }
    return $null
}

$python312 = Get-Python312Path
if (-not $python312) {
    throw 'Python 3.12 is required. Install Python 3.12 (64-bit) from python.org, then run start.bat again.'
}

if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvVersion = & $venvPython -c 'import sys; print(sys.version_info[:2])'
    if ($LASTEXITCODE -ne 0 -or ([string]$venvVersion).Trim() -ne '(3, 12)') {
        Write-Host 'Replacing the existing virtual environment because it does not use Python 3.12.'
        Remove-Item -LiteralPath (Join-Path $backendDir '.venv') -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $python312 -m venv (Join-Path $backendDir '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
}

& $venvPython -m pip install --requirement (Join-Path $backendDir 'requirements.lock')
if ($LASTEXITCODE -ne 0) { throw 'Could not install backend dependencies.' }
if ($env:LLM_OCR_INSTALL_PADDLE -eq '1') {
    & $venvPython -m pip install --requirement (Join-Path $backendDir 'requirements-paddle.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Optional PaddleOCR installation failed. OpenCV layout editing and OpenAI OCR remain available.'
    }
}
else {
    Write-Host 'Skipping optional PaddleOCR. Set LLM_OCR_INSTALL_PADDLE=1 before setup to install it.'
}
& $venvPython -m pip install --no-deps --editable $backendDir
if ($LASTEXITCODE -ne 0) { throw 'Could not install the backend package.' }
& $venvPython (Join-Path $projectRoot 'scripts\export_portable_templates.py') --validate-only
if ($LASTEXITCODE -ne 0) { throw 'Portable template bootstrap validation failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $backendDir '.env'))) {
    Copy-Item -LiteralPath (Join-Path $backendDir '.env.example') -Destination (Join-Path $backendDir '.env')
    Write-Warning 'Created backend/.env with the safe mock OCR setting. Review OCR_PROVIDER and configure OPENAI_API_KEY before using external OCR.'
}
if (-not (Test-Path -LiteralPath (Join-Path $backendDir 'data\app.db'))) {
    $bootstrapDir = Join-Path $backendDir 'bootstrap\templates'
    $bootstrapDb = Join-Path $bootstrapDir 'app.db'
    if (Test-Path -LiteralPath $bootstrapDb -PathType Leaf) {
        Copy-Item -Path (Join-Path $bootstrapDir '*') -Destination (Join-Path $backendDir 'data') -Recurse
        Write-Host 'Installed the versioned active-template bootstrap data.'
    }
}
Push-Location $backendDir
try {
    & $alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
}
finally { Pop-Location }
Push-Location $frontendDir
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Could not install frontend dependencies.' }
}
finally { Pop-Location }
# Shortcuts and the overlay app hold this checkout's absolute path, so they are made here.
& (Join-Path $projectRoot 'sysover-ray\Install-SysOverRay.ps1')
Write-Host 'Setup completed. Run scripts\dev.ps1 to start the application.'
