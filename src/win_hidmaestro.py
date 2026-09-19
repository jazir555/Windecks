#!/usr/bin/env python3
"""Windows virtual-controller backend with true hardware identity.

ViGEmBus (``src/win_vigem.py``) emulates Xbox 360 / DS4 with fixed
VIDs (``045E``/``054C``) — Steam sees an Xbox pad, never an SC2. This
module drives `HIDMaestro <https://github.com/hifihedgehog/HIDMaestro>`_
(MIT, user-mode UMDF2, no kernel driver, no test-signing boot mode)
instead. HIDMaestro presents exact hardware identity — VID/PID, product
string, HID descriptor, bus type — across DirectInput, XInput, SDL3,
WGI and Steam, and ships a ``steam-controller-2`` persona (28DE:1302,
the 2026 Triton controller: real 372-byte descriptor and the attribute
values Steam validates, read off two independent hardware captures).

HIDMaestro raises output events (rumble/haptic/FFB/LED) to the consumer;
pass ``on_output`` to route them to the physical pad (see
``src/win_haptics.py``) — HIDMaestro documents this explicitly ("Output
is delivered, not routed to hardware ... the consumer's job").

Status: EXPERIMENTAL. Requires the HIDMaestro SDK built
(``scripts\\build_all.cmd`` needs VS2022 + WDK + .NET 10) or a release
binary, plus ``pip install pythonnet`` for the .NET bridge. All
``pythonnet``/SDK imports are lazy so ``--mode vigem`` and the test
suite work without it. ``handle_reports()`` accepts the standard report
dict from ``win_input`` handlers.
"""

# SC2 12-byte button bit -> HIDMaestro HMButton member name. Grips
# (0x8000) and touch/click bits have no standard-pad equivalent here.
SC2_TO_HMBUTTON = {
    0x0001: "A",
    0x0002: "B",
    0x0004: "X",
    0x0008: "Y",
    0x0010: "LeftShoulder",
    0x0020: "RightShoulder",
    0x0040: "Back",
    0x0080: "Start",
    0x0200: "LeftStick",
    0x0400: "RightStick",
    0x0800: "DpadUp",
    0x1000: "DpadDown",
    0x2000: "DpadLeft",
    0x4000: "DpadRight",
}

# HIDMaestro profile IDs of interest (built-in catalog).
PROFILE_SC2_2026 = "steam-controller-2"      # 28DE:1302 Triton
PROFILE_SC1 = "steam-controller-composite"   # 28DE:1102 wired 2015 pad
PROFILE_DECK = "steam-deck-composite"        # 28DE:1205 Neptune USB identity

# HMGamepadState axis property candidates, in preference order. The exact
# member names vary by SDK version; each is probed with setattr guarded
# by try/except, so unknown names are skipped, never fatal.
_AXIS_PROPS = (
    ("lx", ("LeftStickX", "LeftThumbX", "ThumbLX")),
    ("ly", ("LeftStickY", "LeftThumbY", "ThumbLY")),
    ("rx", ("RightStickX", "RightThumbX", "ThumbRX")),
    ("ry", ("RightStickY", "RightThumbY", "ThumbRY")),
    ("lt", ("LeftTrigger", "TriggerL")),
    ("rt", ("RightTrigger", "TriggerR")),
)


def _short01(v):
    return max(-1.0, min(1.0, float(v) / 32768.0))


def _byte01(v):
    return max(0.0, min(1.0, float(v) / 255.0))


