#Requires -Version 5.1
<#
.SYNOPSIS
  Fix Intel(R) Wireless Bluetooth Code 31 when a second (Realtek) radio is present.
.DESCRIPTION
  Root cause (verified 2026-09-18 on this host):
    - System log: BTHUSB Event 6 "Only one active Bluetooth adapter is
      supported at a time."
    - Intel USB\VID_8087&PID_0025 fails with CM_PROB_FAILED_ADD / Code 31,
      driver 24.20.0.3 (oem150.inf) is correctly bound — the driver itself
      is healthy, Windows is refusing to start the second radio.
    - The active Realtek radio also logs BTHUSB Event 34 on every boot:
      no LE peripheral-role support, so WinRT GATT advertising fails with
      WinError -2147024580. Switching to the onboard Intel radio is the
      fix for both issues.
  This script (requires elevation):
    1. Disables the Realtek Bluetooth Adapter (dongle).
    2. Enables the Intel(R) Wireless Bluetooth(R) adapter.
    3. Rescans devices + restarts the Bluetooth services.
    4. Prints final radio status + recent BTHUSB events for verification.
  To re-enable the dongle later: Enable-PnpDevice -InstanceId 'USB\VID_0BDA&PID_C820*' -Confirm:$false
  NOTE: disabling the active radio drops current BT connections briefly;
  devices re-pair/reconnect through the Intel radio afterwards.
#>
$ErrorActionPreference = "Stop"

function Assert-Admin {
  $p = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
  if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "This script must run elevated (Run as Administrator)."
  }
}
Assert-Admin

$IntelId = "USB\VID_8087&PID_0025\5&16B1B90B&0&14"
$RealtekWildcard = "USB\VID_0BDA&PID_C820*"

Write-Host "[*] Step 1/4: disabling Realtek adapter ($RealtekWildcard)..."
$rt = Get-PnpDevice | Where-Object { $_.InstanceId -like $RealtekWildcard }
if ($null -eq $rt) {
  Write-Host "[*] No Realtek VID_0BDA&PID_C820 device found; skipping disable."
} else {
  foreach ($d in $rt) {
    Write-Host ("    - {0} [{1}] status={2}" -f $d.FriendlyName, $d.InstanceId, $d.Status)
    if ($d.Status -ne "OK") {
      Write-Host ("    - already {0} ({1}); skipping disable." -f $d.Status, $d.Problem)
      continue
    }
    try {
      Disable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
    } catch {
      Write-Host ("    - disable skipped/failed (already inactive?): {0}" -f $_.Exception.Message)
    }
  }
  Write-Host "[+] Step 1 done (Realtek not active)."
}

Write-Host "[*] Step 2/4: restarting Intel adapter ($IntelId)..."
Write-Host ("    - problem status before restart: {0}" -f ((Get-PnpDeviceProperty -InstanceId $IntelId -KeyName DEVPKEY_Device_ProblemStatus -ErrorAction SilentlyContinue).Data))
# pnputil /restart-device power-cycles the USB function; Enable-PnpDevice is a
# no-op when the device is already "enabled" but stuck in FAILED_ADD.
& pnputil /restart-device "$IntelId"
Start-Sleep -Seconds 8

Write-Host "[*] Step 3/4: rescanning devices + restarting Bluetooth services..."
pnputil /scan-devices | Out-Null
Start-Sleep -Seconds 5
foreach ($svc in @("bthserv", "BTAGService")) {
  try { Restart-Service -Name $svc -Force -ErrorAction Stop; Write-Host ("    - {0} restarted." -f $svc) }
  catch { Write-Host ("    - {0} restart skipped: {1}" -f $svc, $_.Exception.Message) }
}
Start-Sleep -Seconds 5

Write-Host "[*] Step 4/4: verification..."
Get-PnpDevice -Class Bluetooth |
  Where-Object { $_.FriendlyName -like "*Bluetooth*Adapter*" -or $_.FriendlyName -like "*Wireless Bluetooth*" } |
  Format-Table FriendlyName, Status, Problem, InstanceId -AutoSize | Out-String | Write-Host

Write-Host "[*] Recent BTHUSB events (look for absence of Event 6 / 34):"
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName=@('BTHUSB')} -MaxEvents 6 -ErrorAction SilentlyContinue |
  Select-Object TimeCreated, Id, Message | Format-Table -Wrap -AutoSize | Out-String | Write-Host

$intel = Get-PnpDevice -InstanceId $IntelId -ErrorAction SilentlyContinue
if ($intel -and $intel.Status -eq "OK") {
  Write-Host "[+] SUCCESS: Intel(R) Wireless Bluetooth(R) is OK."
  Write-Host "    Next: re-test BLE advertising: python src/main_windows.py --mode ble"
  Write-Host "    If advertising still fails, the radio lacks LE peripheral role"
  Write-Host "    (check System log for BTHUSB Event 34 after reboot)."
} else {
  Write-Host ("[!] Intel status: {0} / problem: {1}" -f $intel.Status, $intel.Problem)
  Write-Host "    Next steps if still Error:"
  Write-Host "    1. Full shutdown (not Restart), unplug power 30s, boot (power-cycles the Intel USB function)."
  Write-Host "    2. Then: pnputil /remove-device ""$IntelId"" ; pnputil /scan-devices (keep driver, fresh enum)."
  Write-Host "    3. Then: reinstall Intel BT driver 24.x from Intel.com."
  Write-Host "    4. Check BIOS: onboard Bluetooth/WiFi must be Enabled; USB autosuspend off for the Intel port."
}
