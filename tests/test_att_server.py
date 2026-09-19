#!/usr/bin/env python3
"""ATT spec-compliance tests (findings-backlog §3) — socket-free.

Drives AttServer._handle_pdu with a fake connection: no BLE hardware,
no root, no Linux. Covers: blob offset edge (0x07 vs empty success),
permission checks (0x02/0x03), CCCD length (0x0D), UUID-length PDU
validation (0x04), MTU caps on discovery responses, dynamic labels.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gatt_db  # noqa: E402
from gatt_db import build_sc2_database  # noqa: E402
from att_server import AttServer  # noqa: E402


class FakeConn:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def close(self):
        pass


def _srv(mtu=23):
    db = build_sc2_database()
    s = AttServer(db, mtu=517)
    s.mtu = mtu
    s.conn = FakeConn()
    return s


def _find(db, uuid16, nth=0):
    hits = [h for h, a in sorted(db.attributes.items())
            if a.uuid == struct.pack("<H", uuid16)]
    return hits[nth]


def _err(pdu):
    assert pdu[0] == 0x01, pdu.hex()
    return pdu[1], struct.unpack("<H", pdu[2:4])[0], pdu[4]


def test_blob_offset_equal_len_returns_empty_success():
    s = _srv()
    h = _find(s.db, 0x2A4B)  # report map (long, readable)
    n = len(s.db.read_attribute(h))
    s._handle_pdu(struct.pack("<BHH", 0x0C, h, n))
    resp = s.conn.sent[-1]
    assert resp[0] == 0x0D and resp[1:] == b"", resp.hex()


def test_blob_offset_past_end_is_invalid_offset():
    s = _srv()
    h = _find(s.db, 0x2A4B)
    n = len(s.db.read_attribute(h))
    s._handle_pdu(struct.pack("<BHH", 0x0C, h, n + 1))
    assert _err(s.conn.sent[-1]) == (0x0C, h, 0x07)


def test_read_write_only_denied():
    s = _srv()
    h = _find(s.db, 0x2A4C)  # HID control point (WNR only)
    s._handle_pdu(struct.pack("<BH", 0x0A, h))
    assert _err(s.conn.sent[-1]) == (0x0A, h, 0x02)
    assert s._diag_perm_denied, "denial not logged"


def test_write_read_only_denied():
    s = _srv()
    h = _find(s.db, 0x2A4A)  # HID info (read only)
    s._handle_pdu(struct.pack("<BH", 0x12, h) + b"\x00")
    assert _err(s.conn.sent[-1]) == (0x12, h, 0x03)


def test_write_cmd_to_read_only_dropped_silently():
    s = _srv()
    h = _find(s.db, 0x2A4A)
    s._handle_pdu(struct.pack("<BH", 0x52, h) + b"\x00")
    assert s.conn.sent == [], "write command must not respond"
    assert s._diag_perm_denied, "denial not logged"


def test_cccd_write_wrong_length():
    s = _srv()
    h = _find(s.db, 0x2902)
    s._handle_pdu(struct.pack("<BH", 0x12, h) + b"\x01")
    assert _err(s.conn.sent[-1]) == (0x12, h, 0x0D)


def test_group_request_bad_uuid_length():
    s = _srv()
    s._handle_pdu(bytes([0x10]) + struct.pack("<HH", 1, 0xFFFF) + b"\x00\x00\x00")
    assert _err(s.conn.sent[-1]) == (0x10, 1, 0x04)


def test_read_by_type_capped_to_mtu():
    s = _srv(mtu=23)
    s._handle_pdu(bytes([0x08]) + struct.pack("<HH", 1, 0xFFFF)
                  + struct.pack("<H", 0x2803))
    resp = s.conn.sent[-1]
    assert resp[0] == 0x09
    assert len(resp) <= 23, f"response {len(resp)} exceeds MTU 23"
    assert (len(resp) - 2) % resp[1] == 0, "entries must not be split"


def test_read_by_group_capped_to_mtu():
    s = _srv(mtu=23)
    s._handle_pdu(bytes([0x08 + 0x08]) + struct.pack("<HH", 1, 0xFFFF))
    resp = s.conn.sent[-1]
    assert resp[0] == 0x11
    assert len(resp) <= 23, f"response {len(resp)} exceeds MTU 23"


def test_declaration_read_still_allowed():
    s = _srv()
    h = _find(s.db, 0x2800)
    s._handle_pdu(struct.pack("<BH", 0x0A, h))
    assert s.conn.sent[-1][0] == 0x0B


def test_dynamic_labels():
    s = _srv()
    hits = [h for h, a in sorted(s.db.attributes.items())
            if a.uuid == struct.pack("<H", 0x2A4D)
            and len(a.value) == 45]
    assert hits, "no 45-byte report characteristic"
    label = s._handle_label(hits[0])
    assert "45" in label, label
    assert s._handle_label(0xFFFF) == "?"
