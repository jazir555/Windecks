# Decktroller: Steam Deck → Steam Controller 2026 (BLE + Virtual USB)

Make a Steam Deck present itself as a Steam Controller 2026 (SC2) over Bluetooth Low Energy or as a virtual USB device, enabling Steam Input support (trackpads, gyro, back buttons) without physical USB wired mode.

[MIT License](LICENSE)

## Current Status

**BLE mode working**: Gamepad, trackpads, gyro, back buttons, standard HID input, in-game rumble via `SDL_RumbleJoystick()`. Steam Client recognizes the Deck as an SC2 controller with full Steam Input features.

**Virtual USB mode working**: `main_virtual_usb.py` creates a full virtual USB composite device (mouse, keyboard, controller) via `vhci_hcd` + USB/IP protocol on any Linux host. Steam Client sees it as a real USB Steam Controller (VID 0x28DE, PID 0x1205) with correct `bInterfaceNumber`. Handles SC2 command protocol (GET_ATTRIBUTES, GET_SERIAL, etc.). Run with `sudo python3 src/main_virtual_usb.py`.

**Not working**: Steam-generated haptics (trackpad clicks, UI feedback via 0x8F commands) — architecturally blocked. The haptic scheduler at `0x123e5d0` is never called for BLE controllers (GDB confirmed). `CPulseHapticWorkItem` fires with 0.0ms runtime because the work item short-circuits before entering the scheduler. The block happens upstream in controller setup/dispatch. A real SC2 also doesn't get these haptics over BLE.

**Stable**: Registration completes reliably. No zombie disconnects after clearing stale BlueZ state.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Steam Deck (Peripheral)                     │
│                                                              │
│  main_l2cap.py                                               │
│  ├─ GLib main loop (BlueZ D-Bus advertising)                 │
│  ├─ Agent1 (auto-confirm pairing via dbus-python)            │
│  └─ Raw L2CAP ATT server thread                              │
│     └─ Binds to C2:12:34:56:78:9A CID 4                     │
│     └─ Handles all ATT PDU exchange                           │
│     └─ Serves GATT database (87 attributes, 6 services)      │
│                                                              │
│  ┌─────────────────────┐  ┌──────────────────────────────┐  │
│  │  gatt_db.py         │  │  att_server.py               │  │
│  │  (87 attributes,    │  │  (Raw L2CAP socket           │  │
│  │   6 services)       │  │   on CID 4)                  │  │
│  └─────────────────────┘  └──────────────────────────────┘  │
│                                                              │
│  BlueZ handles:                                              │
│  ├─ SMP pairing (kernel, CID 6)                              │
│  ├─ LE advertising (LEAdvertisingManager1)                   │
│  └─ Agent registration (AgentManager1)                       │
│                                                              │
│  Input: /dev/hidraw3 (Neptune controller, USB iface 2)       │
│  ├─ input_handler.py reads 64-byte HID reports               │
│  ├─ Maps Neptune buttons → SC2 12-byte report                │
│  └─ Sends as ATT notifications (no Report ID prefix)         │
│                                                              │
│  Lizard mode: periodically re-sends 0x81 cmd to disable      │
└──────────────────────────────────────────────────────────────┘
              │
              │ BLE (static random addr C2:12:34:56:78:9A)
              ▼
┌──────────────────────────────────────────────────────────────┐
│                    Host PC (Central)                          │
│                                                              │
│  BlueZ hog-ll driver → /dev/hidrawN → Steam Client           │
└──────────────────────────────────────────────────────────────┘
```

### Input Path (NOT gated — works immediately)

```
BLE ATT Notification → hog-ll → UHID → /dev/hidrawN
  → PollControllers (0x011e0930)
    → vtable[0x10]: non-blocking HID read
    → vtable[0x30]: PARSE report
    → vtable[0x34]: normalize sticks, apply deadzone
    → vtable[0x38]: apply calibration from VDF config
    → vtable[0x3c]: detect changes
  → Master Controller Processing (FUN_0126b130)
  → Per-Controller Processing (FUN_01285c30)
    → FUN_012857d0: generate Steam Input data
    → Create work item for Steam Input system
```

### Command Path (GATED — blocked upstream for BLE)

```
CPulseHapticWorkItem fires → 0.0ms runtime → never enters scheduler
  Block happens BEFORE 0x123e5d0 — upstream in controller setup/dispatch
  Gate check at 0x123e5fb: cmp byte [esi+0x17c], 0 is UNREACHABLE for BLE
  Real SC2 also doesn't get 0x8F over BLE — only via USB/Puck dongle

Commands that DO work (via ATT feature reports, not scheduler):
  0x80 (Rumble), 0x81 (Clear Mappings), 0x83 (Get Attributes)
  0x85 (Set Mode), 0x87 (Set Settings), 0xae (Get Serial)
