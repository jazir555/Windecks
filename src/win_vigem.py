#!/usr/bin/env python3
"""Windows virtual-controller backend (replaces Linux vhci_hcd + /dev/uhid).

Linux equivalents: src/main_virtual_usb.py (vhci_hcd + USB/IP) and
src/main_uhid.py (/dev/uhid). On Windows both are replaced by ViGEmBus
via the `vgamepad` package (VX360Gamepad).

SC2 12-byte gamepad layout (see input_handler.SC2InputReport.to_bytes):
    [0-1] buttons LE, [2-3] lx LE short, [4-5] ly, [6-7] rx, [8-9] ry,
    [10] left trigger 0-255, [11] right trigger 0-255.

Neptune-derived 12-byte button bits (see input_handler._parse_neptune_report):
    0x0001 A, 0x0002 B, 0x0004 X, 0x0008 Y,
    0x0010 L1, 0x0020 R1,
    0x0040 select/options, 0x0080 start/menu, 0x0100 steam/guide,
    0x0200 L3, 0x0400 R3,
    0x0800 dpad-up, 0x1000 dpad-down, 0x2000 dpad-left, 0x4000 dpad-right,
    0x8000 back grips L4/L5/R4/R5 (no Xbox equivalent — dropped).

Limitation: ViGEmBus emulates Xbox 360 (VID 045E) / DS4 (VID 054C), so it
cannot spoof SC2 VID 28DE / PID 1303. Steam sees an Xbox pad, not an SC2,
via this path. True SC2 spoof on Windows requires the BLE GATT-server
path (src/win_ble.py). See docs/windows-port.md.

`import vgamepad` raises VIGEM_ERROR_BUS_NOT_FOUND when the ViGEmBus
driver is not installed (it connects at module import time), so all
vgamepad imports here are lazy + guarded. Pass a fake gamepad object
(e.g. unittest.mock.MagicMock) for driver-free testing.
"""

import struct

# SC2 12-byte button bit -> XUSB_BUTTON attribute name. 0x8000 (grips)
# has no Xbox equivalent and is intentionally dropped.
SC2_TO_XUSB = {
    0x0001: "XUSB_GAMEPAD_A",
    0x0002: "XUSB_GAMEPAD_B",
    0x0004: "XUSB_GAMEPAD_X",
    0x0008: "XUSB_GAMEPAD_Y",
    0x0010: "XUSB_GAMEPAD_LEFT_SHOULDER",
    0x0020: "XUSB_GAMEPAD_RIGHT_SHOULDER",
    0x0040: "XUSB_GAMEPAD_BACK",
    0x0080: "XUSB_GAMEPAD_START",
    0x0100: "XUSB_GAMEPAD_GUIDE",
    0x0200: "XUSB_GAMEPAD_LEFT_THUMB",
    0x0400: "XUSB_GAMEPAD_RIGHT_THUMB",
    0x0800: "XUSB_GAMEPAD_DPAD_UP",
    0x1000: "XUSB_GAMEPAD_DPAD_DOWN",
    0x2000: "XUSB_GAMEPAD_DPAD_LEFT",
    0x4000: "XUSB_GAMEPAD_DPAD_RIGHT",
}

# XUSB dpad flag values (duplicated here so mapping tests don't need the
# driver installed; must match vgamepad.win.vigem_commons.XUSB_BUTTON).
XUSB_FLAG = {
    "XUSB_GAMEPAD_DPAD_UP": 0x0001,
    "XUSB_GAMEPAD_DPAD_DOWN": 0x0002,
    "XUSB_GAMEPAD_DPAD_LEFT": 0x0004,
    "XUSB_GAMEPAD_DPAD_RIGHT": 0x0008,
    "XUSB_GAMEPAD_START": 0x0010,
    "XUSB_GAMEPAD_BACK": 0x0020,
    "XUSB_GAMEPAD_LEFT_THUMB": 0x0040,
    "XUSB_GAMEPAD_RIGHT_THUMB": 0x0080,
    "XUSB_GAMEPAD_LEFT_SHOULDER": 0x0100,
    "XUSB_GAMEPAD_RIGHT_SHOULDER": 0x0200,
    "XUSB_GAMEPAD_GUIDE": 0x0400,
    "XUSB_GAMEPAD_A": 0x1000,
    "XUSB_GAMEPAD_B": 0x2000,
    "XUSB_GAMEPAD_X": 0x4000,
    "XUSB_GAMEPAD_Y": 0x8000,
}


def parse_sc2_12b(report):
    """Parse a 12-byte SC2 gamepad report into a dict.

    Raises ValueError on wrong length. Sticks are signed 16-bit LE,
    triggers unsigned 8-bit (same ranges XInput uses).
    """
    if report is None or len(report) != 12:
        raise ValueError(f"SC2 12-byte report must be 12 bytes, got {len(report) if report else 0}")
    buttons = struct.unpack_from("<H", report, 0)[0]
    lx = struct.unpack_from("<h", report, 2)[0]
    ly = struct.unpack_from("<h", report, 4)[0]
    rx = struct.unpack_from("<h", report, 6)[0]
    ry = struct.unpack_from("<h", report, 8)[0]
    return {
        "buttons": buttons,
        "lx": lx, "ly": ly, "rx": rx, "ry": ry,
        "lt": report[10], "rt": report[11],
    }


