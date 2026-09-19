#Requires -Version 5.1
<#
.SYNOPSIS
  Bring up the Realtek 8821CU WiFi NIC without disturbing Intel Bluetooth.
.DESCRIPTION
  The Realtek PID_C820 dongle is a single composite USB device
  (USB\VID_0BDA&PID_C820\123456) whose functions are:
    - MI_00  Realtek Bluetooth Adapter
    - MI_02  Realtek 8821CU Wireless LAN 802.11ac USB NIC
  The earlier Intel-BT fix disabled the whole composite parent, which put
  BOTH functions into CM_PROB_PHANTOM (WiFi included) so the Intel radio
  could be the sole active BT radio.
  WiFi (MI_02) is not gated by Windows' single-active-BT-radio policy — it
  was only down because its parent was disabled. So we can get WiFi back
  WITHOUT re-opening the BT conflict:
    1. Enable the composite parent + the MI_02 WiFi function.
    2. Individually DISABLE only the Realtek BT function (MI_00) so it does
       not try to become the BT radio and kick Intel back to Code 31.
    3. Keep Intel active; verify composite + WiFi are OK and Intel still OK.
  Requires elevation.
#>
$ErrorActionPreference = "Stop"

function Assert-Admin {
  $p = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
  if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "This script must run elevated (Run as Administrator)."
  }
}
Assert-Admin

$CompositeId  = "USB\VID_0BDA&PID_C820\123456"
$RealtekBtId  = "USB\VID_0BDA&PID_C820&MI_00\7&1D92DF5B&0&0000"
$RealtekWifiId = "USB\VID_0BDA&PID_C820&MI_02\7&1D92DF5B&0&0002"
$IntelId      = "USB\VID_8087&PID_0025\5&16B1B90B&0&14"

Write-Host "[*] Step 1/4: enabling Realtek composite parent ($CompositeId)..."
$comp = Get-PnpDevice -InstanceId $CompositeId -ErrorAction SilentlyContinue
if ($null -eq $comp) {
  Write-Host "[!] Composite not found — is the Realtek dongle plugged in?"
} elseif ($comp.Status -eq "OK") {
  Write-Host "[*] Composite already OK; skipping enable."
} else {
  try { Enable-PnpDevice -InstanceId $CompositeId -Confirm:$false -ErrorAction Stop; Write-Host "[+] Composite enable requested." }
  catch { Write-Host ("[!] Composite enable failed: {0}" -f $_.Exception.Message) }
}
Start-Sleep -Seconds 5
pnputil /scan-devices | Out-Null
Start-Sleep -Seconds 5

Write-Host "[*] Step 2/4: enabling Realtek WiFi function ($RealtekWifiId)..."
$wifi = Get-PnpDevice -InstanceId $RealtekWifiId -ErrorAction SilentlyContinue
if ($null -eq $wifi) {
  Write-Host "[!] WiFi function not found after enable; dongle may need replug."
} elseif ($wifi.Status -eq "OK") {
  Write-Host "[*] WiFi already OK; skipping enable."
} else {
  try { Enable-PnpDevice -InstanceId $RealtekWifiId -Confirm:$false -ErrorAction Stop; Write-Host "[+] WiFi enable requested." }
  catch { Write-Host ("[!] WiFi enable failed: {0}" -f $_.Exception.Message) }
  # Enable is a no-op on a phantom device that never re-enumerated; power-cycle it.
  & pnputil /restart-device "$RealtekWifiId"
}
Start-Sleep -Seconds 8

Write-Host "[*] Step 3/4: disabling ONLY Realtek BT function ($RealtekBtId) to protect Intel...$([Environment]::NewLine)      (composite may wake MI_00; we keep it off so Intel stays the BT radio)"
$bt = Get-PnpDevice -InstanceId $RealtekBtId -ErrorAction SilentlyContinue
if ($null -eq $bt) {
  Write-Host "[*] Realtek BT function not present; nothing to disable."
} else {
  Write-Host ("    current: status={0} problem={1}" -f $bt.Status, $bt.Problem)
  if ($bt.Status -ne "OK") {
    Write-Host "    already not active; using pnputil to ensure off."
  }
  try { Disable-PnpDevice -InstanceId $RealtekBtId -Confirm:$false -ErrorAction Stop; Write-Host "[+] Realtek BT disabled." }
  catch { Write-Host ("[!] Disable failed: {0} (continuing)" -f $_.Exception.Message) }
}
Start-Sleep -Seconds 3

Write-Host "[*] Step 4/4: verification..."
Write-Host "--- Realtek PID_C820 devices ---"
Get-PnpDevice | Where-Object { $_.InstanceId -like "USB\VID_0BDA&PID_C820*" } |
  Format-Table FriendlyName, Status, Problem, Class -AutoSize | Out-String | Write-Host
Write-Host "--- Bluetooth radios ---"
Get-PnpDevice -Class Bluetooth |
  Where-Object { $_.FriendlyName -like "*Bluetooth*Adapter*" -or $_.FriendlyName -like "*Wireless Bluetooth*" } |
  Format-Table FriendlyName, Status, Problem -AutoSize | Out-String | Write-Host
Write-Host "--- Network adapters (Realtek 8821CU) ---"
Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue |
  Where-Object { $_.InterfaceDescription -like "*8821*" } |
  Format-Table Name, InterfaceDescription, Status, MacAddress -AutoSize | Out-String | Write-Host

$intel = Get-PnpDevice -InstanceId $IntelId -ErrorAction SilentlyContinue
$wi = Get-PnpDevice -InstanceId $RealtekWifiId -ErrorAction SilentlyContinue
$ct = Get-PnpDevice -InstanceId $CompositeId -ErrorAction SilentlyContinue
if ($wi -and $wi.Status -eq "OK" -and $intel -and $intel.Status -eq "OK") {
  Write-Host "[+] SUCCESS: Realtek WiFi is OK and Intel Bluetooth is still OK."
  Write-Host "    Use the WiFi function's network (e.g. netsh wlan connect or Settings)."
} else {
  Write-Host ("[!] Composite={0} WiFi={1} Intel={2}" -f $ct.Status, $wi.Status, $intel.Status)
  Write-Host "    Next steps:"
  Write-Host "    1. Unplug/replug the Realtek dongle to re-enumerate its composite."
  Write-Host "    2. pnputil /scan-devices."
  Write-Host "    3. Re-run this script."
}