```

### Haptic Pipeline (game rumble — working)

```
Game → SDL_RumbleJoystick(low_freq, high_freq)
  → HIDAPI_DriverSteamTriton_RumbleJoystick() [every 6ms, 40ms throttle]
  → SDL_hid_write(device, buffer, 10) [Report ID 0x80]
  → write("/dev/hidrawN") → kernel hidraw
  → uhid_hid_output_raw() → UHID_OUTPUT → BlueZ hog-ll
  → forward_report() → gatt_write_char() [ATT 0x12, handle 0x0019]
  → _on_haptic_write() → _forward_haptic_to_neptune()
  → PackedRumbleReport to /dev/hidraw3 → Neptune ERM motors
```

## Why Raw L2CAP?

BlueZ 5.86 on SteamOS has a bug: its GATT listener socket is bound to the adapter's **public address**, but BLE connections arrive on the **static random address**. The kernel can't route the ATT channel → `connect_cb` never fires → connection drops after ~4 seconds.

The fix: use a raw L2CAP socket on CID 4 bound directly to the static random address, bypassing BlueZ's GATT server entirely.

## Quick Start

### Option A: BLE Mode (Deck → Host PC)

Requires a Steam Deck and a host PC with Bluetooth.

### Prerequisites

- Steam Deck with Bluetooth enabled
- Host PC with BlueZ and Steam Client
- Python 3.13+ on Deck

### Setup

1. Copy `pii.env.example` to `pii.env` and fill in your values
2. Run `scripts/setup.sh` on the Deck (first time only)
3. Deploy with `scripts/deploy.sh`
4. Connect from host: `bluetoothctl connect C2:12:34:56:78:9A`

### Option B: Virtual USB Mode (any Linux host, no Deck needed)

No Bluetooth or Steam Deck required. Creates a virtual USB SC2 on any Linux machine with Steam installed.

```bash
sudo python3 src/main_virtual_usb.py --name "Steam Controller 2026"
# Check: lsusb | grep 28de
# Check: ls -la /dev/hidraw*
```

### Option C: BLE Mode — On the Host

```bash
# Connect (NOT pair — pair tries BR/EDR which tears down LE)
bluetoothctl connect C2:12:34:56:78:9A

# Check hidraw
ls -la /dev/hidraw*

