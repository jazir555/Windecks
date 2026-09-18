#!/usr/bin/env python3
"""Shared SC2 Feature Report command handler — pure Python, OS-agnostic.

Extracted from main_virtual_usb.py / main_uhid.py / main_l2cap.py so the
Windows port (and tests) can exercise the Steam handshake without importing
Linux-only modules (fcntl, dbus, socket AF_BLUETOOTH, /dev/uhid).

Protocol (Steam <-> controller via Feature Reports):
  1. Host SET_REPORT (Feature, ID 0x01/0x02) with command bytes, e.g. [0x83 ...]
  2. Host GET_REPORT (Feature, same ID) reads the queued 64-byte response.

Covers: 0x81 CLEAR_MAPPINGS, 0x82 GET_DIGITAL_MAPPINGS, 0x83 GET_ATTRIBUTES,
0x85 SET_DEFAULT_DIGITAL_MAPPINGS, 0x86 FACTORY_RESET (in-memory only),
0x87 SET_SETTINGS_VALUES, 0x88 CLEAR_SETTINGS_VALUES, 0x89 GET_SETTINGS_VALUES,
0x8B GET_SETTINGS_MAXS, 0x8C GET_SETTINGS_DEFAULTS, 0x8D SET_CONTROLLER_MODE,
0x8E LOAD_DEFAULT_SETTINGS, 0x90/0x95 reboot (ACK-only, never acted on),
0x9F TURN_OFF (ACK-only), 0xA1 GET_DEVICE_INFO, 0xAE GET_SERIAL,
0xB4/B5 protocol, 0xBA GET_CHIP_ID, 0xC5/E9 SET/GET_LED_COLOR,
0xDB/DC user store, 0xED/EE/EF/F0 READ/STAGE/COMMIT/DELETE_SETTING,
0xEE/0xEF feature messages, 0x95 bootloader,
0xF2 MAPPING_ACK. See docs/sc2-protocol.md.

Opcode sources: mwdmwd/sc26re app/src/valve_feature.h (authoritative enum
from working open firmware) and CouchTurtle/sc2-research
docs/FIRMWARE_PROTOCOL.md (update protocol, settings registry, opcodes).
Settings defaults (83 registers) transcribed from sc26re
app/src/ibex_settings_registry.c SETTING_ENTRY(default, min, max, ...).
"""
import struct

HID_REPORT_TYPE_FEATURE = 3
HID_REPORT_TYPE_OUTPUT = 2

# (default, max) per settings register, from sc26re ibex_settings_registry.c.
# Only default/max are needed: 0x89 reads current-or-default, 0x8B reads max,
# 0x8C reads default, 0x86/0x88/0x8E restore defaults.
SC2_SETTING_DEFAULTS = {
    0: (0, 10), 1: (2, 10), 2: (0, 360), 3: (1200, 25000),
    4: (0, 1), 5: (0, 1), 6: (0, 255), 7: (0, 255), 8: (0, 1),
    9: (1, 1), 10: (7000, 16384), 11: (200, 1000), 12: (100, 1000),
    13: (50, 500), 14: (5500, 32767), 15: (923, 2000), 16: (382, 2000),
    17: (2, 10), 18: (8000, 20000), 19: (1770, 4096), 20: (1630, 4096),
    21: (5, 500), 22: (2, 8), 23: (2, 8), 24: (20, 30), 25: (40, 99),
    26: (0, 1), 27: (-10, 12), 28: (16500, 4096), 29: (15000, 4096),
    30: (500, 1000), 31: (400, 800), 32: (800, 1200), 33: (0, 1),
    34: (100, 400), 35: (80, 100), 36: (0, 100), 37: (0, 1),
    38: (50, 1000), 39: (0, 1), 40: (20, 180), 41: (3900, 25000),
    42: (1, 1), 43: (0, 1), 44: (50, 100), 45: (50, 100), 46: (0, 2),
    47: (0, 16), 48: (0, 32767), 49: (2, 2), 50: (900, 32767),
    51: (250, 32767), 52: (40, 100), 53: (40, 100), 54: (100, 100),
    55: (100, 100), 56: (10, 100), 57: (10, 100), 58: (0, 100),
    59: (0, 100), 60: (0, 1), 61: (0, 15), 62: (0, 2), 63: (150, 300),
    64: (4, 16), 65: (1, 1), 66: (1, 1), 67: (0, 7), 68: (90, 99),
    69: (1, 1), 70: (1, 2), 71: (1, 1), 72: (1200, 16000),
    73: (1000, 16000), 74: (3, 3), 75: (0, 1), 76: (-3, 6),
    77: (0, 1), 78: (1, 1), 79: (2, 4), 80: (1, 2), 81: (0, 1),
    82: (3, 3),
}

