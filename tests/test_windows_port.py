#!/usr/bin/env python3
"""Windows port tests — driver-free (no ViGEmBus, no BLE hardware).

Covers: gatt_db reuse, SC2CommandHandler round-trip, win_input synthetic
dict shape + report builders, win_vigem 12-byte parsing/button mapping
with a fake gamepad, platform_compat probing, win_ble UUID helpers.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gatt_db import build_sc2_database  # noqa: E402
from sc2_commands import SC2CommandHandler  # noqa: E402
from platform_compat import IS_WINDOWS  # noqa: E402
import win_input  # noqa: E402
from win_input import (  # noqa: E402
    SyntheticWinInput, build_12b, build_45b, build_47b,
    _axis_to_short, _axis_to_trigger, get_system_battery_percent,
)
from win_vigem import (  # noqa: E402
    parse_sc2_12b, sc2_buttons_to_xusb_flags, XUSB_FLAG, WinVigemTarget,
)
from win_ble import uuid16_str, _winrt_props, _parse_haptic_80  # noqa: E402
from win_haptics import RumbleRouter, make_pygame_output  # noqa: E402
from win_hidmaestro import WinHidMaestroTarget  # noqa: E402


def test_gatt_db_builds():
    db = build_sc2_database()
    assert len(db.attributes) >= 80, f"only {len(db.attributes)} attributes"
    assert len(db.services) == 6
    pnp = None
    for h, attr in db.attributes.items():
        if attr.uuid == struct.pack("<H", 0x2A50):
            pnp = bytes(attr.value)
    assert pnp is not None, "PnP ID characteristic missing"
    assert pnp[1:3] == bytes([0xDE, 0x28]), f"VID != 28DE: {pnp.hex()}"
    assert pnp[3:5] == bytes([0x03, 0x13]), f"PID != 1303: {pnp.hex()}"


def test_sc2_get_attributes_roundtrip():
    h = SC2CommandHandler()
    resp = h.handle_set_report(3, 0x01, bytes([0x83] + [0] * 63))
    assert resp is not None and resp[0] == 0x83 and len(resp) == 64
    back = h.handle_get_report(3, 0x01)
    assert back == resp


def test_sc2_get_serial_format():
    h = SC2CommandHandler()
    resp = h.handle_set_report(3, 0x01, bytes([0xAE] + [0] * 63))
    assert resp[0] == 0xAE and resp[2] == 0x01, resp[:4].hex()
    assert resp[3:4] == b"F", resp[3:23]


def test_sc2_settings_defaults_and_maxs():
    h = SC2CommandHandler()
    # 0x89 with no overrides returns real firmware defaults (sc26re).
    resp = h.handle_set_report(3, 0x01, bytes([0x89, 0x00, 0x02, 0x09, 0x40] + [0] * 59))
    assert resp[0] == 0x89 and resp[1] == 0x02
    assert resp[2:5] == bytes([0x09, 0x01, 0x00]), resp[2:5].hex()  # lizard_mode=1
    assert resp[5:8] == bytes([0x40, 0x04, 0x00]), resp[5:8].hex()  # frame_rate=4
    back = h.handle_get_report(3, 0x01)
    assert back == resp
    # 0x8C defaults / 0x8B maxs shapes.
    d = h.handle_set_report(3, 0x01, bytes([0x8C, 0x00, 0x01, 0x44] + [0] * 60))
    assert d[:5] == bytes([0x8C, 0x01, 0x44, 0x5A, 0x00]), d[:5].hex()  # reg68 default=90
    m = h.handle_set_report(3, 0x01, bytes([0x8B, 0x00, 0x01, 0x44] + [0] * 60))
    assert m[:5] == bytes([0x8B, 0x01, 0x44, 0x63, 0x00]), m[:5].hex()  # max=99
    # 0x87 override is clamped to max, visible via 0x89.
    h.handle_set_report(3, 0x01, bytes([0x87, 0x03, 0x02, 0x44, 0xFF, 0xFF] + [0] * 58))
    r = h.handle_set_report(3, 0x01, bytes([0x89, 0x00, 0x01, 0x44] + [0] * 60))
    assert r[2:5] == bytes([0x44, 0x63, 0x00]), r[2:5].hex()
    # 0x8E restores defaults.
    h.handle_set_report(3, 0x01, bytes([0x8E] + [0] * 63))
    r2 = h.handle_set_report(3, 0x01, bytes([0x89, 0x00, 0x01, 0x44] + [0] * 60))
    assert r2[2:5] == bytes([0x44, 0x5A, 0x00]), r2[2:5].hex()  # back to 90


def test_sc2_stage_commit_and_read_delete():
    h = SC2CommandHandler()
    h.handle_set_report(3, 0x01, bytes([0xEE, 0x00, 0x09, 0x00, 0x00] + [0] * 59))
    r = h.handle_set_report(3, 0x01, bytes([0xED, 0x00, 0x09] + [0] * 61))
    assert r[:5] == bytes([0xED, 0x01, 0x09, 0x01, 0x00]), r[:5].hex()  # still default
    h.handle_set_report(3, 0x01, bytes([0xEF] + [0] * 63))
    r2 = h.handle_set_report(3, 0x01, bytes([0xED, 0x00, 0x09] + [0] * 61))
    assert r2[:5] == bytes([0xED, 0x01, 0x09, 0x00, 0x00]), r2[:5].hex()  # staged applied
    h.handle_set_report(3, 0x01, bytes([0xF0, 0x00, 0x09] + [0] * 61))
    r3 = h.handle_set_report(3, 0x01, bytes([0xED, 0x00, 0x09] + [0] * 61))
    assert r3[:5] == bytes([0xED, 0x01, 0x09, 0x01, 0x00]), r3[:5].hex()  # default back


def test_sc2_dangerous_opcodes_ack_only():
    h = SC2CommandHandler()
    for cmd in (0x90, 0x95, 0x9F, 0xB5, 0xFE):
        resp = h.handle_set_report(3, 0x01, bytes([cmd] + [0] * 63))
        assert resp is not None and resp[0] == cmd and resp[1] == 0x00 and len(resp) == 64


def test_sc2_extended_ack_only():
    h = SC2CommandHandler()
    for cmd in (0xA7, 0xA9, 0xAA, 0xAB, 0xAC, 0xAD, 0xAF, 0xB0, 0xB1,
                0xB2, 0xB3, 0xB6, 0xB7, 0xB8, 0xB9, 0xBF, 0xC1, 0xC4,
                0xEA, 0xEB):
        resp = h.handle_set_report(3, 0x01, bytes([cmd] + [0] * 63))
        assert resp is not None and resp[0] == cmd and resp[1] == 0x00 and len(resp) == 64


def test_sc2_labels_and_battery():
    h = SC2CommandHandler()
    for cmd in (0x84, 0x8A, 0xBE):
        resp = h.handle_set_report(3, 0x01, bytes([cmd] + [0] * 63))
        assert resp is not None and resp[0] == cmd and resp[1] == 0x00 and len(resp) == 64


def test_sc2_digital_mappings_store():
    h = SC2CommandHandler()
    # Empty store reads back the 0xFF marker.
    e = h.handle_set_report(3, 0x01, bytes([0x82, 0x00, 0x00] + [0] * 61))
    assert e[:3] == bytes([0x82, 0x01, 0xFF]), e[:3].hex()
    # Store a blob via 0x80 ([1] = body length, body at [2:]).
    blob = bytes(range(1, 11))
    h.handle_set_report(3, 0x01, bytes([0x80, len(blob)]) + blob + bytes(64 - 2 - len(blob)))
    r = h.handle_set_report(3, 0x01, bytes([0x82, 0x00, 0x00] + [0] * 61))
    assert r[0] == 0x82 and r[1] == len(blob) and r[2:2 + len(blob)] == blob
    # Offset slice + past-the-end marker.
    r2 = h.handle_set_report(3, 0x01, bytes([0x82, 0x00, 0x04] + [0] * 61))
    assert r2[2:2 + 6] == blob[4:]
    r3 = h.handle_set_report(3, 0x01, bytes([0x82, 0x00, 0x3C] + [0] * 61))
    assert r3[:3] == bytes([0x82, 0x01, 0xFF])
    # 0x81 clears the store.
    h.handle_set_report(3, 0x01, bytes([0x81, 0x00] + [0] * 62))
    r4 = h.handle_set_report(3, 0x01, bytes([0x82, 0x00, 0x00] + [0] * 61))
    assert r4[:3] == bytes([0x82, 0x01, 0xFF])


def test_sc2_system_info_variants():
    h = SC2CommandHandler()
    v0 = h.handle_set_report(3, 0x01, bytes([0xF2, 0x00, 0x00] + [0] * 61))
    assert v0[0] == 0xF2 and v0[1] == 0x29 and len(v0) == 64
    assert v0[3:7] == bytes([0x74, 0xFE, 0x3B, 0x6A])  # build timestamp LE
    v1 = h.handle_set_report(3, 0x01, bytes([0xF2, 0x00, 0x01] + [0] * 61))
    assert v1[0] == 0xF2 and v1[1] == 34 and v1[2] == 0x01 and len(v1) == 64
    v2 = h.handle_set_report(3, 0x01, bytes([0xF2, 0x00, 0x02] + [0] * 61))
    assert v2[:3] == bytes([0xF2, 0x09, 0x02]) and len(v2) == 64


def test_sc2_device_info_led_userstore():
    h = SC2CommandHandler()
    d = h.handle_set_report(3, 0x01, bytes([0xA1, 0x00, 0x01] + [0] * 61))
    assert d[0] == 0xA1 and d[1] == 0x12 and d[2] == 0x01 and len(d) == 64
    d0 = h.handle_set_report(3, 0x01, bytes([0xA1, 0x00, 0x00] + [0] * 61))
    assert d0[2:20] == b"\x00" * 18
    h.handle_set_report(3, 0x01, bytes([0xC5, 0x00, 10, 20, 30, 40] + [0] * 58))
    g = h.handle_set_report(3, 0x01, bytes([0xE9] + [0] * 63))
    assert g[:6] == bytes([0xE9, 0x04, 10, 20, 30, 40]), g[:6].hex()
    h.handle_set_report(3, 0x01, bytes([0xDC, 0x00, 0x07, 1, 2, 3, 4, 5] + [0] * 56))
    u = h.handle_set_report(3, 0x01, bytes([0xDB, 0x00, 0x07] + [0] * 61))
    assert u[0] == 0xDB and u[2:7] == bytes([1, 2, 3, 4, 5]), u[:8].hex()


def test_synthetic_dict_shape():
    got = []
    src = SyntheticWinInput(on_report=got.append)
    rep = src.make_reports()
    assert set(rep) == {"gamepad_12b", "gamepad_45b", "gamepad_47b",
                        "mouse_4b", "kbd_8b", "battery"}
    assert rep["gamepad_12b"] == b"\x00" * 12
    assert len(rep["gamepad_45b"]) == 45
    assert len(rep["gamepad_47b"]) == 47
    assert rep["mouse_4b"] is None and rep["kbd_8b"] is None
    # Battery is the live host level (None on desktops without a battery).
    assert rep["battery"] is None or 0 <= rep["battery"] <= 100


def test_system_battery_never_raises():
    v = get_system_battery_percent()
    assert v is None or 0 <= v <= 100


def test_build_12b_45b_layout():
    r12 = build_12b(0x0001 | 0x0800, 1000, -1000, 2000, -2000, 128, 255)
    assert struct.unpack_from("<H", r12, 0)[0] == 0x0801
    assert struct.unpack_from("<h", r12, 2)[0] == 1000
    assert r12[10] == 128 and r12[11] == 255
    r45 = build_45b(0xAB, 0x12345678, timestamp_us=0xDEADBEEF)
    assert r45[0] == 0xAB
    assert struct.unpack_from("<I", r45, 1)[0] == 0x12345678
    assert struct.unpack_from("<I", r45, 29)[0] == 0xDEADBEEF
    assert len(r45) == 45
    r47 = build_47b(0xAB, 0x12345678, timestamp_us=0xDEADBEEF)
    assert len(r47) == 47
    assert r47[:45] == r45
    assert r47[45:] == b"\x00\x00"


def test_axis_conversions():
    assert _axis_to_short(1.0) == 32767
    assert _axis_to_short(-1.0) == -32768
    assert _axis_to_short(0.0) == 0
    assert _axis_to_trigger(1.0) == 255
    assert _axis_to_trigger(-1.0) == 0
    assert _axis_to_trigger(0.0) == 127


def test_parse_sc2_12b():
    raw = build_12b(0x0001, 16, 32, 48, 64, 7, 9)
    s = parse_sc2_12b(raw)
    assert s == {"buttons": 1, "lx": 16, "ly": 32, "rx": 48, "ry": 64,
                 "lt": 7, "rt": 9}
    try:
        parse_sc2_12b(b"\x00" * 11)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_button_mapping_spot():
    assert sc2_buttons_to_xusb_flags(0x0001) == XUSB_FLAG["XUSB_GAMEPAD_A"]
    assert sc2_buttons_to_xusb_flags(0x0800) == XUSB_FLAG["XUSB_GAMEPAD_DPAD_UP"]
    dpad = sc2_buttons_to_xusb_flags(0x0800 | 0x1000 | 0x2000 | 0x4000)
    assert dpad == (XUSB_FLAG["XUSB_GAMEPAD_DPAD_UP"]
                    | XUSB_FLAG["XUSB_GAMEPAD_DPAD_DOWN"]
                    | XUSB_FLAG["XUSB_GAMEPAD_DPAD_LEFT"]
                    | XUSB_FLAG["XUSB_GAMEPAD_DPAD_RIGHT"])
    # grips have no Xbox equivalent
    assert sc2_buttons_to_xusb_flags(0x8000) == 0


class FakePad:
    def __init__(self):
        self.pressed = set()
        self.lj = self.rj = self.lt = self.rt = None
        self.updates = 0

    def press_button(self, button=None, **kw):
        self.pressed.add(button if button is not None else kw.get("button"))

    def release_button(self, button=None, **kw):
        b = button if button is not None else kw.get("button")
        self.pressed.discard(b)

    def left_joystick(self, x_value=0, y_value=0):
        self.lj = (x_value, y_value)

    def right_joystick(self, x_value=0, y_value=0):
        self.rj = (x_value, y_value)

    def left_trigger(self, value=0):
        self.lt = value

    def right_trigger(self, value=0):
        self.rt = value

    def update(self):
        self.updates += 1

    def reset(self):
        self.pressed.clear()


def test_vigem_send_with_fake_pad():
    fake = FakePad()
    t = WinVigemTarget(gamepad=fake)
    state = t.send_report(build_12b(0x0001 | 0x0800, 100, 200, 300, 400, 10, 20))
    assert fake.updates == 1
    assert fake.lj == (100, 200) and fake.rj == (300, 400)
    assert fake.lt == 10 and fake.rt == 20
    assert XUSB_FLAG["XUSB_GAMEPAD_A"] in fake.pressed
    assert XUSB_FLAG["XUSB_GAMEPAD_DPAD_UP"] in fake.pressed
    assert XUSB_FLAG["XUSB_GAMEPAD_B"] not in fake.pressed
    assert state[0] == sc2_buttons_to_xusb_flags(0x0001 | 0x0800)
    # release path
    t.send_report(build_12b(0, 0, 0, 0, 0, 0, 0))
    assert len(fake.pressed) == 0


def test_vigem_rumble_callback():
    seen = []
    fake = FakePad()
    t = WinVigemTarget(gamepad=fake,
                       on_rumble=lambda l, s, led: seen.append((l, s, led)))
    t._on_notification(None, None, 1000, 2000, 0)
    assert t.last_rumble == (1000, 2000)
    assert seen == [(1000, 2000, 0)]


def test_winble_uuid_helpers():
    assert uuid16_str(0x1812) == "00001812-0000-1000-8000-00805F9A34FB"
    assert uuid16_str(0x2A4D) == "00002A4D-0000-1000-8000-00805F9A34FB"
    assert _winrt_props(0x02 | 0x10) == ["READ", "NOTIFY"]
    assert _winrt_props(0x02 | 0x08 | 0x04) == ["READ", "WRITE_WITHOUT_RESPONSE", "WRITE"]


def test_winble_server_driver_free():
    from win_ble import WinBleServer
    seen = []
    s = WinBleServer(device_name="Test", on_feature_write=seen.append)
    # No loop running: update_reports must be a safe no-op.
    s.update_reports({"gamepad_12b": b"\x00" * 12,
                      "gamepad_45b": b"\x00" * 45})
    # Feature writes route through SC2CommandHandler + callback.
    s._on_feature(bytes([0x83] + [0] * 63))
    assert seen == [bytes([0x83] + [0] * 63)]
    assert s._sc2 is not None


def test_winble_haptic_80_parse_and_callback():
    import struct
    from win_ble import WinBleServer
    # Stripped 9-byte form (hog-ll strips the Report ID):
    # [0]=type, [1-2]=intensity, [3-4]=left, [5]=gain, [6-7]=right, [8]=gain.
    stripped = (bytes([0x01, 0x00, 0x00]) + struct.pack("<H", 1000)
                + bytes([0x00]) + struct.pack("<H", 2000) + bytes([0x00]))
    assert len(stripped) == 9
    assert _parse_haptic_80(stripped) == (1000, 2000)
    # Full form with Report ID prefix (WinRT write path).
    full = bytes([0x80]) + stripped
    assert _parse_haptic_80(full) == (1000, 2000)
    assert _parse_haptic_80(b"\x00") is None
    seen = []
    s = WinBleServer(device_name="Test",
                     on_haptic=lambda l, r: seen.append((l, r)))
    s._on_haptic_write(stripped)
    assert seen == [(1000, 2000)]


def test_winble_feature_read_roundtrip():
    from win_ble import WinBleServer
    s = WinBleServer(device_name="Test")
    # SET via write path queues a GET_ATTRIBUTES response; the feature
    # read callback must return it (this is what a host sees on read).
    s._on_feature(bytes([0x83] + [0] * 63))
    resp = s._sc2.handle_get_report(3, 0x83)
    assert resp is not None and resp[0] == 0x83 and len(resp) == 64


def test_sc2_battery_level_clamped():
    h = SC2CommandHandler()
    h.set_battery_level(42)
    assert h.battery_level == 42
    h.set_battery_level(999)
    assert h.battery_level == 100
    h.set_battery_level(-5)
    assert h.battery_level == 0
    h.set_battery_level("nonsense")  # invalid input leaves level unchanged
    assert h.battery_level == 0
    # 0xBE keeps the open-firmware empty-body shape regardless of level.
    h.set_battery_level(77)
    resp = h.handle_set_report(3, 0x01, bytes([0xBE] + [0] * 63))
    assert resp[:2] == bytes([0xBE, 0x00]) and len(resp) == 64


def test_rumble_router_scaling_and_throttle():
    seen = []
    r = RumbleRouter([lambda l, rr, d: seen.append((l, rr, d))],
                     min_interval_ms=1000)
    assert r.from_vigem(255, 128, 0) is True
    assert seen[-1] == (1.0, 128 / 255.0, 200)
    # Identical repeat inside the window is dropped.
    assert r.from_vigem(255, 128, 0) is False
    assert r.dropped >= 1
    # Changed values always pass.
    assert r.from_vigem(0, 0, 0) is True
    assert seen[-1] == (0.0, 0.0, 0)
    # BLE 0-65535 scaling.
    assert r.from_ble_haptic(65535, 32768) is True
    assert seen[-1][0] == 1.0 and abs(seen[-1][1] - 32768 / 65535.0) < 1e-9
    # Disabled router never forwards.
    r2 = RumbleRouter([lambda l, rr, d: seen.append((l, rr, d))], enabled=False)
    assert r2.from_vigem(255, 255, 0) is False


def test_pygame_output_guards_missing_api():
    class NoRumble:
        pass
    out = make_pygame_output(lambda: NoRumble())
    out(1.0, 1.0, 100)  # must not raise

    class Rumbler:
        def __init__(self):
            self.calls = []
        def rumble(self, low, high, duration):
            self.calls.append((low, high, duration))
        def stop_rumble(self):
            self.calls.append("stop")
    js = Rumbler()
    out2 = make_pygame_output(lambda: js)
    out2(0.5, 0.25, 150)
    assert js.calls == [(0.5, 0.25, 150)]
    out2(0.0, 0.0, 0)
    assert js.calls[-1] == "stop"
    out3 = make_pygame_output(lambda: None)
    out3(1.0, 1.0, 100)  # must not raise


class FakeHMController:
    def __init__(self):
        self.states = []

    def SubmitState(self, state):
        self.states.append(state)


def test_hidmaestro_dict_mode_mapping():
    fake = FakeHMController()
    t = WinHidMaestroTarget(controller=fake)
    st = t.send_report(build_12b(0x0001 | 0x0800 | 0x8000, 16384, -16384, 0, 0, 255, 0))
    assert st[0] == (0x0001 | 0x0800 | 0x8000)
    assert len(fake.states) == 1
    s = fake.states[0]
    assert "A" in s["buttons"] and "DpadUp" in s["buttons"]
    assert "B" not in s["buttons"]
    assert abs(s["lx"] - 0.5) < 1e-6 and abs(s["ly"] + 0.5) < 1e-6
    assert s["lt"] == 1.0 and s["rt"] == 0.0
    # Grips have no standard-pad equivalent and are dropped from names.
    assert not any("rip" in b for b in s["buttons"])
    # handle_reports dict shape + output event path.
    t.handle_reports({"gamepad_12b": build_12b(0x0002, 0, 0, 0, 0, 0, 0)})
    assert fake.states[-1]["buttons"] == ["B"]
    seen = []
    t2 = WinHidMaestroTarget(controller=FakeHMController(),
                             on_output=lambda l, r: seen.append((l, r)))

    class Args:
        LeftMotor = 0.7
        RightMotor = 0.3
    t2._on_output_event(None, Args())
    assert seen == [(0.7, 0.3)]
    assert t2.last_output == (0.7, 0.3)
