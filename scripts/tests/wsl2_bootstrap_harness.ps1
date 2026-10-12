<#
.SYNOPSIS
  Bounded behavioural harness for scripts/install-wsl2.ps1 (REL1-B4H, H7).

.DESCRIPTION
  Drives the real bootstrap through the observed failure and success cases without
  creating or touching any WSL distribution. A stub `wsl` shim (wsl.cmd +
  wsl_stub.ps1) is placed first on PATH and answers the three shapes the bootstrap
  uses: `--status`, `--list --quiet`, and `-d <distro> -- bash -lc <payload>`.

  Run from Windows PowerShell:
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\wsl2_bootstrap_harness.ps1
  Optional: -ScriptPath <path-to-install-wsl2.ps1> to run the same cases against a
  different revision (used to prove RED before a fix and GREEN after).

  Exits non-zero if any case fails.
#>
[CmdletBinding()]
param(
  [string]$ScriptPath,
  [string]$RepoRoot
)

$ErrorActionPreference = "Stop"

# Resolve our own location in the body, never in a param default: $PSCommandPath
# is empty during parameter binding when the host is launched with -File from a
# WSL-spawned powershell.exe (the very defect H1 fixes in install-wsl2.ps1).
# .NET path APIs, not Split-Path: Split-Path returns an empty string for a UNC
# location, which would silently shift the argument list built below.
$selfPath = $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($selfPath)) { $selfPath = $PSCommandPath }
if ([string]::IsNullOrWhiteSpace($selfPath)) { throw "Harness location unresolved; run with -ScriptPath and -RepoRoot." }
$harness = [System.IO.Path]::GetDirectoryName($selfPath)
if ([string]::IsNullOrWhiteSpace($harness)) { throw "Harness directory unresolved from '$selfPath'." }
if ([string]::IsNullOrWhiteSpace($ScriptPath)) {
  $ScriptPath = Join-Path ([System.IO.Path]::GetDirectoryName($harness)) "install-wsl2.ps1"
}
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
  $RepoRoot = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetDirectoryName($harness))
}
if ([string]::IsNullOrWhiteSpace($RepoRoot)) { throw "Repository root unresolved; pass -RepoRoot explicitly." }

# The supported contract requires a Windows DRIVE-LETTER repository path (a UNC
# or provider path cannot be mapped into WSL as /mnt/<drive>/...). When this
# harness itself lives on a drive letter that path is used; otherwise a small
# placeholder tree on the local drive stands in, because the stub never reads the
# repository contents.
if ($RepoRoot -match '^[A-Za-z]:[\\/]') {
  $DriveRepo = $RepoRoot
} else {
  $DriveRepo = Join-Path $env:TEMP "openjm-harness-repo"
  New-Item -ItemType Directory -Force -Path (Join-Path $DriveRepo "scripts") | Out-Null
}

$failures = @()
$passes = 0

function Write-Stub {
  param([string]$Dir)
  New-Item -ItemType Directory -Force -Path $Dir | Out-Null
  @'
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0wsl_stub.ps1" %*
'@ | Set-Content -Encoding ASCII (Join-Path $Dir "wsl.cmd")

  @'
$all = ($args -join ' ')
if ($all -match '--status') { Write-Output 'Default Version: 2'; exit 0 }
if ($all -match '--list') {
  if ([string]::IsNullOrWhiteSpace($env:WSLSTUB_DISTROS)) { exit 0 }
  ($env:WSLSTUB_DISTROS -split ';') | ForEach-Object { Write-Output $_ }
  exit 0
}
if ($all -match 'REPO=') {
  Write-Output ("PROBEPAYLOAD=" + ($all -replace '=', '[:]'))
  Write-Output $env:WSLSTUB_UID
  # Model the difference between a sudo BINARY and USABLE non-interactive sudo.
  # The stub answers the probe it actually received: the pre-correction probe
  # only asked whether a binary exists, the corrected probe also runs
  # `sudo -n true`, so a binary-only account is reported differently to each.
  $sudoBinary = ($env:WSLSTUB_SUDO -eq '1') -or ($env:WSLSTUB_SUDO_BINARY -eq '1')
  $sudoUsable = ($env:WSLSTUB_SUDO -eq '1') -or ($env:WSLSTUB_SUDO_USABLE -eq '1')
  $probeAsksNonInteractive = ($all -match 'sudo -n true')
  if ($sudoBinary -and ((-not $probeAsksNonInteractive) -or $sudoUsable)) { Write-Output 'PRIV=SUDO' } else { Write-Output 'PRIV=NO_SUDO' }
  if ($env:WSLSTUB_GIT  -eq '1') { Write-Output 'GIT=YES'  } else { Write-Output 'GIT=NO' }
  if ($env:WSLSTUB_CURL -eq '1') { Write-Output 'CURL=YES' } else { Write-Output 'CURL=NO' }
  if ($env:WSLSTUB_PY311 -eq '1') { Write-Output 'PY311=YES' } else { Write-Output 'PY311=NO' }
  if ($env:WSLSTUB_NODE -eq '1') { Write-Output 'NODE=YES' } else { Write-Output 'NODE=NO' }
  if ($env:WSLSTUB_REPO -eq '1') { Write-Output 'REPO=YES' } else { Write-Output 'REPO=NO' }
  exit 0
}
if ($all -match 'node --version') {
  if (-not [string]::IsNullOrWhiteSpace($env:WSLSTUB_NODE_VERSION)) { Write-Output $env:WSLSTUB_NODE_VERSION }
  exit 0
}
if ($all -match 'apt-get') { exit [int]$env:WSLSTUB_APT_EXIT }
if ($all -match 'install-linux.sh') {
  Write-Output ("DELEGATED " + $all)
  exit [int]$env:WSLSTUB_INSTALL_EXIT
}
exit 0
'@ | Set-Content -Encoding ASCII (Join-Path $Dir "wsl_stub.ps1")
}

