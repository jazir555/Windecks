#!/usr/bin/env python3
"""Configure the Deck BT adapter via raw kernel MGMT socket (no btmgmt).

Replaces scripts/config_bt.py on systems where the btmgmt tool cannot
talk to the kernel (observed on SteamOS: every btmgmt command fails
with "Unable to send", even as root with bluetoothd stopped).

Speaks the BlueZ Management API (doc/mgmt-api.txt) directly over an
HCI_CHANNEL_CONTROL socket (needs root / CAP_NET_ADMIN):
    power off -> bredr off -> static-addr C2:12:34:56:78:9A -> power on

MGMT opcodes (lib/mgmt.h): SET_POWERED=0x0005, SET_BREDR=0x002A,
SET_STATIC_ADDRESS=0x002B. Events: CMD_COMPLETE=0x0001.
"""
import ctypes
import ctypes.util
import select
import socket
import struct
import sys

AF_BLUETOOTH = 31
BTPROTO_HCI = 1
HCI_CHANNEL_CONTROL = 3

MGMT_OP_SET_POWERED = 0x0005
MGMT_OP_SET_BREDR = 0x002A
MGMT_OP_SET_STATIC_ADDRESS = 0x002B

MGMT_EV_CMD_COMPLETE = 0x0001

STATIC_ADDR = "C2:12:34:56:78:9A"


def _parse_bdaddr(s):
    parts = s.split(":")
    if len(parts) != 6:
        raise ValueError(f"bad address {s!r}")
    return bytes(int(p, 16) for p in reversed(parts))  # little-endian


def _libc():
    name = ctypes.util.find_library("c")
    if not name:
        raise OSError("libc not found")
    return ctypes.CDLL(name, use_errno=True)


def _mgmt_socket():
    s = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
    # struct sockaddr_hci { sa_family_t hci_family; uint16_t hci_dev;
    #                       uint16_t hci_channel }: dev 0xFFFF = all.
    # NOTE: CPython rejects raw bytes for AF_BLUETOOTH bind, so call the
    # syscall directly (same approach as att_server.py's L2CAP bind).
    raw = struct.pack("HHH", AF_BLUETOOTH, 0xFFFF, HCI_CHANNEL_CONTROL)
    libc = _libc()
    r = libc.bind(s.fileno(), raw, len(raw))
    if r != 0:
        err = ctypes.get_errno()
        s.close()
        raise OSError(err, f"mgmt bind failed: {os_strerror(err)}")
    s.setblocking(False)
    return s


def os_strerror(err):
    try:
        import os
        return os.strerror(err)
    except Exception:
        return f"errno {err}"


def _mgmt_cmd(sock, opcode, index, params, timeout=5.0):
    req = struct.pack("<HHH", opcode, index, len(params)) + bytes(params)
    sock.send(req)
    deadline = timeout
    buf = b""
    while deadline > 0:
        r, _, _ = select.select([sock], [], [], min(deadline, 1.0))
        deadline -= 1.0
        if not r:
            continue
        try:
            buf += sock.recv(1024)
        except BlockingIOError:
            continue
        while len(buf) >= 6:
            ev, idx, elen = struct.unpack_from("<HHH", buf, 0)
            if len(buf) < 6 + elen:
                break
            payload = buf[6:6 + elen]
            buf = buf[6 + elen:]
            if ev == MGMT_EV_CMD_COMPLETE and len(payload) >= 3:
                op, status = struct.unpack_from("<HB", payload, 0)
                if op == opcode:
                    return status, bytes(payload[3:])
    raise TimeoutError(f"no complete event for opcode 0x{opcode:04x}")


def main():
    import argparse
    p = argparse.ArgumentParser(description="Deck BT setup via raw MGMT")
    p.add_argument("index", nargs="?", type=int, default=0)
    p.add_argument("addr", nargs="?", default=STATIC_ADDR,
                   help="static random address (00:00:00:00:00:00 clears)")
    p.add_argument("--bredr", choices=["on", "off"], default="off")
    args = p.parse_args()
    addr = _parse_bdaddr(args.addr)
    sock = _mgmt_socket()
    steps = [
        ("power off", MGMT_OP_SET_POWERED, b"\x00"),
        (f"bredr {args.bredr}", MGMT_OP_SET_BREDR,
         b"\x01" if args.bredr == "on" else b"\x00"),
        (f"static-addr {args.addr}", MGMT_OP_SET_STATIC_ADDRESS, addr),
        ("power on", MGMT_OP_SET_POWERED, b"\x01"),
    ]
    rc = 0
    for label, op, params in steps:
        try:
            status, _ = _mgmt_cmd(sock, op, args.index, params)
            print(f"  {label}: status=0x{status:02x} "
                  + ("OK" if status == 0 else "FAILED"))
            if status != 0:
                # static-addr on an already-set address can return "busy";
                # keep going so power is restored.
                rc = 1
        except Exception as e:
            print(f"  {label}: ERROR {type(e).__name__}: {e}")
            rc = 1
    sock.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
