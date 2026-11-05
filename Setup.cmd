@echo off
setlocal
pushd "%~dp0" || exit /b 1

if not exist ".venv\Scripts\python.exe" (
    py -3.11 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m colour_shifter.model
if errorlevel 1 goto failed

echo Setup complete. Open Start.cmd to run Colour Shifter.
popd
pause
exit /b 0

:failed
echo Setup failed. Check that 64-bit Python 3.11 and an internet connection are available.
popd
pause
exit /b 1
