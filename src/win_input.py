#!/usr/bin/env python3
"""Windows input sources (replaces Linux input_handler.py Neptune path).

Linux reads the Neptune controller at /dev/hidraw3 (USB interface 2,
raw 64-byte reports, type 0x09) plus evdev/Xbox fallback. Neither exists
on Windows. This module provides:

  SyntheticWinInput — idle 12-byte + 45-byte reports at ~10 Hz, same
      dict shape as Linux SyntheticInputHandler (no-deck mode).
  PygameWinInput    — physical joystick/XInput capture via pygame,
      mapped to the same dict shape.

Report dict shape (shared with Linux on_report consumers):
    {'gamepad_12b': bytes(12), 'gamepad_45b': bytes(45)|None,
     'mouse_4b': bytes(4)|None, 'kbd_8b': bytes(8)|None}

45-byte SC2 layout mirrors input_handler._parse_neptune_report:
    [0] seq, [1-4] buttons32, [5-6] LT16, [7-8] RT16,
    [9-10] lx, [11-12] ly, [13-14] rx, [15-16] ry,
    [17-20] lpad x/y, [21-22] pressure L, [23-26] rpad x/y,
    [27-28] pressure R, [29-32] timestamp us, [33-44] accel/gyro.
Pygame has no trackpads/IMU, so those fields stay zero.
"""

import struct
import threading
import time

HZ_SYNTHETIC = 10
HZ_PYGAME = 60

# pygame button index -> SC2 12-byte button bit (standard mapping:
# 0=A 1=B 2=X 3=Y 4=LB 5=RB 6=Back 7=Start 8=L3 9=R3).
PYGAME_BUTTON_TO_SC2 = {
    0: 0x0001,  # A
    1: 0x0002,  # B
    2: 0x0004,  # X
    3: 0x0008,  # Y
    4: 0x0010,  # LB
    5: 0x0020,  # RB
    6: 0x0040,  # Back/Select
    7: 0x0080,  # Start
    8: 0x0200,  # L3
    9: 0x0400,  # R3
}


def _axis_to_short(v):
    """pygame axis -1.0..1.0 -> signed 16-bit."""
    v = max(-1.0, min(1.0, float(v)))
    if v <= -1.0:
        return -32768
    return max(-32768, min(32767, int(v * 32767)))


def _axis_to_trigger(v):
    """pygame axis -1.0..1.0 -> trigger 0..255."""
    v = max(-1.0, min(1.0, float(v)))
    return max(0, min(255, int((v + 1.0) / 2.0 * 255)))


def build_12b(buttons, lx=0, ly=0, rx=0, ry=0, lt=0, rt=0):
    report = bytearray(12)
    struct.pack_into("<H", report, 0, buttons & 0xFFFF)
    struct.pack_into("<h", report, 2, lx)
    struct.pack_into("<h", report, 4, ly)
    struct.pack_into("<h", report, 6, rx)
    struct.pack_into("<h", report, 8, ry)
    report[10] = lt & 0xFF
    report[11] = rt & 0xFF
    return bytes(report)


def build_45b(seq_num, buttons32, lx=0, ly=0, rx=0, ry=0,
              lt16=0, rt16=0, timestamp_us=0):
    report45 = bytearray(45)
    report45[0] = seq_num & 0xFF
    struct.pack_into("<I", report45, 1, buttons32 & 0xFFFFFFFF)
    struct.pack_into("<h", report45, 5, lt16)
    struct.pack_into("<h", report45, 7, rt16)
    struct.pack_into("<h", report45, 9, lx)
    struct.pack_into("<h", report45, 11, ly)
    struct.pack_into("<h", report45, 13, rx)
    struct.pack_into("<h", report45, 15, ry)
    # trackpads / pressure / IMU stay zero (no Windows equivalent)
    struct.pack_into("<I", report45, 29, timestamp_us & 0xFFFFFFFF)
    return bytes(report45)


def empty_reports(gamepad_12b=None, gamepad_45b=None):
    return {
        "gamepad_12b": gamepad_12b,
        "gamepad_45b": gamepad_45b,
        "mouse_4b": None,
        "kbd_8b": None,
    }


