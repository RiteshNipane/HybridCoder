# Starts the Hybrid AI Coding Agent (backend + frontend)
# Run from project root: .\scripts\start.ps1

param(
    [switch]$NoBrowser,
    [int]$BackendPort = 0,
    [int]$FrontendPort = 0
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  Hybrid AI Coding Agent - Startup Script" -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan

# ── 1. Check .env exists ────────────────────────────────────
$EnvFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "[ERROR] .env file not found." -ForegroundColor Red
    Write-Host "        Run: Copy-Item .env.example .env" -ForegroundColor Yellow
    Write-Host "        Then add your GEMINI_API_KEY." -ForegroundColor Yellow
    exit 1
}
Write-Host "[OK] .env found" -ForegroundColor Green

# ── 2. Load .env for port values ────────────────────────────
$EnvContent = Get-Content $EnvFile
$EnvVars = @{}
foreach ($line in $EnvContent) {
    if ($line -match '^\s*([^#][^=]+)=(.*)$') {
        $EnvVars[$Matches[1].Trim()] = $Matches[2].Trim()
    }
}

if ($BackendPort -eq 0) {
    $BackendPort = if ($EnvVars.ContainsKey("APP_PORT")) { [int]$EnvVars["APP_PORT"] } else { 8000 }
}
if ($FrontendPort -eq 0) { $FrontendPort = 3000 }
$BackendHost = if ($EnvVars.ContainsKey("APP_HOST")) { $EnvVars["APP_HOST"] } else { "127.0.0.1" }

Write-Host "[INFO] Backend : http://$($BackendHost):$BackendPort" -ForegroundColor Gray
Write-Host "[INFO] Frontend: http://localhost:$FrontendPort" -ForegroundColor Gray

# ── 3. Activate venv if present ─────────────────────────────
$VenvActivate = Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    Write-Host "[OK] Activating .venv..." -ForegroundColor Green
    & $VenvActivate
} else {
    Write-Host "[WARN] No .venv found — using system Python" -ForegroundColor Yellow
}

# ── 4. Check Ollama is running ──────────────────────────────
Write-Host "[INFO] Checking Ollama..." -ForegroundColor Gray
try {
    $null = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3
    Write-Host "[OK] Ollama is running" -ForegroundColor Green
} catch {
    Write-Host "[WARN] Ollama is not reachable. Starting it..." -ForegroundColor Yellow
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
    try {
        $null = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 5
        Write-Host "[OK] Ollama started" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Ollama still not reachable. Local model may be unavailable." -ForegroundColor Yellow
    }
}

# ── 5. Start uvicorn backend ─────────────────────────────────
Write-Host ""
Write-Host "[START] Launching backend (uvicorn)..." -ForegroundColor Cyan
$BackendProcess = Start-Process -FilePath "python" `
    -ArgumentList "-m", "uvicorn", "backend.main:app",
                  "--host", $BackendHost,
                  "--port", $BackendPort,
                  "--reload" `
    -WorkingDirectory $ProjectRoot `
    -PassThru `
    -WindowStyle Normal

Write-Host "[INFO] Backend PID: $($BackendProcess.Id)" -ForegroundColor Gray

# Wait for backend to be ready
Write-Host "[INFO] Waiting for backend to be ready..." -ForegroundColor Gray
$BackendReady = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $null = Invoke-RestMethod "http://$($BackendHost):$BackendPort/health" -TimeoutSec 2
        $BackendReady = $true
        break
    } catch { }
}

if ($BackendReady) {
    Write-Host "[OK] Backend is ready" -ForegroundColor Green
} else {
    Write-Host "[WARN] Backend didn't respond in time — it may still be starting" -ForegroundColor Yellow
}

# ── 6. Start Vite frontend ──────────────────────────────────
Write-Host "[START] Launching frontend (Vite)..." -ForegroundColor Cyan
$FrontendDir = Join-Path $ProjectRoot "frontend"

# On Windows, Start-Process can't launch "npm" directly; it must be npm.cmd
$NpmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $NpmCmd) {
    Write-Host "[ERROR] npm.cmd not found. Install Node.js: winget install OpenJS.NodeJS.LTS" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    Write-Host "[INFO] Installing frontend dependencies..." -ForegroundColor Gray
    Push-Location $FrontendDir
    & $NpmCmd install --silent
    Pop-Location
}

$FrontendProcess = Start-Process -FilePath $NpmCmd `
    -ArgumentList "run", "dev", "--", "--port", $FrontendPort `
    -WorkingDirectory $FrontendDir `
    -PassThru `
    -WindowStyle Normal

Write-Host "[INFO] Frontend PID: $($FrontendProcess.Id)" -ForegroundColor Gray

# Save PIDs for stop script
$PidFile = Join-Path $ProjectRoot ".running_pids"
"$($BackendProcess.Id),$($FrontendProcess.Id)" | Set-Content $PidFile

# Wait a moment for Vite to boot
Start-Sleep -Seconds 3

# ── 7. Open browser ─────────────────────────────────────────
if (-not $NoBrowser) {
    Write-Host "[INFO] Opening browser..." -ForegroundColor Gray
    Start-Process "http://localhost:$FrontendPort"
}

Write-Host ""
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  Agent is running!" -ForegroundColor Green
Write-Host "  Frontend : http://localhost:$FrontendPort" -ForegroundColor White
Write-Host "  Backend  : http://$($BackendHost):$BackendPort" -ForegroundColor White
Write-Host "  API Docs : http://$($BackendHost):$BackendPort/docs" -ForegroundColor White
Write-Host ""
Write-Host "  To stop: .\scripts\stop.ps1" -ForegroundColor Yellow
Write-Host "====================================================" -ForegroundColor Cyan

# Keep script alive so Ctrl+C kills everything
try {
    Wait-Process -Id $BackendProcess.Id
} catch {
    # Backend exited
}
