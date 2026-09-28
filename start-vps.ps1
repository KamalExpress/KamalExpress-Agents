# ==============================================================================
# Kamal Express — 1-Click Startup Script for Windows Server VPS
# ==============================================================================
param(
    [switch]$NoBrowser = $false
)

$Host.UI.RawUI.WindowTitle = "Kamal Express AI Platform"

Write-Host ""
Write-Host "===========================================================" -ForegroundColor Cyan
Write-Host "  🚀 Launching Kamal Express AI Platform on VPS" -ForegroundColor Green
Write-Host "===========================================================" -ForegroundColor Cyan

# 1. Start Chrome CDP in Background (if not already running on 9222)
$CdpRunning = $false
try {
    $TestHttp = Invoke-WebRequest -Uri "http://localhost:9222/json/version" -TimeoutSec 1 -UseBasicParsing -ErrorAction SilentlyContinue
    if ($TestHttp.StatusCode -eq 200) { $CdpRunning = $true }
} catch {}

if (-not $CdpRunning) {
    Write-Host "  [1/2] Dispatched Chrome CDP on port 9222..." -ForegroundColor Yellow
    $LaunchScript = Join-Path $PSScriptRoot "Launch-Chrome-CDP.ps1"
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -File `"$LaunchScript`" -RealProfile -NoProxy" -WindowStyle Minimized
    Start-Sleep -Seconds 2
} else {
    Write-Host "  [1/2] Chrome CDP is already listening on port 9222." -ForegroundColor Green
}

# 2. Start FastAPI Server
Write-Host "  [2/2] Starting Kamal Express API Server (:8080)..." -ForegroundColor Yellow
$Uvicorn = Join-Path $PSScriptRoot "venv\Scripts\uvicorn.exe"

if (-not (Test-Path $Uvicorn)) {
    Write-Error "Virtual environment not found. Please run setup-vps.ps1 first."
    exit 1
}

if (-not $NoBrowser) {
    Start-Process "http://localhost:8080"
}

& $Uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
