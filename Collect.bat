@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto failed
set "OBS_PYTHON=%~dp0.venv\Scripts\python.exe"
"%OBS_PYTHON%" -m observatory build
if errorlevel 1 goto failed
"%OBS_PYTHON%" -m observatory validate dist/api/v1
if errorlevel 1 goto failed
echo.
echo PASS: live collection finished. Check dist/report.json for source fallbacks.
pause
exit /b 0
:failed
echo FAIL: run Validate.bat to install dependencies and verify the offline baseline.
pause
exit /b 1
