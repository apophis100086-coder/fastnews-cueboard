param(
    [switch]$Disable,
    [switch]$Status
)
$ErrorActionPreference = 'Stop'
if ($Disable -and $Status) { throw 'Choose either -Disable or -Status.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcherPath = Join-Path $projectRoot 'tools\open_dashboard.py'
$pythonPath = Join-Path $projectRoot 'runtime\python\pythonw.exe'
$consolePython = Join-Path $projectRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
    $consolePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
}
$startupPath = Join-Path ([Environment]::GetFolderPath('Startup')) ('华泰柏瑞快讯看板 '+(Split-Path -Leaf $projectRoot)+'.lnk')
$desktopPath = Join-Path ([Environment]::GetFolderPath('Desktop')) ('华泰柏瑞快讯看板 '+(Split-Path -Leaf $projectRoot)+'.lnk')
$owner = 'Fastnews WorkBuddy | ' + $projectRoot
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
        throw 'Set up the source environment or extract the complete release before enabling startup.'
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
    url = (& $consolePython -X utf8 -c 'import sys; sys.path.insert(0,sys.argv[1]); from tools.open_dashboard import URL; print(URL)' $projectRoot)
} | ConvertTo-Json
