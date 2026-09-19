#!/usr/bin/env python3
"""Directed BLE GATT check without prior discovery (Windows/WinRT).

Bleak requires a device to be discovered/cached before connecting, which
fails when the observer's scanner sees nothing. This tool uses
BluetoothLEDevice.FromBluetoothAddressAsync directly for a directed
connection to a known static address (e.g. the Deck's C2:12:34:56:78:9A),
then enumerates services and reads the PnP ID + device name.

Usage:
    python src/direct_connect_ble.py [ADDRESS] [--timeout 15]
"""
import argparse
import asyncio
import sys


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Directed BLE GATT check")
    p.add_argument("address", nargs="?", default="C2:12:34:56:78:9A")
    p.add_argument("--timeout", type=float, default=15.0)
    return p.parse_args(argv)


async def _run(address, timeout):
    from winrt.windows.devices.bluetooth import BluetoothLEDevice
    from winrt.windows.devices.bluetooth.genericattributeprofile import GattCommunicationStatus

    addr_int = int(address.replace(":", ""), 16)
    print(f"[*] Directed connect to {address} (0x{addr_int:012X}) ...")
    device = await asyncio.wait_for(
        BluetoothLEDevice.from_bluetooth_address_async(addr_int),
        timeout=timeout)
    if device is None:
        print("[FAIL] FromBluetoothAddressAsync returned None "
              "(no LL connection)")
        return 1
    print(f"[PASS] connected: name={device.name!r} "
          f"status={device.connection_status}")
    svcs = await asyncio.wait_for(device.get_gatt_services_async(), timeout=timeout)
    if svcs.status != GattCommunicationStatus.SUCCESS:
        print(f"[FAIL] service discovery: {svcs.status}")
        return 1
    print(f"[PASS] {len(svcs.services)} services:")
    for s in svcs.services:
        print(f"  {s.uuid}")
    want = {
        "00001812-0000-1000-8000-00805f9a34fb": None,  # HID
        "0000180f-0000-1000-8000-00805f9a34fb": None,  # Battery
        "0000180a-0000-1000-8000-00805f9a34fb": None,  # DIS
    }
    found = {str(s.uuid).lower() for s in svcs.services}
    for uuid in want:
        print(f"  [{'PASS' if uuid in found else 'FAIL'}] {uuid}")
    # Read PnP ID from DIS.
    for s in svcs.services:
        if str(s.uuid).lower() == "0000180a-0000-1000-8000-00805f9a34fb":
            chars = await asyncio.wait_for(s.get_characteristics_async(), timeout=timeout)
            for c in chars.characteristics:
                if str(c.uuid).lower() == "00002a50-0000-1000-8000-00805f9a34fb":
                    val = await asyncio.wait_for(c.read_value_async(), timeout=timeout)
                    if val.status == GattCommunicationStatus.SUCCESS:
                        import ctypes
                        buf = val.value
                        raw = bytes((ctypes.c_ubyte * buf.length)())
                        from winrt.windows.storage.streams import DataReader
                        DataReader.from_buffer(buf).read_bytes(raw)
                        print(f"[PASS] PnP ID: {bytes(raw).hex()}")
                        vid = bytes(raw)[1:3].hex()
                        pid = bytes(raw)[3:5].hex()
                        print(f"       VID={vid} PID={pid}")
                        if bytes(raw)[1:3] == bytes([0xDE, 0x28]) and \
                                bytes(raw)[3:5] == bytes([0x03, 0x13]):
                            print("[PASS] VID=28DE PID=1303 (SC2 BLE)")
                            return 0
                        print("[FAIL] PnP mismatch")
                        return 2
    print("[FAIL] PnP characteristic not found")
    return 2


def main(argv=None):
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args.address, args.timeout))
    except asyncio.TimeoutError:
        print("[-] timed out (no connection / no response)")
        return 2
    except Exception as e:
        print(f"[-] {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
