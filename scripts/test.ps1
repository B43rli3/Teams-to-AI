# PowerShell test script for Windows
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "FEHLER: Virtuelle Umgebung nicht gefunden." -ForegroundColor Red
    exit 1
}

Write-Host "Fuehre Tests aus..." -ForegroundColor Green
& $VenvPython -m pytest tests/ -v --tb=short
exit $LASTEXITCODE
