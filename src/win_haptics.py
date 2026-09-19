#!/usr/bin/env python3
"""Host-to-physical rumble routing (Windows).

Problem: game rumble reaches our *virtual* devices (ViGEm notification
callback, BLE 0x80 writes), but with no physical pad to shake, the user
feels nothing. This module routes those host rumble events back to the
physical input device.

Outputs are plain callables ``fn(left01, right01, duration_ms)`` so the
router stays hardware-free and testable. Use
:func:`make_pygame_output` to build one from a pygame joystick (probed
with ``getattr`` — pygame's ``Joystick.rumble`` exists on real
joysticks but there is no guarantee in every build, so absence degrades
to a logged no-op instead of a crash).

Scaling: ViGEm motors are 0-255, SC2 0x80 speeds are 0-65535; both are
normalized to 0.0-1.0 for the physical device.
"""

import time


def _clamp01(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


class RumbleRouter:
    """Fan-out host rumble events to physical outputs with throttling.

    Args:
        outputs: list of ``fn(left01, right01, duration_ms)`` callables.
        min_interval_ms: minimum gap between forwarded events (identical
            repeats inside the window are dropped; changed values always
            pass). Games can emit rumble at frame rate; physical APIs
            (SDL rumble, WinRT Vibration) don't need 60 Hz updates.
        enabled: master switch (``--no-rumble``).
    """

    def __init__(self, outputs=None, min_interval_ms=30, enabled=True):
        self.outputs = list(outputs or [])
        self.min_interval_ms = max(0, min_interval_ms)
        self.enabled = enabled
        self.last_event = (None, None, 0.0)  # (left, right, monotonic)
        self.forwarded = 0
        self.dropped = 0

    def add_output(self, fn):
        self.outputs.append(fn)

    def send(self, left01, right01, duration_ms=200):
        """Forward one rumble event. Returns True if forwarded."""
        if not self.enabled or not self.outputs:
            self.dropped += 1
            return False
        left, right = _clamp01(left01), _clamp01(right01)
        now = time.monotonic()
        prev_l, prev_r, prev_t = self.last_event
        if (prev_l, prev_r) == (left, right) and \
                (now - prev_t) * 1000.0 < self.min_interval_ms:
            self.dropped += 1
            return False
        self.last_event = (left, right, now)
        ok = False
        for fn in self.outputs:
            try:
                fn(left, right, int(duration_ms))
                ok = True
            except Exception as e:
                print(f"[-] rumble output error: {type(e).__name__}: {e}")
        if ok:
            self.forwarded += 1
        else:
            self.dropped += 1
        return ok

    # -- host-side entry points --------------------------------------
    def from_vigem(self, large, small, led=0):
        """ViGEm notification: motors 0-255."""
        print(f"[rumble] host -> physical: large={large} small={small} led={led}")
        duration = 200 if (large or small) else 0
        if large or small:
            return self.send(large / 255.0, small / 255.0, duration)
        return self.send(0.0, 0.0, 0)

    def from_ble_haptic(self, left, right):
        """SC2 0x80 speeds 0-65535."""
        print(f"[ble] haptic 0x80 -> physical: left={left} right={right}")
        if left or right:
            return self.send(left / 65535.0, right / 65535.0, 200)
        return self.send(0.0, 0.0, 0)

    def stop(self):
        self.send(0.0, 0.0, 0)


def make_pygame_output(joystick_getter):
    """Build a router output from a pygame joystick getter.

    ``joystick_getter`` is a zero-arg callable returning the active
    ``pygame.joystick.Joystick`` or None. The ``rumble`` method is
    resolved per call (joysticks can come and go).
    """
    def _out(left01, right01, duration_ms):
        js = None
        try:
            js = joystick_getter()
        except Exception:
            js = None
        if js is None:
            return
        rumble = getattr(js, "rumble", None)
        if not callable(rumble):
            return
        try:
            if left01 or right01:
                rumble(left01, right01, int(duration_ms))
            else:
                stop = getattr(js, "stop_rumble", None)
                if callable(stop):
                    stop()
                else:
                    rumble(0.0, 0.0, 0)
        except Exception as e:
            print(f"[-] pygame rumble error: {type(e).__name__}: {e}")
    return _out
