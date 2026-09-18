#Requires -Version 5.1
<#
.SYNOPSIS
  First-time Windows setup for Windecks: Python deps + ViGEmBus driver.
.DESCRIPTION
  1. Installs Python packages (pip install --pre for the winsdk beta).
  2. Installs the ViGEmBus driver silently (required by vgamepad).
     vgamepad 0.1.0's setup.py otherwise launches the MSI interactively
     and hangs non-interactive pip installs — install the driver FIRST.
#>
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "[*] Installing Python dependencies..."
pip install --pre -r (Join-Path $RepoRoot "requirements-windows.txt")

$VigemUrl = "https://github.com/ViGEm/ViGEmBus/releases/download/setup-v1.17.333/ViGEmBusSetup_x64.msi"
$MsiPath = Join-Path $env:TEMP "ViGEmBusSetup_x64.msi"

$Found = Get-Service -Name ViGEmBus -ErrorAction SilentlyContinue
if ($Found) {
  Write-Host "[*] ViGEmBus driver already installed."
} else {
  Write-Host "[*] Downloading ViGEmBus driver..."
  Invoke-WebRequest -Uri $VigemUrl -OutFile $MsiPath
  Write-Host "[*] Installing ViGEmBus silently (requires elevation)..."
  Start-Process msiexec.exe -ArgumentList "/i `"$MsiPath`" /qn /norestart" -Verb RunAs -Wait
  Write-Host "[+] ViGEmBus installed."
}

Write-Host "[+] Setup complete. Run: powershell -File scripts/run-windows.ps1"
