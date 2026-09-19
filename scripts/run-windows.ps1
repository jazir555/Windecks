#Requires -Version 5.1
<#
.SYNOPSIS
  Run the Windecks Windows entrypoint.
.PARAMETER Mode
  vigem (default) | hidmaestro | ble | both
.PARAMETER Input
  synthetic | pygame | auto (default)
.PARAMETER Name
  BLE device name for -Mode ble/both.
.PARAMETER Profile
  HIDMaestro profile id for -Mode hidmaestro (default steam-controller-2).
.PARAMETER NoRumble
  Disable host->physical rumble routing.
#>
param(
  [ValidateSet("vigem", "hidmaestro", "ble", "both")][string]$Mode = "vigem",
  [ValidateSet("synthetic", "pygame", "auto")][string]$Input = "auto",
  [string]$Name = "Steam Controller 2026",
  [string]$Profile = "steam-controller-2",
  [switch]$NoRumble
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$env:PYTHONPATH = (Join-Path $RepoRoot "src") + ";" + $env:PYTHONPATH
$Args = @("--mode", $Mode, "--input", $Input, "--name", $Name, "--profile", $Profile)
if ($NoRumble) { $Args += "--no-rumble" }
python (Join-Path $RepoRoot "src\main_windows.py") @Args
