@echo off
REM CoWorker - local Windows packaging launcher.
REM Double-click this file (or run it from a terminal) to build an UNPACKED
REM Windows app locally (frontend + bundled backend + native helper + electron),
REM then open it. Mirrors the macOS pack-local.command.
REM
REM It simply invokes pack-local-win.ps1 with the execution policy bypassed,
REM because the default PowerShell policy blocks local .ps1 scripts.
REM
REM Usage:
REM   pack-local-win.bat                 # full build + open
REM   pack-local-win.bat --no-open       # build but don't launch
REM   pack-local-win.bat --skip-frontend --skip-backend --skip-helper
REM   pack-local-win.bat --clean         # wipe release/ first

setlocal
cd /d "%~dp0"

set "PS1=%~dp0pack-local-win.ps1"
if not exist "%PS1%" (
  echo pack-local-win.ps1 not found next to this launcher.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo Pack exited with code %EXITCODE%.
  pause
)

endlocal & exit /b %EXITCODE%
