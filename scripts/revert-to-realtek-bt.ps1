#Requires -Version 5.1
<#
.SYNOPSIS
  Revert the Intel BT fix: disable Intel, switch back to the Realtek radio.
.DESCRIPTION
  Mirror of scripts/fix-intel-bt.ps1 with the roles swapped.
  This script (requires elevation):
    1. Disables the Intel(R) Wireless Bluetooth(R) adapter.
    2. Re-enables the Realtek composite dongle (parent first) and
       restarts the Realtek Bluetooth Adapter function.
    3. Rescans devices + restarts the Bluetooth services.
    4. Prints final radio status + recent BTHUSB events for verification.
  WARNING: the Realtek radio lacks LE peripheral role (BTHUSB Event 34),
  so `python src/main_windows.py --mode ble` advertising will fail again
  with WinError -2147024580 while Realtek is active. This revert is for
  connectivity fallback only.
  NOTE: re-enabling the Realtek composite also brings back its 8821CU
  WiFi NIC (MI_02) as a second wireless adapter; disable it in Device
  Manager if unwanted. Disabling the active radio drops current BT
  connections briefly; devices reconnect through the Realtek radio.
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
$RealtekBtId = "USB\VID_0BDA&PID_C820&MI_00\7&1D92DF5B&0&0000"

Write-Host "[*] Step 1/4: disabling Intel adapter ($IntelId)..."
$in = Get-PnpDevice -InstanceId $IntelId -ErrorAction SilentlyContinue
if ($null -eq $in) {
  Write-Host "[*] No Intel VID_8087&PID_0025 device found; skipping disable."
} elseif ($in.Status -ne "OK") {
  Write-Host ("[*] Intel already {0} ({1}); skipping disable." -f $in.Status, $in.Problem)
} else {
  try {
    Disable-PnpDevice -InstanceId $IntelId -Confirm:$false -ErrorAction Stop
    Write-Host "[+] Intel disabled."
  } catch {
    Write-Host ("[!] Intel disable failed: {0}" -f $_.Exception.Message)
  }
}

Write-Host "[*] Step 2/4: re-enabling Realtek adapter ($RealtekWildcard)..."
$rt = Get-PnpDevice | Where-Object { $_.InstanceId -like $RealtekWildcard } |
  Sort-Object { if ($_.InstanceId -like "*MI_*") { 1 } else { 0 } }, InstanceId
if ($null -eq $rt) {
  Write-Host "[*] No Realtek VID_0BDA&PID_C820 device found; is the dongle plugged in?"
} else {
  foreach ($d in $rt) {
    Write-Host ("    - {0} [{1}] status={2}" -f $d.FriendlyName, $d.InstanceId, $d.Status)
    try {
      # Composite parent must be enabled before its MI_00/MI_02 functions.
      Enable-PnpDevice -InstanceId $d.InstanceId -Confirm:$false -ErrorAction Stop
      Write-Host "      enable requested."
    } catch {
      Write-Host ("      enable skipped/failed: {0}" -f $_.Exception.Message)
    }
  }
  Start-Sleep -Seconds 5
  # pnputil /restart-device power-cycles the USB function; Enable-PnpDevice
  # alone is a no-op on a device stuck in a failed/phantom state.
  Write-Host ("    - restarting BT function ($RealtekBtId)...")
  & pnputil /restart-device "$RealtekBtId"
  Start-Sleep -Seconds 8
  Write-Host "[+] Step 2 done."
}

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

Write-Host "[*] Recent BTHUSB events (expect Event 6/34 while Realtek is active):"
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName=@('BTHUSB')} -MaxEvents 6 -ErrorAction SilentlyContinue |
  Select-Object TimeCreated, Id, Message | Format-Table -Wrap -AutoSize | Out-String | Write-Host

$bt = Get-PnpDevice -InstanceId $RealtekBtId -ErrorAction SilentlyContinue
if ($bt -and $bt.Status -eq "OK") {
  Write-Host "[+] SUCCESS: Realtek Bluetooth Adapter is OK (Intel disabled)."
  Write-Host "    Reminder: --mode ble advertising is NOT supported on this radio"
  Write-Host "    (BTHUSB Event 34, WinError -2147024580). Re-run fix-intel-bt.ps1"
  Write-Host "    to switch back to Intel for BLE GATT-server work."
} else {
  Write-Host ("[!] Realtek BT status: {0} / problem: {1}" -f $bt.Status, $bt.Problem)
  Write-Host "    Next steps if still not OK:"
  Write-Host "    1. Unplug/replug the Realtek dongle (power-cycles its USB functions)."
  Write-Host "    2. Then: pnputil /scan-devices."
  Write-Host "    3. Check the dongle's composite parent (USB\VID_0BDA&PID_C820) is Enabled in Device Manager."
}
