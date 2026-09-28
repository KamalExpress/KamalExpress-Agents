# ==============================================================================
# Kamal Express AI Platform — Windows Server VPS Turnkey Setup Script
# ==============================================================================
# Run on Windows Server VPS to configure Python, Chrome, Virtual Environment,
# Playwright, and all AI dependencies with a single command.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\setup-vps.ps1
# ==============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  KAMAL EXPRESS AI AGENT PLATFORM - WINDOWS SERVER VPS SETUP" -ForegroundColor Green
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  Operating System : $([System.Environment]::OSVersion.VersionString)" -ForegroundColor Gray
Write-Host "  Setup Directory  : $PSScriptRoot" -ForegroundColor Gray
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. Check & Install Google Chrome
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "[1/6] Checking Google Chrome installation..." -ForegroundColor Yellow

$ChromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$ChromeExe = $ChromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($ChromeExe) {
    Write-Host "  [OK] Google Chrome is already installed: $ChromeExe" -ForegroundColor Green
} else {
    Write-Host "  [DOWNLOAD] Google Chrome not found. Downloading Chrome Standalone Installer..." -ForegroundColor Yellow
    $ChromeInstaller = Join-Path $env:TEMP "GoogleChromeStandaloneEnterprise64.msi"
    $ChromeUrl = "https://dl.google.com/chrome/install/GoogleChromeStandaloneEnterprise64.msi"
    
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $ChromeUrl -OutFile $ChromeInstaller -UseBasicParsing
    
    Write-Host "  [INSTALL] Installing Google Chrome..." -ForegroundColor Yellow
    Start-Process msiexec.exe -ArgumentList "/i `"$ChromeInstaller`" /qn /norestart" -Wait
    
    $ChromeExe = $ChromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($ChromeExe) {
        Write-Host "  [OK] Google Chrome successfully installed: $ChromeExe" -ForegroundColor Green
    } else {
        Write-Warning "Chrome installation completed. Please verify Chrome is available in PATH."
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Check & Install Python 3.12
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/6] Checking Python installation..." -ForegroundColor Yellow

$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
$PythonOk = $false

if ($PythonCmd) {
    $PyVerOutput = & python --version 2>&1
    if ($PyVerOutput -match "Python 3\.(1[0-9]|[2-9][0-9])") {
        Write-Host "  [OK] Python is installed: $PyVerOutput" -ForegroundColor Green
        $PythonOk = $true
    }
}

if (-not $PythonOk) {
    Write-Host "  [DOWNLOAD] Compatible Python (>=3.10) not found. Downloading Python 3.12.7 64-bit..." -ForegroundColor Yellow
    $PyInstaller = Join-Path $env:TEMP "python-3.12.7-amd64.exe"
    $PyUrl = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
    
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $PyUrl -OutFile $PyInstaller -UseBasicParsing
    
    Write-Host "  [INSTALL] Installing Python 3.12 (with PATH registration)..." -ForegroundColor Yellow
    Start-Process $PyInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
    
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    
    $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCmd) {
        Write-Host "  [OK] Python installed successfully: $(& python --version)" -ForegroundColor Green
    } else {
        Write-Error "Python installation failed. Please install Python 3.10+ manually from python.org"
        exit 1
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. Setup Virtual Environment
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[3/6] Configuring Python Virtual Environment (venv)..." -ForegroundColor Yellow

$VenvDir = Join-Path $PSScriptRoot "venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "  Creating new virtual environment at: $VenvDir" -ForegroundColor Cyan
    & python -m venv "$VenvDir"
}

Write-Host "  Upgrading pip, setuptools, and wheel..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip setuptools wheel --quiet

# ─────────────────────────────────────────────────────────────────────────────
# 4. Install Dependencies & Playwright
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[4/6] Installing Python dependencies from requirements.txt..." -ForegroundColor Yellow

$ReqFile = Join-Path $PSScriptRoot "requirements.txt"
if (Test-Path $ReqFile) {
    & $VenvPip install -r "$ReqFile" --quiet
    Write-Host "  [OK] Python packages installed successfully." -ForegroundColor Green
} else {
    Write-Error "requirements.txt not found in $PSScriptRoot"
    exit 1
}

Write-Host "  Installing Playwright browser binaries..." -ForegroundColor Cyan
$PlaywrightExe = Join-Path $VenvDir "Scripts\playwright.exe"
if (Test-Path $PlaywrightExe) {
    & $PlaywrightExe install chromium
    Write-Host "  [OK] Playwright Chromium engine installed." -ForegroundColor Green
}

# ─────────────────────────────────────────────────────────────────────────────
# 5. Initialize Configuration & Data Directories
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[5/6] Verifying configuration and data directories..." -ForegroundColor Yellow

$EnvFile = Join-Path $PSScriptRoot ".env"
$EnvExample = Join-Path $PSScriptRoot ".env.example"

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvFile
        Write-Host "  [OK] Created .env from .env.example" -ForegroundColor Green
    } else {
        $DefaultEnv = @(
            "AI_PROVIDER=ollama",
            "API_HOST=0.0.0.0",
            "API_PORT=8080",
            "API_RELOAD=true",
            "BROWSER_MODE=cdp",
            "BROWSER_CDP_URL=http://localhost:9222"
        )
        $DefaultEnv -join "`n" | Set-Content $EnvFile
        Write-Host "  [OK] Generated default .env file" -ForegroundColor Green
    }
} else {
    Write-Host "  [OK] Existing .env file found." -ForegroundColor Green
}

