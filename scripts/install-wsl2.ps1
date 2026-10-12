<#
.SYNOPSIS
  Bootstrap the supported Windows + WSL2 environment for OpenJM Enterprise AI.

.DESCRIPTION
  VS8 Workstream B. Runs on the Windows host. It does not require repo-specific
  tribal knowledge: it verifies WSL2, ensures an Ubuntu distribution, confirms
  the repository is reachable from inside WSL, reports the exact missing
  prerequisite when one is absent, then delegates to the same Linux installer so
  both platforms exercise one script.

  Thin delegator by contract: this layer never resolves Python or Node
  dependencies. The committed locks and the canonical
  `scripts/install-linux.sh` remain the single dependency authority. PowerShell
  performs no dependency resolution, no lock parsing and no application
  installation of its own.

  Python 3.11 is an OPERATOR PREREQUISITE, not something this script installs.
  Stock Ubuntu 24.04 (Noble) ships Python 3.12, so `apt-get install python3.11`
  is not a valid path there, and installing a pinned 3.11 from an unverified
  third-party source would widen the release supply chain. The bootstrap
  therefore reports the missing toolchain precisely and fails early instead of
  attempting a fragile install. Node.js 22 is likewise never auto-installed.

  Idempotent: safe to run again. It never deletes user data. Distribution
  detection is read-only by default: pass -AllowDistroInstall to install the
  target distribution, or -DetectOnly to run detection and exit without any
  install or delegation. Without -AllowDistroInstall it refuses to install a
  different distribution automatically.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\install-wsl2.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\install-wsl2.ps1 -RepoPath C:\src\OpenJM-Enterprise-AI -Profile production
#>
[CmdletBinding()]
param(
  [string]$Distro = "Ubuntu",
  # No default here on purpose. A parameter default that calls Join-Path on an
  # empty $PSScriptRoot throws during parameter binding, before the script body
  # can report anything useful. Resolution happens below, where it can be
  # explicit, ordered and diagnostic.
  [string]$RepoPath,
  [ValidateSet("development", "production")]
  [string]$Profile = "development",
  [switch]$SkipFrontend,
  [switch]$SkipInstall,
  [switch]$AllowDistroInstall,
  [switch]$DetectOnly
)

$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Error $msg; exit 1 }

# --- 0. Resolve the repository path -----------------------------------------
# Order: explicit -RepoPath wins; otherwise derive from the script location using
# invocation metadata ($PSCommandPath, then $MyInvocation). The current working
# directory is never used as a fallback: running the script from an unrelated
# directory must not silently target the wrong repository. If nothing can be
# resolved safely, fail with a precise message.
function Resolve-RepoRoot {
  param([string]$ExplicitPath)

  if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
    if (-not (Test-Path -LiteralPath $ExplicitPath)) {
      Fail "Repository path does not exist: $ExplicitPath"
    }
    # ProviderPath, not Path: for a UNC or provider-qualified location, .Path
    # returns a PowerShell provider prefix (Microsoft.PowerShell.Core\FileSystem::)
    # that is not a usable filesystem path.
    return (Resolve-Path -LiteralPath $ExplicitPath).ProviderPath
  }

  $scriptPath = $PSCommandPath
  if ([string]::IsNullOrWhiteSpace($scriptPath)) { $scriptPath = $MyInvocation.MyCommand.Path }
  if ([string]::IsNullOrWhiteSpace($scriptPath)) { $scriptPath = $MyInvocation.MyCommand.Definition }
  if ([string]::IsNullOrWhiteSpace($scriptPath)) {
    Fail "Cannot resolve the script location to locate the repository, so the repository path is unresolved. Re-run with -RepoPath <path-to-repository>."
  }

  $candidate = Join-Path (Split-Path -Parent $scriptPath) ".."
  if (-not (Test-Path -LiteralPath $candidate)) {
    Fail "Resolved repository path does not exist: $candidate (derived from script location '$scriptPath'). Pass -RepoPath explicitly."
  }
  return (Resolve-Path -LiteralPath $candidate).ProviderPath
}

$RepoPath = Resolve-RepoRoot -ExplicitPath $RepoPath
Write-Host "Repository: $RepoPath"

