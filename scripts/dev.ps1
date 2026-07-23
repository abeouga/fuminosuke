param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot 'backend'
$frontendDir = Join-Path $projectRoot 'frontend'
$python = Join-Path $backendDir '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'backend/.venv is missing. Run scripts/setup.ps1 first.'
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
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        Stop-ProcessTree ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-StaleProjectServers {
    $backendMarker = $python.ToLowerInvariant()
    $frontendMarker = (Join-Path $frontendDir 'node_modules').ToLowerInvariant()
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
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
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
}
finally {
    Pop-Location
}

$backendPort = Find-FreeLocalPort 8000
$frontendPort = Find-FreeLocalPort 5173
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
    $backend = Start-Process -FilePath $python -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',([string]$backendPort) -WorkingDirectory $backendDir -WindowStyle Hidden -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr -PassThru
    $health = Wait-ForBackend "$backendUrl/api/v1/health" $backend

    $expectedProvider = Get-Content -LiteralPath (Join-Path $backendDir '.env') -ErrorAction SilentlyContinue |
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
