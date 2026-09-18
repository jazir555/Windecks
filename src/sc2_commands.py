#!/usr/bin/env python3
"""Shared SC2 Feature Report command handler — pure Python, OS-agnostic.

Extracted from main_virtual_usb.py / main_uhid.py / main_l2cap.py so the
Windows port (and tests) can exercise the Steam handshake without importing
Linux-only modules (fcntl, dbus, socket AF_BLUETOOTH, /dev/uhid).

Protocol (Steam <-> controller via Feature Reports):
  1. Host SET_REPORT (Feature, ID 0x01/0x02) with command bytes, e.g. [0x83 ...]
  2. Host GET_REPORT (Feature, same ID) reads the queued 64-byte response.

Covers: 0x81 CLEAR_MAPPINGS, 0x82 GET_DIGITAL_MAPPINGS, 0x83 GET_ATTRIBUTES,
0x85 SET_DEFAULT_DIGITAL_MAPPINGS, 0x87 SET_SETTINGS_VALUES, 0x89 GET_SETTINGS_VALUES,
0x8C GET_SETTINGS_DEFAULTS, 0x8D SET_CONTROLLER_MODE, 0xAE GET_SERIAL,
0xB4/B5 protocol, 0xBA GET_CHIP_ID, 0xEE/0xEF feature messages, 0x95 bootloader,
0xF2 MAPPING_ACK. See docs/sc2-protocol.md.
"""
import struct

HID_REPORT_TYPE_FEATURE = 3
HID_REPORT_TYPE_OUTPUT = 2


