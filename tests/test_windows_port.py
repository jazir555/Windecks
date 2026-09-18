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
    SyntheticWinInput, build_12b, build_45b,
    _axis_to_short, _axis_to_trigger,
)
from win_vigem import (  # noqa: E402
    parse_sc2_12b, sc2_buttons_to_xusb_flags, XUSB_FLAG, WinVigemTarget,
)
from win_ble import uuid16_str, _winrt_props  # noqa: E402


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


def test_synthetic_dict_shape():
    got = []
    src = SyntheticWinInput(on_report=got.append)
    rep = src.make_reports()
    assert set(rep) == {"gamepad_12b", "gamepad_45b", "mouse_4b", "kbd_8b"}
    assert rep["gamepad_12b"] == b"\x00" * 12
    assert len(rep["gamepad_45b"]) == 45
    assert rep["mouse_4b"] is None and rep["kbd_8b"] is None


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
