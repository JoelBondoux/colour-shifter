@echo off
setlocal
pushd "%~dp0" || exit /b 1

if not exist ".venv\Scripts\python.exe" (
    py -3.14 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(not ((3, 14, 10) <= sys.version_info < (3, 15)))"
if errorlevel 1 goto runtime
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m colour_shifter.model
if errorlevel 1 goto failed

echo Setup complete. Open Start.cmd to run Colour Shifter.
popd
pause
exit /b 0

:failed
echo Setup failed. Check that x64 Python 3.14.10 or later in the 3.14 series and an internet connection are available.
popd
pause
exit /b 1

:runtime
echo This .venv uses an older Python runtime. Move it aside and rerun Setup.cmd with x64 Python 3.14.10 or later in the 3.14 series.
popd
pause
exit /b 1
