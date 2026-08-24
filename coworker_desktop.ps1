# Coworker Desktop - Windows dev launcher (PowerShell).
# Usage:
#   .\coworker_desktop.ps1                  # build frontend + start backend + launch desktop
#   $env:COWORKER_SKIP_DESKTOP = "1"
#   .\coworker_desktop.ps1                  # backend only (testing mode)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendPort = if ($env:COWORKER_BACKEND_PORT) { $env:COWORKER_BACKEND_PORT } else { "9527" }

Write-Host "=== Coworker Desktop ===" -ForegroundColor Cyan

Write-Host "[0/6] Releasing port $BackendPort..."
$listeners = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listeners) {
  $proc = Get-Process -Id $l.OwningProcess -ErrorAction SilentlyContinue
  if ($proc) {
    Write-Host "  Releasing port $BackendPort held by $($proc.ProcessName) (pid $($proc.Id))"
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  }
}
Write-Host "  OK"

Write-Host "[1/6] Preparing Python backend..."
if (Test-Path "$RootDir\backend\venv\Scripts\python.exe") {
  $BackendPy = "$RootDir\backend\venv\Scripts\python.exe"
} else {
  $BackendPy = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $BackendPy) { throw "No python found. Create a venv at backend\venv first." }
}

Write-Host "[2/6] Preparing Node dependencies..."
Write-Host "  OK"

Write-Host "[3/6] Building frontend..."
Push-Location "$RootDir\frontend"
try { npm run build | Out-Host } finally { Pop-Location }
Write-Host "  OK"

Write-Host "[4/6] Starting backend..."
$Backend = Start-Process -FilePath $BackendPy -ArgumentList "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", $BackendPort, "--app-dir", "$RootDir\backend" -WorkingDirectory $RootDir -PassThru -WindowStyle Hidden

$backendReady = $false
for ($i = 0; $i -lt 80; $i++) {
  if ($Backend.HasExited) {
    throw "Backend process exited unexpectedly."
  }
  try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$BackendPort/health" -TimeoutSec 2 -ErrorAction Stop
    if ($resp.StatusCode -eq 200) { $backendReady = $true; break }
  } catch { Start-Sleep -Milliseconds 250 }
}
if (-not $backendReady) { throw "Backend did not become ready on port $BackendPort within timeout." }
Write-Host "  OK - backend ready on 127.0.0.1:$BackendPort"

Write-Host "[5/6] Launching desktop..."
if ($env:COWORKER_SKIP_DESKTOP -eq "1") {
  Write-Host "  skipped (testing mode). Backend stays on 127.0.0.1:$BackendPort."
  Write-Host "  Press Ctrl+C to stop the backend."
  try { $Backend.WaitForExit() } finally { Stop-Process -Id $Backend.Id -Force -ErrorAction SilentlyContinue }
  exit 0
}

$env:COWORKER_BACKEND_HOST = "127.0.0.1"
$env:COWORKER_BACKEND_PORT = $BackendPort
$env:COWORKER_DEV = "1"
try {
  Push-Location $RootDir
  try { npm run desktop | Out-Host } finally { Pop-Location }
} finally {
  Stop-Process -Id $Backend.Id -Force -ErrorAction SilentlyContinue
}

Write-Host "Coworker Desktop stopped" -ForegroundColor Bold
