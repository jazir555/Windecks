#!/usr/bin/env python3
"""Windows BLE GATT server (replaces Linux att_server.py + adv.py/bluez.py).

Linux uses a raw L2CAP ATT server on CID 4 bound to a static random
address (BlueZ GATT is broken on SteamOS: its listener binds the public
address so connect_cb never fires). Windows has no L2CAP CID 4 / BlueZ,
so the path here is WinRT `GattServiceProvider` (via bleak's `winrt-*`
packages; the `winsdk` meta-package only ships pre-releases).

GATT content is sourced from gatt_db.build_sc2_database() (single source
of truth): 6 services (GAP, GATT, HID 0x1812, Battery, DIS, Valve custom
SC2), 16-bit UUIDs expanded to the Bluetooth base UUID.

Status: EXPERIMENTAL. HID-over-GATT (0x1812) as a WinRT-published service
against the Windows HOGP host + Steam is untested over the air.
ViGEm (`--mode vigem`) is the working path for playable input; this
module is the true-SC2-spoof path (VID 28DE / PID 1303 via PnP ID).

All winrt imports are lazy so `--mode vigem` and the test suite work
without BLE hardware present. The server runs its own asyncio loop in a
background thread; update_reports() is thread-safe.

WinRT limitations vs the Linux ATT server (documented, not fixable from
here): Report Reference descriptors (0x2908) have no public WinRT API,
so hosts identify HID reports by characteristic order + UUID (which
matches the gatt_db layout); CCCDs are managed automatically by WinRT
for NOTIFY characteristics.
"""

import asyncio
import struct
import threading
import uuid as _uuid

BT_BASE = "0000{:04X}-0000-1000-8000-00805F9A34FB"


def uuid16_str(u16):
    return BT_BASE.format(u16 & 0xFFFF)


def _to_buffer(data):
    from winrt.windows.storage.streams import DataWriter
    w = DataWriter()
    w.write_bytes(bytes(data or b""))
    return w.detach_buffer()


def _buffer_to_bytes(buf):
    try:
        import ctypes
        length = buf.length
        out = (ctypes.c_ubyte * length)()
        from winrt.windows.storage.streams import DataReader
        r = DataReader.from_buffer(buf)
        r.read_bytes(out)
        return bytes(out)
    except Exception:
        return b""


# (gatt_db 16-bit char UUID, ATT prop mask) -> WinRT prop names.
# ATT props: READ=0x02 WNR=0x04 WRITE=0x08 NOTIFY=0x10 INDICATE=0x20
def _winrt_props(att_props):
    names = []
    if att_props & 0x02:
        names.append("READ")
    if att_props & 0x04:
        names.append("WRITE_WITHOUT_RESPONSE")
    if att_props & 0x08:
        names.append("WRITE")
    if att_props & 0x10:
        names.append("NOTIFY")
    if att_props & 0x20:
        names.append("INDICATE")
    return names


def _parse_haptic_80(data):
    """Parse an SC2 0x80 rumble payload -> (left, right) or None.

    Mirrors main_l2cap._on_haptic_write: hog-ll strips the Report ID, so
    the payload arrives without the 0x80 prefix (type at [0], left at
    [3], right at [6]).
    """
    if not data or len(data) < 9:
        return None
    if data[0] == 0x80 and len(data) >= 10:
        # Full form with Report ID prefix (WinRT write path).
        try:
            left = struct.unpack_from("<H", data, 4)[0]
            right = struct.unpack_from("<H", data, 7)[0]
            return (left, right)
        except struct.error:
            return None
    try:
        left = struct.unpack_from("<H", data, 3)[0]
        right = struct.unpack_from("<H", data, 6)[0]
        return (left, right)
    except struct.error:
        return None


