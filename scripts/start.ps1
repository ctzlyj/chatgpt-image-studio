param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $root
$python = Join-Path $root '.venv\Scripts\python.exe'
$address = "http://127.0.0.1:$Port"
try {
    $status = Invoke-RestMethod -Uri "$address/api/bootstrap" -TimeoutSec 2
    if ($status.application -eq 'chatgpt-image-studio') {
        Write-Host "Already running: $address"
        if (-not $NoBrowser) { Start-Process $address }
        exit 0
    }
    throw 'PORT_ALREADY_USED'
} catch {
    if ($_.Exception.Message -eq 'PORT_ALREADY_USED') { throw 'This port is used by another application. Use -Port with a different port.' }
}
if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22 or newer, then run Start.cmd again.' }
if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $launcher) { throw 'Install Python 3.12 or newer, then run Start.cmd again.' }
    & $launcher.Source (Join-Path $root 'scripts\check_runtime.py') --version-only
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required.' }
    & $launcher.Source -m venv (Join-Path $root '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create Python environment.' }
}
& $python (Join-Path $root 'scripts\check_runtime.py')
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r (Join-Path $root 'requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the network and retry.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $root 'dist\index.html'))) {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
$data = Join-Path $root 'data'
[System.IO.Directory]::CreateDirectory($data) | Out-Null
$process = Start-Process -FilePath $python -ArgumentList @(('"' + (Join-Path $root 'run.py') + '"'), '--port', $Port) -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $data 'server.log') -RedirectStandardError (Join-Path $data 'server-error.log') -PassThru
$process.Id | Set-Content -LiteralPath (Join-Path $data 'server.pid')
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) { throw 'The service exited. See data/server-error.log.' }
    try {
        $status = Invoke-RestMethod -Uri "$address/api/bootstrap" -TimeoutSec 2
        if ($status.application -eq 'chatgpt-image-studio') {
            Write-Host "Ready: $address"
            Write-Host 'Data stays in the local data folder. Run Stop.cmd to stop the service.'
            if (-not $NoBrowser) { Start-Process $address }
            exit 0
        }
    } catch {}
}
throw 'Startup did not become ready. Inspect data/server-error.log before retrying.'
