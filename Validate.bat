@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto no_python
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto failed
set "OBS_PYTHON=%~dp0.venv\Scripts\python.exe"
"%OBS_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto failed
"%OBS_PYTHON%" -m unittest discover -s tests -v
if errorlevel 1 goto failed
"%OBS_PYTHON%" -m observatory build --offline
if errorlevel 1 goto failed
"%OBS_PYTHON%" -m observatory validate public/api/v2
if errorlevel 1 goto failed
where node >nul 2>nul
if errorlevel 1 goto no_node
node --test clients/tests/reader.test.mjs
if errorlevel 1 goto failed
echo.
echo PASS: offline registry, evidence, scoring, matching and client tests.
echo Generated table: %~dp0public
pause
exit /b 0
:no_node
echo Node.js 24 or newer is required for the Camelot reader tests.
pause
exit /b 1
:no_python
echo Python 3.11 or newer is required.
pause
exit /b 1
:failed
echo FAIL: see the error above.
pause
exit /b 1
