param(
    [string]$Document = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot "backend\.venv\Scripts\python.exe"
$Acceptance = Join-Path $RepoRoot "scripts\acceptance.py"

if (-not (Test-Path $Python)) {
    throw "Backend virtual environment not found. Run scripts\setup-windows.ps1 first."
}

$args = @($Acceptance)
if (-not [string]::IsNullOrWhiteSpace($Document)) {
    if (-not (Test-Path $Document)) {
        throw "Document not found: $Document"
    }
    $args += @("--document", (Resolve-Path $Document).Path)
}

& $Python @args
exit $LASTEXITCODE