class SyntheticWinInput:
    """Idle reports at ~10 Hz (Windows no-deck mode)."""

    def __init__(self, on_report=None, hz=HZ_SYNTHETIC):
        self.on_report = on_report
        self.hz = hz
        self._thread = None
        self._running = False
        self.seq_num = 0
        self.start_time = time.monotonic()

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[+] Synthetic Windows input started (no-deck mode)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def make_reports(self):
        self.seq_num = (self.seq_num + 1) & 0xFF
        ts = int((time.monotonic() - self.start_time) * 1000000) & 0xFFFFFFFF
        return empty_reports(
            gamepad_12b=b"\x00" * 12,
            gamepad_45b=build_45b(self.seq_num, 0, timestamp_us=ts),
        )

    def _loop(self):
        period = 1.0 / max(1, self.hz)
        while self._running:
            if self.on_report:
                self.on_report(self.make_reports())
            time.sleep(period)


class PygameWinInput:
    """Physical joystick capture via pygame (XInput on Windows).

    Maps the first detected joystick: buttons 0-9, axes
    LX/LY/RX/RY + LT/RT (axes 4/5 when present), hat -> dpad.
    """

    def __init__(self, on_report=None, hz=HZ_PYGAME, joystick_index=0):
        self.on_report = on_report
        self.hz = hz
        self.joystick_index = joystick_index
        self._thread = None
        self._running = False
        self.seq_num = 0
        self.start_time = time.monotonic()
        self._js = None

    def start(self):
        try:
            import pygame
        except ImportError:
            print("[-] pygame not installed (pip install pygame)")
            return
        pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() <= self.joystick_index:
            print("[-] No pygame joystick found; falling back to synthetic idle")
            pygame.joystick.quit()
            return
        self._js = pygame.joystick.Joystick(self.joystick_index)
        self._js.init()
        print(f"[+] Pygame joystick: {self._js.get_name()}")
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        if self._js is not None:
            try:
                import pygame
                self._js.quit()
                pygame.joystick.quit()
            except Exception:
                pass
            self._js = None

    def poll_once(self):
        """Read the joystick once and build the report dict (testable)."""
        import pygame
        pygame.event.pump()
        js = self._js
        buttons = 0
        for idx in range(js.get_numbuttons()):
            try:
                pressed = js.get_button(idx)
            except Exception:
                continue
            if pressed and idx in PYGAME_BUTTON_TO_SC2:
                buttons |= PYGAME_BUTTON_TO_SC2[idx]
        # Hat -> dpad bits 11-14.
        try:
            for h in range(js.get_numhats()):
                hx, hy = js.get_hat(h)
                if hx < 0:
                    buttons |= 0x2000
                elif hx > 0:
                    buttons |= 0x4000
                if hy > 0:
                    buttons |= 0x0800
                elif hy < 0:
                    buttons |= 0x1000
        except Exception:
            pass

        def ax(i, default=0.0):
            try:
                return js.get_axis(i) if i < js.get_numaxes() else default
            except Exception:
                return default

        lx, ly = _axis_to_short(ax(0)), _axis_to_short(ax(1))
        rx, ry = _axis_to_short(ax(2)), _axis_to_short(ax(3))
        lt = _axis_to_trigger(ax(4)) if js.get_numaxes() > 4 else 0
        rt = _axis_to_trigger(ax(5)) if js.get_numaxes() > 5 else 0

        self.seq_num = (self.seq_num + 1) & 0xFF
        ts = int((time.monotonic() - self.start_time) * 1000000) & 0xFFFFFFFF
        lt16 = min(32767, lt << 7)
        rt16 = min(32767, rt << 7)
        return empty_reports(
            gamepad_12b=build_12b(buttons, lx, ly, rx, ry, lt, rt),
            gamepad_45b=build_45b(self.seq_num, buttons & 0xFFFF,
                                  lx, ly, rx, ry, lt16, rt16, ts),
        )

    def _loop(self):
        period = 1.0 / max(1, self.hz)
        while self._running:
            try:
                reports = self.poll_once()
            except Exception as e:
                print(f"[-] pygame poll error: {type(e).__name__}: {e}")
                time.sleep(period)
                continue
            if self.on_report:
                self.on_report(reports)
            time.sleep(period)


def create_input(source="auto", on_report=None, **kwargs):
    """Factory: 'synthetic' | 'pygame' | 'auto' (pygame if present, else synthetic)."""
    if source == "synthetic":
        return SyntheticWinInput(on_report=on_report, **kwargs)
    if source == "pygame":
        return PygameWinInput(on_report=on_report, **kwargs)
    # auto
    try:
        import pygame  # noqa: F401
        pygame.init()
        has_js = pygame.joystick.get_count() > 0
        pygame.quit()
        if has_js:
            return PygameWinInput(on_report=on_report, **kwargs)
    except Exception:
        pass
    return SyntheticWinInput(on_report=on_report, **kwargs)
