<#
.SYNOPSIS
    Build Craveable: virtual environment, dependencies, tests, single-file executable.
.EXAMPLE
    .\scripts\build.ps1            # full build -> dist\craveable.exe
    .\scripts\build.ps1 -SkipTests
    .\scripts\build.ps1 -NoExe     # install + test only
#>
param(
    [switch]$SkipTests,
    [switch]$NoExe
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "==> creating virtual environment (.venv)" -ForegroundColor Cyan
    python -m venv .venv
}
Write-Host "==> installing craveable and dev dependencies" -ForegroundColor Cyan
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -e ".[dev]"

if (-not $SkipTests) {
    Write-Host "==> running tests" -ForegroundColor Cyan
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

if (-not $NoExe) {
    Write-Host "==> packaging dist\craveable.exe with PyInstaller" -ForegroundColor Cyan
    & $venvPython -m PyInstaller --noconfirm --clean --onefile --console --name craveable `
        --add-data "craveable\data;craveable\data" `
        --exclude-module matplotlib --exclude-module PIL --exclude-module tkinter `
        --exclude-module scipy --exclude-module IPython --exclude-module anthropic --exclude-module pydantic `
        scripts\entry.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    Write-Host "==> built $(Resolve-Path dist\craveable.exe)" -ForegroundColor Green
    & .\dist\craveable.exe --version
}
