# Launch-Chrome-CDP.ps1
# =============================================================
# Starts Google Chrome with the remote debugging port (CDP) open.
#
# Modes:
#   1. Real Personal Profile (re-uses your real logged-in Google accounts & cookies):
#      .\Launch-Chrome-CDP.ps1 -RealProfile
#      .\Launch-Chrome-CDP.ps1 -RealProfile -ProfileName "Default"
#      .\Launch-Chrome-CDP.ps1 -RealProfile -ProfileName "Profile 1"
#
#   2. Isolated Side-by-Side Profile (runs alongside existing Chrome without closing it):
#      .\Launch-Chrome-CDP.ps1
#
# Options:
#   -NoProxy : Disables proxy and uses direct local connection
#   -Port    : Custom port (default: 9222)
# =============================================================
param(
    [int]$Port = 9222,
    [switch]$RealProfile = $false,
    [string]$ProfileName = "Default",
    [string]$UserDataDir = "",
    [string]$TargetUrl = "https://pk-gr-services.gvcworld.eu",
    [switch]$NoProxy = $false
)

$ChromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)

$Chrome = $ChromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Chrome) {
    Write-Error "Chrome executable not found. Please install Google Chrome."
    exit 1
}

# Determine User Data Directory
if ($RealProfile) {
    $UserDataDir = "$env:LOCALAPPDATA\Google\Chrome\User Data"
    
    # Check if Chrome is already running
    $RunningChrome = Get-Process chrome -ErrorAction SilentlyContinue
    if ($RunningChrome) {
        Write-Host ""
        Write-Host "  [NOTICE] To attach CDP to your REAL personal Chrome profile ($ProfileName)," -ForegroundColor Yellow
        Write-Host "           existing Chrome processes must restart with the debugging flag." -ForegroundColor Yellow
        $Confirm = Read-Host "  Close running Chrome and restart with CDP? (Y/n)"
        if ($Confirm -eq "" -or $Confirm -match "^[Yy]") {
            Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        } else {
            Write-Host "  Switching to dedicated side-by-side profile instead..." -ForegroundColor Gray
            $RealProfile = $false
            $UserDataDir = "$env:LOCALAPPDATA\Google\Chrome\CDP-Profile"
        }
    }
} elseif (-not $UserDataDir) {
    $UserDataDir = "$env:LOCALAPPDATA\Google\Chrome\CDP-Profile"
}

if (-not (Test-Path $UserDataDir)) {
    New-Item -ItemType Directory -Path $UserDataDir -Force | Out-Null
}

$ProxyServer = ""
$ProxyUser = ""
$ProxyPass = ""
$IpFile = Join-Path $PSScriptRoot "data\ips-list-pk.txt"

if (-not $NoProxy -and (Test-Path $IpFile)) {
    $Lines = Get-Content $IpFile | Where-Object { $_.Trim() -and -not $_.StartsWith("#") }
    if ($Lines.Count -gt 0) {
        $Parts = $Lines[0].Split(":")
        if ($Parts.Count -ge 4) {
            $HostName = $Parts[0]
            $ProxyPort = $Parts[1]
            $ProxyUser = $Parts[2]
            $ProxyPass = $Parts[3]
            $ProxyServer = "http://$HostName`:$ProxyPort"
        }
    }
}

Write-Host ""
Write-Host "  ===========================================================" -ForegroundColor Cyan
Write-Host "    Kamal Express - Chrome CDP Launcher" -ForegroundColor Green
Write-Host "  ===========================================================" -ForegroundColor Cyan
Write-Host "  Chrome Executable : $Chrome" -ForegroundColor Gray
if ($RealProfile) {
    Write-Host "  Profile Mode      : Real Personal Profile ($ProfileName)" -ForegroundColor Green
} else {
    Write-Host "  Profile Mode      : Dedicated Isolated Profile" -ForegroundColor Yellow
}
Write-Host "  User Data Dir     : $UserDataDir" -ForegroundColor Gray
Write-Host "  CDP URL           : ws://localhost:$Port" -ForegroundColor Yellow
Write-Host "  Target URL        : $TargetUrl" -ForegroundColor Cyan
if ($ProxyServer) {
    Write-Host "  Proxy Server      : $ProxyServer (Residential PK)" -ForegroundColor Magenta
    Write-Host "  Proxy User        : $ProxyUser" -ForegroundColor Magenta
} else {
    Write-Host "  Proxy Mode        : Direct Connection (No Proxy)" -ForegroundColor Gray
}
Write-Host "  ===========================================================" -ForegroundColor Cyan
Write-Host ""

$ExtDir = Join-Path $PSScriptRoot "tools\chrome-proxy-extension"

$Arguments = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$UserDataDir",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled"
)

if ($RealProfile -and $ProfileName) {
    $Arguments += "--profile-directory=$ProfileName"
}

if ($ProxyServer) {
    $Arguments += "--proxy-server=$ProxyServer"
    if (Test-Path $ExtDir) {
        $Arguments += "--load-extension=$ExtDir"
    }
}

$Arguments += $TargetUrl

Start-Process -FilePath $Chrome -ArgumentList $Arguments

Start-Sleep -Seconds 2

try {
    $Tcp = New-Object System.Net.Sockets.TcpClient("127.0.0.1", $Port)
    $Tcp.Close()
    Write-Host "  [OK] Chrome started and listening on CDP port $Port!" -ForegroundColor Green
    if ($RealProfile) {
        Write-Host "  [OK] Attached to your real Chrome profile with all Google accounts active." -ForegroundColor Green
    } else {
        Write-Host "  [OK] Attached to dedicated profile." -ForegroundColor Green
    }
} catch {
    Write-Host "  [INFO] Chrome process dispatched. Please check the opened window." -ForegroundColor Yellow
}
Write-Host ""