class WinBleServer:
    """WinRT GATT server advertising the SC2 database."""

    def __init__(self, device_name="Steam Controller 2026",
                 on_feature_write=None, on_haptic=None):
        self.device_name = device_name
        self.on_feature_write = on_feature_write
        self.on_haptic = on_haptic
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._start_error = None
        self._providers = []   # GattServiceProvider list
        self._notify_chars = {}  # key -> GattLocalCharacteristic
        self._static_values = {}  # key -> bytes (latest readable value)
        self._lock = threading.Lock()
        self.advertising = False
        try:
            from sc2_commands import SC2CommandHandler
            self._sc2 = SC2CommandHandler()
        except Exception:
            self._sc2 = None

    # -- lifecycle ----------------------------------------------------
    def start(self, timeout=20):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise RuntimeError("WinRT GATT server did not start in time")
        if self._start_error:
            raise self._start_error

    def stop(self):
        for prov in self._providers:
            try:
                prov.stop_advertising()
            except Exception:
                pass
        self.advertising = False
        loop, self._loop = self._loop, None
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def service_count(self):
        return len(self._providers)

    # -- input path ---------------------------------------------------
    def update_reports(self, reports):
        """Push latest input reports to notify characteristics (thread-safe)."""
        if self._loop is None:
            return
        rep = dict(reports or {})
        try:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._notify_all(rep)))
        except Exception as e:
            print(f"[-] win_ble update error: {e}")

    async def _notify_all(self, reports):
        # Linux (main_l2cap forward_report) sends the 45-byte SC2 report on
        # BOTH the HID 0x45 handle and the Valve ch1 handle; 12-byte goes
        # on the HID 0x01 handle. Mirror that here. 47-byte / mouse /
        # keyboard / battery are forwarded when present.
        p45 = reports.get("gamepad_45b")
        p47 = reports.get("gamepad_47b")
        battery = reports.get("battery")
        if isinstance(battery, int):
            battery = bytes([max(0, min(100, battery))])
        mapping = (
            ("gamepad_12b", reports.get("gamepad_12b")),
            ("gamepad_45b", p45),
            ("valve_ch1", p45),
            ("gamepad_47b", p47),
            ("valve_ch2", p47),
            ("mouse_4b", reports.get("mouse_4b")),
            ("kbd_8b", reports.get("kbd_8b")),
            ("battery", battery),
        )
        for key, data in mapping:
            if not data:
                continue
            char = self._notify_chars.get(key)
            if char is None:
                continue
            try:
                await char.notify_value_async(_to_buffer(data))
                with self._lock:
                    self._static_values[key] = bytes(data)
            except Exception:
                pass

    # -- server internals ---------------------------------------------
    def _run(self):
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._build_and_advertise())
        except Exception as e:
            self._start_error = e
        finally:
            self._ready.set()
            if self._loop is not None:
                try:
                    self._loop.run_forever()
                except Exception:
                    pass

    async def _build_and_advertise(self):
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattServiceProvider,
            GattLocalCharacteristicParameters,
            GattCharacteristicProperties,
            GattProtectionLevel,
        )
        import gatt_db
        from gatt_db import (
            SVC_HID, SVC_BATTERY, SVC_DEVICE_INFO,
            CHR_BATTERY_LEVEL, CHR_DEVICE_NAME, CHR_APPEARANCE,
            CHR_MANUFACTURER_NAME, CHR_MODEL_NUMBER, CHR_SERIAL_NUMBER,
            CHR_FIRMWARE_REVISION, CHR_HARDWARE_REVISION,
            CHR_SOFTWARE_REVISION,
            CHR_REPORT, CHR_HID_INFO, CHR_REPORT_MAP, CHR_PNP_ID,
            SC2_HID_SERVICE_UUID, SC2_INPUT_CH1_UUID, SC2_INPUT_CH2_UUID,
            SC2_REPORT_CH_UUID,
        )

        db = gatt_db.build_sc2_database(self.device_name)

        # Index HID characteristic initial values by (report-id, report-type)
        # from the Report Reference descriptors so notify keys are stable.
        hid_report_handles = {}  # (report_id, report_type) -> value bytes
        for handle in sorted(db.attributes.keys()):
            attr = db.attributes[handle]
            if attr.uuid == gatt_db.uuid16_to_bytes(CHR_REPORT):
                for dh in sorted(db.attributes.keys()):
                    d = db.attributes[dh]
                    if (d.uuid == gatt_db.uuid16_to_bytes(gatt_db.DESC_REPORT_REF)
                            and dh > handle and dh < handle + 4
                            and len(d.value) == 2):
                        hid_report_handles[(d.value[0], d.value[1])] = bytes(attr.value)

        def _hid_init(report_id, report_type, size):
            return hid_report_handles.get((report_id, report_type),
                                          b"\x00" * size)

        async def _make_provider(svc_uuid_str):
            # WinRT create_async takes a Guid (python uuid.UUID), not str.
            result = await GattServiceProvider.create_async(
                _uuid.UUID(svc_uuid_str))
            if result.error != 0:
                raise RuntimeError(f"GattServiceProvider.create_async({svc_uuid_str}) error={result.error}")
            return result.service_provider

        async def _add_char(service, char_uuid_str, att_props, initial, key=None,
                            on_write=None, on_read=None):
            params = GattLocalCharacteristicParameters()
            props = GattCharacteristicProperties(0)
            for name in _winrt_props(att_props):
                props |= getattr(GattCharacteristicProperties, name)
            params.characteristic_properties = props
            params.static_value = _to_buffer(initial)
            params.read_protection_level = GattProtectionLevel.PLAIN
            params.write_protection_level = GattProtectionLevel.PLAIN
            result = await service.create_characteristic_async(
                _uuid.UUID(char_uuid_str), params)
            if result.error != 0:
                raise RuntimeError(f"create_characteristic({char_uuid_str}) error={result.error}")
            char = result.characteristic
            if key:
                self._notify_chars[key] = char
                with self._lock:
                    self._static_values[key] = bytes(initial)
            if on_write is not None:
                def _handler(sender, args, _cb=on_write):
                    # WinRT fires characteristic events on arbitrary threads;
                    # marshal back onto the server loop thread.
                    loop = self._loop
                    if loop is not None:
                        loop.call_soon_threadsafe(
                            lambda: asyncio.ensure_future(
                                self._handle_write(args, _cb)))
                char.add_write_requested(_handler)

            def _read_handler(sender, args, _key=key, _val=bytes(initial),
                              _cb=on_read):
                loop = self._loop
                if loop is not None:
                    loop.call_soon_threadsafe(
                        lambda: asyncio.ensure_future(
                            self._handle_read(args, _key, _val, _cb)))
            char.add_read_requested(_read_handler)
            return char

        def _feature_read(report_id):
            def _cb():
                if self._sc2 is not None:
                    try:
                        return bytes(self._sc2.handle_get_report(3, report_id))
                    except Exception:
                        pass
                with self._lock:
                    return bytes(self._static_values.get(
                        f"feature_{report_id:02X}", b"\x00" * 64))
            return _cb

        # HID service (0x1812): full gatt_db layout in WinRT publication
        # order (info, protocol mode, report map, control point, then
        # reports). WinRT auto-manages CCCDs for NOTIFY characteristics;
        # Report Reference descriptors have no public WinRT API — hosts
        # identify reports by characteristic order and UUID, matching the
        # gatt_db layout.
        hid = await _make_provider(uuid16_str(SVC_HID))
        from gatt_db import CHR_PROTOCOL_MODE, CHR_HID_CONTROL_POINT
        await _add_char(hid.service, uuid16_str(CHR_HID_INFO), 0x02,
                        b"\x11\x01\x00\x02")  # bcdHID 1.11, flags 0x02
        await _add_char(hid.service, uuid16_str(CHR_PROTOCOL_MODE),
                        0x02 | 0x04, bytes([0x01]))  # Report Protocol
        await _add_char(hid.service, uuid16_str(CHR_REPORT_MAP), 0x02,
                        db.build_report_map())
        await _add_char(hid.service, uuid16_str(CHR_HID_CONTROL_POINT),
                        0x04, b"\x00")
        # Input reports.
        await _add_char(hid.service, uuid16_str(CHR_REPORT), 0x02 | 0x10,
                        _hid_init(0x01, 0x01, 12), key="gamepad_12b")
        await _add_char(hid.service, uuid16_str(CHR_REPORT), 0x02 | 0x10,
                        _hid_init(0x45, 0x01, 45), key="gamepad_45b")
        await _add_char(hid.service, uuid16_str(CHR_REPORT), 0x02 | 0x10,
                        _hid_init(0x47, 0x01, 47), key="gamepad_47b")
        await _add_char(hid.service, uuid16_str(CHR_REPORT), 0x02 | 0x10,
                        _hid_init(0x03, 0x01, 4), key="mouse_4b")
        await _add_char(hid.service, uuid16_str(CHR_REPORT), 0x02 | 0x10,
                        _hid_init(0x04, 0x01, 8), key="kbd_8b")
        # Output reports: gamepad output (0x02) + haptic rumble (0x80).
        await _add_char(hid.service, uuid16_str(CHR_REPORT),
                        0x02 | 0x08 | 0x04, _hid_init(0x02, 0x02, 1),
                        key="output_02", on_write=self._on_feature)
        await _add_char(hid.service, uuid16_str(CHR_REPORT),
                        0x02 | 0x08 | 0x04, _hid_init(0x80, 0x02, 10),
                        key="haptic_80", on_write=self._on_haptic_write)
        # Feature reports (SC2 command channel).
        for rid in (0x02, 0x01, 0x85, 0x86, 0x87, 0x8F):
            await _add_char(hid.service, uuid16_str(CHR_REPORT),
                            0x02 | 0x08 | 0x04, b"\x00" * 64,
                            key=f"feature_{rid:02X}",
                            on_write=self._on_feature,
                            on_read=_feature_read(rid))
        self._providers.append(hid)

        # Battery service.
        bat = await _make_provider(uuid16_str(SVC_BATTERY))
        await _add_char(bat.service, uuid16_str(CHR_BATTERY_LEVEL),
                        0x02 | 0x10, bytes([100]), key="battery")
        self._providers.append(bat)

        # Device Information: full strings + PnP (VID 28DE PID 1303).
        dis = await _make_provider(uuid16_str(SVC_DEVICE_INFO))
        pnp = bytes([0x02, 0xDE, 0x28, 0x03, 0x13, 0x00, 0x01])
        await _add_char(dis.service, uuid16_str(CHR_MANUFACTURER_NAME),
                        0x02, b"Valve Software")
        await _add_char(dis.service, uuid16_str(CHR_MODEL_NUMBER),
                        0x02, b"Steam Controller 2026")
        await _add_char(dis.service, uuid16_str(CHR_SERIAL_NUMBER),
                        0x02, b"F0000-0000-00000000")
        await _add_char(dis.service, uuid16_str(CHR_FIRMWARE_REVISION),
                        0x02, b"1.0.0")
        await _add_char(dis.service, uuid16_str(CHR_HARDWARE_REVISION),
                        0x02, b"1.0.0")
        await _add_char(dis.service, uuid16_str(CHR_SOFTWARE_REVISION),
                        0x02, b"1.0.0")
        await _add_char(dis.service, uuid16_str(CHR_PNP_ID), 0x02, pnp)
        self._providers.append(dis)

        # Valve custom SC2 service (input mirrors + report channel).
        # Linux sends 45-byte reports on both the HID 0x45 handle and
        # Valve ch1; mirror that via update_reports().
        valve = await _make_provider(SC2_HID_SERVICE_UUID)
        await _add_char(valve.service, SC2_INPUT_CH1_UUID, 0x02 | 0x10,
                        b"\x00" * 45, key="valve_ch1")
        await _add_char(valve.service, SC2_INPUT_CH2_UUID, 0x02 | 0x10,
                        b"\x00" * 47, key="valve_ch2")
        await _add_char(valve.service, SC2_REPORT_CH_UUID,
                        0x02 | 0x08 | 0x04, b"\x00" * 64,
                        key="valve_report", on_write=self._on_feature,
                        on_read=_feature_read(0x01))
        self._providers.append(valve)

        # GAP name/appearance are set from the advertisement + device name;
        # WinRT derives GAP from the radio, so only advertise HID here.
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattServiceProviderAdvertisingParameters,
        )
        params = GattServiceProviderAdvertisingParameters()
        params.is_connectable = True
        params.is_discoverable = True
        # NOTE: the status briefly reports ABORTED(3) right after start on
        # some radios before settling to STARTED(2) — transient, not fatal.
        def _on_adv_status(sender, _args):
            try:
                print(f"[ble] advertisement status: {sender.advertisement_status}")
            except Exception:
                pass
        started = 0
        try:
            for prov in self._providers:
                try:
                    prov.add_advertisement_status_changed(_on_adv_status)
                except Exception:
                    pass
                prov.start_advertising_with_parameters(params)
                started += 1
        except OSError as e:
            # Seen on radios without LE peripheral-role support, e.g.
            # WinError -2147024580 "does not support the command feature".
            # GATT providers above were created fine — only advertising
            # (the peripheral role) is rejected by this radio/driver.
            for prov in self._providers:
                try:
                    prov.stop_advertising()
                except Exception:
                    pass
            raise RuntimeError(
                "BLE advertising not supported by this radio/driver "
                f"({e}). The GATT database built OK; try a radio with LE "
                "peripheral-role support (or fix the Intel BT driver if "
                "present). --mode vigem remains fully usable."
            ) from e
        self.advertising = started > 0
        print(f"[+] WinRT BLE advertising as '{self.device_name}' "
              f"({len(self._providers)} services)")

    async def _handle_read(self, args, key, static_value, on_read=None):
        try:
            req = await args.get_request_async()
            value = None
            if on_read is not None:
                try:
                    value = on_read()
                except Exception as e:
                    print(f"[-] win_ble read callback error: {e}")
            if value is None:
                with self._lock:
                    value = bytes(self._static_values.get(key, static_value))
            req.respond_with_value(_to_buffer(value))
        except Exception:
            pass

    async def _handle_write(self, args, callback):
        try:
            req = await args.get_request_async()
            data = _buffer_to_bytes(req.value)
            try:
                callback(data)
            except Exception as e:
                print(f"[-] win_ble write callback error: {e}")
            req.respond()
        except Exception:
            pass

    def _on_haptic_write(self, data):
        """Route host 0x80 rumble writes to the haptic callback + SC2 handler."""
        data = bytes(data or b"")
        parsed = _parse_haptic_80(data)
        if parsed is not None and self.on_haptic is not None:
            try:
                self.on_haptic(*parsed)
            except Exception as e:
                print(f"[-] win_ble haptic callback error: {e}")
        # Also feed through the generic feature path for logging.
        self._on_feature(data)

    def _on_feature(self, data):
        """Route host feature writes through SC2CommandHandler (if present)."""
        data = bytes(data or b"")
        if self._sc2 is not None and data:
            try:
                report_id = data[0] if len(data) else 0
                self._sc2.handle_set_report(3, report_id, data)
                with self._lock:
                    key = f"feature_{report_id:02X}"
                    if key in self._static_values:
                        try:
                            self._static_values[key] = bytes(
                                self._sc2.handle_get_report(3, report_id))
                        except Exception:
                            pass
            except Exception as e:
                print(f"[-] win_ble SC2 command error: {e}")
        if self.on_feature_write:
            try:
                self.on_feature_write(bytes(data))
            except Exception as e:
                print(f"[-] win_ble feature callback error: {e}")
