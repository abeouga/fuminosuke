param(
    # Build app\SysOverRay.exe again even when it is already there.
    [switch]$Rebuild
)

# Creates this checkout's SysOverRay files that Git does not keep, because they hold the
# absolute path of the checkout: the built app\SysOverRay.exe and the two shortcuts.
# Run it again after moving the checkout.

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$projectRoot = Split-Path -Parent $root
$appDir = Join-Path $root 'app'
$exe = Join-Path $appDir 'SysOverRay.exe'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if ($Rebuild -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    $dotnet = Get-Command 'dotnet.exe' -ErrorAction SilentlyContinue
    if ($dotnet) {
        & $dotnet.Source publish (Join-Path $root 'SysOverRay\SysOverRay.csproj') -c Release -r win-x64 `
            --self-contained false -p:PublishSingleFile=true -o $appDir
        if ($LASTEXITCODE -ne 0) { throw 'Could not build SysOverRay.' }
    }
    else {
        Write-Warning 'The .NET SDK is not installed, so SysOverRay.exe was not built. Start-LlmOcr.ps1 and its shortcut still work.'
    }
}

$shell = New-Object -ComObject WScript.Shell

# Starts the backend and frontend without a window; SysOverRay also starts through this one.
$start = $shell.CreateShortcut((Join-Path $root 'llm-ocr-deployを起動.lnk'))
$start.TargetPath = $powershell
$start.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $root 'Start-LlmOcr.ps1') + '" -NoBrowser'
$start.WorkingDirectory = $projectRoot
$start.WindowStyle = 7
$start.Save()
Write-Host "Created: $($start.FullName)"

if (Test-Path -LiteralPath $exe -PathType Leaf) {
    $overlay = $shell.CreateShortcut((Join-Path $root 'SysOverRayを起動.lnk'))
    $overlay.TargetPath = $exe
    $overlay.WorkingDirectory = $appDir
    $overlay.WindowStyle = 1
    $overlay.Description = 'llm-ocrの起動・停止・再起動オーバーレイ'
    $overlay.Save()
    Write-Host "Created: $($overlay.FullName)"
}
