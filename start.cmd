@echo off
rem One-click launcher for Windows.  Double-click this file (or run start.cmd
rem from a terminal).  It delegates to run.ps1 with the execution policy
rem bypassed for this invocation only, so no system setting is changed.
setlocal
cd /d "%~dp0"

where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
)

if errorlevel 1 (
    echo.
    echo [docsgpt-mini] launcher exited with an error.
    pause
)
endlocal
