<#
.SYNOPSIS
  One-command installer for Smartphone Use (run from the repo root).

.DESCRIPTION
  1. Installs dependencies via skills/smartphone-use/scripts/install-deps.ps1
     (adb + scrcpy + Pillow; pass -NoUiautomator to skip uiautomator2).
  2. Links the canonical skills/smartphone-use for every SKILL.md-compatible
     agent (.agents = Codex, .claude = Claude Code, .cursor = Cursor).
  3. Runs `phone.py status` so you see exactly what is missing (cable? RSA prompt?).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1
#>
[CmdletBinding()]
param(
  [switch]$NoUiautomator
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Skill = Join-Path $Root 'skills/smartphone-use'

function Link-Skill($LinkDir) {
  $link = Join-Path $Root (Join-Path $LinkDir 'smartphone-use')
  if (Test-Path -LiteralPath $link) {
    Write-Host "[ok] $LinkDir/smartphone-use already linked." -ForegroundColor Green
    return
  }
  New-Item -ItemType Directory -Path (Join-Path $Root $LinkDir) -Force | Out-Null
  try {
    New-Item -ItemType SymbolicLink -Path $link -Target $Skill | Out-Null
    Write-Host "[ok] Linked $LinkDir/smartphone-use (symlink)." -ForegroundColor Green
  } catch {
    # Symlinks need Developer Mode/admin; junctions always work for local dirs.
    New-Item -ItemType Junction -Path $link -Target $Skill | Out-Null
    Write-Host "[ok] Linked $LinkDir/smartphone-use (junction fallback)." -ForegroundColor Green
  }
}

# 1. Dependencies.
$deps = Join-Path $Skill 'scripts/install-deps.ps1'
if ($NoUiautomator) {
  & $deps -NoUiautomator
} else {
  & $deps
}

# 2. Skill links (canonical skills/ stays the single source of truth).
Link-Skill '.agents/skills'
Link-Skill '.claude/skills'
Link-Skill '.cursor/skills'

# 3. Diagnose (informational: a missing device is normal on first run).
# winget/pip only refresh PATH for NEW terminals — reload it here so the
# status check below sees freshly installed tools in THIS session.
$env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') +
  ';' + [System.Environment]::GetEnvironmentVariable('Path', 'User')
Write-Host ''
Write-Host 'Diagnosis (missing device on first run is normal):' -ForegroundColor Cyan
$phone = Join-Path $Skill 'scripts/phone.py'
python $phone status
Write-Host ''
Write-Host 'Next: enable USB debugging (see skills/smartphone-use/references/usb-setup.md),' -ForegroundColor Cyan
Write-Host 'then: python scripts/phone.py connect-usb   (from skills/smartphone-use/)' -ForegroundColor Cyan