function Merge-Hashtable {
  param([hashtable]$Base, [hashtable]$Over)
  $out = @{}
  foreach ($k in $Base.Keys) { $out[$k] = $Base[$k] }
  foreach ($k in $Over.Keys) { $out[$k] = $Over[$k] }
  return $out
}

function Set-Scenario {
  param([hashtable]$Vars)
  foreach ($k in @("WSLSTUB_DISTROS","WSLSTUB_UID","WSLSTUB_SUDO","WSLSTUB_GIT","WSLSTUB_CURL",
                   "WSLSTUB_PY311","WSLSTUB_NODE","WSLSTUB_REPO","WSLSTUB_NODE_VERSION",
                   "WSLSTUB_INSTALL_EXIT","WSLSTUB_APT_EXIT","WSLSTUB_SUDO_BINARY","WSLSTUB_SUDO_USABLE")) {
    $v = if ($Vars.ContainsKey($k)) { [string]$Vars[$k] } else { "" }
    Set-Item -Path ("Env:" + $k) -Value $v
  }
}

function Invoke-Case {
  # NOT $Args: that name collides with PowerShell's automatic $args and
  # silently drops every argument passed to this function.
  param([string]$Name, [hashtable]$Vars, [string[]]$ScriptArgs, [bool]$ExpectSuccess, [string]$ExpectMatch, [string]$ExpectPayload = "")

  Set-Scenario -Vars $Vars
  $stdout = Join-Path $stubDir "case.out.txt"
  $stderr = Join-Path $stubDir "case.err.txt"
  $cleanArgs = @($ScriptArgs | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
  $argv = @("-NoProfile","-ExecutionPolicy","Bypass","-File",$ScriptPath) + $cleanArgs
  # A UNC working directory makes cmd.exe warn on stderr ("UNC paths are not
  # supported"), which a Stop error preference turns into a false failure. The
  # child therefore always runs with a local working directory.
  $localCwd = if ($env:TEMP) { $env:TEMP } else { $env:USERPROFILE }
  $proc = Start-Process -FilePath "powershell" -ArgumentList $argv -NoNewWindow -Wait -PassThru `
            -WorkingDirectory $localCwd `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  $text = ((Get-Content -Raw $stdout -ErrorAction SilentlyContinue) + "`n" +
           (Get-Content -Raw $stderr -ErrorAction SilentlyContinue))
  $ok = $true
  if ($ExpectSuccess -and $proc.ExitCode -ne 0) { $ok = $false }
  if (-not $ExpectSuccess -and $proc.ExitCode -eq 0) { $ok = $false }
  if ($ExpectMatch -ne "" -and ($text -notmatch [regex]::Escape($ExpectMatch))) { $ok = $false }
  # The stub echoes what it received, so a malformed WSL path cannot hide behind a
  # hard-coded REPO=YES.
  if ($ExpectPayload -ne "" -and ($text -notmatch [regex]::Escape($ExpectPayload))) { $ok = $false }

  if ($ok) {
    $script:passes++
    Write-Host ("[PASS] {0} (exit {1})" -f $Name, $proc.ExitCode)
  } else {
    $script:failures += $Name
    Write-Host ("[FAIL] {0} (exit {1}, expected success={2}, match='{3}', payload='{4}')" -f $Name, $proc.ExitCode, $ExpectSuccess, $ExpectMatch, $ExpectPayload)
    Write-Host ("       output: " + ($text -replace "`r?`n", " | ").Substring(0, [Math]::Min(400, $text.Length)))
  }
}

# cmd.exe cannot use a UNC path as %~dp0, so the stub lives on a local drive.
$stubDir = Join-Path $env:TEMP "openjm-wslstub"
Write-Stub -Dir $stubDir
$env:PATH = "$stubDir;$env:PATH"

$healthy = @{ WSLSTUB_DISTROS = "Ubuntu;Ubuntu-24.04;openjm-rel1b4"; WSLSTUB_UID = "1000"; WSLSTUB_SUDO = "1";
              WSLSTUB_GIT = "1"; WSLSTUB_CURL = "1"; WSLSTUB_PY311 = "1"; WSLSTUB_NODE = "1";
              WSLSTUB_REPO = "1"; WSLSTUB_NODE_VERSION = "v22.9.0"; WSLSTUB_INSTALL_EXIT = "0"; WSLSTUB_APT_EXIT = "0" }

# 1. script-location resolution (no -RepoPath) resolves the repository
Invoke-Case "1 script-location resolution (UNC refused)" $healthy @() $false "Unsupported repository path"
# 2. explicit -RepoPath wins
Invoke-Case "2 explicit RepoPath" $healthy @("-RepoPath", $DriveRepo) $true "Repository:"
# 3. repository not visible inside WSL fails clearly, not downstream
Invoke-Case "3 repo not visible" (Merge-Hashtable $healthy @{ WSLSTUB_REPO = "0" }) @("-RepoPath", $DriveRepo) $false "Repository path unresolved"
# 3b. distro absent fails clearly
Invoke-Case "3b distro absent" (Merge-Hashtable $healthy @{ WSLSTUB_DISTROS = "Ubuntu-24.04" }) @("-Distro","openjm-rel1b4","-RepoPath", $DriveRepo) $false "is absent"
# 4. root + missing Python 3.11
Invoke-Case "4 root missing py311" (Merge-Hashtable $healthy @{ WSLSTUB_UID = "0"; WSLSTUB_PY311 = "0" }) @("-RepoPath", $DriveRepo) $false "Python 3.11 absent"
# 5. non-root with sudo, prerequisites present, delegates
Invoke-Case "5 non-root with sudo" $healthy @("-RepoPath", $DriveRepo) $true "DELEGATED"
# 6. non-root without sudo and missing prerequisite
Invoke-Case "6 non-root no sudo" (Merge-Hashtable $healthy @{ WSLSTUB_SUDO = "0"; WSLSTUB_PY311 = "0" }) @("-RepoPath", $DriveRepo) $false "cannot elevate privileges"
# 7. stock Noble: py311 absent, sudo available, must fail without apt-installing python
Invoke-Case "7 stock noble no py311" (Merge-Hashtable $healthy @{ WSLSTUB_PY311 = "0" }) @("-RepoPath", $DriveRepo) $false "Python 3.11 absent"
# 8. existing python 3.11 proceeds
Invoke-Case "8 py311 present" $healthy @("-RepoPath", $DriveRepo) $true "DELEGATED"
# 9. frontend included and Node absent
Invoke-Case "9 node absent" (Merge-Hashtable $healthy @{ WSLSTUB_NODE = "0" }) @("-RepoPath", $DriveRepo) $false "Node.js 22 is required"
# 9b. frontend included and wrong Node major
Invoke-Case "9b node wrong major" (Merge-Hashtable $healthy @{ WSLSTUB_NODE_VERSION = "v20.11.0" }) @("-RepoPath", $DriveRepo) $false "Node.js 22 is required"
# 10. -SkipFrontend propagates and Node is not required
Invoke-Case "10 skip frontend" (Merge-Hashtable $healthy @{ WSLSTUB_NODE = "0" }) @("-RepoPath", $DriveRepo, "-SkipFrontend") $true "--skip-frontend"
# 11. development profile propagation
Invoke-Case "11 development profile" $healthy @("-RepoPath", $DriveRepo, "-Profile", "development") $true "--profile development"
# 12. production profile propagation
Invoke-Case "12 production profile" $healthy @("-RepoPath", $DriveRepo, "-Profile", "production") $true "--profile production"
# 13. canonical installer non-zero exit is propagated (non-zero, naming the exit)
Invoke-Case "13 installer exit propagated" (Merge-Hashtable $healthy @{ WSLSTUB_INSTALL_EXIT = "7" }) @("-RepoPath", $DriveRepo) $false "exit 7"
# 14. missing git with no privilege available fails with the privilege message
Invoke-Case "14 git absent no privilege" (Merge-Hashtable $healthy @{ WSLSTUB_SUDO = "0"; WSLSTUB_GIT = "0" }) @("-RepoPath", $DriveRepo) $false "cannot elevate privileges"
# 15. missing git as root installs it via apt directly and continues
Invoke-Case "15 git absent as root" (Merge-Hashtable $healthy @{ WSLSTUB_UID = "0"; WSLSTUB_GIT = "0" }) @("-RepoPath", $DriveRepo) $true "DELEGATED"

# --- REL1-B4H review findings -------------------------------------------------

$uncRepo = "\\wsl.localhost\Ubuntu-24.04\home\sjeva\openjm-enterprise-ai"

# F2-1 valid C:\ maps to /mnt/c/...
Invoke-Case "F2-1 drive C maps" $healthy @("-RepoPath", "C:\rel1b4\OpenJM-Enterprise-AI") $true "DELEGATED" "/mnt/c/rel1b4/OpenJM-Enterprise-AI"

# F2-2 another drive maps to /mnt/d/... (a real drive letter via subst, removed after)
$substMade = $false
if (-not (Test-Path "D:\")) {
  $backing = Join-Path $env:TEMP "openjm-devtmp"
  New-Item -ItemType Directory -Force -Path (Join-Path $backing "OpenJM-Enterprise-AI") | Out-Null
  subst D: $backing | Out-Null
  $substMade = $true
}
if (Test-Path "D:\") {
  Invoke-Case "F2-2 drive D maps" $healthy @("-RepoPath", "D:\OpenJM-Enterprise-AI") $true "DELEGATED" "/mnt/d/OpenJM-Enterprise-AI"
} else {
  $script:failures += "F2-2 drive D maps (no D: available)"
  Write-Host "[FAIL] F2-2 drive D maps: no D: drive available to test"
}

# F2-3 UNC script location is refused, not mapped
Invoke-Case "F2-3 UNC script location refused" $healthy @() $false "Unsupported repository path"

# F2-4 explicit valid drive RepoPath overrides a UNC script location
Invoke-Case "F2-4 explicit drive overrides UNC" $healthy @("-RepoPath", "C:\rel1b4\OpenJM-Enterprise-AI") $true "Repository: C:\rel1b4\OpenJM-Enterprise-AI" "/mnt/c/rel1b4/OpenJM-Enterprise-AI"

# F2-5 explicit UNC RepoPath that exists is still refused
Invoke-Case "F2-5 explicit UNC refused" $healthy @("-RepoPath", $uncRepo) $false "Unsupported repository path"

# F1-1 uid != 0, sudo binary present but `sudo -n true` fails, prerequisite missing
Invoke-Case "F1-1 sudo present but not usable" (Merge-Hashtable $healthy @{ WSLSTUB_SUDO = ""; WSLSTUB_SUDO_BINARY = "1"; WSLSTUB_SUDO_USABLE = "0"; WSLSTUB_PY311 = "0" }) @("-RepoPath", "C:\rel1b4\OpenJM-Enterprise-AI") $false "cannot elevate privileges"
# F1-2 uid != 0, usable sudo, prerequisite missing -> same precise message path is
# not taken; the base package path may proceed, so assert the privilege state is
# reported as usable.
Invoke-Case "F1-2 usable sudo reported" (Merge-Hashtable $healthy @{ WSLSTUB_SUDO_BINARY = "1"; WSLSTUB_SUDO_USABLE = "1" }) @("-RepoPath", "C:\rel1b4\OpenJM-Enterprise-AI") $true "non-root with sudo"
# F1-3 uid 0 root path reported
Invoke-Case "F1-3 root reported" (Merge-Hashtable $healthy @{ WSLSTUB_UID = "0" }) @("-RepoPath", "C:\rel1b4\OpenJM-Enterprise-AI") $true "root (uid 0)"

if ($substMade) { subst D: /D | Out-Null }

Remove-Item -Recurse -Force $stubDir -ErrorAction SilentlyContinue

Write-Host ""
Write-Host ("WSL2 bootstrap harness: {0} passed, {1} failed" -f $passes, $failures.Count)
if ($failures.Count -gt 0) {
  Write-Host ("failed cases: " + ($failures -join ", "))
  exit 1
}
exit 0
