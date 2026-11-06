param([string]$PythonPath = '.venv\Scripts\python.exe')

$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw 'Run Setup.cmd and install requirements-build.txt into .venv before building.'
    }
    $python = (Resolve-Path -LiteralPath $PythonPath).Path
    & $python -c "import platform, sys; sys.exit(not (platform.system() == 'Windows' and platform.machine().upper() == 'AMD64' and (3, 14, 10) <= sys.version_info < (3, 15)))"
    if ($LASTEXITCODE -ne 0) { throw 'Build with x64 Python 3.14.10 or a later 3.14 patch release.' }
    foreach ($path in @('build', 'dist', 'dist\Colour Shifter')) {
        $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Use a regular directory for $path."
        }
    }
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
