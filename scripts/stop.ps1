$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pidFile = Join-Path $root 'data\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'No application PID file found.'; exit 0 }
$serverPid = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
if (-not $process) { Write-Host 'The application is already stopped.'; exit 0 }
$expectedExecutable = [System.IO.Path]::GetFullPath((Join-Path $root '.venv\Scripts\python.exe'))
$expectedScript = [System.IO.Path]::GetFullPath((Join-Path $root 'run.py'))
if ($process.ExecutablePath -ne $expectedExecutable -or -not $process.CommandLine.Contains($expectedScript)) { throw 'PID does not belong to this application; refusing to stop it.' }
Stop-Process -Id $serverPid
Remove-Item -LiteralPath $pidFile
Write-Host 'Stopped. Unfinished jobs will be marked interrupted on the next start; verify the ChatGPT website before retrying.'
