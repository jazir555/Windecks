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

## BLE validation (2026-09-18, host with Realtek BT + broken Intel BT)

- GATT database construction via WinRT: **works**. Providers + characteristics
  created with `error=0`, including NOTIFY characteristics and the Valve
  128-bit custom UUIDs (`100F6C7A/7C/34`).
- Fixes found by live testing: `GattServiceProvider.create_async` and
  `create_characteristic_async` require `uuid.UUID` (Guid), not `str`;
  characteristic read/write event handlers fire on arbitrary WinRT threads
  and must marshal via `loop.call_soon_threadsafe`.
- Advertising on this host: **fails** — `start_advertising_with_parameters`
  raises `WinError -2147024580` ("device does not support the command
  feature"). The radio lacks LE peripheral-role support (Intel BT adapter
  is in driver-Error state; Realtek is the active radio). `WinBleServer`
  reports this as a clear `RuntimeError`; `--mode vigem` is unaffected.
- Note: this host already shows a cached `BTHLEDEVICE … VID&0228DE` +
  `SteamController` entry — a previous Deck SC2-spoof connection — so the
  Windows HOGP host side is known-good; only local advertising needs a
  capable radio to re-validate end to end.

## Steam visibility

- `--mode vigem`: Xbox 360 pad in Steam. Playable today; Steam Input sees a
  standard Xbox controller (no SC2 trackpad/gyro/haptic pages).
- `--mode ble`: advertises the SC2 GATT database (PnP VID `28DE` PID `1303`,
  Valve custom service). Validate with `bluetoothctl`-equivalent / nRF
  Connect, then Steam. Host pairing is done in Windows Settings (no agent).
