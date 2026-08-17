param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot 'backend'
$frontendDir = Join-Path $projectRoot 'frontend'
$python = Join-Path $backendDir '.venv\Scripts\python.exe'
$alembic = Join-Path $backendDir '.venv\Scripts\alembic.exe'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'backend/.venv is missing. Run scripts/setup.ps1 first.'
}
if (-not (Test-Path -LiteralPath $alembic -PathType Leaf)) {
    throw 'Alembic is missing from backend/.venv. Run scripts/setup.ps1 first.'
}
$envFile = Join-Path $backendDir '.env'
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw 'backend/.env is missing. Copy backend/.env.example to backend/.env, review OCR_PROVIDER, and configure OPENAI_API_KEY before starting.'
}
$venvVersion = & $python -c 'import sys; print(sys.version_info[:2])'
if ($LASTEXITCODE -ne 0 -or ([string]$venvVersion).Trim() -ne '(3, 12)') {
    throw 'backend/.venv must use Python 3.12. Run setup.bat after installing Python 3.12.'
}

function Test-LocalPortInUse([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.ConnectAsync('127.0.0.1', $Port)
        return $connect.Wait(200) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Find-FreeLocalPort([int]$PreferredPort) {
    foreach ($candidate in $PreferredPort..($PreferredPort + 20)) {
        if (-not (Test-LocalPortInUse $candidate)) { return $candidate }
    }
    throw "No free local port was found near $PreferredPort."
}

function Stop-ProcessTree([int]$ProcessId) {
    # taskkill stops child processes too and does not require WMI access.  Some
    # managed Windows environments block Win32_Process queries, which must not
    # prevent the development servers from starting or being cleaned up.
    try {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $ProcessId /T /F 2>$null | Out-Null
    }
    catch {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-StaleProjectServers {
    $backendMarker = $python.ToLowerInvariant()
    $frontendMarker = (Join-Path $frontendDir 'node_modules').ToLowerInvariant()
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    }
    catch {
        Write-Warning 'Could not inspect existing development servers; using available ports instead.'
        return
    }
    foreach ($process in $processes) {
        $commandLine = ([string]$process.CommandLine).ToLowerInvariant().Replace('/', '\')
        $isBackend = $commandLine.Contains($backendMarker) -and
            $commandLine.Contains('uvicorn') -and
            $commandLine.Contains('app.main:app')
        $isFrontend = $commandLine.Contains($frontendMarker) -and
            $commandLine.Contains('vite')
        if (($isBackend -or $isFrontend) -and [int]$process.ProcessId -ne $PID) {
            Stop-ProcessTree ([int]$process.ProcessId)
        }
    }
    Start-Sleep -Milliseconds 400
}

function Wait-ForBackend([string]$HealthUrl, [System.Diagnostics.Process]$Process) {
    $lastError = $null
    foreach ($attempt in 1..60) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "Backend exited during startup. See $backendErr"
        }
        try {
            return Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 2
        }
        catch {
            $lastError = $_
            Start-Sleep -Milliseconds 250
        }
    }
    throw "Backend health check timed out: $lastError"
}

function Wait-ForFrontend([string]$FrontendUrl, [System.Diagnostics.Process]$Process) {
    foreach ($attempt in 1..60) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "Frontend exited during startup. See $frontendErr"
        }
        try {
            Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 2 | Out-Null
            return
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    throw "Frontend startup timed out. See $frontendErr"
}

$logDir = Join-Path $backendDir 'data\logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$backendOut = Join-Path $logDir 'dev-backend.out.log'
$backendErr = Join-Path $logDir 'dev-backend.err.log'
$frontendOut = Join-Path $logDir 'dev-frontend.out.log'
$frontendErr = Join-Path $logDir 'dev-frontend.err.log'

Stop-StaleProjectServers

Push-Location $backendDir
try {
    & $alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
}
finally {
    Pop-Location
}

$oauthEnabled = Get-Content -LiteralPath $envFile |
    Where-Object { $_ -match '^GOOGLE_OAUTH_ENABLED=' } |
    Select-Object -Last 1 |
    ForEach-Object { ($_ -split '=', 2)[1].Trim().ToLowerInvariant() }
if ($oauthEnabled -in @('true', '1', 'yes', 'on')) {
    if ((Test-LocalPortInUse 8000) -or (Test-LocalPortInUse 5173)) {
        throw 'Google OAuth requires the registered local ports 8000 and 5173. Stop the process using those ports, then restart.'
    }
    $backendPort = 8000
    $frontendPort = 5173
}
else {
    $backendPort = Find-FreeLocalPort 8000
    $frontendPort = Find-FreeLocalPort 5173
}
$backendUrl = "http://127.0.0.1:$backendPort"
$frontendUrl = "http://127.0.0.1:$frontendPort"

if ($backendPort -ne 8000) {
    Write-Warning "Port 8000 is already in use. This run will use backend port $backendPort."
}
if ($frontendPort -ne 5173) {
    Write-Warning "Port 5173 is already in use. This run will use frontend port $frontendPort."
}

$backend = $null
$frontend = $null
try {
    $paddleOcrDisabled = Get-Content -LiteralPath $envFile |
        Where-Object { $_ -match '^PADDLEOCR_DISABLED=' } |
        Select-Object -Last 1 |
        ForEach-Object { ($_ -split '=', 2)[1].Trim() }
    $previousPaddleOcrDisabled = $env:PADDLEOCR_DISABLED
    if ($null -ne $paddleOcrDisabled) {
        $env:PADDLEOCR_DISABLED = $paddleOcrDisabled
    }
    try {
        $backend = Start-Process -FilePath $python -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',([string]$backendPort) -WorkingDirectory $backendDir -WindowStyle Hidden -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr -PassThru
    }
    finally {
        if ($null -eq $previousPaddleOcrDisabled) {
            Remove-Item Env:PADDLEOCR_DISABLED -ErrorAction SilentlyContinue
        }
        else {
            $env:PADDLEOCR_DISABLED = $previousPaddleOcrDisabled
        }
    }
    $health = Wait-ForBackend "$backendUrl/api/v1/health" $backend

    $expectedProvider = Get-Content -LiteralPath $envFile |
        Where-Object { $_ -match '^OCR_PROVIDER=' } |
        Select-Object -Last 1 |
        ForEach-Object { ($_ -split '=', 2)[1].Trim().ToLowerInvariant() }
    $actualProvider = ([string]$health.ocrProvider).ToLowerInvariant()
    if ($expectedProvider -and $actualProvider -ne $expectedProvider) {
        throw "OCR provider mismatch: expected '$expectedProvider', but backend reports '$actualProvider'."
    }

    $previousBackendUrl = $env:LLM_OCR_BACKEND_URL
    $env:LLM_OCR_BACKEND_URL = $backendUrl
    try {
        $frontend = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev','--','--host','127.0.0.1','--port',([string]$frontendPort),'--strictPort' -WorkingDirectory $frontendDir -WindowStyle Hidden -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr -PassThru
    }
    finally {
        if ($null -eq $previousBackendUrl) {
            Remove-Item Env:LLM_OCR_BACKEND_URL -ErrorAction SilentlyContinue
        }
        else {
            $env:LLM_OCR_BACKEND_URL = $previousBackendUrl
        }
    }

    Wait-ForFrontend $frontendUrl $frontend
    Write-Host "Backend: $backendUrl/docs"
    Write-Host "Frontend: $frontendUrl"
    Write-Host "OCR provider: $actualProvider"
    Write-Host 'Press Ctrl+C to stop both processes.'

    if (-not $NoBrowser) {
        try {
            Start-Process $frontendUrl | Out-Null
        }
        catch {
            Write-Warning "Could not open the browser automatically. Open $frontendUrl manually."
        }
    }

    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Milliseconds 500
        $backend.Refresh()
        $frontend.Refresh()
    }
}
finally {
    if ($frontend) { Stop-ProcessTree $frontend.Id }
    if ($backend) { Stop-ProcessTree $backend.Id }
}
