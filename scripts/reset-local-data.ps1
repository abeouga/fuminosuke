param(
    [Parameter(Mandatory = $true)]
    [string]$Confirm
)

$ErrorActionPreference = 'Stop'

if ($Confirm -cne 'RESET-LOCAL-DATA') {
    throw 'Pass -Confirm RESET-LOCAL-DATA to reset all local data.'
}

$projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$dataDir = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'backend\data'))
$expectedPrefix = $projectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar

if (-not $dataDir.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Safety check failed. No data was removed.'
}

if (Test-Path -LiteralPath $dataDir -PathType Container) {
    Get-ChildItem -LiteralPath $dataDir -Force | Remove-Item -Recurse -Force
}
else {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
}

New-Item -ItemType File -Path (Join-Path $dataDir '.gitkeep') -Force | Out-Null
Write-Host "Local data reset completed: $dataDir"