$DataDir = Join-Path $PSScriptRoot "data"
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
}

$ProxyFile = Join-Path $DataDir "ips-list-pk.txt"
if (-not (Test-Path $ProxyFile)) {
    $DefaultProxies = @(
        "pk.decodo.com:10001:spbisytqkz:GuiSe08Bwcg2~ciC3b",
        "pk.decodo.com:10002:spbisytqkz:GuiSe08Bwcg2~ciC3b",
        "pk.decodo.com:10003:spbisytqkz:GuiSe08Bwcg2~ciC3b",
        "pk.decodo.com:10004:spbisytqkz:GuiSe08Bwcg2~ciC3b",
        "pk.decodo.com:10005:spbisytqkz:GuiSe08Bwcg2~ciC3b",
        "pk.decodo.com:10006:spbisytqkz:GuiSe08Bwcg2~ciC3b",
        "pk.decodo.com:10007:spbisytqkz:GuiSe08Bwcg2~ciC3b",
        "pk.decodo.com:10008:spbisytqkz:GuiSe08Bwcg2~ciC3b",
        "pk.decodo.com:10009:spbisytqkz:GuiSe08Bwcg2~ciC3b",
        "pk.decodo.com:10010:spbisytqkz:GuiSe08Bwcg2~ciC3b"
    )
    $DefaultProxies -join "`n" | Set-Content $ProxyFile
    Write-Host "  [OK] Created default Pakistan residential proxy list in data\ips-list-pk.txt" -ForegroundColor Green
}

# ─────────────────────────────────────────────────────────────────────────────
# 6. Run System Self-Verification
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[6/6] Running system verification test..." -ForegroundColor Yellow

$TestScript = Join-Path $PSScriptRoot "test_end_to_end.py"
if (Test-Path $TestScript) {
    & $VenvPython "$TestScript"
}

Write-Host ""
Write-Host "==============================================================================" -ForegroundColor Green
Write-Host "  KAMAL EXPRESS VPS SETUP COMPLETED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "==============================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  To start the system at any time on this VPS:" -ForegroundColor Cyan
Write-Host "    1. Launch Chrome CDP:  .\Launch-Chrome-CDP.ps1 -RealProfile -NoProxy" -ForegroundColor Yellow
Write-Host "    2. Start API Server:   .\venv\Scripts\uvicorn api.main:app --host 0.0.0.0 --port 8080" -ForegroundColor Yellow
Write-Host "    3. Access Dashboard:   http://localhost:8080" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Or simply double-click: Start-KamalExpress.bat" -ForegroundColor Green
Write-Host "==============================================================================" -ForegroundColor Green
Write-Host ""
