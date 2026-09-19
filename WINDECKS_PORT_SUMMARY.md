# Windecks — Windows Port Summary

**Date:** 2026-09-18
**Fork:** https://github.com/jazir555/Windecks.git (cloned to `~/Documents/Windecks`)
**Upstream working dir (do not use):** `~/Documents/spoofdeck` (original repo — port files copied from there)

## What was done this session

1. Cloned the `Windecks` fork fresh into `C:\Users\mmeadow\Documents\Windecks` (origin = fork URL, HEAD = `20ab13e`).
2. Started the Linux → Windows port. Copied 3 new, verified-working files from spoofdeck:
   - `requirements-windows.txt` (new)
   - `src/platform_compat.py` (new)
   - `src/sc2_commands.py` (new)
 3. Smoke-tested on Windows (Python 3.11, `sys.platform == 'win32'`):
    - `gatt_db.build_sc2_database()` → 90 attributes, 6 services — OK, no Linux deps
    - `SC2CommandHandler` SET/GET_REPORT round-trip: `0x83` → `64 B`, `GET_SERIAL` → `serial[0]='F'`, `status=0x01` — OK
    - `platform_compat` → `IS_WINDOWS=True`, `HAS_VGAMEPAD=False` (driver not installed), `HAS_WINSDK=True` (via winrt-*), `HAS_BLEAK=True`, `HAS_PYGAME=True`
4. Implemented the full Windows stack (all driver-free testable, `tests/test_windows_port.py` — 11 passed):
    - `src/win_vigem.py` — SC2 12-byte → `VX360Gamepad` map + host-rumble callback
    - `src/win_input.py` — synthetic 10 Hz + pygame joystick source (same report-dict shape)
    - `src/main_windows.py` — entrypoint (`--mode vigem|ble|both`, `--input synthetic|pygame|auto`)
    - `src/win_ble.py` — WinRT `GattServiceProvider` server (experimental, HOGP interop untested)
    - `scripts/setup-windows.ps1` / `scripts/run-windows.ps1`, `docs/windows-port.md`
5. Fixed `requirements-windows.txt`: `winsdk>=1.0.0b1` (beta-only, use `pip install --pre`; `winrt-*` via bleak also works), `vgamepad>=0.1.0` (no 1.0 exists; driver MSI must be installed FIRST or pip hangs on the interactive installer).
6. `--mode ble` live validation (2026-09-18, this host): GATT build works end-to-end via WinRT (`error=0` on all providers/characteristics incl. NOTIFY + Valve 128-bit UUIDs). Two live-test bugs fixed: `create_async` needs `uuid.UUID` (not `str`); r/w event handlers must marshal via `loop.call_soon_threadsafe`. Advertising initially failed on the Realtek radio (`WinError -2147024580`, no LE peripheral role; see Intel BT fix 2026-09-18 below) — `WinBleServer` raises a clear `RuntimeError` for that case. The cached `SteamController` BTHLE entry is a Steam Controller 1 (separate device) — ignore for SC2 validation. `tests/test_windows_port.py`: 12/12 pass.
7. Intel Bluetooth Code 31 fixed (2026-09-18, this host): root cause was Windows' single-active-radio policy (BTHUSB Event 6), not a bad driver — Realtek 8821CU dongle (`VID_0BDA&PID_C820`) blocked onboard Intel (`VID_8087&PID_0025`, driver 24.20.0.3 healthy). Fix: `scripts/fix-intel-bt.ps1` (Realtek skipped as already-disabled, Intel power-cycled via `pnputil /restart-device`, services restarted with `-Force`) → Intel `OK`, no new BTHUSB Event 6/34. **BLE advertising then succeeded on Intel**: `[+] WinRT BLE advertising as 'Steam Controller 2026' (4 services)`. Remaining: validate against nRF Connect + Steam HOGP.
8. Follow-up validation (2026-09-18, Intel radio, ViGEmBus installed): `--mode ble` re-verified through the real entrypoint (status log shows transient ABORTED(3) → STARTED(2); normal, now logged by a status handler in `win_ble.py`). `--mode vigem --input synthetic` runs end to end against a real `VX360Gamepad`; SC2→XUSB spot-check `0x0001|0x0800|0x4000` → `0x1009` correct. Still open: over-the-air confirmation from a second device.
9. BLE parity + OTA tooling (2026-09-19, this host, no second device available): `src/win_ble.py` brought to full `gatt_db` order — HID publishes all inputs (0x01/0x45/0x47/mouse/kbd), outputs (0x02 + haptic 0x80 with `on_haptic` callback parsing both full and hog-ll-stripped forms), and all six feature reports with reads served from `SC2CommandHandler`; DIS publishes all seven strings + PnP; all four providers advertise. `update_reports()` mirrors Linux `forward_report` (45 B → HID 0x45 + Valve ch1). `src/win_input.py` report dict gains `gamepad_47b` (placeholder: 45 B + 2 zero bytes — Linux never sends 47 B) + `battery`. `src/main_windows.py` logs BLE feature writes + 0x80 haptics. New `src/verify_windows_ble.py` (bleak central: scan → services → PnP/battery/HID-info/report-map reads → NOTIFY subscribe) ready for second-device validation. `tests/test_windows_port.py`: 18/18 pass.

