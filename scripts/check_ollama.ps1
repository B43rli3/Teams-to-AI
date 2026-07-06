# PowerShell script to check Ollama availability
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"

$OllamaUrl = "http://127.0.0.1:11434"
$OllamaModel = "qwen3:14b"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match "^OLLAMA_BASE_URL=(.+)$") {
            $OllamaUrl = $Matches[1].Trim()
        }
        if ($_ -match "^OLLAMA_MODEL=(.+)$") {
            $OllamaModel = $Matches[1].Trim()
        }
    }
}

Write-Host "Pruefe Ollama unter $OllamaUrl ..." -ForegroundColor Cyan

try {
    $tagsResponse = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -Method Get -TimeoutSec 5
    Write-Host "Ollama ist erreichbar." -ForegroundColor Green

    $models = $tagsResponse.models | ForEach-Object { $_.name }
    if ($models) {
        Write-Host "`nInstallierte Modelle:" -ForegroundColor Cyan
        $models | ForEach-Object { Write-Host "  - $_" }
    } else {
        Write-Host "Keine Modelle installiert." -ForegroundColor Yellow
    }

    if ($models -contains $OllamaModel) {
        Write-Host "`nKonfiguriertes Modell '$OllamaModel' ist vorhanden." -ForegroundColor Green
    } else {
        Write-Host "`nWARNUNG: Modell '$OllamaModel' nicht gefunden." -ForegroundColor Yellow
        Write-Host "Installieren mit: ollama pull $OllamaModel" -ForegroundColor Yellow
    }
} catch {
    Write-Host "FEHLER: Ollama ist nicht erreichbar unter $OllamaUrl" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "`nStarten Sie Ollama mit: ollama serve" -ForegroundColor Yellow
    exit 1
}
