# pack-local-win.ps1 — local Windows packaging (test build). Mirrors pack-local.command.
# Builds the frontend + bundled Python backend + native helper, then produces an
# UNPACKED CoWorker app via electron-builder --dir (no installer), and opens it.
#
# Usage (via pack-local-win.bat):
#   pack-local-win.bat                                     # full build + open
#   pack-local-win.bat --no-open                           # build but don't launch
#   pack-local-win.bat --skip-frontend --skip-backend --skip-helper
#   pack-local-win.bat --clean                             # wipe release/ first
#
# Local builds are UNSIGNED (code signing is done by CI release.yml).

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $Npm) { $Npm = "npm.cmd" }

$NoOpen        = $args -contains "--no-open"
$SkipFrontend  = $args -contains "--skip-frontend"
$SkipBackend   = $args -contains "--skip-backend"
$SkipHelper    = $args -contains "--skip-helper"
$Clean         = $args -contains "--clean"

function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!] $m"  -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [X] $m"  -ForegroundColor Red; exit 1 }

function Stop-RunningApp() {
  # A running app locks its files under release\win-unpacked, which makes
  # electron-builder fail with "EPERM: unlink ...pybackend.exe". Terminate the
  # app, its bundled backend and the native helper first, and free the API port.
  $any = $false
  foreach ($img in 'CoWorker', 'pybackend', 'cwautoma-win') {
    Get-Process -Name $img -ErrorAction SilentlyContinue | ForEach-Object {
      $any = $true
      Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
  }
  Get-NetTCPConnection -LocalPort 9527 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    $any = $true
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
  }
  if ($any) { Start-Sleep -Milliseconds 800 }
  return $any
}

Write-Host "=== CoWorker Local Package (win --dir) ===" -ForegroundColor Cyan

if ($Clean -and (Test-Path "$RootDir\release")) {
  Write-Host "[0/5] Cleaning release/ output..."
  Remove-Item -Recurse -Force "$RootDir\release"
  Ok "release/ removed"
}

# ── dependencies: Python venv + node_modules (root + frontend) ──────────────
$VenvDir = "$RootDir\backend\venv"
$Py = "$VenvDir\Scripts\python.exe"
$Req = "$RootDir\backend\requirements.txt"
$Stamp = "$VenvDir\.requirements-stamp"
if (-not (Test-Path $Py)) {
  Write-Host "  Python venv missing - creating it"
  & python -m venv $VenvDir
  if ($LASTEXITCODE -ne 0) { Fail "failed to create venv at $VenvDir" }
}
$VenvStale = $true
if (Test-Path $Stamp) {
  $VenvStale = (Test-Path $Req) -and ((Get-Item $Req).LastWriteTime -gt (Get-Item $Stamp).LastWriteTime)
}
if ($VenvStale) {
  Write-Host "  Python dependencies out of date - installing $Req"
  & $Py -m pip install --upgrade pip; if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed" }
  & $Py -m pip install -r $Req;         if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
  New-Item -ItemType File -Path $Stamp -Force | Out-Null
} else {
  Ok "Python venv ready"
}

foreach ($dir in @("$RootDir", "$RootDir\frontend")) {
  if (-not (Test-Path "$dir\node_modules")) {
    Write-Host "  Node dependencies missing - npm install in $dir"
    Push-Location $dir
    try { & $Npm install; if ($LASTEXITCODE -ne 0) { Fail "npm install failed in $dir" } }
    finally { Pop-Location }
  } else {
    Ok "node_modules ready ($dir)"
  }
}

# ── [1] frontend ────────────────────────────────────────────────────────────
if ($SkipFrontend) {
  Write-Host "[1/5] Skipping frontend build (reusing frontend/dist)"
} else {
  Write-Host "[1/5] Building frontend..."
  Push-Location "$RootDir\frontend"
  try { & $Npm run build; if ($LASTEXITCODE -ne 0) { Fail "frontend build failed" } }
  finally { Pop-Location }
  Ok "Frontend built"
}

