#!/usr/bin/env python3
"""Windows entrypoint (replaces Linux main_l2cap.py / main_virtual_usb.py).

Modes:
    vigem     physical/synthetic input -> ViGEmBus VX360Gamepad (Xbox pad
              visible to Steam; NOT an SC2 spoof — ViGEm VID/PID fixed).
    hidmaestro  input -> HIDMaestro virtual controller with true hardware
              identity (28DE:1302 via the steam-controller-2 persona;
              experimental, needs HIDMaestro SDK + pythonnet).
    ble       input -> WinRT BLE GATT server advertising as SC2
              (src/win_ble.py; experimental, HOGP host interop untested).
    both      vigem + ble simultaneously.

Input: --input synthetic|pygame|auto (default auto).

Haptics: host rumble (ViGEm notifications, BLE 0x80 writes, HIDMaestro
output events) is routed back to the physical pad via RumbleRouter
(src/win_haptics.py) unless --no-rumble is given. Steam-generated 0x8F
haptics never arrive over BLE by Steam design (real SC2 behaves the
same) — see docs/findings-backlog.md.
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
    p.add_argument("--mode", choices=["vigem", "hidmaestro", "ble", "both"],
                   default="vigem", help="Output backend (default: vigem)")
    p.add_argument("--input", dest="input_source",
                   choices=["synthetic", "pygame", "auto"], default="auto",
                   help="Input source (default: auto)")
    p.add_argument("--name", default="Steam Controller 2026",
                   help="BLE device name for --mode ble|both")
    p.add_argument("--profile", default="steam-controller-2",
                   help="HIDMaestro profile id for --mode hidmaestro")
    p.add_argument("--hz", type=int, default=60,
                   help="Input poll rate for pygame source (default: 60)")
    p.add_argument("--no-rumble", dest="rumble", action="store_false",
                   default=True, help="Disable host->physical rumble routing")
    return p.parse_args(argv)


def _build_vigem(on_rumble=None):
    from win_vigem import WinVigemTarget
    return WinVigemTarget(on_rumble=on_rumble)


def _build_hidmaestro(profile, on_output=None):
    from win_hidmaestro import WinHidMaestroTarget
    return WinHidMaestroTarget(profile=profile, on_output=on_output)


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
    hidmaestro = None
    ble = None

    # Input source is created first so the rumble router can target the
    # physical joystick (pygame) once it is running.
    src_kwargs = {"hz": args.hz} if args.input_source in ("pygame", "auto") else {}
    try:
        inp = create_input(source=args.input_source, on_report=None, **src_kwargs)
    except TypeError:
        inp = create_input(source=args.input_source)

    router = None
    if args.rumble:
        from win_haptics import RumbleRouter, make_pygame_output
        router = RumbleRouter(
            [make_pygame_output(lambda: getattr(inp, "_js", None))],
            enabled=True)
        # Synthetic input has no joystick; the output degrades to a no-op
        # until a pygame joystick appears.
    else:
        print("[*] Rumble routing disabled (--no-rumble).")

    def on_rumble(large, small, led=0):
        if router is not None:
            router.from_vigem(large, small, led)
        else:
            print(f"[rumble] host -> device: large={large} small={small} led={led}")

    def on_hm_output(left01, right01):
        if router is not None:
            router.send(left01, right01, 200)
        else:
            print(f"[hidmaestro] output: left={left01:.2f} right={right01:.2f}")

    def on_ble_feature(data):
        print(f"[ble] feature write: {len(data)} B id=0x{data[0]:02X}" if data
              else "[ble] feature write: empty")

    def on_ble_haptic(left, right):
        if router is not None:
            router.from_ble_haptic(left, right)
        else:
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

    if args.mode == "hidmaestro":
        try:
            hidmaestro = _build_hidmaestro(args.profile, on_output=on_hm_output)
            print(f"[+] HIDMaestro controller connected (profile '{args.profile}')")
        except RuntimeError as e:
            print(f"[-] {e}")
            return 2

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
        if hidmaestro is not None:
            try:
                hidmaestro.handle_reports(reports)
            except Exception as e:
                print(f"[-] hidmaestro update error: {e}")
        if ble is not None:
            try:
                ble.update_reports(reports)
            except Exception as e:
                print(f"[-] ble update error: {e}")

    inp.on_report = on_report
    inp.start()
    print("[*] Running — Ctrl+C to stop.")
    try:
        while not stop.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        print("[*] Stopping...")
        if router is not None:
            try:
                router.stop()
            except Exception:
                pass
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
        if hidmaestro is not None:
            try:
                hidmaestro.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
