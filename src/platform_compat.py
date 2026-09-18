#!/usr/bin/env python3
"""Platform compatibility helpers (Linux Deck <-> Windows).

Linux-only modules in this repo:
  att_server.py  -> AF_BLUETOOTH L2CAP CID 4 raw socket + ctypes bind (BlueZ kernel)
  adv.py/agent.py/bluez.py -> BlueZ D-Bus (org.bluez.*)
  main_virtual_usb.py -> vhci_hcd + USB/IP (needs /sys/devices/platform/vhci_hcd.0, fcntl, root)
  main_uhid.py -> /dev/uhid (Linux-only)
  input_handler.py (parts) -> /dev/hidraw*, evdev, fcntl EVIOCGRAB, select on fds

Windows equivalents (see docs/windows-port.md):
  vhci_hcd/UHID -> ViGEmBus via `vgamepad` (VX360Gamepad)
  BlueZ D-Bus + L2CAP ATT -> WinRT GattServiceProvider (winsdk) [src/win_ble.py]
  evdev/hidraw Neptune -> pygame/XInput + synthetic reports [src/win_input.py]
  fcntl/ioctl feature reports -> SC2CommandHandler pure-python [src/sc2_commands.py]

This module centralises OS detection and optional-dependency probing so the
Windows entrypoint degrades gracefully when drivers are missing.
"""
import sys

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


def optional_import(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


HAS_VGAMEPAD = optional_import("vgamepad")
# WinRT GATT provider: `winsdk` meta-package only ships pre-releases; the
# `winrt-*` packages (pulled in by bleak) provide the same API. Accept either.
HAS_WINSDK = optional_import("winsdk") or optional_import(
    "winrt.windows.devices.bluetooth.genericattributeprofile")
HAS_BLEAK = optional_import("bleak")
HAS_PYGAME = optional_import("pygame")

LINUX_TO_WINDOWS = {
    "att_server.py (L2CAP CID4)": "src/win_ble.py (WinRT GattServiceProvider)",
    "adv.py / bluez.py (BlueZ LEAdvertisingManager1)": "src/win_ble.py start_advertising()",
    "agent.py (BlueZ Agent1 auto-confirm)": "Windows handles SMP pairing in Settings; no agent needed",
    "main_virtual_usb.py (vhci_hcd + USB/IP)": "src/win_vigem.py (ViGEmBus VX360Gamepad)",
    "main_uhid.py (/dev/uhid)": "src/win_vigem.py (ViGEmBus VX360Gamepad)",
    "input_handler Neptune /dev/hidraw3": "src/win_input.py (pygame/XInput + synthetic)",
    "input_handler evdev/Xbox": "src/win_input.py (pygame joystick)",
    "fcntl HIDIOCGFEATURE/HIDIOCSFEATURE": "src/sc2_commands.py (in-memory synthetic responses)",
}
