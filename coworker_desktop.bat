@echo off
REM Coworker Desktop - Windows one-click launcher.
REM Double-click this file (or run it from a terminal) to start the desktop app.
REM It simply invokes coworker_desktop.ps1 with the execution policy bypassed,
REM because the default PowerShell policy blocks local .ps1 scripts.

setlocal
cd /d "%~dp0"

set "PS1=%~dp0coworker_desktop.ps1"
if not exist "%PS1%" (
  echo coworker_desktop.ps1 not found next to this launcher.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo Coworker Desktop exited with code %EXITCODE%.
  pause
)

endlocal & exit /b %EXITCODE%