def sc2_buttons_to_xusb_flags(sc2_buttons):
    """Map SC2 12-byte button bits to an XUSB button bitmask (int)."""
    flags = 0
    for bit, name in SC2_TO_XUSB.items():
        if sc2_buttons & bit:
            flags |= XUSB_FLAG[name]
    return flags


def _load_xusb_button_enum():
    """Import XUSB_BUTTON without triggering vgamepad/__init__'s VBus connect.

    `import vgamepad` connects to the bus at module level, so we load the
    leaf modules directly by path. Raises RuntimeError if unavailable.
    """
    import importlib.util
    import pathlib
    import sys
    try:
        import site
        candidates = site.getsitepackages()
    except Exception:
        candidates = sys.path
    for base in candidates:
        p = pathlib.Path(base) / "vgamepad" / "win" / "vigem_commons.py"
        if p.is_file():
            spec = importlib.util.spec_from_file_location("windecks_vigem_commons", str(p))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.XUSB_BUTTON
    # Fallback: regular import (works when the driver IS installed).
    import vgamepad  # noqa: F401
    from vgamepad.win.vigem_commons import XUSB_BUTTON
    return XUSB_BUTTON


class WinVigemTarget:
    """VX360Gamepad wrapper driven by SC2 12-byte reports.

    Args:
        gamepad: optional pre-constructed gamepad (fake/mock for tests).
        on_rumble: optional callback(left_motor, right_motor, led) invoked
            when the host game sends rumble (mirrors Linux 0x80 handling
            where SDL_RumbleJoystick reaches _on_haptic_write).
    """

    def __init__(self, gamepad=None, on_rumble=None):
        self.on_rumble = on_rumble
        self._gamepad = gamepad
        self._XUSB_BUTTON = None
        self.last_state = None  # last (flags, lx, ly, rx, ry, lt, rt) tuple
        self.last_rumble = (0, 0)
        if gamepad is None:
            self._connect()

    def _connect(self):
        try:
            import vgamepad
            self._XUSB_BUTTON = vgamepad.win.vigem_commons.XUSB_BUTTON \
                if hasattr(vgamepad, "win") else _load_xusb_button_enum()
            from vgamepad.win.virtual_gamepad import VX360Gamepad
            self._gamepad = VX360Gamepad()
        except Exception as e:
            raise RuntimeError(
                "ViGEmBus driver not available (install setup-v1.17.333, "
                "see scripts/setup-windows.ps1). "
                f"Underlying error: {type(e).__name__}: {e}"
            )
        try:
            self._gamepad.register_notification(self._on_notification)
        except Exception:
            pass  # notification support is best-effort

    def _xusb(self, name):
        if self._XUSB_BUTTON is not None:
            return getattr(self._XUSB_BUTTON, name)
        # Driver-free path (tests with a mock gamepad): resolve via int flag.
        return XUSB_FLAG[name]

    def _on_notification(self, client, target, large_motor, small_motor, led_number):
        self.last_rumble = (large_motor, small_motor)
        if self.on_rumble:
            try:
                self.on_rumble(large_motor, small_motor, led_number)
            except Exception as e:
                print(f"[-] win_vigem rumble callback error: {e}")

    def send_report(self, report_12b):
        """Push one 12-byte SC2 report to the virtual Xbox 360 pad."""
        state = parse_sc2_12b(report_12b)
        flags = sc2_buttons_to_xusb_flags(state["buttons"])
        if self._gamepad is None:
            raise RuntimeError("ViGEm gamepad not connected")
        gp = self._gamepad
        # Press/release each mapped button to match the bitmask.
        try:
            for _bit, name in SC2_TO_XUSB.items():
                btn = self._xusb(name)
                if flags & XUSB_FLAG[name]:
                    gp.press_button(button=btn)
                else:
                    gp.release_button(button=btn)
        except TypeError:
            # Older vgamepad signature: press_button(btn) positionally.
            for _bit, name in SC2_TO_XUSB.items():
                btn = self._xusb(name)
                if flags & XUSB_FLAG[name]:
                    gp.press_button(btn)
                else:
                    gp.release_button(btn)
        gp.left_joystick(x_value=state["lx"], y_value=state["ly"])
        gp.right_joystick(x_value=state["rx"], y_value=state["ry"])
        gp.left_trigger(value=state["lt"])
        gp.right_trigger(value=state["rt"])
        gp.update()
        self.last_state = (flags, state["lx"], state["ly"],
                           state["rx"], state["ry"], state["lt"], state["rt"])
        return self.last_state

    def handle_reports(self, reports):
        """Accept the standard report dict from win_input handlers."""
        data = (reports or {}).get("gamepad_12b")
        if data:
            return self.send_report(data)
        return None

    def close(self):
        gp, self._gamepad = self._gamepad, None
        if gp is not None:
            try:
                gp.reset()
            except Exception:
                pass
