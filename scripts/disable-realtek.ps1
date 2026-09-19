#Requires -Version 5.1
<#
.SYNOPSIS
  Disable all Realtek 8821CU USB functions (Bluetooth + WiFi) so the
  onboard Intel stack is the only radio. Requires elevation.
  Result log is written to $env:TEMP\disable-realtek-result.txt
#>
$ErrorActionPreference = "Continue"
$result = "$env:TEMP\disable-realtek-result.txt"
"disable-realtek run: $(Get-Date -Format o)" | Out-File $result

$p = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  "ERROR: not elevated" | Out-File $result -Append
  exit 1
}

foreach ($d in (Get-PnpDevice | Where-Object { $_.InstanceId -like "USB\VID_0BDA&PID_C820*" })) {
  $line = "{0} [{1}] status={2}" -f $d.FriendlyName, $d.InstanceId, $d.Status
  $line | Out-File $result -Append
  if ($d.Status -eq "OK") {
    try {
      Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
      "  -> disabled" | Out-File $result -Append
    } catch {
      "  -> disable failed: $($_.Exception.Message)" | Out-File $result -Append
    }
  } else {
    try {
      Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
      "  -> formally disabled (was $($d.Status))" | Out-File $result -Append
    } catch {
      "  -> already inactive, left as-is ($($d.Status))" | Out-File $result -Append
    }
  }
}
"done" | Out-File $result -Append
