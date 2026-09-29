param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $RepoRoot "backend"
$Frontend = Join-Path $RepoRoot "frontend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Backend virtual environment not found. Run scripts\setup-windows.ps1 first."
}

$backendCommand = @"
Set-Location '$Backend'
& '$Python' -m uvicorn app.main:app --reload --port 8000
"@

$frontendCommand = @"
Set-Location '$Frontend'
npm run dev
"@

Write-Host "Starting OpenJM backend..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-Command", $backendCommand
)

Write-Host "Starting OpenJM frontend..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-Command", $frontendCommand
)

Write-Host "Waiting for backend health endpoint..."
$healthy = $false
for ($i = 0; $i -lt 45; $i++) {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2
        if ($response.status -eq "ok") {
            $healthy = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if ($healthy) {
    Write-Host "Backend is healthy." -ForegroundColor Green
} else {
    Write-Warning "Backend did not become healthy within the wait window. Check the backend PowerShell window for errors."
}

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:5173"
}

Write-Host ""
Write-Host "Frontend: http://127.0.0.1:5173"
Write-Host "Backend:  http://127.0.0.1:8000"
Write-Host "Health:   http://127.0.0.1:8000/api/health"
