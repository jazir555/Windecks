#Requires -Version 5.1
<#
.SYNOPSIS
  Run the Windecks Windows entrypoint.
.PARAMETER Mode
  vigem (default) | ble | both
.PARAMETER Input
  synthetic | pygame | auto (default)
.PARAMETER Name
  BLE device name for -Mode ble/both.
#>
param(
  [ValidateSet("vigem", "ble", "both")][string]$Mode = "vigem",
  [ValidateSet("synthetic", "pygame", "auto")][string]$Input = "auto",
  [string]$Name = "Steam Controller 2026"
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$env:PYTHONPATH = (Join-Path $RepoRoot "src") + ";" + $env:PYTHONPATH
python (Join-Path $RepoRoot "src\main_windows.py") --mode $Mode --input $Input --name $Name