# Check input device
evtest /dev/input/eventN
```

## What's Implemented

- **Raw L2CAP ATT server** on CID 4 (bypasses BlueZ GATT bug)
- **GATT database**: GAP, GATT, HID, Battery, Device Info, Valve Custom SC2
- **SMP pairing**: Auto-confirm via BlueZ Agent1 D-Bus interface
- **Input capture**: Reads Neptune controller from `/dev/hidraw3` (64-byte HID reports)
- **SC2 report mapping**: Neptune → 12-byte gamepad + 45-byte SC2 Custom (buttons, sticks, triggers, trackpads, gyro)
- **Synthetic SC2 command handler**: Responds to GET_ATTRIBUTES, GET_SERIAL, SET_SETTINGS, and 10 other commands with correct formats
- **Lizard mode fix**: Periodically re-sends 0x81 command to prevent lizard mode re-enabling; EVIOCGRAB grabs event4/event5 at startup
- **Auto-recovery**: Retries Neptune hidraw device on crash (2s delay, 10 retries)
- **In-game rumble**: Full haptic pipeline confirmed working — games calling `SDL_RumbleJoystick()` produce rumble on Neptune motors via PackedRumbleReport format
- **Feature Reports in HID Report Map**: Feature Reports 0x00, 0x01, 0x85 declared in HID descriptor for Steam's SC2 HIDAPI driver
- **CCCD timing fix**: Sends initial zero notifications when CCCDs are enabled to pre-fill UHID queue
- **Virtual USB mode** (`main_virtual_usb.py`): Full virtual USB composite device via `vhci_hcd` + USB/IP protocol. 3 HID interfaces with correct `bInterfaceNumber`. SC2 command handler responds to all Steam queries. Steam Client registers it as a Steam Controller — verified via `controller.txt` showing `BYieldingCompleteSteamControllerRegistration`

## Reverse Engineering Findings

This project produced a detailed map of Steam's controller handling architecture through static binary analysis of `steamclient.so` (49MB, 141,343 functions) and the SC2 BLE firmware (`ibex_firmware.bin`, 2,027 functions).

### Steam Client (steamclient.so) — Key Findings

| Finding | Address | Description |
|---------|---------|-------------|
| Gate mechanism | `[esi+0x17c]` | 7 interactions: SET on error, CLEAR on normal, READ as bitmask, embedded in messages |
| Haptic scheduler | `0x0123e5d0` | **Never called for BLE controllers** — block is upstream in controller setup |
| Gate CHECK | `0x0123e5fb` | `cmp byte [esi+0x17c], 0` — unreachable for BLE controllers |
| Input path | `0x011e0930` | `PollControllers` — bypasses gate entirely, 6975 bytes |
| PID dispatch | `0x0121ba96` | BLE sets `[eax+0xbc]=2`, USB sets `=1`. Post-construction field, not vtable selector |
| Vtable A | `+0x02e6ce2c` | BLE controller vtable, shared by many objects |
| Vtable C | `+0x02e6c940` | Scheduler-expected entries at +0x74/+0x84 |
| Graphics API | `[eax+0x160]` | Writer at `0x019aec80` — values 1=GL, 2=Vulkan, 3=D3D12 |
| vtable[0x50] | 9 call sites | Fire-and-forget dispatch: SET_SETTINGS, gyro, mode switch |
| Controller struct | 15+ offsets | 0xbc (post-construction state), 0x10c (transport), 0x17c (gate) |

### Firmware (ibex_firmware.bin) — Key Findings

| Finding | Address | Description |
|---------|---------|-------------|
| Command dispatch | `0x000383c4` | TBH jump table, 144 entries at `0x383d2` |
| Dispatch architecture | Pure lookup | Descriptor-based, not function-pointer. Caller builds message → event system |
| 0xf2 ACK | `0x00042132` | 6-byte minimal response: `[01 00 00 00 00 f2]`, no payload |
| Motor output | Command 0x80 | uint16 LE speed + int8 gain per motor, I2S peripheral |
| Haptic sequencer | I2S driver | Script IDs + gain + master_gain_db, stereo waveform output |
| Flash limitation | 66.9% | Binary is 350KB of 512KB flash. Descriptors at 0x59b10-0x5a332 beyond dump |

### Full Decompilation

- **steamclient.so**: 141,343 functions, 6.7M lines, 185 MB — `~/ghidra-projects/exports/32bit/full_decompiled_32bit.c`
- **ibex_firmware.bin**: 2,027 functions, 73,645 lines — `spoofdeck-ghidra/ibex_firmware.bin_decompiled.c`
- **proteus_firmware.bin**: 790 functions, 36,117 lines — `spoofdeck-ghidra/proteus_firmware.bin_decompiled.c`

## Project Structure

```
├── AGENTS.md                        # Project continuation guide (read first)
├── README.md                        # This file
├── docs/
│   ├── sc2-protocol.md              # SC2 BLE protocol — PIDs, UUIDs, report formats
│   ├── att-server-implementation.md # ATT opcode table, handle layout, host discovery
│   ├── findings-backlog.md          # Known issues and technical findings
│   ├── hardware-findings.md         # Deck hardware: USB, BT, HID descriptors
│   ├── steam-client-analysis.md     # steamclient.so analysis, firmware files
│   └── archive/                     # Historical: challenges.md (all resolved)
├── research/
│   ├── triton-firmware-reference.md # SC2 BLE firmware RE (state machine, HID, commands)
│   ├── puck-crossref-reference.md   # Puck dongle firmware + cross-transport analysis
│   ├── serial-format-analysis.md    # Serial validation analysis
│   └── archive/                     # Completed research (roadmap, BlueZ debug, etc.)
├── src/
│   ├── main_l2cap.py                # ENTRYPOINT (BLE) — raw L2CAP ATT server
│   ├── main_virtual_usb.py          # ENTRYPOINT (USB) — vhci_hcd virtual USB device
│   ├── main_uhid.py                 # UHID virtual device (simpler, limited Steam support)
│   ├── att_server.py                # Raw L2CAP ATT server (CID 4)
│   ├── gatt_db.py                   # GATT database (87 attributes, 6 services)
│   ├── input_handler.py             # Neptune HID → SC2 input mapping
│   ├── agent.py                     # BlueZ Agent1 (auto-confirm)
│   ├── adv.py                       # BLE advertisement
│   └── bluez.py                     # BlueZ D-Bus helpers
├── scripts/
│   ├── setup.sh                     # Deck setup (first time only)
│   ├── deploy.sh                    # Deploy source + restart service
│   ├── diagnose.sh                  # Full Deck status diagnostic
│   ├── config_bt.py                 # Configure BT adapter
│   ├── extract_proto_trace.py       # Structured log parser
│   ├── pair.py                      # Pexpect auto-pair (handles KDE dialog)
│   └── connect_deck.py              # BLE connection (subprocess-based)
├── firmware/
│   ├── ibex_firmware.bin            # Triton SC2 BLE firmware (350KB, nRF52833)
│   └── proteus_firmware.bin         # Puck Dongle firmware (194KB)
├── ghidra-projects/exports/32bit/   # Full decompilation (141K functions, 6.7M lines)
├── dbus-config/                     # D-Bus system policy
├── deprecated/                      # Broken/unused: main.py, gatt_app.py
├── patches/                         # LD_AUDIT libraries, Steam wrapper, kernel patches
└── scratch/                         # Temporary captures (gitignored)
```

## Reading the Documentation

The project has two layers: **what we built** (src/) and **what we learned** (docs/, research/). Here's how to navigate:

### For Users

Start with this README, then `docs/sc2-protocol.md` for the protocol basics. If you want to deploy, follow `scripts/setup.sh` and `scripts/deploy.sh`.

### For Developers

Read `AGENTS.md` first — it's the full project context including architecture, connection details, and how to run. Then:

1. `src/main_l2cap.py` — the entrypoint, wires everything together
2. `src/att_server.py` — the ATT protocol implementation
3. `src/gatt_db.py` — the GATT database with all 87 attributes
4. `src/input_handler.py` — the Neptune → SC2 report mapping

### For Reverse Engineers

The deepest technical content is in `research/`:

| Document | What It Covers | Depth |
|----------|---------------|-------|
| `research/triton-firmware-reference.md` | SC2 BLE firmware — state machine, HID input, command dispatch, 100 commands | **Start here** |
| `research/puck-crossref-reference.md` | Puck dongle firmware, ESB protocol, firmware vs host cross-reference | Cross-binary analysis |
| `docs/att-server-implementation.md` | ATT opcode table, handle layout, host discovery sequence | ATT protocol |
| `docs/findings-backlog.md` | All known issues ranked by severity, with evidence | Bug tracker |
| `docs/steam-client-analysis.md` | steamclient.so analysis methodology and findings | Binary RE |
| `docs/hardware-findings.md` | Deck hardware: USB interfaces, BT adapter, HID descriptors | Hardware |

### The Full Picture

For the complete reverse engineering story:

1. **AGENTS.md** — the executive summary (read this first, always)
2. **research/triton-firmware-reference.md** — SC2 BLE firmware analysis
3. **research/puck-crossref-reference.md** — Puck firmware + cross-transport analysis
4. **ghidra-projects/exports/32bit/full_decompiled_32bit.c** — the raw evidence (6.7M lines)
5. **spoofdeck-ghidra/** — firmware decompilations

The decompilation files are large but searchable. Use `grep` to find specific patterns:
```bash
# Find all 0x8F references
grep -n "0x8f\|0x8F\|== 143" ~/ghidra-projects/exports/32bit/full_decompiled_32bit.c