# ── [2] bundled Python backend (PyInstaller) ────────────────────────────────
if ($SkipBackend) {
  Write-Host "[2/5] Skipping backend build (reusing backend/dist)"
} else {
  Write-Host "[2/5] Building Python backend (PyInstaller)..."
  if (-not (Test-Path "$VenvDir\Lib\site-packages\PyInstaller")) {
    Warn "PyInstaller missing in venv - installing it"
    & $Py -m pip install pyinstaller; if ($LASTEXITCODE -ne 0) { Fail "PyInstaller install failed" }
  }
  Push-Location "$RootDir\backend"
  try {
    # Pre-build gate: prove the document stack imports before freezing.
    & $Py -c "from coworker.documents.selfcheck import run_documents_selfcheck; raise SystemExit(run_documents_selfcheck())"
    if ($LASTEXITCODE -ne 0) { Fail "Document dependency self-check failed (pip install -r backend/requirements.txt)" }
    Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
    & $Py -m PyInstaller --clean --noconfirm pybackend.spec
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed" }
  } finally { Pop-Location }
  Ok "Backend bundled"

  # Post-build gate: the FROZEN binary must carry its bundled data + native libs.
  $Frozen = "$RootDir\backend\dist\pybackend.exe"
  if (-not (Test-Path $Frozen)) { Fail "Frozen backend not found at backend\dist\pybackend.exe" }
  & $Frozen --selfcheck-documents
  if ($LASTEXITCODE -ne 0) { Fail "Frozen backend document self-check failed (missing bundled data/native lib)" }
  Ok "Frozen backend verified"
}

# ── [3] native computer-use helper ──────────────────────────────────────────
if ($SkipHelper) {
  Write-Host "[3/5] Skipping helper build (reusing electron/cw-automa-win/bin)"
} else {
  Write-Host "[3/5] Building native helper (cwautoma-win)..."
  if (Get-Process -Name cwautoma-win -ErrorAction SilentlyContinue) {
    Warn "a running cwautoma-win holds the exe - stopping it before rebuild"
    Get-Process -Name cwautoma-win -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
  }
  & $Npm run build:automa-win
  if ($LASTEXITCODE -ne 0) { Fail "native helper build failed (is the app still running? close it)" }
  if (-not (Test-Path "$RootDir\electron\cw-automa-win\bin\cwautoma-win.exe")) { Fail "cwautoma-win.exe not produced" }
  Ok "Native helper built"
}

# ── [4] package (electron-builder --dir, unpacked) ──────────────────────────
Write-Host "[4/5] Packaging app (electron-builder --win --dir)..."
$EB = "$RootDir\node_modules\.bin\electron-builder.cmd"
if (-not (Test-Path $EB)) { Fail "electron-builder not found (run npm install at repo root)" }
if (Stop-RunningApp) { Ok "stopped a running CoWorker instance (freed locked files)" }
Push-Location $RootDir
try {
  $attempt = 0
  while ($true) {
    $attempt++
    & $EB --win --config electron-builder.config.json --dir
    if ($LASTEXITCODE -eq 0) { break }
    if ($attempt -ge 2) { Fail "electron-builder failed" }
    Warn "electron-builder failed (attempt $attempt) - stopping running app and retrying"
    Stop-RunningApp | Out-Null
    Start-Sleep -Seconds 1
  }
} finally { Pop-Location }
Ok "Package created"

# ── [5] locate + open ───────────────────────────────────────────────────────
$AppExe = Get-ChildItem "$RootDir\release\win-unpacked" -Filter "CoWorker.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $AppExe) {
  Warn "Could not locate release\win-unpacked\CoWorker.exe - not launching."
  exit 0
}
Write-Host "[5/5] Result: $($AppExe.FullName)"
if ($NoOpen) {
  Write-Host "  Skipped launch (--no-open)."
  exit 0
}

# Fully terminate any running CoWorker (app + helper + bundled backend) BEFORE
# opening the fresh build, so Windows cannot reactivate the OLD (stale) binary.
Stop-RunningApp | Out-Null
Start-Process -FilePath $AppExe.FullName
Ok "Launched CoWorker (fresh build)"

Write-Host ""
Write-Host "Done" -ForegroundColor Green
