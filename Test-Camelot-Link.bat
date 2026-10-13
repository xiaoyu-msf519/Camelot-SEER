@echo off
setlocal
cd /d "%~dp0"
set "CAM_LINK_SRC=%~1"
if not defined CAM_LINK_SRC set "CAM_LINK_SRC=%~dp0..\src"
if not exist "%CAM_LINK_SRC%\packages\camelot\provider-hub\src\registry.ts" goto missing
set "CAM_LINK_SCRIPT=%~dp0scripts\test-camelot-link.mjs"
set "CAM_BOOT_SCRIPT=%~dp0scripts\test-camelot-boot.mjs"
pushd "%CAM_LINK_SRC%"
node --import tsx/esm "%CAM_LINK_SCRIPT%" .
if errorlevel 1 goto failed
node --import tsx/esm "%CAM_BOOT_SCRIPT%" .
if errorlevel 1 goto failed
popd
echo PASS: real Pages feed, Camelot registry, routing guards and Core background sync.
pause
exit /b 0
:failed
popd
echo FAIL: inspect the output above. The network deadline remains enforced.
pause
exit /b 1
:missing
echo Camelot source checkout is required. Pass its path as the first argument.
pause
exit /b 1
