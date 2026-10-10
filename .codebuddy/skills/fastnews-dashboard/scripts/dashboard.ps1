param(
  [ValidateSet('Open','Status','Probe')][string]$Action='Open',
  [string]$ProjectPath=''
)
$ErrorActionPreference='Stop'
if (-not $ProjectPath) {
  $taskCandidate=Get-Item -LiteralPath $PSScriptRoot
  while ($taskCandidate -and -not (Test-Path -LiteralPath (Join-Path $taskCandidate.FullName 'WORKBUDDY_ORIGIN.json'))) { $taskCandidate=$taskCandidate.Parent }
  if (-not $taskCandidate) { throw '请明确指定项目目录，避免打开其他工作区。' }
  $ProjectPath=$taskCandidate.FullName
}
$taskRoot=(Resolve-Path -LiteralPath $ProjectPath).Path
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'WORKBUDDY_ORIGIN.json'))) { throw '所选路径不是 WorkBuddy 看板项目。' }
$taskPython=Join-Path $taskRoot 'runtime/python/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython=Join-Path $taskRoot '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $taskPython)) { throw '请先按 README.md 安装源码环境，或完整解压发行包。' }
if ($Action -eq 'Open') {
  $taskLauncher=Join-Path $taskRoot 'tools/open_dashboard.py'
  $taskWindowless=Join-Path (Split-Path -Parent $taskPython) 'pythonw.exe'
  Start-Process -FilePath $taskWindowless -ArgumentList ('"'+$taskLauncher+'"') -WorkingDirectory $taskRoot -WindowStyle Hidden
} elseif ($Action -eq 'Status') {
  & $taskPython -X utf8 (Join-Path $taskRoot 'tools/agent_bridge.py') status
} else {
  & $taskPython -X utf8 (Join-Path $taskRoot 'tools/open_dashboard.py') --entry
}