class SC2CommandHandler:
    """Handles SC2 Feature Report commands (synthetic responses). No OS deps."""

    def __init__(self):
        self.steam_input_mode = False
        self._settings_store = {}   # register_index -> value
        self._pending_response = {}  # report_id -> bytes (64)

    # -- HID-level entry points -------------------------------------------
    def handle_set_report(self, report_type, report_id, data):
        data = bytes(data or b"")
        if report_type == HID_REPORT_TYPE_FEATURE:
            response = self._handle_feature_report(report_id, data)
            if response:
                self._pending_response[report_id] = response
            return response
        if report_type == HID_REPORT_TYPE_OUTPUT:
            self._handle_output_report(report_id, data)
            return None
        return None

    def handle_get_report(self, report_type, report_id):
        if report_type == HID_REPORT_TYPE_FEATURE:
            return self._handle_feature_read(report_id)
        return b"\x00" * 64

    # keep UHID-style aliases used by main_uhid.py
    handle_feature_report = None  # placeholder replaced below
    handle_feature_read = None

    def _handle_feature_report(self, report_id, data):
        cmd = data[0] if len(data) > 0 else 0
        if cmd == 0x85 or report_id == 0x85:
            return self._handle_mode_switch(data)
        if cmd in (0x81, 0x83, 0x87, 0x89, 0x8C, 0x8D, 0xAE, 0xBA,
                   0xB4, 0xB5, 0xEE, 0xEF, 0xF2, 0x95, 0x82):
            return self._handle_sc2_command(report_id, data)
        if cmd == 0x8F:
            return self._handle_haptic_command(data)
        if len(data) > 0:
            return self._handle_sc2_command(report_id, data)
        return b"\x00" * 64

    def _handle_feature_read(self, report_id):
        response = self._pending_response.pop(report_id, None)
        if response:
            return response
        response = self._pending_response.pop(0x00, None)
        if response:
            return response
        return b"\x00" * 64

    def _handle_mode_switch(self, data):
        if data:
            mode = data[1] if len(data) > 1 and data[0] == 0x85 else data[0]
            if mode == 0x01:
                self.steam_input_mode = True
            elif mode == 0x00:
                self.steam_input_mode = False
        return b"\x00" * 64

    def _handle_haptic_command(self, data):
        return b"\x00" * 64

    def _handle_output_report(self, report_id, data):
        # Rumble payload parsing mirrors main_l2cap._on_haptic_write (stripped 9-byte form).
        if data and len(data) >= 9:
            try:
                left = struct.unpack_from("<H", data, 3)[0]
                right = struct.unpack_from("<H", data, 6)[0]
                self.last_rumble = (left, right)
            except struct.error:
                self.last_rumble = (0, 0)
        else:
            self.last_rumble = (0, 0)

    def _handle_sc2_command(self, report_id, value):
        if len(value) < 1:
            return b"\x00" * 64
        cmd = value[0]
        if cmd == 0x83:  # GET_ATTRIBUTES
            return bytes(bytearray([
                0x83, 0x2d,
                0x01, 0x03, 0x13, 0x00, 0x00,
                0x02, 0xff, 0xbf, 0x69, 0x41,
                0x0a, 0x2b, 0x12, 0xa9, 0x62,
                0x04, 0xad, 0xf1, 0xe4, 0x65,
                0x09, 0x2e, 0x00, 0x00, 0x00,
                0x0b, 0xa0, 0x0f, 0x00, 0x00,
                0x0d, 0x00, 0x00, 0x00, 0x00,
                0x0c, 0x00, 0x00, 0x00, 0x00,
                0x0e, 0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            ]))
        if cmd == 0xAE:  # GET_SERIAL — serial[0] must be 'F', byte[2]==0x01
            serial = b"F0000-0000-00000000"
            resp = bytearray([0xAE, 0x15, 0x01])
            resp += serial[:20].ljust(20, b"\x00")
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0xBA:  # GET_CHIP_ID
            chip_id = bytes([0x4E, 0x58, 0x50, 0x35, 0x33, 0x37, 0x30, 0x30,
                             0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36])
            resp = bytearray([0xBA, 0x11, 0x00]) + chip_id
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0x81:
            return bytes(bytearray([0x81, 0x00]) + bytearray(62))
        if cmd == 0x87:  # SET_SETTINGS_VALUES — persist register for 0x89 reads
            register = value[3] if len(value) > 3 else 0
            payload_len = value[2] if len(value) > 2 else 0
            val_len = max(0, payload_len - 1)
            data_val = value[4:4 + val_len] if len(value) >= 4 + val_len else value[4:6]
            if len(data_val) >= 2:
                self._settings_store[register] = struct.unpack_from("<H", data_val)[0]
            elif len(data_val) == 1:
                self._settings_store[register] = data_val[0]
            else:
                self._settings_store[register] = 0
            resp = bytearray([0x87, payload_len, register]) + bytes(data_val)
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0x89:  # GET_SETTINGS_VALUES
            num_regs = value[2] if len(value) > 2 else 0
            resp = bytearray([0x89, num_regs])
            for i in range(num_regs):
                reg = value[3 + i] if len(value) > 3 + i else 0
                val = self._settings_store.get(reg, 0)
                resp += bytes([reg, val & 0xFF, (val >> 8) & 0xFF])
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0xF2:
            return bytes(bytearray([0x01, 0x00, 0x00, 0x00, 0x00, 0xF2]) + bytearray(58))
        if cmd == 0x85:
            return bytes(bytearray([0x85, 0x00]) + bytearray(62))
        if cmd == 0x8D:
            self._handle_mode_switch(value)
            return bytes(bytearray([0x8D, 0x00]) + bytearray(62))
        if cmd == 0xB4:
            return bytes(bytearray([0xB4, 0x00, 0x01]) + bytearray(61))
        if cmd == 0xB5:
            return bytes(bytearray([0xB5, 0x00]) + bytearray(62))
        if cmd == 0xEE:
            return bytes(bytearray([0xEE, 0x00]) + bytearray(62))
        if cmd == 0xEF:
            return bytes(bytearray([0xEF, 0x00]) + bytearray(62))
        if cmd == 0x95:
            return bytes(bytearray([0x95, 0x00]) + bytearray(62))
        if cmd == 0x8C:
            return bytes(bytearray([0x8C, 0x00]) + bytearray(62))
        if cmd == 0x82:
            return bytes(bytearray([0x82, 0xFF, 0x02]) + bytearray(61))
        return bytes(bytearray([cmd, 0x00]) + bytearray(62))


# Backfill the UHID-style public aliases.
SC2CommandHandler.handle_feature_report = SC2CommandHandler._handle_feature_report
SC2CommandHandler.handle_feature_read = SC2CommandHandler._handle_feature_read
