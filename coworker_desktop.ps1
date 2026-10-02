# Coworker Desktop - Windows dev launcher (PowerShell).
# Mirrors coworker_desktop.command / coworker_desktop.sh.
# Usage:
#   .\coworker_desktop.ps1                  # build frontend + start backend + launch desktop
#   $env:COWORKER_SKIP_DESKTOP = "1"; .\coworker_desktop.ps1   # backend only (testing mode)
#   $env:COWORKER_FORCE_BUILD = "1"; .\coworker_desktop.ps1    # force a frontend rebuild

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendPort = if ($env:COWORKER_BACKEND_PORT) { $env:COWORKER_BACKEND_PORT } else { "9527" }
# Call npm.cmd explicitly: bare `npm` resolves to npm.ps1, which the default
# PowerShell execution policy blocks.
$Npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $Npm) { $Npm = "npm.cmd" }

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
$VenvDir = "$RootDir\backend\venv"
$venvPy = "$VenvDir\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  Write-Host "  Python venv missing at backend\venv - creating it"
  & python -m venv $VenvDir
  if ($LASTEXITCODE -ne 0) { throw "Failed to create Python venv at backend\venv." }
}
$venvPy = "$VenvDir\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { throw "Failed to create Python venv at backend\venv." }
# Reinstall requirements when requirements.txt is newer than the stamp touched
# after the last successful install (mirrors py_venv_stale in scripts/check-deps.sh).
$reqFile = "$RootDir\backend\requirements.txt"
$venvStamp = "$VenvDir\.requirements-stamp"
$venvStale = $true
if (Test-Path $venvStamp) {
  $venvStale = (Test-Path $reqFile) -and ((Get-Item $reqFile).LastWriteTime -gt (Get-Item $venvStamp).LastWriteTime)
}
if ($venvStale) {
  Write-Host "  Python dependencies out of date - installing $reqFile"
  & $venvPy -m pip install --upgrade pip
  if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
  & $venvPy -m pip install -r $reqFile
  if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
  New-Item -ItemType File -Path $venvStamp -Force | Out-Null
} else {
  Write-Host "  Python venv ready"
}
$BackendPy = $venvPy

Write-Host "[2/6] Preparing Node dependencies..."
# Mirrors node_tree_complete / node_tree_stale in scripts/check-deps.sh.
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

function Test-NodeTreeStale {
  param([string]$Dir)
  $stamp = "$Dir\node_modules\.package-lock.json"
  $manifest = "$Dir\package.json"
  $lockfile = "$Dir\package-lock.json"
  if (-not (Test-Path "$Dir\node_modules")) { return $true }
  if (-not (Test-Path $manifest)) { return $true }
  if (-not (Test-Path $stamp)) { return $true }
  if ((Test-Path $lockfile) -and ((Get-Item $lockfile).LastWriteTime -gt (Get-Item $stamp).LastWriteTime)) { return $true }
  if ((Get-Item $manifest).LastWriteTime -gt (Get-Item $stamp).LastWriteTime) { return $true }
  if (-not (Test-NodeTreeComplete -Dir $Dir)) { return $true }
  return $false
}

foreach ($dir in @("$RootDir", "$RootDir\frontend")) {
  $what = if ($dir -eq $RootDir) { "Root dependencies" } else { "Frontend dependencies" }
  if (-not (Test-NodeTreeStale -Dir $dir)) {
    Write-Host "  $what ready"
  } else {
    Write-Host "  $what out of date - running npm install"
    Push-Location $dir
    try { & $Npm install | Out-Host; if ($LASTEXITCODE -ne 0) { throw "npm install failed in $dir" } } finally { Pop-Location }
    if (Test-NodeTreeStale -Dir $dir) { throw "$what still incomplete after npm install." }
  }
}

# Rebuild the frontend only when its inputs changed since the last build
# (mirrors frontend_needs_build in coworker_desktop.command / .sh).
function Test-FrontendNeedsBuild {
  if ($env:COWORKER_FORCE_BUILD -eq "1") { return $true }
  $entry = "$RootDir\frontend\dist\index.html"
  if (-not (Test-Path $entry)) { return $true }
  $entryTime = (Get-Item $entry).LastWriteTime
  foreach ($src in @(
    "$RootDir\frontend\package.json",
    "$RootDir\frontend\package-lock.json",
    "$RootDir\frontend\vite.config.ts",
    "$RootDir\frontend\index.html"
  )) {
    if ((Test-Path $src) -and ((Get-Item $src).LastWriteTime -gt $entryTime)) { return $true }
  }
  $newer = Get-ChildItem "$RootDir\frontend\src" -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -gt $entryTime } | Select-Object -First 1
  if ($newer) { return $true }
  return $false
}

if (Test-FrontendNeedsBuild) {
  Write-Host "[3/6] Building frontend..."
  Push-Location "$RootDir\frontend"
  try { & $Npm run build | Out-Host; if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" } } finally { Pop-Location }
  Write-Host "  OK"
} else {
  Write-Host "[3/6] Frontend up to date - skipping build (COWORKER_FORCE_BUILD=1 to rebuild)"
  Write-Host "  OK"
}

Write-Host "[4/6] Starting backend..."
# Persistent LLM request logging (messages + tools + sampling params -> data_dir/llm-requests.log).
# On by default so every launch captures the exact bodies CW sends - useful for
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

# Recursively terminate a process and all of its descendants - the desktop
# process (npm cmd shim -> node -> electron helpers) must all die together.
function Stop-ProcessTree {
  param([int]$TargetPid)
  $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$TargetPid" -ErrorAction SilentlyContinue)
  foreach ($child in $children) {
    Stop-ProcessTree -TargetPid $child.ProcessId
  }
  Stop-Process -Id $TargetPid -Force -ErrorAction SilentlyContinue
}

Write-Host "[5/6] Launching desktop..."
if ($env:COWORKER_SKIP_DESKTOP -eq "1") {
  Write-Host "  skipped (testing mode). Backend stays on 127.0.0.1:$BackendPort."
  Write-Host "  Press Ctrl+C to stop the backend."
  try { $Backend.WaitForExit() } finally { Stop-ProcessTree -TargetPid $Backend.Id }
  exit 0
}

$env:COWORKER_BACKEND_HOST = "127.0.0.1"
$env:COWORKER_BACKEND_PORT = $BackendPort
$env:COWORKER_DEV = "1"

# --ignore-scripts skips the `predesktop` npm hook, which points at a bash
# script (scripts/check-node-deps.sh) that cmd.exe cannot run on Windows. The
# equivalent dependency check already ran in step [2/6].
$Desktop = Start-Process -FilePath $Npm -ArgumentList "run", "desktop", "--ignore-scripts" -WorkingDirectory $RootDir -PassThru -NoNewWindow
try {
  # Mirror the mac/Linux monitor_backend: if the backend dies, take the desktop
  # down with it instead of leaving a window pointed at a dead server.
  while (-not $Desktop.HasExited) {
    if ($Backend.HasExited) {
      Write-Host "  Backend process exited; stopping desktop." -ForegroundColor Red
      Stop-ProcessTree -TargetPid $Desktop.Id
      break
    }
    Start-Sleep -Milliseconds 500
  }
  $Desktop.WaitForExit()
} finally {
  Stop-ProcessTree -TargetPid $Backend.Id
}

Write-Host "Coworker Desktop stopped" -ForegroundColor Bold
