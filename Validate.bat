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
"%OBS_PYTHON%" -m observatory validate dist/api/v1
if errorlevel 1 goto failed
echo.
echo PASS: offline registry, evidence, scoring, matching and client tests.
echo Generated data: %~dp0dist
pause
exit /b 0
:no_python
echo Python 3.11 or newer is required.
pause
exit /b 1
:failed
echo FAIL: see the error above.
pause
exit /b 1
