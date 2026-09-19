# Windows Port

Windecks on Windows replaces each Linux-only layer with a Windows equivalent.
`src/gatt_db.py` and `src/sc2_commands.py` are pure Python and reused as-is.

| Linux (spoofdeck) | Windows (Windecks) | Status |
|---|---|---|
| `gatt_db.py` | reuse as-is | verified |
| SC2 Feature Report logic | `src/sc2_commands.py` | verified |
| `att_server.py` (L2CAP CID 4 raw socket) | `src/win_ble.py` (WinRT `GattServiceProvider`) | experimental |
| `adv.py`/`bluez.py` (BlueZ advertising) | `win_ble.py` advertising | experimental |
| `agent.py` (BlueZ Agent1) | not needed — Windows handles SMP pairing | n/a |
| `main_virtual_usb.py` (vhci_hcd) + `main_uhid.py` (/dev/uhid) | `src/win_vigem.py` (ViGEmBus `VX360Gamepad`) | working |
| `input_handler.py` Neptune `/dev/hidraw3` + evdev | `src/win_input.py` (pygame/XInput + synthetic) | working |
| `fcntl` HID feature-report ioctls | in-memory responses in `sc2_commands.py` | done |
| `scripts/*.sh` | `scripts/setup-windows.ps1`, `scripts/run-windows.ps1` | done |
| entrypoint | `src/main_windows.py` (`--mode vigem|ble|both`) | done |

## Setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-windows.ps1
powershell -File scripts/run-windows.ps1 -Mode vigem
```

## Key constraints

- Windows has `socket.AF_BLUETOOTH` but no L2CAP CID 4 / BlueZ — a raw ATT
  server is not portable; WinRT GATT server is the path.
- `main_virtual_usb.py` imports `fcntl` at top — cannot be imported on
  Windows; hence the `sc2_commands.py` extraction.
- ViGEmBus emulates Xbox 360 / DS4 (VID `045E`/`054C`), so it cannot spoof
  SC2 VID `28DE`/PID `1303` — Steam sees an Xbox pad, not an SC2, via that
  path. True SC2 spoof on Windows requires the BLE GATT-server path.
- WinRT GATT server needs `bleak` (pulls in `winrt-*` projections) and the
  Bluetooth capability; HID-over-GATT (0x1812) as a published service is
  untested against the Windows HOGP host + Steam.
- `vgamepad` 0.1.0's `setup.py` launches the ViGEmBus MSI interactively,
  hanging non-interactive pip installs. Install the driver first
  (`setup-windows.ps1` uses `msiexec /qn`); pip then detects it and skips
  the prompt. Without the driver, `import vgamepad` raises
  `VIGEM_ERROR_BUS_NOT_FOUND` at import time — all Windecks imports of it
  are lazy/guarded for this reason.

## BLE validation (2026-09-18/19, host with Realtek BT + Intel BT)

- GATT database construction via WinRT: **works**. Providers + characteristics
  created with `error=0`, including NOTIFY characteristics and the Valve
  128-bit custom UUIDs (`100F6C7A/7C/34`).
- Fixes found by live testing: `GattServiceProvider.create_async` and
  `create_characteristic_async` require `uuid.UUID` (Guid), not `str`;
  characteristic read/write event handlers fire on arbitrary WinRT threads
  and must marshal via `loop.call_soon_threadsafe`.
- Publication coverage (2026-09-19, `src/win_ble.py`): full `gatt_db` order
  is mirrored — HID publishes info, protocol mode, report map, control
  point, inputs 0x01 (12 B) / 0x45 (45 B) / 0x47 (47 B) / mouse / keyboard,
  outputs 0x02 + haptic 0x80, and all six feature reports
  (0x02/0x01/0x85/0x86/0x87/0x8F); DIS publishes manufacturer, model,
  serial (`F0000-...`, matching the `GET_SERIAL` first-byte rule), fw/hw/sw
  revisions + PnP (`28DE`/`1303`); Battery + Valve ch1/ch2/report are all
  present. All four providers advertise (HID, Battery, DIS, Valve), not
  just HID. Feature reads are served from `SC2CommandHandler` so a host
  `GET_REPORT` after `SET_REPORT` returns the queued response; 0x80 writes
  are parsed (both full and hog-ll-stripped forms) and delivered to the
  `on_haptic` callback. `update_reports()` mirrors Linux `forward_report`:
  45 B → HID 0x45 + Valve ch1, 47 B → HID 0x47 + Valve ch2, plus 12 B /
  mouse / keyboard / battery.
- 47-byte note: the Linux Neptune path only ever sends 12 B + 45 B, so
  `win_input.build_47b()` is a documented placeholder (45 B fields + 2
  zero bytes) until the firmware ch2 format is confirmed.
- Advertising: **works on the Intel radio** (verified 2026-09-18 after the
  Code 31 fix below) — `[+] WinRT BLE advertising as 'Steam Controller 2026'
  (4 services)`. It previously failed on the Realtek radio with
  `WinError -2147024580`. No new BTHUSB Event 34 after switching to Intel,
  confirming LE peripheral-role support. `WinBleServer` still reports a
  clear `RuntimeError` if advertising is ever unsupported; `--mode vigem`
  is unaffected.
- Note: the cached `SteamController` BTHLE entry on this host is a Steam
  Controller 1 (separate device) — ignore it for SC2 validation.
- Status quirk: advertisement status briefly reports ABORTED(3) right after
  start before settling to STARTED(2) — transient on this radio, not fatal
  (logged by the status handler in `win_ble.py`).
- OTA verifier (2026-09-19, `src/verify_windows_ble.py`, bleak central):
  run on a SECOND device while `--mode ble` advertises —
  `python src/verify_windows_ble.py` scans for the device name, checks all
  four services, reads PnP (`28DE`/`1303`), battery, HID info, report map,
  and subscribes to NOTIFY. Same-host scan-while-advertising is not
  expected to work (radio is in peripheral role). Still open:
  over-the-air confirmation from a second device (nRF Connect /
  Deck `bluetoothctl` discovery + Steam Input recognizing the SC2).

## ViGEm validation (2026-09-18, ViGEmBus driver installed)

- `import vgamepad` OK; `WinVigemTarget` connects to a real `VX360Gamepad`.
- `main_windows.py --mode vigem --input synthetic` runs end to end
  (Xbox 360 target connected, synthetic reports flowing).
- Spot-check: SC2 `0x0001|0x0800|0x4000` → XUSB `0x1009` (A + DPAD_UP +
  DPAD_RIGHT) with sticks/triggers passed through — mapping correct.

## Intel Bluetooth Code 31 fix (dual-radio conflict, 2026-09-18)

Symptoms: `Intel(R) Wireless Bluetooth(R)` (`USB\VID_8087&PID_0025`) shows
`CM_PROB_FAILED_ADD` / Code 31 while `Realtek Bluetooth Adapter`
(`USB\VID_0BDA&PID_C820`) is OK.

Root cause: Windows supports only **one active Bluetooth adapter at a time**
(System log: BTHUSB Event 6 "Only one active Bluetooth adapter is supported
at a time"). The Intel driver (24.20.0.3, `oem150.inf`) is correctly bound —
`setupapi.dev.log` shows the driver install succeeding — so this is a
policy block, not a corrupt driver. Bonus: the Realtek radio lacks LE
peripheral role (BTHUSB Event 34), which is exactly what `--mode ble`
advertising needs, so the Intel radio is the one to keep.

Fix (elevated): `powershell -ExecutionPolicy Bypass -File scripts/fix-intel-bt.ps1`
which skips the already-disabled Realtek adapter, power-cycles the Intel
adapter via `pnputil /restart-device` (`Enable-PnpDevice` is a no-op on a
device stuck in `FAILED_ADD`), rescans devices (`pnputil /scan-devices`),
and restarts `bthserv`/`BTAGService` (with `-Force`).
Verified 2026-09-18: Intel went `Error/CM_PROB_FAILED_ADD` (problem status
`3221225473`) → `OK/CM_PROB_NONE`, BTHUSB logged only Event 18 (link-key
notice, benign) with no new Event 6/34, and `--mode ble` advertising
succeeded.
Re-enable the dongle later with:
`Enable-PnpDevice -InstanceId 'USB\VID_0BDA&PID_C820*' -Confirm:$false`.
If Intel is still Code 31 after the script: cold reboot (full shutdown, not
fast-startup restart) so it enumerates as the sole radio; then Device
Manager > uninstall device (keep driver) > scan; then reinstall Intel BT
24.x; then check BIOS onboard-Bluetooth is Enabled.

## Steam visibility

- `--mode vigem`: Xbox 360 pad in Steam. Playable today; Steam Input sees a
  standard Xbox controller (no SC2 trackpad/gyro/haptic pages).
- `--mode ble`: advertises the SC2 GATT database (PnP VID `28DE` PID `1303`,
  Valve custom service). Validate with `bluetoothctl`-equivalent / nRF
  Connect, then Steam. Host pairing is done in Windows Settings (no agent).
