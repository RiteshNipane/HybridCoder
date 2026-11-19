# Environment Verification Script
# Usage: .\scripts\check_env.ps1

Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "  Hybrid AI Coding Agent - Environment Pre-Flight" -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan

$allPassed = $true

# 1. Python check
try {
    $pyVer = python --version 2>&1
    Write-Host "[OK] Python: $pyVer" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Python not found" -ForegroundColor Red
    $allPassed = $false
}

# 2. Node & npm
try {
    $nodeVer = node --version 2>&1
    $npmVer = npm --version 2>&1
    Write-Host "[OK] Node.js: $nodeVer | npm: $npmVer" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Node.js or npm missing" -ForegroundColor Red
    $allPassed = $false
}

# 3. Git
try {
    $gitVer = git --version 2>&1
    Write-Host "[OK] Git: $gitVer" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Git missing" -ForegroundColor Red
    $allPassed = $false
}

# 4. Ollama
try {
    $ollamaTags = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3 -ErrorAction Stop
    $models = $ollamaTags.models | ForEach-Object { $_.name }
    Write-Host "[OK] Ollama running. Installed models: $($models -join ', ')" -ForegroundColor Green
    if ($models -contains "qwen2.5-coder:7b") {
        Write-Host "     -> qwen2.5-coder:7b verified!" -ForegroundColor Cyan
    } else {
        Write-Host "     -> [WARN] qwen2.5-coder:7b not found. Run: ollama pull qwen2.5-coder:7b" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[WARN] Ollama not currently running on port 11434. Start with: ollama serve" -ForegroundColor Yellow
}

# 5. GPU & VRAM
try {
    $gpu = nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits 2>$null
    if ($gpu) {
        $parts = $gpu.Split(',')
        Write-Host "[OK] GPU: $($parts[0].Trim()) | VRAM: $($parts[1].Trim())MB total ($($parts[2].Trim())MB free)" -ForegroundColor Green
    } else {
        Write-Host "[INFO] No NVIDIA GPU detected or nvidia-smi not in PATH (CPU mode)" -ForegroundColor Gray
    }
} catch {
    Write-Host "[INFO] GPU check skipped" -ForegroundColor Gray
}

# 6. .env check
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
if (Test-Path $envPath) {
    Write-Host "[OK] .env configuration file exists" -ForegroundColor Green
} else {
    Write-Host "[WARN] .env file not found. Copy .env.example to .env to set credentials." -ForegroundColor Yellow
}

Write-Host "====================================================" -ForegroundColor Cyan
if ($allPassed) {
    Write-Host "Environment is ready! Start with: .\scripts\start.ps1" -ForegroundColor Green
} else {
    Write-Host "Please resolve any [FAIL] items before starting." -ForegroundColor Red
}

