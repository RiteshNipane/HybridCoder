# Stop all running Hybrid AI Coding Agent processes (backend & frontend)
# Usage: .\scripts\stop.ps1

Write-Host "Stopping Hybrid AI Coding Agent processes..." -ForegroundColor Cyan

# 1. Kill python uvicorn processes running backend.main:app
$uvicornProcesses = Get-WmiObject Win32_Process | Where-Object {
    $_.CommandLine -match "backend\.main:app"
}
if ($uvicornProcesses) {
    foreach ($p in $uvicornProcesses) {
        Write-Host "Stopping backend process PID: $($p.ProcessId)" -ForegroundColor Yellow
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "No backend uvicorn processes found." -ForegroundColor Gray
}

# 2. Kill node processes running vite dev server for hybrid-coding-agent
$nodeProcesses = Get-WmiObject Win32_Process | Where-Object {
    $_.Name -eq "node.exe" -and $_.CommandLine -match "vite"
}
if ($nodeProcesses) {
    foreach ($p in $nodeProcesses) {
        Write-Host "Stopping frontend Vite process PID: $($p.ProcessId)" -ForegroundColor Yellow
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "No frontend Vite processes found." -ForegroundColor Gray
}

Write-Host "All agent processes stopped." -ForegroundColor Green