# Find all gate interactions
grep -n "0x17c" ~/ghidra-projects/exports/32bit/full_decompiled_32bit.c

# Find all vtable[0x50] calls
grep -n "0x50\]" ~/ghidra-projects/exports/32bit/full_decompiled_32bit.c
```

## Known Issues

- **Steam-generated haptics not working** — Architecturally blocked. The haptic scheduler at `0x123e5d0` is never called for BLE controllers. `CPulseHapticWorkItem` fires with 0.0ms runtime (work item short-circuits before entering scheduler). Block is upstream in controller setup/dispatch. Real SC2 also doesn't get 0x8F haptics over BLE. See `docs/findings-backlog.md`.
- **Firmware binary truncated** — `ibex_firmware.bin` is 66.9% of nRF52833's 512KB flash. Command descriptors at 0x59b10-0x5a332 are beyond the dump. Full flash dump via J-Link/SWD needed.
- **PnP ID warning** — BlueZ logs `Error reading PNP_ID: Protocol error` (non-fatal)
- **KDE pairing dialog** — Host shows dialog during pairing, user must click "yes"
- **Stale BlueZ state** — After code changes break a connection, clear bond data and restart BlueZ daemon. See `AGENTS.md` for the fix.

## Contributing

**No real Steam Controller 2026 was used in this project.** Everything was built through reverse engineering `steamclient.so` and Bluetooth protocol analysis. A real SC2 device would unlock:

- **BLE traffic capture** — btmon capture from a real SC2 would show exact ATT traffic, confirming our synthetic command responses
- **Haptics** — Capturing what Steam sends for trackpad clicks and UI feedback
- **Protocol refinements** — Verifying edge cases and timing behavior
- **Firmware flash dump** — J-Link/SWD dump of the full nRF52833 flash would resolve the remaining firmware unknowns

If you have a real SC2 and want to help, start with `docs/findings-backlog.md` for the full technical analysis.

## Acknowledgments

- SDL3 source for SC2 HID report format documentation
- BlueZ source code (HOG profile, L2CAP reference)
- SteamOS community research
- InputPlumber project for Neptune protocol documentation and PackedRumbleReport format
- Ghidra for binary analysis tooling
