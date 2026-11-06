$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Run Setup.cmd and install requirements-build.txt into .venv before building.'
    }
    & $python -c "import platform; assert platform.system() == 'Windows' and platform.machine().upper() == 'AMD64', 'Build on x64 Windows with x64 Python.'"
    if ($LASTEXITCODE -ne 0) { throw 'The build requires x64 Windows and x64 Python.' }
    & $python -m colour_shifter.model
    if ($LASTEXITCODE -ne 0) { throw 'Model verification failed.' }
    & $python -m PyInstaller --noconfirm packaging/colour-shifter.spec
    if ($LASTEXITCODE -ne 0) { throw 'Could not package Colour Shifter.' }

    $archiveName = 'Colour-Shifter-Windows-x64.zip'
    $archive = Join-Path 'dist' $archiveName
    & $python -m zipfile -c $archive 'dist/Colour Shifter'
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the release ZIP.' }
    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $archiveName" | Set-Content -LiteralPath "$archive.sha256" -Encoding ascii
    Write-Output "Created $archive"
}
finally {
    Pop-Location
}
