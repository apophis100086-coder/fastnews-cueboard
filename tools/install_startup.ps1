param(
    [switch]$Disable,
    [switch]$Status
)
$ErrorActionPreference = 'Stop'
if ($Disable -and $Status) { throw 'Choose either -Disable or -Status.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcherPath = Join-Path $projectRoot 'tools\open_dashboard.py'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$consolePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$startupPath = Join-Path ([Environment]::GetFolderPath('Startup')) '华泰柏瑞快讯看板.lnk'
$desktopPath = Join-Path ([Environment]::GetFolderPath('Desktop')) '华泰柏瑞快讯看板.lnk'
$owner = 'Fastnews Cueboard | ' + $projectRoot
if (-not ('Fastnews.WindowsShortcut' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot 'WindowsShortcut.cs')
}

function Assert-OwnedShortcut([string]$path) {
    if (Test-Path -LiteralPath $path) {
        $existing = [Fastnews.WindowsShortcut]::Read($path)
        if ($existing.Description -ne $owner) {
            throw "An unrelated shortcut already exists; left unchanged: $path"
        }
    }
}

function Test-Shortcut([string]$path, [string]$arguments) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $link = [Fastnews.WindowsShortcut]::Read($path)
    return ($link.Description -eq $owner -and $link.TargetPath -eq $pythonPath -and
        $link.Arguments -eq $arguments -and $link.WorkingDirectory -eq $projectRoot)
}

function Save-Shortcut([string]$path, [string]$arguments) {
    [Fastnews.WindowsShortcut]::Save($path, $pythonPath, $arguments, $projectRoot, $owner)
}

$startupArguments = '"' + $launcherPath + '" --serve'
$desktopArguments = '"' + $launcherPath + '"'
if ($Disable) {
    Assert-OwnedShortcut $startupPath
    if (Test-Path -LiteralPath $startupPath) { Remove-Item -LiteralPath $startupPath }
} elseif (-not $Status) {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'Create .venv and install requirements.lock before enabling startup.'
    }
    & $consolePython -c 'import yaml, tzdata'
    if ($LASTEXITCODE -ne 0) { throw 'The project Python runtime is missing dependencies.' }
    Assert-OwnedShortcut $startupPath
    Assert-OwnedShortcut $desktopPath
    Save-Shortcut $desktopPath $desktopArguments
    Save-Shortcut $startupPath $startupArguments
}

[ordered]@{
    startup_installed = (Test-Shortcut $startupPath $startupArguments)
    desktop_installed = (Test-Shortcut $desktopPath $desktopArguments)
    runtime_exists = (Test-Path -LiteralPath $pythonPath)
    startup_path = $startupPath
    desktop_path = $desktopPath
    python = $pythonPath
    arguments = $startupArguments
    url = 'http://127.0.0.1:8765/'
} | ConvertTo-Json
