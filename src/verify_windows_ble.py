#!/usr/bin/env python3
"""OTA verifier for the Windecks WinRT BLE GATT server (bleak central).

Run on a SECOND device (phone with nRF Connect, or another PC / the Deck
with bluetoothctl) while `--mode ble` is advertising on the Windecks host.
Validating from the same Windows host that is advertising is NOT expected
to work reliably (the radio is in peripheral role).

Usage (second machine, or this machine after stopping the advertiser):
    python src/verify_windows_ble.py [--name "Steam Controller 2026"]
                                     [--timeout 10] [--notify-secs 5]

Checks:
    1. Advertisement found (name match).
    2. Services present: HID 0x1812, Battery 0x180F, DIS 0x180A,
       Valve custom 100f6c32-....
    3. PnP ID reads as VID 28DE / PID 1303.
    4. Battery level 0..100, HID info bcdHID, report map non-empty.
    5. NOTIFY subscribe on a HID input characteristic receives data
       (requires --mode ble input flowing, e.g. synthetic).

Requires: pip install bleak
"""

import argparse
import asyncio
import struct
import sys

HID_SVC = "00001812-0000-1000-8000-00805f9a34fb"
BAT_SVC = "0000180f-0000-1000-8000-00805f9a34fb"
DIS_SVC = "0000180a-0000-1000-8000-00805f9a34fb"
VALVE_SVC = "100f6c32-1735-4313-b402-38567131e5f3"
CHR_PNP = "00002a50-0000-1000-8000-00805f9a34fb"
CHR_BATTERY = "00002a19-0000-1000-8000-00805f9a34fb"
CHR_HID_INFO = "00002a4a-0000-1000-8000-00805f9a34fb"
CHR_REPORT_MAP = "00002a4b-0000-1000-8000-00805f9a34fb"
CHR_REPORT = "00002a4d-0000-1000-8000-00805f9a34fb"


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Verify Windecks BLE SC2 advertisement")
    p.add_argument("--name", default="Steam Controller 2026")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--notify-secs", type=float, default=5.0)
    p.add_argument("--address", default=None,
                   help="Skip scan, connect directly to this BLE address")
    return p.parse_args(argv)


async def _run(args):
    from bleak import BleakScanner, BleakClient

    results = {"pass": [], "fail": []}

    def ok(msg):
        results["pass"].append(msg)
        print(f"[PASS] {msg}")

    def fail(msg):
        results["fail"].append(msg)
        print(f"[FAIL] {msg}")

    address = args.address
    if not address:
        print(f"[*] Scanning {args.timeout:.0f}s for '{args.name}' ...")
        devices = await BleakScanner.discover(timeout=args.timeout)
        match = None
        for d in devices:
            if (d.name or "") == args.name:
                match = d
                break
        if match is None:
            fail(f"advertisement for '{args.name}' not found "
                 f"({len(devices)} devices seen)")
            for d in devices[:10]:
                print(f"      seen: {d.address} name={d.name!r}")
            return 1
        address = match.address
        ok(f"advertisement found: {match.address} name={match.name!r}")
    else:
        print(f"[*] Connecting directly to {address} ...")

    async with BleakClient(address) as client:
        print("[*] Connected, discovering services ...")
        svcs = {s.uuid.lower(): s for s in client.services}
        for uuid, label in ((HID_SVC, "HID 0x1812"), (BAT_SVC, "Battery 0x180F"),
                            (DIS_SVC, "DIS 0x180A"), (VALVE_SVC, "Valve custom")):
            if uuid in svcs:
                ok(f"service present: {label}")
            else:
                fail(f"service missing: {label}")

        async def _read(uuid, label):
            try:
                data = bytes(await client.read_gatt_char(uuid))
                ok(f"read {label}: {len(data)} B ({data[:16].hex()})")
                return data
            except Exception as e:
                fail(f"read {label}: {type(e).__name__}: {e}")
                return None

        pnp = await _read(CHR_PNP, "PnP ID")
        if pnp is not None:
            if len(pnp) >= 5 and pnp[1:3] == bytes([0xDE, 0x28]) \
                    and pnp[3:5] == bytes([0x03, 0x13]):
                ok("PnP ID VID=28DE PID=1303")
            else:
                fail(f"PnP ID mismatch: {pnp.hex()}")
        bat = await _read(CHR_BATTERY, "Battery")
        if bat is not None and len(bat) >= 1 and 0 <= bat[0] <= 100:
            ok(f"battery level sane: {bat[0]}%")
        elif bat is not None:
            fail(f"battery level out of range: {bat.hex()}")
        hid_info = await _read(CHR_HID_INFO, "HID info")
        if hid_info is not None and hid_info[:2] == b"\x11\x01":
            ok("HID info bcdHID 1.11")
        rmap = await _read(CHR_REPORT_MAP, "report map")
        if rmap is not None and len(rmap) > 100:
            ok(f"report map length {len(rmap)}")
        elif rmap is not None:
            fail(f"report map too short: {len(rmap)}")

        # Subscribe to the first NOTIFY-capable HID report characteristic.
        target = None
        for s in client.services:
            if s.uuid.lower() not in (HID_SVC, VALVE_SVC):
                continue
            for c in s.characteristics:
                if c.uuid.lower() == CHR_REPORT and "notify" in c.properties:
                    target = c
                    break
            if target:
                break
        if target is None:
            fail("no NOTIFY report characteristic found")
        else:
            got = asyncio.Event()

            def _cb(_sender, data):
                print(f"[+] notify {len(data)} B: {bytes(data)[:16].hex()}")
                got.set()

            try:
                await client.start_notify(target, _cb)
                ok(f"subscribed NOTIFY on {target.uuid}")
                try:
                    await asyncio.wait_for(got.wait(), timeout=args.notify_secs)
                    ok("notification received")
                except asyncio.TimeoutError:
                    fail(f"no notification in {args.notify_secs:.0f}s "
                         "(is input flowing? try --input synthetic)")
                await client.stop_notify(target)
            except Exception as e:
                fail(f"NOTIFY subscribe: {type(e).__name__}: {e}")

    print(f"\n==== {len(results['pass'])} passed, {len(results['fail'])} failed ====")
    return 0 if not results["fail"] else 2


def main(argv=None):
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except ImportError:
        print("[-] bleak not installed (pip install bleak)", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[-] verify failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