# --- 1. Confirm WSL2 is available -------------------------------------------
try { $null = wsl --status 2>$null } catch { Fail "WSL is not installed. Run 'wsl --install' in an elevated shell, reboot, and re-run." }

$defaultVersion = (wsl --status 2>$null | Select-String -Pattern "Default Version" | ForEach-Object { $_.Line })
Write-Host "WSL status: $defaultVersion"
if ($defaultVersion -and $defaultVersion -match "1") {
  Write-Warning "Default WSL version is 1. Set it with: wsl --set-default-version 2"
}

# --- 2. Detect the target distribution (read-only) --------------------------
# `wsl --list --quiet` is UTF-16LE on Windows; strip NULs, a leading BOM and
# whitespace before comparing, and compare case-insensitively. Detection is
# read-only: a distribution is only installed when -AllowDistroInstall is given
# explicitly, so a parsing surprise can never register a different distro.
function Get-WslDistros {
  $raw = (wsl --list --quiet) 2>$null
  if ($null -eq $raw) { return @() }
  return @(
    $raw |
      ForEach-Object { ($_ -replace "`0", "") -replace "^\uFEFF", "" } |
      ForEach-Object { $_.Trim() } |
      Where-Object { $_ -ne "" }
  )
}

$distros = Get-WslDistros
$haveDistro = @($distros | Where-Object { $_ -ieq $Distro }).Count -gt 0
if (-not $haveDistro) {
  Write-Host "Detected WSL distributions: $($distros -join ', ')"
  if (-not $AllowDistroInstall) {
    Fail ("WSL distribution '$Distro' is absent from 'wsl --list --quiet' (detected: " +
          "$($distros -join ', ')). Pass -AllowDistroInstall to install it explicitly, or " +
          "install it yourself. Refusing to install a different distribution automatically.")
  }
  Write-Host "Installing distribution '$Distro' (explicitly requested)..."
  wsl --install -d $Distro
  Fail "Distribution installed. Complete first-run user setup, then re-run this script."
}

if ($DetectOnly) {
  Write-Host "Detected distribution '$Distro'. Read-only detection complete."
  exit 0
}

