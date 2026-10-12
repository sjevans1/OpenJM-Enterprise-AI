$all = ($args -join ' ')
if ($all -match '--status') { Write-Output 'Default Version: 2'; exit 0 }
if ($all -match '--list') {
  if ([string]::IsNullOrWhiteSpace($env:WSLSTUB_DISTROS)) { exit 0 }
  ($env:WSLSTUB_DISTROS -split ';') | ForEach-Object { Write-Output $_ }
  exit 0
}
if ($all -match 'REPO=') {
  Write-Output $env:WSLSTUB_UID
  if ($env:WSLSTUB_SUDO -eq '1') { Write-Output 'PRIV=SUDO' } else { Write-Output 'PRIV=NO_SUDO' }
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
