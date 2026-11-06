@echo off
setlocal
pushd "%~dp0" || exit /b 1

if not exist ".venv\Scripts\python.exe" goto setup
if not exist "models\birefnet-lite-512.onnx" goto setup
".venv\Scripts\python.exe" -c "import sys; sys.exit(not ((3, 14, 10) <= sys.version_info < (3, 15)))"
if errorlevel 1 goto setup

".venv\Scripts\python.exe" -m colour_shifter
set "colour_shifter_exit=%errorlevel%"
popd
if not "%colour_shifter_exit%"=="0" pause
exit /b %colour_shifter_exit%

:setup
echo Run Setup.cmd once before opening Colour Shifter.
popd
pause
exit /b 1
