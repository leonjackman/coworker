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
$venvPy = "$RootDir\backend\venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  Write-Host "  Python venv missing at backend\venv - creating it"
  python -m venv "$RootDir\backend\venv" -ErrorAction Stop
}
$venvPy = "$RootDir\backend\venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { throw "Failed to create Python venv at backend\venv." }
# (Re)install requirements when the venv binary is older than requirements.txt.
$reqFile = "$RootDir\backend\requirements.txt"
if ((Test-Path $reqFile) -and ((Get-Item $venvPy).LastWriteTime -lt (Get-Item $reqFile).LastWriteTime)) {
  Write-Host "  Python dependencies out of date - installing $reqFile"
  & $venvPy -m pip install --upgrade pip
  if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
  & $venvPy -m pip install -r $reqFile
  if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
} else {
  Write-Host "  Python venv ready"
}
$BackendPy = $venvPy

Write-Host "[2/6] Preparing Node dependencies..."
function Test-NodeTreeComplete {
  param([string]$Dir)
  if (-not (Test-Path "$Dir\node_modules")) { return $false }
  $manifest = Get-Content "$Dir\package.json" -Raw | ConvertFrom-Json
  $deps = @{}
  if ($manifest.dependencies) { $manifest.dependencies.PSObject.Properties | ForEach-Object { $deps[$_.Name] = $_.Value } }
  if ($manifest.devDependencies) { $manifest.devDependencies.PSObject.Properties | ForEach-Object { $deps[$_.Name] = $_.Value } }
  foreach ($name in $deps.Keys) {
    if (-not (Test-Path "$Dir\node_modules\$name")) { return $false }
  }
  return $true
}
foreach ($dir in @("$RootDir", "$RootDir\frontend")) {
  $what = if ($dir -eq $RootDir) { "Root dependencies" } else { "Frontend dependencies" }
  if (Test-NodeTreeComplete -Dir $dir) {
    Write-Host "  $what ready"
  } else {
    Write-Host "  $what incomplete - running npm install"
    Push-Location $dir
    try { npm install | Out-Host; if ($LASTEXITCODE -ne 0) { throw "npm install failed in $dir" } } finally { Pop-Location }
    if (-not (Test-NodeTreeComplete -Dir $dir)) { throw "$what still incomplete after npm install." }
  }
}

Write-Host "[3/6] Building frontend..."
Push-Location "$RootDir\frontend"
try { npm run build | Out-Host; if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" } } finally { Pop-Location }
Write-Host "  OK"

Write-Host "[4/6] Starting backend..."
# Persistent LLM request logging (messages + tools + sampling params → data_dir/llm-requests.log).
# On by default so every launch captures the exact bodies CW sends — useful for
# diagnosing tool-call / degradation issues without having to set an env var.
$env:COWORKER_LLM_LOG = if ($env:COWORKER_LLM_LOG) { $env:COWORKER_LLM_LOG } else { "1" }
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