## Linux → Windows mapping (agreed design)

| Linux (spoofdeck) | Windows (Windecks) | Status |
|---|---|---|
| `gatt_db.py` (pure Python) | reuse as-is | ✅ verified |
| SC2 Feature Report logic in `main_l2cap/main_uhid/main_virtual_usb` | `src/sc2_commands.py` (extracted, OS-agnostic) | ✅ verified |
| `att_server.py` (L2CAP CID 4 raw socket) | `src/win_ble.py` (WinRT `GattServiceProvider`) | ✅ implemented (experimental, HOGP interop untested) |
| `adv.py`/`bluez.py` (BlueZ advertising) | `win_ble.py` advertising | ✅ implemented (experimental) |
| `agent.py` (BlueZ Agent1) | not needed — Windows handles SMP pairing in Settings | ✅ doc only |
| `main_virtual_usb.py` (vhci_hcd) + `main_uhid.py` (/dev/uhid) | `src/win_vigem.py` (ViGEmBus `VX360Gamepad`) | ✅ implemented + tested (fake pad) |
| `input_handler.py` Neptune `/dev/hidraw3` + evdev | `src/win_input.py` (pygame/XInput + synthetic) | ✅ implemented + tested |
| `fcntl` HID feature-report ioctls | in-memory responses in `sc2_commands.py` | ✅ done |
| `scripts/*.sh` | `scripts/*.ps1` (setup/run) | ✅ done |
| `src/main_windows.py` entrypoint (`--mode vigem|ble|both`) | | ✅ done |
| `docs/windows-port.md` (setup, limitations, Steam visibility) | | ✅ done |

## Key constraints found

- Windows has `socket.AF_BLUETOOTH` but no L2CAP CID 4 / BlueZ — raw ATT server is not portable; WinRT GATT server is the path.
- `main_virtual_usb.py` imports `fcntl` at top → cannot even be imported on Windows; hence the `sc2_commands.py` extraction.
- ViGEmBus emulates Xbox 360 / DS4 (VID `045E`/`054C`), so it cannot spoof SC2 VID `28DE`/PID `1303` — Steam sees an Xbox pad, not an SC2, via that path. True SC2 spoof on Windows requires the BLE GATT-server path.
- WinRT GATT server needs the `winsdk` package and the Bluetooth capability; HID-over-GATT (0x1812) as a custom service is untested against Windows HOGP host + Steam.

## Next steps (in order)

1. ~~`pip install -r requirements-windows.txt`~~ — done (`pip install --pre`; ViGEmBus driver installed via setup script 2026-09-18).
2. ~~Implement `src/win_vigem.py`, `src/win_input.py`, `src/main_windows.py`, `src/win_ble.py`, scripts, docs, tests~~ — done, 12/12 tests pass.
3. ~~Live-test `run-windows.ps1 -Mode vigem`~~ — done 2026-09-18 (real VX360Gamepad, synthetic reports flowing).
4. Validate `--mode ble` over the air against nRF Connect + Steam (advertising confirmed STARTED locally; HOGP interop untested). Tooling ready: `python src/verify_windows_ble.py` on a second device (see `docs/windows-port.md`).
5. ~~Commit on a `windows-port` branch and push~~ — done (`9a20e7f`); push follow-up commits below.