# --- 3. Map the Windows repo path to a WSL path -----------------------------
$drive = $RepoPath.Substring(0, 1).ToLower()
$rest = $RepoPath.Substring(2).Replace('\', '/')
$wslPath = "/mnt/$drive$rest"
Write-Host "Windows repo: $RepoPath"
Write-Host "WSL path:     $wslPath"

# --- 4. Inspect the WSL runtime before changing anything --------------------
# One read-only probe reports the repository visibility, the effective user, the
# privilege state and the presence of each required prerequisite, so the script
# can name the exact missing category instead of failing later with a misleading
# downstream error such as "Python 3.11 is required".
function Invoke-WslProbe {
  $probe = "id -u; " +
    "(command -v sudo >/dev/null 2>&1 && echo PRIV=SUDO || echo PRIV=NO_SUDO); " +
    "(command -v git >/dev/null 2>&1 && echo GIT=YES || echo GIT=NO); " +
    "(command -v curl >/dev/null 2>&1 && echo CURL=YES || echo CURL=NO); " +
    "(command -v python3.11 >/dev/null 2>&1 && echo PY311=YES || echo PY311=NO); " +
    "(command -v node >/dev/null 2>&1 && echo NODE=YES || echo NODE=NO); " +
    "(test -d '$wslPath' && echo REPO=YES || echo REPO=NO)"
  return (wsl -d $Distro -- bash -lc $probe) 2>$null
}

$probeOutput = Invoke-WslProbe
$probeText = ($probeOutput | Out-String)
$uid = ($probeOutput | Where-Object { $_ -match '^\d+$' } | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($uid)) {
  Fail "Could not inspect the WSL runtime '$Distro'. Confirm the distribution starts ('wsl -d $Distro -- true') and re-run."
}
$isRoot = ([int]$uid -eq 0)
$hasSudo = $probeText -match 'PRIV=SUDO'
$hasGit = $probeText -match 'GIT=YES'
$hasCurl = $probeText -match 'CURL=YES'
$hasPy311 = $probeText -match 'PY311=YES'
$hasNode = $probeText -match 'NODE=YES'
$repoVisible = $probeText -match 'REPO=YES'

$privState = if ($isRoot) { "root (uid 0)" } elseif ($hasSudo) { "non-root with sudo (uid $uid)" } else { "non-root without sudo (uid $uid)" }
Write-Host "WSL runtime: '$Distro' user state = $privState"

# 4a. Repository visibility inside WSL.
if (-not $repoVisible) {
  Fail "Repository path unresolved: '$wslPath' is not visible inside WSL distribution '$Distro'. Check the Windows path and the distribution mount."
}

# 4b. Python 3.11 is a prerequisite the operator provisions (see .DESCRIPTION).
if (-not $hasPy311) {
  $remedy = "Install a supported Python 3.11 runtime inside '$Distro', then re-run this script."
  $remedy += " Stock Ubuntu 24.04 provides Python 3.12, so this bootstrap deliberately does not install Python 3.11 from an unpinned source."
  if ($isRoot) {
    Fail "Python 3.11 absent in '$Distro' ($privState). $remedy"
  } elseif ($hasSudo) {
    Fail "Python 3.11 absent in '$Distro' ($privState). $remedy"
  } else {
    Fail "Required prerequisites are missing and this user cannot elevate privileges: Python 3.11 is absent in '$Distro' ($privState). Provision Python 3.11 / required packages or use a sudo-capable account."
  }
}

# 4c. Base packages apt can supply on a stock archive (git and curl only, never
# Python). Installed directly when already root; via sudo when that is available.
function Install-AptPackage {
  param([string]$Package)
  if ($isRoot) {
    Write-Host "Installing $Package inside '$Distro' (already root; apt used directly)..."
    wsl -d $Distro -- bash -lc "apt-get update && apt-get install -y $Package"
  } else {
    Write-Host "Installing $Package inside '$Distro' (via sudo)..."
    wsl -d $Distro -- bash -lc "sudo -n apt-get update && sudo -n apt-get install -y $Package"
  }
  if ($LASTEXITCODE -ne 0) {
    Fail "Could not install the base package '$Package' inside '$Distro' (apt exit $LASTEXITCODE). Install it manually and re-run."
  }
}

foreach ($pkg in @(@{ Name = "git"; Present = $hasGit }, @{ Name = "curl"; Present = $hasCurl })) {
  if (-not $pkg.Present) {
    if (-not ($isRoot -or $hasSudo)) {
      Fail "Required prerequisites are missing and this user cannot elevate privileges: '$($pkg.Name)' is absent in '$Distro' ($privState). Provision it or use a sudo-capable account."
    }
    Install-AptPackage -Package $pkg.Name
  }
}

# 4d. Node.js 22 is required for the frontend and is never auto-installed.
if (-not $SkipFrontend) {
  if (-not $hasNode) {
    Fail "Node.js 22 is required for the frontend build and is absent in '$Distro' ($privState). Install Node.js 22 in the distribution, or re-run with -SkipFrontend to omit the frontend."
  }
  $nodeVersion = ((wsl -d $Distro -- bash -lc "node --version") 2>$null | Out-String).Trim()
  if ($nodeVersion -notmatch '^v22\.') {
    Fail "Node.js 22 is required for the frontend build; found '$nodeVersion' in '$Distro'. Install Node.js 22, or re-run with -SkipFrontend to omit the frontend."
  }
  Write-Host "Node: $nodeVersion"
}

if ($SkipInstall) {
  Write-Host "Prerequisites verified. Skipping install as requested."
  exit 0
}

# --- 5. Run the same installer used on a native Linux host -------------------
$linuxArgs = "--profile $Profile"
if ($SkipFrontend) { $linuxArgs += " --skip-frontend" }
wsl -d $Distro -- bash -lc "cd '$wslPath' && bash scripts/install-linux.sh $linuxArgs"
$exit = $LASTEXITCODE
if ($exit -ne 0) { Fail "Linux installer failed inside WSL (exit $exit)." }

Write-Host ""
Write-Host "== WSL2 bootstrap complete ($Profile profile) =="
Write-Host "Start the backend inside WSL:"
Write-Host "  wsl -d $Distro -- bash -lc `"cd '$wslPath/backend' && source .venv/bin/activate && env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`""
