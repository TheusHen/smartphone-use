<#
.SYNOPSIS
  Installs Smartphone Use dependencies on Windows (ADB + scrcpy + Python helpers).

.DESCRIPTION
  - Installs Android platform-tools (adb) and scrcpy via winget.
  - Installs Python `uiautomator2` (skip with -NoUiautomator) and `pillow`
    (needed for `screenshot --annotate`).
  - Verifies `adb --version` and `scrcpy --version` at the end.
  - Idempotent: safe to run multiple times.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/install-deps.ps1
#>
[CmdletBinding()]
param(
  [switch]$NoUiautomator
)

$ErrorActionPreference = 'Stop'

function Test-Command($Name) {
  $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Install-WithWinget($PackageId, $ExeName) {
  if (Test-Command $ExeName) {
    Write-Host "[ok] $ExeName already installed." -ForegroundColor Green
    return
  }
  if (-not (Test-Command 'winget')) {
    throw "winget not found and $ExeName is missing. Install $PackageId manually: https://winget.run"
  }
  Write-Host "Installing $PackageId via winget..." -ForegroundColor Cyan
  winget install --exact --id $PackageId --source winget `
    --accept-source-agreements --accept-package-agreements
}

try {
  # 1. ADB (Android platform-tools).
  Install-WithWinget 'Google.PlatformTools' 'adb'

  # 2. scrcpy (live mirror / human control).
  Install-WithWinget 'Genymobile.scrcpy' 'scrcpy'

  # 3. Python helpers: uiautomator2 (optional) + Pillow (screenshot --annotate).
  if (Test-Command 'python') {
    if (-not $NoUiautomator) {
      Write-Host 'Installing Python package: uiautomator2...' -ForegroundColor Cyan
      python -m pip install --upgrade uiautomator2
    }
    Write-Host 'Installing Python package: pillow...' -ForegroundColor Cyan
    python -m pip install --upgrade pillow
  } else {
    Write-Warning 'Python not found; skipping Python helpers (phone.py core works without them).'
  }

  # 4. Verify.
  Write-Host ''
  Write-Host 'Verification:' -ForegroundColor Cyan
  $adbVersion = (adb --version 2>&1 | Select-Object -First 1)
  $scrcpyVersion = (scrcpy --version 2>&1 | Select-Object -First 1)
  Write-Host "[ok] $adbVersion" -ForegroundColor Green
  Write-Host "[ok] $scrcpyVersion" -ForegroundColor Green
  Write-Host ''
  Write-Host 'Next steps:' -ForegroundColor Cyan
  Write-Host '  1. Open a NEW terminal (PATH refresh).'
  Write-Host '  2. python scripts/phone.py status --json'
  Write-Host '  3. Enable USB debugging on the phone (see references/usb-setup.md).'
} catch {
  Write-Error "install-deps failed: $($_.Exception.Message)"
  exit 1
}
