#!/usr/bin/env python3
"""Windows entrypoint (replaces Linux main_l2cap.py / main_virtual_usb.py).

Modes:
    vigem   physical/synthetic input -> ViGEmBus VX360Gamepad (Xbox pad
            visible to Steam; NOT an SC2 spoof — ViGEm VID/PID are fixed).
    ble     input -> WinRT BLE GATT server advertising as SC2
            (src/win_ble.py; experimental, HOGP host interop untested).
    both    vigem + ble simultaneously.

Input: --input synthetic|pygame|auto (default auto).
"""

import argparse
import signal
import sys
import threading
import time

from platform_compat import IS_WINDOWS, HAS_VGAMEPAD, HAS_PYGAME, HAS_BLEAK
from win_input import create_input


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Windecks Windows entrypoint")
    p.add_argument("--mode", choices=["vigem", "ble", "both"], default="vigem",
                   help="Output backend (default: vigem)")
    p.add_argument("--input", dest="input_source",
                   choices=["synthetic", "pygame", "auto"], default="auto",
                   help="Input source (default: auto)")
    p.add_argument("--name", default="Steam Controller 2026",
                   help="BLE device name for --mode ble|both")
    p.add_argument("--hz", type=int, default=60,
                   help="Input poll rate for pygame source (default: 60)")
    return p.parse_args(argv)


def _build_vigem(on_rumble=None):
    from win_vigem import WinVigemTarget
    return WinVigemTarget(on_rumble=on_rumble)


def main(argv=None):
    args = _parse_args(argv)
    print(f"[*] Windecks Windows mode={args.mode} input={args.input_source}")
    if not IS_WINDOWS:
        print("[!] Not running on Windows — continuing anyway (dev/test).")

    stop = threading.Event()

    def _sig(*_a):
        stop.set()
    try:
        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)
    except Exception:
        pass

    vigem = None
    ble = None

    def on_rumble(large, small, led=0):
        print(f"[rumble] host -> device: large={large} small={small} led={led}")

    def on_ble_feature(data):
        print(f"[ble] feature write: {len(data)} B id=0x{data[0]:02X}" if data
              else "[ble] feature write: empty")

    def on_ble_haptic(left, right):
        print(f"[ble] haptic 0x80: left={left} right={right}")

    if args.mode in ("vigem", "both"):
        if not HAS_VGAMEPAD:
            print("[-] vgamepad/ViGEmBus not available: install the ViGEmBus "
                  "driver (scripts/setup-windows.ps1), then pip install vgamepad.")
            if args.mode == "vigem":
                return 2
        else:
            try:
                vigem = _build_vigem(on_rumble=on_rumble)
                print("[+] ViGEm Xbox 360 target connected")
            except RuntimeError as e:
                print(f"[-] {e}")
                if args.mode == "vigem":
                    return 2
                vigem = None

    if args.mode in ("ble", "both"):
        if not HAS_BLEAK:
            print("[-] bleak/winrt not available (pip install bleak).")
            if args.mode == "ble":
                return 2
        else:
            try:
                from win_ble import WinBleServer
                ble = WinBleServer(device_name=args.name,
                                   on_feature_write=on_ble_feature,
                                   on_haptic=on_ble_haptic)
                ble.start()
                print(f"[+] BLE GATT server started as '{args.name}'")
            except Exception as e:
                print(f"[-] BLE server failed: {type(e).__name__}: {e}")
                if args.mode == "ble":
                    return 2
                ble = None

    def on_report(reports):
        if vigem is not None:
            try:
                vigem.handle_reports(reports)
            except Exception as e:
                print(f"[-] vigem update error: {e}")
        if ble is not None:
            try:
                ble.update_reports(reports)
            except Exception as e:
                print(f"[-] ble update error: {e}")

    src_kwargs = {"hz": args.hz} if args.input_source in ("pygame", "auto") else {}
    try:
        inp = create_input(source=args.input_source, on_report=on_report, **src_kwargs)
    except TypeError:
        inp = create_input(source=args.input_source, on_report=on_report)
    inp.start()
    print("[*] Running — Ctrl+C to stop.")
    try:
        while not stop.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        print("[*] Stopping...")
        try:
            inp.stop()
        except Exception:
            pass
        if ble is not None:
            try:
                ble.stop()
            except Exception:
                pass
        if vigem is not None:
            try:
                vigem.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