class WinHidMaestroTarget:
    """HIDMaestro-backed virtual controller driven by SC2 reports.

    Args:
        profile: HIDMaestro profile id (default ``steam-controller-2``).
        controller: optional pre-constructed controller object (fake for
            tests — needs ``SubmitState(state)``).
        on_output: optional callback(left01, right01) for host
            rumble/haptic output events raised by the driver.
        context_factory: optional zero-arg callable returning an
            HMContext-like object (dependency injection for tests).
    """

    def __init__(self, profile=PROFILE_SC2_2026, controller=None,
                 on_output=None, context_factory=None):
        self.profile = profile
        self.on_output = on_output
        self._controller = controller
        self._context = None
        self._HMButton = None
        # Injected controllers (tests) accept the plain state dict;
        # SDK-managed controllers get a translated HMGamepadState.
        self._dict_mode = controller is not None and context_factory is None
        self.last_state = None
        self.last_output = (0.0, 0.0)
        if controller is None:
            self._connect(context_factory)

    def _connect(self, context_factory=None):
        try:
            import clr  # noqa: F401  (pythonnet)
        except Exception as e:
            raise RuntimeError(
                "HIDMaestro backend needs pythonnet (pip install pythonnet) "
                "plus the HIDMaestro SDK/runtime. See docs/windows-port.md. "
                f"Underlying error: {type(e).__name__}: {e}"
            )
        try:
            if context_factory is None:
                import HIDMaestro as _hm
                ctx = _hm.HMContext()
            else:
                ctx = context_factory()
            ctx.LoadDefaultProfiles()
            ctx.InstallDriver()
            prof = ctx.GetProfile(self.profile)
            if prof is None:
                raise RuntimeError(f"profile '{self.profile}' not found")
            self._controller = ctx.CreateController(prof)
            self._context = ctx
            try:
                import HIDMaestro as _hm2
                self._HMButton = _hm2.HMButton
            except Exception:
                self._HMButton = None
            try:
                self._controller.OutputReceived += self._on_output_event
            except Exception:
                pass  # output events are best-effort
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"HIDMaestro connect failed: {type(e).__name__}: {e}")

    # -- output path (host -> physical, via caller) --------------------
    def _on_output_event(self, sender, args):
        left = right = 0.0
        try:
            left = float(getattr(args, "LeftMotor", 0.0) or 0.0)
            right = float(getattr(args, "RightMotor", 0.0) or 0.0)
            if left <= 1.0 and right <= 1.0 and \
                    (getattr(args, "LeftMotor", None) is None):
                # Integer 0-255-style payload.
                left = float(getattr(args, "LargeMotor", 0) or 0) / 255.0
                right = float(getattr(args, "SmallMotor", 0) or 0) / 255.0
        except Exception:
            pass
        self.last_output = (left, right)
        if self.on_output:
            try:
                self.on_output(left, right)
            except Exception as e:
                print(f"[-] hidmaestro output callback error: {e}")

    # -- input path ----------------------------------------------------
    def _button_names(self, sc2_buttons):
        names = []
        for bit, name in SC2_TO_HMBUTTON.items():
            if sc2_buttons & bit:
                names.append(name)
        return names

    def send_report(self, report_12b):
        """Push one 12-byte SC2 report to the virtual controller."""
        import struct
        if report_12b is None or len(report_12b) != 12:
            raise ValueError("SC2 12-byte report must be 12 bytes")
        buttons = struct.unpack_from("<H", report_12b, 0)[0]
        lx = struct.unpack_from("<h", report_12b, 2)[0]
        ly = struct.unpack_from("<h", report_12b, 4)[0]
        rx = struct.unpack_from("<h", report_12b, 6)[0]
        ry = struct.unpack_from("<h", report_12b, 8)[0]
        lt, rt = report_12b[10], report_12b[11]
        return self.send_state(buttons, lx, ly, rx, ry, lt, rt)

    def send_state(self, buttons, lx=0, ly=0, rx=0, ry=0, lt=0, rt=0):
        """Push parsed state. Works against fakes (tests) and the SDK."""
        if self._controller is None:
            raise RuntimeError("HIDMaestro controller not connected")
        names = self._button_names(buttons)
        state = {"buttons": names,
                 "lx": _short01(lx), "ly": _short01(ly),
                 "rx": _short01(rx), "ry": _short01(ry),
                 "lt": _byte01(lt), "rt": _byte01(rt)}
        ctrl = self._controller
        submit = getattr(ctrl, "SubmitState", None)
        if submit is None:
            raise RuntimeError("controller has no SubmitState")
        if self._dict_mode:
            submit(state)
        else:
            submit(self._build_hm_state(ctrl, state))
        self.last_state = (buttons, lx, ly, rx, ry, lt, rt)
        return self.last_state

    def _build_hm_state(self, ctrl, state):
        """Translate the state dict to an SDK HMGamepadState (best-effort)."""
        new_state = None
        try:
            import HIDMaestro as _hm
            factory = getattr(_hm, "HMGamepadState", None)
            if callable(factory):
                try:
                    new_state = factory()
                except Exception:
                    new_state = None
        except Exception:
            new_state = None
        if new_state is None:
            return state  # let SubmitState try the dict form
        # Buttons: OR the HMButton members by name.
        if self._HMButton is not None:
            combined = 0
            for name in state["buttons"]:
                try:
                    combined |= int(getattr(self._HMButton, name))
                except Exception:
                    pass
            for prop in ("Buttons", "buttons", "PressedButtons"):
                try:
                    setattr(new_state, prop, combined)
                    break
                except Exception:
                    continue
        # Axes/triggers: probe candidate property names.
        for key, candidates in _AXIS_PROPS:
            for prop in candidates:
                try:
                    setattr(new_state, prop, float(state[key]))
                    break
                except Exception:
                    continue
        return new_state

    def handle_reports(self, reports):
        data = (reports or {}).get("gamepad_12b")
        if data:
            return self.send_report(data)
        return None

    def close(self):
        ctrl, self._controller = self._controller, None
        if ctrl is not None:
            for meth in ("Dispose", "dispose", "Remove", "close"):
                try:
                    fn = getattr(ctrl, meth, None)
                    if callable(fn):
                        fn()
                        break
                except Exception:
                    pass
