# Run from PowerShell after dependencies and .env have been set up.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$occupied = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners().Port
if (8001 -in $occupied -or 5173 -in $occupied) {
    throw 'Ports 8001 and 5173 must be free. Stop the existing preview first.'
}
$python = Join-Path $pwd 'backend/.venv/Scripts/python.exe'
& $python -m alembic -c backend/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
& $python -m lead_agent.seed --demo
if ($LASTEXITCODE -ne 0) { throw 'Demo seed failed.' }
$logs = New-Item -ItemType Directory -Path runtime-logs -Force
$api = Start-Process -FilePath $python -ArgumentList '-m uvicorn lead_agent.api.main:app --reload --reload-dir backend/src --host 127.0.0.1 --port 8001 --no-access-log' -WorkingDirectory $pwd -WindowStyle Hidden -RedirectStandardOutput "$logs/api.log" -RedirectStandardError "$logs/api-error.log" -PassThru
try {
    $worker = Start-Process -FilePath $python -ArgumentList '-m lead_agent.worker.operations' -WorkingDirectory $pwd -WindowStyle Hidden -RedirectStandardOutput "$logs/operations.log" -RedirectStandardError "$logs/operations-error.log" -PassThru
    $web = Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList './node_modules/vite/bin/vite.js --configLoader runner --host 127.0.0.1 --port 5173 --strictPort' -WorkingDirectory (Join-Path $pwd 'frontend') -WindowStyle Hidden -RedirectStandardOutput "$logs/frontend.log" -RedirectStandardError "$logs/frontend-error.log" -PassThru
} catch {
    taskkill.exe /PID $api.Id /T /F | Out-Null
    if ($worker) { taskkill.exe /PID $worker.Id /T /F | Out-Null }
    throw
}
@($api.Id, $web.Id, $worker.Id) | Set-Content "$logs/local-dev.pids"
Write-Output 'Preview: http://localhost:5173 | API: http://localhost:8001/docs'
Write-Output 'Choose Continue to local workspace. Source edits reload automatically.'
Write-Output 'Stop: Get-Content runtime-logs/local-dev.pids | ForEach-Object { taskkill.exe /PID $_ /T /F }'