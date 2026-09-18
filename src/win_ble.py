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
against the Windows HOGP host + Steam is untested. ViGEm (`--mode vigem`)
is the working path for playable input; this module is the true-SC2-spoof
path (VID 28DE / PID 1303 via PnP ID) to validate next.

All winrt imports are lazy so `--mode vigem` and the test suite work
without BLE hardware present. The server runs its own asyncio loop in a
background thread; update_reports() is thread-safe.
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


class WinBleServer:
    """WinRT GATT server advertising the SC2 database."""

    def __init__(self, device_name="Steam Controller 2026", on_feature_write=None):
        self.device_name = device_name
        self.on_feature_write = on_feature_write
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._start_error = None
        self._providers = []   # GattServiceProvider list
        self._notify_chars = {}  # key -> GattLocalCharacteristic
        self._static_values = {}  # key -> bytes (latest readable value)
        self._lock = threading.Lock()
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
        loop, self._loop = self._loop, None
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

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
        mapping = (
            ("gamepad_12b", reports.get("gamepad_12b")),
            ("gamepad_45b", reports.get("gamepad_45b")),
        )
        for key, data in mapping:
            if not data:
                continue
            char = self._notify_chars.get(key)
            if char is None:
                continue
            try:
                await char.notify_value_async(_to_buffer(data))
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
            CHR_REPORT, CHR_HID_INFO, CHR_REPORT_MAP, CHR_PNP_ID,
            SC2_HID_SERVICE_UUID, SC2_INPUT_CH1_UUID, SC2_INPUT_CH2_UUID,
            SC2_REPORT_CH_UUID,
        )

        db = gatt_db.build_sc2_database(self.device_name)

        # Index HID characteristics by (report-id, report-type) from the
        # Report Reference descriptors so notify keys are stable.
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

        async def _make_provider(svc_uuid_str):
            # WinRT create_async takes a Guid (python uuid.UUID), not str.
            result = await GattServiceProvider.create_async(
                _uuid.UUID(svc_uuid_str))
            if result.error != 0:
                raise RuntimeError(f"GattServiceProvider.create_async({svc_uuid_str}) error={result.error}")
            return result.service_provider

        async def _add_char(service, char_uuid_str, att_props, initial, key=None,
                            on_write=None):
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

            def _read_handler(sender, args, _val=bytes(initial)):
                loop = self._loop
                if loop is not None:
                    loop.call_soon_threadsafe(
                        lambda: asyncio.ensure_future(
                            self._handle_read(args, _val)))
            char.add_read_requested(_read_handler)
            return char

        # HID service (0x1812): info, protocol mode, report map, control
        # point, input notifies, feature channel. (WinRT auto-manages CCCDs
        # for NOTIFY characteristics; Report Reference descriptors have no
        # public WinRT API — hosts identify reports by characteristic order
        # and UUID, matching gatt_db layout.)
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
        await _add_char(hid.service, uuid16_str(CHR_REPORT), 0x02 | 0x10,
                        hid_report_handles.get((0x01, 0x01), b"\x00" * 12),
                        key="gamepad_12b")
        await _add_char(hid.service, uuid16_str(CHR_REPORT), 0x02 | 0x10,
                        hid_report_handles.get((0x45, 0x01), b"\x00" * 45),
                        key="gamepad_45b")
        await _add_char(hid.service, uuid16_str(CHR_REPORT),
                        0x02 | 0x08 | 0x04, b"\x00" * 64,
                        key="feature_02", on_write=self._on_feature)
        self._providers.append(hid)

        # Battery service.
        bat = await _make_provider(uuid16_str(SVC_BATTERY))
        await _add_char(bat.service, uuid16_str(CHR_BATTERY_LEVEL),
                        0x02 | 0x10, bytes([100]), key="battery")
        self._providers.append(bat)

        # Device Information: manufacturer / model / PnP (VID 28DE PID 1303).
        dis = await _make_provider(uuid16_str(SVC_DEVICE_INFO))
        pnp = bytes([0x02, 0xDE, 0x28, 0x03, 0x13, 0x00, 0x01])
        await _add_char(dis.service, uuid16_str(CHR_PNP_ID), 0x02, pnp)
        self._providers.append(dis)

        # Valve custom SC2 service (input mirrors + report channel).
        valve = await _make_provider(SC2_HID_SERVICE_UUID)
        await _add_char(valve.service, SC2_INPUT_CH1_UUID, 0x02 | 0x10,
                        b"\x00" * 45, key="valve_ch1")
        await _add_char(valve.service, SC2_INPUT_CH2_UUID, 0x02 | 0x10,
                        b"\x00" * 47, key="valve_ch2")
        await _add_char(valve.service, SC2_REPORT_CH_UUID,
                        0x02 | 0x08 | 0x04, b"\x00" * 64,
                        key="valve_report", on_write=self._on_feature)
        self._providers.append(valve)

        # GAP name/appearance are set from the advertisement + device name;
        # WinRT derives GAP from the radio, so only advertise HID here.
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattServiceProviderAdvertisingParameters,
        )
        params = GattServiceProviderAdvertisingParameters()
        params.is_connectable = True
        params.is_discoverable = True
        try:
            hid.start_advertising_with_parameters(params)
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
        print(f"[+] WinRT BLE advertising as '{self.device_name}' "
              f"({len(self._providers)} services)")

    async def _handle_read(self, args, static_value):
        try:
            req = await args.get_request_async()
            req.respond_with_value(_to_buffer(static_value))
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

    def _on_feature(self, data):
        """Route host feature writes through SC2CommandHandler (if present)."""
        if self._sc2 is not None and data:
            try:
                self._sc2.handle_set_report(3, data[0] if len(data) else 0, data)
            except Exception as e:
                print(f"[-] win_ble SC2 command error: {e}")
        if self.on_feature_write:
            try:
                self.on_feature_write(bytes(data))
            except Exception as e:
                print(f"[-] win_ble feature callback error: {e}")
