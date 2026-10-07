<#
.SYNOPSIS
  Bootstrap the supported Windows + WSL2 environment for OpenJM Enterprise AI.

.DESCRIPTION
  VS8 Workstream B. Runs on the Windows host. It does not require repo-specific
  tribal knowledge: it verifies WSL2, ensures an Ubuntu distribution, confirms
  the repository is reachable from inside WSL, then delegates to the same Linux
  installer so both platforms exercise one script.

  Idempotent: safe to run again. It never deletes user data.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\install-wsl2.ps1
#>
[CmdletBinding()]
param(
  [string]$Distro = "Ubuntu",
  [string]$RepoPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Error $msg; exit 1 }

# --- 1. Confirm WSL2 is available -------------------------------------------
try { $null = wsl --status 2>$null } catch { Fail "WSL is not installed. Run 'wsl --install' in an elevated shell, reboot, and re-run." }

$defaultVersion = (wsl --status 2>$null | Select-String -Pattern "Default Version" | ForEach-Object { $_.Line })
Write-Host "WSL status: $defaultVersion"
if ($defaultVersion -and $defaultVersion -match "1") {
  Write-Warning "Default WSL version is 1. Set it with: wsl --set-default-version 2"
}

# --- 2. Ensure the target distribution exists -------------------------------
$distros = (wsl --list --quiet) -replace "`0", ""
if ($distros -notcontains $Distro) {
  Write-Host "Installing distribution '$Distro'..."
  wsl --install -d $Distro
  Fail "Distribution installed. Complete first-run user setup, then re-run this script."
}

# --- 3. Map the Windows repo path to a WSL path -----------------------------
$drive = $RepoPath.Substring(0, 1).ToLower()
$rest = $RepoPath.Substring(2).Replace('\', '/')
$wslPath = "/mnt/$drive$rest"
Write-Host "Windows repo: $RepoPath"
Write-Host "WSL path:     $wslPath"

# --- 4. Verify prerequisites inside WSL, then delegate to the Linux installer
$check = wsl -d $Distro -- bash -lc "test -d '$wslPath' && command -v python3.11 >/dev/null 2>&1 && echo READY || echo PREREQS_MISSING"
if ($check -match "PREREQS_MISSING") {
  Write-Host "Installing Linux prerequisites inside WSL..."
  wsl -d $Distro -- bash -lc "sudo apt-get update && sudo apt-get install -y python3.11 python3.11-venv python3-pip git curl && (command -v node >/dev/null || echo 'Install Node 20+ from https://nodejs.org or nodesource')"
}

if ($SkipInstall) {
  Write-Host "Prerequisites verified. Skipping install as requested."
  exit 0
}

# --- 5. Run the same installer used on a native Linux host -------------------
wsl -d $Distro -- bash -lc "cd '$wslPath' && bash scripts/install-linux.sh"
$exit = $LASTEXITCODE
if ($exit -ne 0) { Fail "Linux installer failed inside WSL (exit $exit)." }

Write-Host ""
Write-Host "== WSL2 bootstrap complete =="
Write-Host "Start the backend inside WSL:"
Write-Host "  wsl -d $Distro -- bash -lc `"cd '$wslPath/backend' && source .venv/bin/activate && env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`""
