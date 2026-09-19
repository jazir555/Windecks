#!/usr/bin/env python3
"""
Raw L2CAP ATT Server for BLE Peripheral.

Bypasses BlueZ's GATT server by using a raw L2CAP socket on CID 4
bound directly to the static random BLE address.

Handles ATT PDU exchange: MTU, service discovery, reads, writes, notifications.
SMP pairing is handled separately by the kernel on CID 6.
"""

import json
import os
import socket
import struct
import ctypes
import ctypes.util
import threading
import time
import select
from collections import defaultdict

# Structured protocol logging (enabled via SPOOFDECK_PROTO_LOG=1)
_PROTO_LOG = os.environ.get('SPOOFDECK_PROTO_LOG', '') == '1'

def _proto_log(event, **kwargs):
    """Emit a structured JSON log line to stderr when SPOOFDECK_PROTO_LOG=1."""
    # NOTE: This function is duplicated in main_l2cap.py. Both must be kept in sync.
    # A shared module was considered but rejected to avoid deployment changes.
    if not _PROTO_LOG:
        return
    entry = {"ts": round(time.monotonic(), 3), "event": event}
    entry.update(kwargs)
    try:
        print(json.dumps(entry), flush=True)
    except Exception:
        pass


# SC2 command byte → human name (for structured logging)
# Keep in sync with main_l2cap.py _SC2_CMD_NAMES
_SC2_CMD_NAMES = {
    0x81: "CLEAR_DIGITAL_MAPPINGS", 0x82: "GET_DIGITAL_MAPPINGS",
    0x83: "GET_ATTRIBUTES", 0x85: "SET_DEFAULT_DIGITAL_MAPPINGS",
    0x87: "SET_SETTINGS_VALUES", 0x89: "GET_SETTINGS_VALUES",
    0x8C: "GET_SETTINGS_DEFAULTS", 0x8D: "SET_CONTROLLER_MODE",
    0xAE: "GET_SERIAL", 0xB4: "PROTOCOL_VERSION",
    0xB5: "PROTOCOL_COMMAND", 0xBA: "GET_CHIP_ID",
    0xEE: "FEATURE_REPORT_WRITE", 0xEF: "FEATURE_REPORT_READ",
    0x95: "ENTER_BOOTLOADER", 0xF2: "GET_SYSTEM_INFO",
}

from gatt_db import (
    GattDatabase, Attribute,
    ATT_OP_ERROR, ATT_OP_MTU_REQ, ATT_OP_MTU_RSP,
    ATT_OP_FIND_INFO_REQ, ATT_OP_FIND_INFO_RSP,
    ATT_OP_READ_BY_TYPE_REQ, ATT_OP_READ_BY_TYPE_RSP,
    ATT_OP_READ_REQ, ATT_OP_READ_RSP,
    ATT_OP_READ_BLOB_REQ, ATT_OP_READ_BLOB_RSP,
    ATT_OP_READ_BY_GROUP_TYPE_REQ, ATT_OP_READ_BY_GROUP_TYPE_RSP,
    ATT_OP_WRITE_REQ, ATT_OP_WRITE_RSP,
    ATT_OP_HANDLE_NFY, ATT_OP_HANDLE_IND, ATT_OP_HANDLE_CNF,
    ATT_OP_WRITE_CMD,
    ATT_ERR_INVALID_HANDLE, ATT_ERR_READ_NOT_PERM, ATT_ERR_WRITE_NOT_PERM,
    ATT_ERR_ATTR_NOT_FOUND, ATT_ERR_REQ_NOT_SUPP, ATT_ERR_INVALID_OFFSET,
    ATT_ERR_INVALID_PDU,
    ATT_PROP_READ, ATT_PROP_WRITE, ATT_PROP_WRITE_NO_RSP,
    GATT_PRIM_SVC_UUID, GATT_CHARAC_UUID, uuid16_to_bytes,
)

# Invalid Attribute Value Length (Core Spec Vol 3, Part F, 3.4.1.1).
ATT_ERR_INVALID_ATTR_LEN = 0x0D

AF_BLUETOOTH = 31
BTPROTO_L2CAP = 0
BT_ATT_CID = 4
BDADDR_LE_RANDOM = 0x02
SOL_BLUETOOTH = 274
BT_SECURITY = 4
BT_SECURITY_LOW = 1
BT_SECURITY_MEDIUM = 2

# Load libc for ctypes bind (Linux only; None on Windows where the raw
# L2CAP path is unavailable — win_ble.py is the Windows equivalent).
_libc_name = ctypes.util.find_library("c")
_libc = ctypes.CDLL(_libc_name, use_errno=True) if _libc_name else None


