param(
  [ValidateSet('Open','Status','Probe')][string]$Action='Open',
  [string]$ProjectPath=(Join-Path ([Environment]::GetFolderPath('Desktop')) '快讯AI看板 WorkBuddy版')
)
$ErrorActionPreference='Stop'
$taskRoot=(Resolve-Path -LiteralPath $ProjectPath).Path
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'WORKBUDDY_ORIGIN.json'))) { throw '所选路径不是 WorkBuddy 看板项目。' }
$taskPython=Join-Path $taskRoot 'runtime/python/python.exe'
if ($Action -eq 'Open') {
  $taskLauncher=Join-Path $taskRoot 'tools/open_dashboard.py'
  $taskWindowless=Join-Path $taskRoot 'runtime/python/pythonw.exe'
  Start-Process -FilePath $taskWindowless -ArgumentList ('"'+$taskLauncher+'"') -WorkingDirectory $taskRoot -WindowStyle Hidden
} elseif ($Action -eq 'Status') {
  & $taskPython -X utf8 (Join-Path $taskRoot 'tools/workbuddy_connect.py')
} else {
  & $taskPython -X utf8 (Join-Path $taskRoot 'tools/workbuddy_connect.py') --probe
}
