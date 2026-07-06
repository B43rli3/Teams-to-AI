# PowerShell start script for Windows
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not (Test-Path $VenvPython)) {
    Write-Host "FEHLER: Virtuelle Umgebung nicht gefunden." -ForegroundColor Red
    Write-Host "Bitte erstellen Sie die venv mit:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\pip install -e `".[dev]`"" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $EnvFile)) {
    Write-Host "FEHLER: .env-Datei nicht gefunden." -ForegroundColor Red
    Write-Host "Bitte kopieren Sie .env.example nach .env und tragen Sie Ihre Werte ein:" -ForegroundColor Yellow
    Write-Host "  Copy-Item .env.example .env" -ForegroundColor Yellow
    exit 1
}

Write-Host "Starte Teams Local LLM..." -ForegroundColor Green

& $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8080