class AttServer:
    """
    Raw L2CAP ATT server for BLE peripheral.

    Binds to a static random BLE address on CID 4 (ATT fixed channel).
    Handles incoming ATT connections and PDU exchange.
    """

    def __init__(self, db, address="C2:12:34:56:78:9A", mtu=517):
        """
        Args:
            db: GattDatabase instance
            address: BLE static random address
            mtu: Server MTU (max attribute value length)
        """
        self.db = db
        self.address = address
        self.server_mtu = mtu
        self.mtu = 23  # Negotiated MTU (starts at default)
        self.sock = None
        self.conn = None
        self.conn_addr = None
        self._running = False
        self._thread = None
        self._notification_handles = set()  # CCCD-enabled handles
        self._client_cccds = {}            # Client address -> CCCD-enabled handles (persisted for bonding)
        self.notification_count = 0
        self._on_connection = None
        self._on_disconnection = None
        # Diagnostic counters
        self._diag_notif_sent = defaultdict(int)     # handle -> count of sent notifications
        self._diag_notif_dropped = defaultdict(int)  # handle -> count of dropped (no CCCD) notifications
        self._diag_writes = []                        # list of (timestamp, handle, uuid_hex, value_hex)
        self._diag_cccd_events = []                   # list of (timestamp, cccd_handle, value_handle, enabled)
        self._diag_perm_denied = []                   # list of (timestamp, op, handle) permission denials
        self._on_cccd_enabled = None

    # -- ATT spec-compliance helpers ------------------------------------
    def _parse_uuid_filter(self, data):
        """Validate the trailing attribute-type UUID of a group/type request.

        Returns (uuid_bytes_or_None, error_code_or_None). Core Spec Vol 3,
        Part F, 3.4.4: the UUID shall be 16-bit or 128-bit; anything else
        is a malformed PDU.
        """
        trailing = data[5:]
        if len(trailing) == 0:
            return None, None
        if len(trailing) in (2, 16):
            return trailing if len(trailing) == 2 else None, None
        return None, ATT_ERR_INVALID_PDU

    def _fit_entries(self, entries, entry_len):
        """Drop trailing whole entries so the value list fits the MTU.

        RSP headers here are 2 bytes (opcode + length/format), so the list
        is capped at mtu - 2. Entries are never split (Core Spec Vol 3,
        Part F, 3.4.4.2: all attribute data in one response shares a size).
        """
        budget = max(0, self.mtu - 2)
        keep = min(len(entries), budget // entry_len) if entry_len else 0
        return entries[:keep]

    @staticmethod
    def _readable(attr):
        # Declaration attributes (service/characteristic declarations) carry
        # no property bits by construction (properties == 0) and are always
        # readable; value attributes must have the READ bit.
        return attr.properties == 0 or bool(attr.properties & ATT_PROP_READ)

    @staticmethod
    def _writable(attr, is_command):
        if attr.properties == 0:
            # Declarations are not writable value attributes.
            return False
        if is_command:
            return bool(attr.properties & ATT_PROP_WRITE_NO_RSP)
        return bool(attr.properties & (ATT_PROP_WRITE | ATT_PROP_WRITE_NO_RSP))

    def _deny(self, op, handle):
        self._diag_perm_denied.append((time.strftime('%H:%M:%S'), op, handle))

    def _handle_label(self, handle):
        """Derive a diagnostic label for any handle from the live database."""
        attr = self.db.lookup(handle)
        if attr is None:
            return "?"
        cccd = uuid16_to_bytes(0x2902).hex()
        if attr.uuid.hex() == cccd:
            vh = self._find_cccd_value_handle(handle)
            return f"CCCD-for-0x{vh:04x}" if vh is not None else "CCCD"
        # Characteristic declaration? Name it by its value handle + UUID.
        if attr.uuid == uuid16_to_bytes(GATT_CHARAC_UUID) and len(attr.value) >= 3:
            vh = attr.value[1] | (attr.value[2] << 8)
            return f"decl-for-0x{vh:04x}"
        # Value attribute: resolve UUID + report reference if present.
        name = self._uuid_label(attr.uuid)
        for dh in (handle + 1, handle + 2, handle + 3):
            d = self.db.lookup(dh)
            if d is None:
                break
            if d.uuid == uuid16_to_bytes(0x2908) and len(d.value) == 2:
                name += f"(ID{d.value[0]:02X}/{'In' if d.value[1] == 1 else 'Out' if d.value[1] == 2 else 'Feat'})"
                break
            if d.uuid in (uuid16_to_bytes(GATT_CHARAC_UUID),
                          uuid16_to_bytes(GATT_PRIM_SVC_UUID)):
                break
        return name

    @staticmethod
    def _uuid_label(uuid_bytes):
        from gatt_db import (
            SVC_GAP, SVC_GATT, SVC_HID, SVC_BATTERY, SVC_DEVICE_INFO,
            CHR_DEVICE_NAME, CHR_APPEARANCE, CHR_SERVICE_CHANGED,
            CHR_HID_INFO, CHR_REPORT_MAP, CHR_HID_CONTROL_POINT,
            CHR_REPORT, CHR_PROTOCOL_MODE, CHR_BATTERY_LEVEL,
            CHR_MANUFACTURER_NAME, CHR_MODEL_NUMBER, CHR_PNP_ID,
            CHR_SERIAL_NUMBER, CHR_FIRMWARE_REVISION,
            CHR_HARDWARE_REVISION, CHR_SOFTWARE_REVISION,
            SC2_HID_SERVICE_UUID, SC2_INPUT_CH1_UUID, SC2_INPUT_CH2_UUID,
            SC2_REPORT_CH_UUID,
        )
        import uuid as _uuid_mod
        u = bytes(uuid_bytes)
        table16 = {
            SVC_GAP: "GAP", SVC_GATT: "GATT", SVC_HID: "HID",
            SVC_BATTERY: "BAT", SVC_DEVICE_INFO: "DIS",
            CHR_DEVICE_NAME: "DevName", CHR_APPEARANCE: "Appear",
            CHR_SERVICE_CHANGED: "SvcChanged", CHR_HID_INFO: "HIDInfo",
            CHR_REPORT_MAP: "RepMap", CHR_HID_CONTROL_POINT: "HIDCtrl",
            CHR_REPORT: "Report", CHR_PROTOCOL_MODE: "ProtoMode",
            CHR_BATTERY_LEVEL: "Batt", CHR_MANUFACTURER_NAME: "Mfr",
            CHR_MODEL_NUMBER: "Model", CHR_PNP_ID: "PnP",
            CHR_SERIAL_NUMBER: "Serial", CHR_FIRMWARE_REVISION: "FwRev",
            CHR_HARDWARE_REVISION: "HwRev", CHR_SOFTWARE_REVISION: "SwRev",
        }
        if len(u) == 2:
            code = struct.unpack('<H', u)[0]
            if code == GATT_PRIM_SVC_UUID:
                return "PrimSvc"
            if code == GATT_CHARAC_UUID:
                return "CharDecl"
            if code == 0x2902:
                return "CCCD"
            if code == 0x2908:
                return "RepRef"
            return table16.get(code, f"0x{code:04X}")
        try:
            s = str(_uuid_mod.UUID(bytes_le=u)).lower()
        except Exception:
            return u.hex()
        return {
            SC2_HID_SERVICE_UUID.lower(): "ValveSvc",
            SC2_INPUT_CH1_UUID.lower(): "ValveCh1",
            SC2_INPUT_CH2_UUID.lower(): "ValveCh2",
            SC2_REPORT_CH_UUID.lower(): "ValveRep",
        }.get(s, s[:8])

    def start(self):
        """Create socket, bind, listen. Loops to accept connections."""
        self._create_socket()
        self._running = True
        
        while self._running:
            print(f"[att] Listening for connection on {self.address} CID {BT_ATT_CID}...")
            try:
                self.conn, self.conn_addr = self.sock.accept()
                print(f"[att] Client connected: {self.conn_addr}")


                
                # Reset MTU to default for new connection
                self.mtu = 23
                
                # Reset diagnostic counters for new connection
                self._diag_notif_sent.clear()
                self._diag_notif_dropped.clear()
                self._diag_writes.clear()
                self._diag_cccd_events.clear()
                self._diag_perm_denied.clear()
                
                # Restore CCCD states for this client if they are bonded/known
                client_ip = self.conn_addr[0] if self.conn_addr else "unknown"
                self._notification_handles = self._client_cccds.setdefault(client_ip, set())
                if self._notification_handles:
                    print(f"[att] Restored CCCD handles for {client_ip}: {[f'0x{h:04x}' for h in self._notification_handles]}")
                    # Notify application that CCCDs are already enabled
                    if self._on_cccd_enabled:
                        for handle in self._notification_handles:
                            self._on_cccd_enabled(handle)
                
                if self._on_connection:
                    self._on_connection(self.conn_addr)

                self._pdu_loop()
                
                # Clean up connection
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
                
            except Exception as e:
                if self._running:
                    print(f"[att] Accept/Connection error: {e}")
                    time.sleep(1)

    def start_async(self):
        """Start in a background thread."""
        self._thread = threading.Thread(target=self.start, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the server."""
        self._running = False
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def _create_socket(self):
        """Create and bind the raw L2CAP ATT socket."""
        if _libc is None:
            raise OSError("Raw L2CAP ATT server requires Linux "
                          "(use src/win_ble.py on Windows).")
        self.sock = socket.socket(AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP)

        # NOTE: Do NOT set BT_SECURITY_MEDIUM — it causes BlueZ HOG profile
        # to require encryption for SET_REPORT, resulting in
        # "Encryption Key Size is insufficient" errors. BT_SECURITY_LOW
        # allows unencrypted ATT operations which is what we need.

        # Build sockaddr_l2: family(2) + psm(2) + bdaddr(6) + cid(2) + addr_type(1)
        addr_bytes = bytes.fromhex(self.address.replace(':', ''))[::-1]
        sockaddr = struct.pack('<HH6sHB',
            AF_BLUETOOTH,
            0,                  # psm (0 for fixed CID)
            addr_bytes,         # bdaddr
            BT_ATT_CID,         # cid = 4 (ATT)
            BDADDR_LE_RANDOM    # addr_type
        )

        # Use ctypes bind() because Python 3.13 doesn't support BLE addr types
        result = _libc.bind(
            self.sock.fileno(),
            ctypes.create_string_buffer(sockaddr),
            len(sockaddr)
        )
        if result != 0:
            err = ctypes.get_errno()
            raise OSError(err, f"bind failed: {err}")

        self.sock.listen(1)

    def _pdu_loop(self):
        """Main loop: read ATT PDUs and send responses."""
        while self._running:
            try:
                data = self.conn.recv(self.mtu + 3)
                if not data:
                    break
                self._handle_pdu(data)
            except Exception as e:
                if self._running:
                    print(f"[att] PDU error: {e}")
                break

        print("[att] Client disconnected")
        self._print_diag_summary()
        if self._on_disconnection:
            self._on_disconnection(self.conn_addr)
        # Reset local active notification handles to empty set, but do not clear
        # the client's persisted configuration in self._client_cccds.
        self._notification_handles = set()

    def _handle_pdu(self, data):
        """Parse ATT opcode and dispatch to handler."""
        opcode = data[0]
        print(f"[att] PDU recv: opcode=0x{opcode:02x} len={len(data)} data={data.hex()}")

        if opcode == ATT_OP_MTU_REQ:
            self._handle_mtu_req(data)
        elif opcode == ATT_OP_READ_BY_GROUP_TYPE_REQ:
            self._handle_read_by_group_type(data)
        elif opcode == ATT_OP_READ_BY_TYPE_REQ:
            self._handle_read_by_type(data)
        elif opcode == ATT_OP_FIND_INFO_REQ:
            self._handle_find_info(data)
        elif opcode == ATT_OP_READ_REQ:
            self._handle_read(data)
        elif opcode == ATT_OP_READ_BLOB_REQ:
            self._handle_read_blob(data)
        elif opcode == ATT_OP_WRITE_REQ:
            self._handle_write(data)
        elif opcode == ATT_OP_WRITE_CMD:
            self._handle_write_cmd(data)
        elif opcode == ATT_OP_HANDLE_CNF:
            pass  # Confirmation of our indication — no action needed
        else:
            self._send_error(opcode, 0x0000, ATT_ERR_REQ_NOT_SUPP)

    def _handle_mtu_req(self, data):
        """Handle Exchange MTU Request (0x02)."""
        if len(data) < 3:
            _proto_log("att_mtu_req", error="INVALID_PDU_LEN", len=len(data))
            self._send_error(ATT_OP_MTU_REQ, 0x0000, ATT_ERR_INVALID_PDU)
            return
        client_mtu = struct.unpack('<H', data[1:3])[0]
        self.mtu = min(client_mtu, self.server_mtu)
        self.mtu = max(self.mtu, 23)  # Minimum MTU is 23
        print(f"[att] MTU exchange: client={client_mtu}, server={self.server_mtu}, negotiated={self.mtu}")
        resp = struct.pack('<BH', ATT_OP_MTU_RSP, self.server_mtu)
        self._send(resp)

    def _handle_read_by_group_type(self, data):
        """Handle Read By Group Type Request (0x10) — service discovery."""
        # Format: opcode(1) + start_handle(2) + end_handle(2) + [uuid(2 or 16)]
        if len(data) < 5:
            self._send_error(data[0], 0, ATT_ERR_INVALID_PDU)
            return
        opcode = data[0]
        start_handle = struct.unpack('<H', data[1:3])[0]
        end_handle = struct.unpack('<H', data[3:5])[0]

        uuid_filter, uuid_err = self._parse_uuid_filter(data)
        if uuid_err is not None:
            self._send_error(opcode, start_handle, uuid_err)
            return

        print(f"[att] ReadByGroupType: start=0x{start_handle:04x} end=0x{end_handle:04x} uuid_filter={uuid_filter.hex() if uuid_filter else None}")

        # Find matching services
        services = self.db.find_services(start_handle, end_handle, uuid_filter)

        if not services:
            print(f"[att] No services found, sending error")
            self._send_error(opcode, start_handle, ATT_ERR_ATTR_NOT_FOUND)
            return

        print(f"[att] Found {len(services)} services")
        # Build response: each service is handle(2) + end_handle(2) + uuid(2)
        # Build response: each service is handle(2) + end_handle(2) + uuid
        # Note: all returned services in a single RSP must be of the same length.
        first_svc_uuid_len = len(services[0][2])
        entries = []
        for svc_start, svc_end, svc_uuid in services:
            if len(svc_uuid) != first_svc_uuid_len:
                break
            entries.append((svc_start, svc_end, svc_uuid))
            print(f"  Service: start=0x{svc_start:04x} end=0x{svc_end:04x} uuid={svc_uuid.hex()}")
        entry_len = 4 + first_svc_uuid_len
        entries = self._fit_entries(entries, entry_len)
        attr_list = b''.join(
            struct.pack('<HH', s, e) + u for s, e, u in entries)

        # Response format: opcode(1) + length(1) + data
        length = 4 + first_svc_uuid_len  # 4 bytes handles + UUID length
        resp = struct.pack('<BB', ATT_OP_READ_BY_GROUP_TYPE_RSP, length) + attr_list
        print(f"[att] Sending ReadByGroupType response: {resp.hex()}")
        self._send(resp)

    def _handle_read_by_type(self, data):
        """Handle Read By Type Request (0x08) — characteristic discovery."""
        if len(data) < 5:
            self._send_error(data[0], 0, ATT_ERR_INVALID_PDU)
            return
        opcode = data[0]
        start_handle = struct.unpack('<H', data[1:3])[0]
        end_handle = struct.unpack('<H', data[3:5])[0]

        uuid_filter, uuid_err = self._parse_uuid_filter(data)
        if uuid_err is not None:
            self._send_error(opcode, start_handle, uuid_err)
            return

        print(f"[att] ReadByType: start=0x{start_handle:04x} end=0x{end_handle:04x} uuid_filter={uuid_filter.hex() if uuid_filter else None}")

        # Find matching characteristics
        chars = self.db.find_characteristics(start_handle, end_handle, uuid_filter)

        if not chars:
            print(f"[att] No characteristics found, sending error")
            self._send_error(opcode, start_handle, ATT_ERR_ATTR_NOT_FOUND)
            return

        print(f"[att] Found {len(chars)} characteristics")
        # Build response: each char is decl_handle(2) + properties(1) + value_handle(2) + uuid
        # Note: all returned characteristics in a single RSP must be of the same length.
        first_char_uuid_len = len(chars[0][3])
        entries = []
        for decl_handle, val_handle, props, char_uuid in chars:
            if len(char_uuid) != first_char_uuid_len:
                break
            entries.append((decl_handle, val_handle, props, char_uuid))
            print(f"  Char: decl=0x{decl_handle:04x} val=0x{val_handle:04x} props=0x{props:02x} uuid={char_uuid.hex()}")
        entry_len = 5 + first_char_uuid_len
        entries = self._fit_entries(entries, entry_len)
        attr_list = b''.join(
            struct.pack('<H', d) + struct.pack('B', p)
            + struct.pack('<H', v) + u for d, v, p, u in entries)

        # Response format: opcode(1) + length(1) + data
        length = 5 + first_char_uuid_len  # 2+1+2 + UUID length
        resp = struct.pack('<BB', ATT_OP_READ_BY_TYPE_RSP, length) + attr_list
        print(f"[att] Sending ReadByType response: {resp.hex()}")
        self._send(resp)

    def _handle_find_info(self, data):
        """Handle Find Information Request (0x04) — descriptor discovery."""
        if len(data) < 5:
            self._send_error(data[0], 0, ATT_ERR_INVALID_PDU)
            return
        opcode = data[0]
        start_handle = struct.unpack('<H', data[1:3])[0]
        end_handle = struct.unpack('<H', data[3:5])[0]

        descriptors = self.db.find_descriptors(start_handle, end_handle)

        if not descriptors:
            self._send_error(opcode, start_handle, ATT_ERR_ATTR_NOT_FOUND)
            return

        # Build response: must only contain UUIDs of the same length in a single response
        first_uuid_len = len(descriptors[0][1])
        same_len = [(h, u) for h, u in descriptors if len(u) == first_uuid_len]
        entry_len = 2 + first_uuid_len
        same_len = self._fit_entries(same_len, entry_len)
        attr_list = b''.join(struct.pack('<H', h) + u for h, u in same_len)

        # Response format: opcode(1) + format(1) + data
        # format: 0x01 for 16-bit UUIDs, 0x02 for 128-bit UUIDs
        fmt = 0x01 if first_uuid_len == 2 else 0x02
        resp = struct.pack('<BB', ATT_OP_FIND_INFO_RSP, fmt) + attr_list
        print(f"[att] FindInfo: sending response fmt={fmt} len={len(resp)}")
        self._send(resp)

    def _handle_read(self, data):
        """Handle Read Request (0x0A)."""
        import time
        ts = time.strftime('%H:%M:%S')
        opcode = data[0]
        if len(data) < 3:
            _proto_log("att_read_req", opcode=f"0x{opcode:02x}", error="INVALID_PDU_LEN", len=len(data))
            self._send_error(opcode, 0x0000, ATT_ERR_INVALID_PDU)
            return
        handle = struct.unpack('<H', data[1:3])[0]

        print(f"[att] [{ts}] Read Request: handle=0x{handle:04x}")
        attr = self.db.lookup(handle)
        if attr is None:
            print(f"[att] [{ts}] Read FAILED: handle=0x{handle:04x} -> ERR_INVALID_HANDLE")
            _proto_log("att_read_req", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                       error="INVALID_HANDLE")
            self._send_error(opcode, handle, ATT_ERR_INVALID_HANDLE)
            return
        if not self._readable(attr):
            print(f"[att] [{ts}] Read FAILED: handle=0x{handle:04x} -> ERR_READ_NOT_PERM")
            self._deny("read", handle)
            _proto_log("att_read_req", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                       error="READ_NOT_PERM")
            self._send_error(opcode, handle, ATT_ERR_READ_NOT_PERM)
            return
        value = self.db.read_attribute(handle)

        # Cap response to MTU - 1 per ATT spec
        capped = value[:self.mtu - 1] if len(value) > self.mtu - 1 else value
        print(f"[att] Read: handle=0x{handle:04x} len={len(value)} data={value.hex()}")
        resp = struct.pack('B', ATT_OP_READ_RSP) + capped
        self._send(resp)

        # Detect SC2 command echo in FR response
        _cmd_byte = None
        _cmd_name = None
        if len(capped) >= 1:
            _cmd_byte = capped[0]
            _cmd_name = _SC2_CMD_NAMES.get(_cmd_byte)

        _proto_log("att_read_req", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                   response=capped[:64].hex(), response_len=len(capped),
                   cmd=f"0x{_cmd_byte:02x}" if _cmd_byte is not None else None,
                   cmd_name=_cmd_name)

    def _handle_read_blob(self, data):
        """Handle Read Blob Request (0x0C) — for values > MTU."""
        opcode = data[0]
        if len(data) < 5:
            _proto_log("att_read_blob", opcode=f"0x{opcode:02x}", error="INVALID_PDU_LEN", len=len(data))
            self._send_error(opcode, 0x0000, ATT_ERR_INVALID_PDU)
            return
        handle = struct.unpack('<H', data[1:3])[0]
        offset = struct.unpack('<H', data[3:5])[0]

        attr = self.db.lookup(handle)
        if attr is None:
            _proto_log("att_read_blob", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                       offset=offset, error="INVALID_HANDLE")
            self._send_error(opcode, handle, ATT_ERR_INVALID_HANDLE)
            return
        if not self._readable(attr):
            self._deny("read_blob", handle)
            _proto_log("att_read_blob", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                       offset=offset, error="READ_NOT_PERM")
            self._send_error(opcode, handle, ATT_ERR_READ_NOT_PERM)
            return
        value = self.db.read_attribute(handle)

        if offset > len(value):
            _proto_log("att_read_blob", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                       offset=offset, value_len=len(value), error="INVALID_OFFSET")
            self._send_error(opcode, handle, ATT_ERR_INVALID_OFFSET)
            return
        # offset == len(value) is legal: return an empty (success) blob.
        # Cap to MTU - 1 per ATT spec
        chunk = value[offset:offset + self.mtu - 1]
        resp = struct.pack('B', ATT_OP_READ_BLOB_RSP) + chunk
        self._send(resp)

        _proto_log("att_read_blob", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                   offset=offset, response_len=len(chunk))

    def _find_cccd_value_handle(self, cccd_handle):
        """Find the value handle of the characteristic that owns this CCCD.

        Searches backwards from the CCCD handle to find the Characteristic
        Declaration (UUID 0x2803), then extracts the value handle from it.
        """
        for h in range(cccd_handle - 1, 0, -1):
            attr = self.db.lookup(h)
            if attr and attr.uuid == uuid16_to_bytes(GATT_CHARAC_UUID):
                if len(attr.value) >= 5:
                    return attr.value[1] | (attr.value[2] << 8)
        return None

    def _handle_write(self, data):
        """Handle Write Request (0x12)."""
        opcode = data[0]
        if len(data) < 3:
            _proto_log("att_write_req", opcode=f"0x{opcode:02x}", error="INVALID_PDU_LEN", len=len(data))
            self._send_error(opcode, 0x0000, ATT_ERR_INVALID_PDU)
            return
        handle = struct.unpack('<H', data[1:3])[0]
        value = data[3:]

        attr = self.db.lookup(handle)
        if attr is None:
            print(f"[att] ❌ Write Request FAILED: handle=0x{handle:04x} ERR_INVALID_HANDLE (attr not found)")
            _proto_log("att_write_req", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                       data=value.hex(), error="INVALID_HANDLE")
            self._send_error(opcode, handle, ATT_ERR_INVALID_HANDLE)
            return

        print(f"[att] ✅ Write Request: handle=0x{handle:04x} uuid={attr.uuid.hex()} len={len(value)} data={value.hex()}")

        if not self._writable(attr, is_command=False):
            print(f"[att] ❌ Write Request FAILED: handle=0x{handle:04x} ERR_WRITE_NOT_PERM")
            self._deny("write", handle)
            _proto_log("att_write_req", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                       data=value.hex(), error="WRITE_NOT_PERM")
            self._send_error(opcode, handle, ATT_ERR_WRITE_NOT_PERM)
            return

        # Record all writes for diagnostics
        ts = time.strftime('%H:%M:%S')
        self._diag_writes.append((ts, handle, attr.uuid.hex(), value.hex()))

        cccd_uuid = uuid16_to_bytes(0x2902)
        enable_handle = None
        if attr.uuid == cccd_uuid:
            if len(value) != 2:
                _proto_log("att_write_req", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                           data=value.hex(), error="INVALID_ATTR_LEN")
                self._send_error(opcode, handle, ATT_ERR_INVALID_ATTR_LEN)
                return
            ccc_value = struct.unpack('<H', value[:2])[0] if len(value) >= 2 else 0
            value_handle = self._find_cccd_value_handle(handle)
            if value_handle is not None:
                enabled = bool(ccc_value & 0x0001)
                if enabled:
                    self._notification_handles.add(value_handle)
                    print(f"[DIAG] ✅ CCCD ENABLED: cccd=0x{handle:04x} → value_handle=0x{value_handle:04x} (ccc=0x{ccc_value:04x})")
                    enable_handle = value_handle
                else:
                    self._notification_handles.discard(value_handle)
                    print(f"[DIAG] ❌ CCCD DISABLED: cccd=0x{handle:04x} → value_handle=0x{value_handle:04x} (ccc=0x{ccc_value:04x})")
                self._diag_cccd_events.append((ts, handle, value_handle, enabled))
                self._print_active_subscriptions()
            else:
                print(f"[att] Warning: could not find value handle for CCCD 0x{handle:04x}")
        else:
            # Non-CCCD write — log prominently for feature reports
            print(f"[DIAG] 📝 WRITE to handle=0x{handle:04x} uuid={attr.uuid.hex()} data={value.hex()}")

        # Detect SC2 command byte (value[1] when value[0] is Report ID)
        _cmd_byte = None
        _cmd_name = None
        if len(value) >= 2:
            _cmd_byte = value[1] if len(value) > 1 else value[0]
            _cmd_name = _SC2_CMD_NAMES.get(_cmd_byte)

        cb_invoked = False
        cb_result = None
        try:
            self.db.write_attribute(handle, value)
            cb_invoked = True
        except Exception as exc:
            cb_result = f"exception:{exc}"

        resp = struct.pack('B', ATT_OP_WRITE_RSP)
        self._send(resp)

        _proto_log("att_write_req", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                   data=value.hex(),
                   cmd=f"0x{_cmd_byte:02x}" if _cmd_byte is not None else None,
                   cmd_name=_cmd_name,
                   response="13", response_len=1,
                   cb_invoked=cb_invoked, cb_result=cb_result)

        if enable_handle is not None and self._on_cccd_enabled:
            self._on_cccd_enabled(enable_handle)

    def _handle_write_cmd(self, data):
        """Handle Write Command (0x52) — no response."""
        opcode = data[0]
        if len(data) < 3:
            _proto_log("att_write_cmd", opcode=f"0x{opcode:02x}", error="INVALID_PDU_LEN", len=len(data))
            return  # Write Command has no response
        handle = struct.unpack('<H', data[1:3])[0]
        value = data[3:]

        attr = self.db.lookup(handle)
        if attr:
            if not self._writable(attr, is_command=True):
                # Write Command has no response: silently drop + count it.
                self._deny("write_cmd", handle)
                print(f"[att] ❌ Write Command DROPPED: handle=0x{handle:04x} ERR_WRITE_NOT_PERM")
                _proto_log("att_write_cmd", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                           data=value.hex(), error="WRITE_NOT_PERM")
                return
            print(f"[att] ✅ Write Command: handle=0x{handle:04x} uuid={attr.uuid.hex()} len={len(value)} data={value.hex()}")

            # Detect SC2 command byte
            _cmd_byte = None
            _cmd_name = None
            if len(value) >= 2:
                _cmd_byte = value[1]
                _cmd_name = _SC2_CMD_NAMES.get(_cmd_byte)

            # CCCD handling for Write Command (0x52): update notification state
            # consistently with Write Request (0x12)
            cccd_uuid = uuid16_to_bytes(0x2902)
            enable_handle = None
            if attr.uuid == cccd_uuid:
                if len(value) != 2:
                    _proto_log("att_write_cmd", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                               data=value.hex(), error="INVALID_ATTR_LEN")
                    return
                ccc_value = struct.unpack('<H', value[:2])[0] if len(value) >= 2 else 0
                value_handle = self._find_cccd_value_handle(handle)
                if value_handle is not None:
                    enabled = bool(ccc_value & 0x0001)
                    if enabled:
                        self._notification_handles.add(value_handle)
                    else:
                        self._notification_handles.discard(value_handle)
                    enable_handle = value_handle

            cb_invoked = False
            cb_result = None
            try:
                self.db.write_attribute(handle, value)
                cb_invoked = True
            except Exception as exc:
                cb_result = f"exception:{exc}"

            _proto_log("att_write_cmd", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                       data=value.hex(),
                       cmd=f"0x{_cmd_byte:02x}" if _cmd_byte is not None else None,
                       cmd_name=_cmd_name,
                       cb_invoked=cb_invoked, cb_result=cb_result)

            if enable_handle is not None and self._on_cccd_enabled:
                self._on_cccd_enabled(enable_handle)
        else:
            print(f"[att] ❌ Write Command FAILED: handle=0x{handle:04x} ERR_INVALID_HANDLE (attr not found)")
            _proto_log("att_write_cmd", opcode=f"0x{opcode:02x}", handle=f"0x{handle:04x}",
                       data=value.hex(), error="INVALID_HANDLE")

    def _send_error(self, request_opcode, handle, error_code):
        """Send ATT Error Response."""
        resp = struct.pack('<BBHB', ATT_OP_ERROR, request_opcode, handle, error_code)
        self._send(resp)

    def _send(self, data):
        """Send raw bytes to the connected client."""
        if self.conn:
            try:
                sent = self.conn.send(data)
                return sent
            except Exception as e:
                print(f"[att] Send error: {e}")
                return 0
        return 0

    def send_notification(self, handle, value):
        """
        Send ATT Handle Value Notification.

        Args:
            handle: Attribute handle
            value: Notification value bytes
        """
        if handle not in self._notification_handles:
            self._diag_notif_dropped[handle] += 1
            # Log first drop and then every 200th drop per handle
            count = self._diag_notif_dropped[handle]
            if count == 1 or count % 200 == 0:
                print(f"[DIAG] 🚫 NOTIFICATION DROPPED (no CCCD): handle=0x{handle:04x} len={len(value)} (dropped {count}x total)")
                print(f"[DIAG]    Active subscriptions: {[f'0x{h:04x}' for h in sorted(self._notification_handles)]}")
            _proto_log("att_notif_dropped", handle=f"0x{handle:04x}", len=len(value))
            return

        # Cap notification to MTU - 3 per ATT spec (opcode + handle)
        capped = value[:self.mtu - 3] if len(value) > self.mtu - 3 else value
        pdu = struct.pack('<BH', ATT_OP_HANDLE_NFY, handle) + capped
        sent = self._send(pdu)
        self.notification_count += 1
        self._diag_notif_sent[handle] += 1
        
        # Throttle logging: gamepad (len > 8) logged every 100th notification to avoid
        # flooding at 1000Hz polling rate. Mouse/keyboard logged every time.
        # notification_count is GLOBAL (not per-handle), so throttle timing depends on
        # which handles are active.
        # Log mouse/keyboard (len <= 8) immediately, and gamepad (len > 8) throttled
        if len(value) <= 8 or (self.notification_count % 100 == 0):
            print(f"[att] Notification sent: handle=0x{handle:04x} len={len(value)} value={value.hex()}")

        _proto_log("att_notif", handle=f"0x{handle:04x}", len=len(value), sent=sent)

    def print_active_subscriptions(self):
        """Public method to print current CCCD subscription state."""
        self._print_active_subscriptions()

    def _print_active_subscriptions(self):
        """Print which handles currently have active CCCD subscriptions."""
        if self._notification_handles:
            labels = [f'0x{h:04x}({self._handle_label(h)})' for h in sorted(self._notification_handles)]
            print(f"[DIAG] 📋 Active CCCD subscriptions: {labels}")
        else:
            print(f"[DIAG] 📋 Active CCCD subscriptions: (none)")

    def _print_diag_summary(self):
        """Print diagnostic summary at disconnect."""
        print("\n" + "=" * 70)
        print("[DIAG] === DIAGNOSTIC SUMMARY (connection ended) ===")
        print("=" * 70)
        
        # CCCD events
        print(f"\n[DIAG] CCCD Events ({len(self._diag_cccd_events)} total):")
        for ts, cccd_h, val_h, enabled in self._diag_cccd_events:
            status = '✅ ENABLED' if enabled else '❌ DISABLED'
            name = self._handle_label(val_h)
            print(f"  {ts}  CCCD 0x{cccd_h:04x} → 0x{val_h:04x} ({name}) {status}")

        if self._diag_perm_denied:
            print(f"\n[DIAG] Permission Denials ({len(self._diag_perm_denied)} total):")
            for ts, op, h in self._diag_perm_denied:
                print(f"  {ts}  {op} handle=0x{h:04x} ({self._handle_label(h)})")
        
        # Non-CCCD writes (feature reports etc)
        non_cccd_writes = [(ts, h, u, v) for ts, h, u, v in self._diag_writes 
                           if h not in {e[1] for e in self._diag_cccd_events}]
        if non_cccd_writes:
            print(f"\n[DIAG] Non-CCCD Writes ({len(non_cccd_writes)} total):")
            for ts, h, uuid_hex, val_hex in non_cccd_writes:
                name = self._handle_label(h)
                print(f"  {ts}  handle=0x{h:04x} ({name}) data={val_hex}")
        
        # Notification stats
        print(f"\n[DIAG] Notifications Sent:")
        for h in sorted(self._diag_notif_sent.keys()):
            name = self._handle_label(h)
            print(f"  0x{h:04x} ({name}): {self._diag_notif_sent[h]}")
        if not self._diag_notif_sent:
            print("  (none)")
        
        print(f"\n[DIAG] Notifications DROPPED (no CCCD):")
        for h in sorted(self._diag_notif_dropped.keys()):
            name = self._handle_label(h)
            print(f"  0x{h:04x} ({name}): {self._diag_notif_dropped[h]}")
        if not self._diag_notif_dropped:
            print("  (none)")
        
        print("=" * 70 + "\n")

    @property
    def connected(self):
        # Technically racy with _pdu_loop cleanup (self.conn = None), but safe because
        # _send() catches socket exceptions. Callers should not assume connected == stable.
        return self.conn is not None