# Opcodes that are ACK-only on a spoofed device: real hardware would reboot,
# power off, wipe, or (re)calibrate. We return success bytes and never act.
ACK_ONLY_OPCODES = frozenset([
    0x86,  # FACTORY_RESET (handled: restores in-memory defaults + ACK)
    0x90,  # REBOOT_TO_ISP (bootloader)
    0x95,  # FIRMWARE_UPDATE_REBOOT
    0x9F,  # TURN_OFF_CONTROLLER
    0xA2,  # WRITE_CALIBRATION_DATA
    0xB5,  # CALIBRATE_GYRO
    0xC0,  # CALIBRATE_ANALOG_TRIGGERS
    0xC2,  # CHECK_GYRO_FW_LOAD
    0xC3,  # CALIBRATE_PRESSURE_SENSORS
    0xCE,  # RESET_IMU
    0xD8,  # CALIBRATE_TRACKPAD_STICK
    0xE2,  # SET_TRACKPAD_SIDE
    0xFE,  # WRITE_PROVISIONING
])


class SC2CommandHandler:
    """Handles SC2 Feature Report commands (synthetic responses). No OS deps."""

    def __init__(self):
        self.steam_input_mode = False
        self._settings_store = {}   # register_index -> value
        self._staged_settings = {}  # 0xEE-staged, applied by 0xEF
        self._user_store = {}       # 0xDB/0xDC key -> bytes
        self._led_color = bytes(4)  # 0xC5/E9 RGBW
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
        if cmd in (0x81, 0x83, 0x86, 0x87, 0x88, 0x89, 0x8B, 0x8C, 0x8D,
                   0x8E, 0x90, 0x95, 0x9F, 0xA1, 0xA2, 0xAE, 0xB4, 0xB5,
                   0xBA, 0xC0, 0xC2, 0xC3, 0xC5, 0xCE, 0xD8, 0xDB, 0xDC,
                   0xE2, 0xE9, 0xED, 0xEE, 0xEF, 0xF0,
                   0xF2, 0x95, 0x82, 0xFE):
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
                val = struct.unpack_from("<H", data_val)[0]
                _, vmax = SC2_SETTING_DEFAULTS.get(register, (0, 0xFFFF))
                self._settings_store[register] = min(val, vmax)
            elif len(data_val) == 1:
                val = data_val[0]
                _, vmax = SC2_SETTING_DEFAULTS.get(register, (0, 0xFFFF))
                self._settings_store[register] = min(val, vmax)
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
                val = self._settings_store.get(
                    reg, SC2_SETTING_DEFAULTS.get(reg, (0, 0))[0])
                resp += bytes([reg, val & 0xFF, (val >> 8) & 0xFF])
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0x8B:  # GET_SETTINGS_MAXS — same shape, max values
            num_regs = value[2] if len(value) > 2 else 0
            resp = bytearray([0x8B, num_regs])
            for i in range(num_regs):
                reg = value[3 + i] if len(value) > 3 + i else 0
                val = SC2_SETTING_DEFAULTS.get(reg, (0, 0))[1]
                resp += bytes([reg, val & 0xFF, (val >> 8) & 0xFF])
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0x86 or cmd == 0x8E:  # FACTORY_RESET / LOAD_DEFAULT_SETTINGS
            # In-memory only: drop overrides so defaults take effect again.
            self._settings_store.clear()
            self._staged_settings.clear()
            return bytes(bytearray([cmd, 0x00]) + bytearray(62))
        if cmd == 0x88:  # CLEAR_SETTINGS_VALUES — restore listed regs
            num_regs = value[2] if len(value) > 2 else 0
            for i in range(num_regs):
                reg = value[3 + i] if len(value) > 3 + i else 0
                self._settings_store.pop(reg, None)
            return bytes(bytearray([0x88, num_regs]) + bytearray(62))
        if cmd in (0x90, 0x95, 0x9F, 0xA2, 0xB5, 0xC0, 0xC2, 0xC3,
                   0xCE, 0xD8, 0xE2, 0xFE):
            # ACK-only: reboot/poweroff/calibration/provisioning would be
            # destructive or meaningless without hardware. Never acted on.
            return bytes(bytearray([cmd, 0x00]) + bytearray(62))
        if cmd == 0xA1:  # GET_DEVICE_INFO — sc26re prepare_device_info shape
            selector = value[2] if len(value) > 2 else 0
            if selector == 1:
                dev_id = bytes([0x46, 0x00, 0x00, 0x00, 0x28, 0xDE,
                                0x03, 0x13, 0x00, 0x00, 0x00, 0x00,
                                0x00, 0x00, 0x00, 0x01])
                resp = bytearray([0xA1, 0x12, selector, 0x00]) + dev_id
            else:
                resp = bytearray([0xA1, 0x12]) + bytearray(18)
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0xC5:  # SET_LED_COLOR — store RGBW
            self._led_color = bytes(value[2:6]).ljust(4, b"\x00")[:4]
            return bytes(bytearray([0xC5, 0x00]) + bytearray(62))
        if cmd == 0xE9:  # GET_LED_COLOR — return stored RGBW
            resp = bytearray([0xE9, 0x04]) + bytearray(self._led_color)
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0xDC:  # SET_USER_STORE — key at [2], blob at [3:]
            key = value[2] if len(value) > 2 else 0
            self._user_store[key] = bytes(value[3:8])
            return bytes(bytearray([0xDC, 0x00]) + bytearray(62))
        if cmd == 0xDB:  # GET_USER_STORE
            key = value[2] if len(value) > 2 else 0
            blob = self._user_store.get(key, b"\x00" * 5)
            resp = bytearray([0xDB, len(blob)]) + bytearray(blob)
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0xEE:  # STAGE_SETTING — buffer until 0xEF commit
            reg = value[2] if len(value) > 2 else 0
            val = struct.unpack_from("<H", value, 3)[0] if len(value) >= 5 else 0
            self._staged_settings[reg] = val
            return bytes(bytearray([0xEE, 0x00]) + bytearray(62))
        if cmd == 0xEF:  # COMMIT_SETTING — apply staged values
            self._settings_store.update(self._staged_settings)
            self._staged_settings.clear()
            return bytes(bytearray([0xEF, 0x00]) + bytearray(62))
        if cmd == 0xED:  # READ_SETTING — single register read
            reg = value[2] if len(value) > 2 else 0
            val = self._settings_store.get(
                reg, SC2_SETTING_DEFAULTS.get(reg, (0, 0))[0])
            resp = bytearray([0xED, 0x01, reg, val & 0xFF, (val >> 8) & 0xFF])
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0xF0:  # DELETE_SETTING — restore default
            reg = value[2] if len(value) > 2 else 0
            self._settings_store.pop(reg, None)
            self._staged_settings.pop(reg, None)
            return bytes(bytearray([0xF0, 0x00]) + bytearray(62))
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
            num_regs = value[2] if len(value) > 2 else 0
            resp = bytearray([0x8C, num_regs])
            for i in range(num_regs):
                reg = value[3 + i] if len(value) > 3 + i else 0
                val = SC2_SETTING_DEFAULTS.get(reg, (0, 0))[0]
                resp += bytes([reg, val & 0xFF, (val >> 8) & 0xFF])
            resp += bytearray(64 - len(resp))
            return bytes(resp)
        if cmd == 0x82:
            return bytes(bytearray([0x82, 0xFF, 0x02]) + bytearray(61))
        return bytes(bytearray([cmd, 0x00]) + bytearray(62))


# Backfill the UHID-style public aliases.
SC2CommandHandler.handle_feature_report = SC2CommandHandler._handle_feature_report
SC2CommandHandler.handle_feature_read = SC2CommandHandler._handle_feature_read
