param(
    [string]$ModelBaseUrl = "",
    [string]$ModelName = "",
    [string]$ModelApiKey = ""
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Set-EnvValue {
    param(
        [string]$Path,
        [string]$Name,
        [string]$Value
    )

    $lines = @()
    if (Test-Path $Path) {
        $lines = Get-Content $Path
    }

    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Name))=") {
            $found = $true
            "$Name=$Value"
        } else {
            $line
        }
    }

    if (-not $found) {
        $updated += "$Name=$Value"
    }

    Set-Content -Path $Path -Value $updated -Encoding UTF8
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $RepoRoot "backend"
$Frontend = Join-Path $RepoRoot "frontend"
$EnvFile = Join-Path $RepoRoot ".env"
$EnvExample = Join-Path $RepoRoot ".env.example"

Write-Step "Checking repository branch"
Push-Location $RepoRoot
try {
    $branch = (git branch --show-current).Trim()
    if ($branch -ne "build/vertical-slice-1") {
        throw "Expected branch 'build/vertical-slice-1' but found '$branch'. Run: git switch build/vertical-slice-1"
    }
    git status --short
} finally {
    Pop-Location
}

Write-Step "Selecting Python 3.11"
$PythonLauncherArgs = $null
try {
    & py -3.11 --version | Out-Host
    $PythonLauncherArgs = @("-3.11")
} catch {
    throw "Python 3.11 was not found through the Windows py launcher. OpenJM Vertical Slice 1 currently requires Python 3.11 because DB-GPT 0.8.2 pins aiohttp 3.8.4, which is not compatible with Python 3.12."
}

Write-Step "Checking Node.js and npm"
node --version | Out-Host
npm --version | Out-Host

Write-Step "Creating local environment file"
if (-not (Test-Path $EnvFile)) {
    Copy-Item $EnvExample $EnvFile
    Write-Host "Created .env from .env.example"
} else {
    Write-Host ".env already exists; preserving existing values."
}

if (-not $ModelBaseUrl) {
    $entered = Read-Host "Hermes/OpenAI-compatible base URL [http://127.0.0.1:8642/v1]"
    $ModelBaseUrl = if ([string]::IsNullOrWhiteSpace($entered)) { "http://127.0.0.1:8642/v1" } else { $entered.Trim() }
}
if (-not $ModelName) {
    $entered = Read-Host "Model name exposed by the endpoint [hermes-agent]"
    $ModelName = if ([string]::IsNullOrWhiteSpace($entered)) { "hermes-agent" } else { $entered.Trim() }
}
if (-not $ModelApiKey) {
    $ModelApiKey = Read-Host "Bearer/API key (press Enter if the local endpoint does not require one)"
}

Set-EnvValue -Path $EnvFile -Name "OPENJM_MODEL_BASE_URL" -Value $ModelBaseUrl
Set-EnvValue -Path $EnvFile -Name "OPENJM_MODEL_NAME" -Value $ModelName
Set-EnvValue -Path $EnvFile -Name "OPENJM_MODEL_API_KEY" -Value $ModelApiKey

Write-Step "Creating backend virtual environment"
$Venv = Join-Path $Backend ".venv"
if (-not (Test-Path $Venv)) {
    & py @PythonLauncherArgs -m venv $Venv
}
$Python = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Virtual environment Python not found at $Python"
}

Write-Step "Installing backend dependencies"
Push-Location $Backend
try {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -e ".[dev]"
} finally {
    Pop-Location
}

Write-Step "Running backend unit tests"
Push-Location $Backend
try {
    & $Python -m pytest
} finally {
    Pop-Location
}

Write-Step "Installing frontend dependencies"
Push-Location $Frontend
try {
    npm install
    npm run build
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Model endpoint: $ModelBaseUrl"
Write-Host "Model name:     $ModelName"
Write-Host ""
Write-Host "Next: powershell -ExecutionPolicy Bypass -File .\scripts\start-windows.ps1"
