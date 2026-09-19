# Steam Controller 2026 BLE Protocol

## Device Identification

| Mode | VID | PID | Product Name |
|------|-----|-----|--------------|
| BLE | 0x28DE | 0x1303 | Steam Controller 2026 |
| USB (wired) | 0x28DE | 0x1302 | Steam Controller 2026 |
| Puck (dongle) | 0x28DE | 0x1304 | Steam Controller Puck |

- **Manufacturer**: Valve Software
- **Vendor ID**: 0x28DE (Valve)
- **PnP ID PID**: 0x1303

## GATT Services

### Firmware-Confirmed Layout

The real SC2 firmware (nRF52833, Zephyr RTOS) registers GATT services as follows:

- **HID Service (0x1812)** — Explicitly registered in firmware (`FUN_0001d8d0`). Contains up to 6 input Report characteristics, up to 10 output/feature Report characteristics, and 1 optional custom CHR_REPORT.
- **GAP (0x1800)** and **GATT (0x1801)** — Pre-registered by Zephyr BLE stack, looked up by firmware.
- **Battery (0x180F)** and **Device Info (0x180A)** — **NOT found in firmware GATT registration**. Storage keys exist for DIS values (`bt/dis/model`), but no GATT registration code. These may be registered elsewhere or not present in SC2 BLE mode.

**Note**: Our spoofing server exposes Battery and Device Info because BlueZ's HOGP driver requires them for `/dev/hidrawN` creation. The real SC2 may not have them — this is an area for further testing.

For the complete handle layout (87 attributes, 6 services) with all UUIDs and handle numbers, see `docs/att-server-implementation.md`.

### CCCD Subscription Architecture

The SC2 BLE profile has **9 CCCDs** (Client Characteristic Configuration Descriptors, UUID `0x2902`). Not all are equally important — some are mandatory per the HID/BLE spec, others are critical for the spoof to function.

| # | Service | Characteristic | Subscribed By | Purpose |
|---|---------|---------------|---------------|---------|
| 1 | GATT (0x1801) | Service Changed | — | Mandatory per BLE spec. Never subscribed in practice for this use case. |
| 2 | HID | Report ID 1 — Gamepad (12B) | hog-ll | Standard HID. Creates `/dev/hidrawN` + `/dev/input/eventN`. Our gamepad notifications flow here. |
| 3 | HID | Report ID 3 — Mouse (4B) | hog-ll | Standard HID. Required by hog-ll for full HOG profile. Not critical for gamepad function. |
| 4 | HID | Report ID 4 — Keyboard (8B) | hog-ll | Standard HID. Same as mouse — required by spec, not critical. |
| 5 | HID | Report ID 0x45 — SC2 Custom (45B) | hog-ll | **Primary input path.** hog-ll subscribes → UHID → Steam reads. Must have CCCD. |
| 6 | HID | Report ID 0x47 — SC2 Extended (47B) | hog-ll | Extended input (adds trackpad timestamps). hog-ll subscribes if present. |
| 7 | Valve Custom | Input CH1 — 0x45 data (45B) | Steam | Steam reads this directly via Valve Custom UUID, bypassing hog-ll. |
| 8 | Valve Custom | Input CH2 — 0x47 data (47B) | Steam | Same as above for extended report. |
| 9 | Battery | Battery Level | hog-ll | Required for hog-ll to create `/dev/hidrawN`. Without it, no hidraw node appears. |

#### Why 0x45/0x47 Are Registered Twice

SC2 Custom Input Reports (0x45, 0x47) appear in **two** services simultaneously:

1. **HID Service** (as CHR_REPORT with Report Reference descriptors) — this is what BlueZ's hog-ll driver sees. hog-ll only processes Report characteristics inside the HID Service. When the host writes a CCCD here, hog-ll creates the UHID device and starts routing notifications to `/dev/hidrawN`.

2. **Valve Custom Service** (with Valve UUIDs `100F6C7A-...`/`100F6C7C-...`) — Steam's `CGetControllerInfoWorkItem` reads directly from these UUIDs, bypassing hog-ll entirely. Steam expects to find input data at these specific Valve UUIDs.

Both copies get the same notification data. The HID Service copies feed the hog-ll/UHID pipeline; the Valve Custom copies are read directly by Steam's controller initialization code.

At runtime, every 45-byte SC2 input report is sent as an ATT notification on **both** handles simultaneously (`main_l2cap.py:857-861`). The Valve Custom handle (`_sc2_report_handle`) and the HID Service CHR_REPORT handle (`_sc2_hid_handle`) receive identical data. This ensures both the hog-ll/UHID pipeline (which feeds `/dev/hidrawN` for games) and Steam's direct Valve UUID reads (which feed the controller initialization chain) get the input data.

#### The CCCD Timing Gap

The CCCD subscription timing is the **root cause of missing Steam haptics**. Here's why:

1. Host connects, discovers GATT services, writes CCCDs
2. BlueZ hog-ll creates UHID device, starts routing to `/dev/hidrawN`
3. Steam's `CGetControllerInfoWorkItem::RunFunc` (0x01218840) calls `SDL_hid_read_timeout` 51× at 100ms intervals
4. **If no data is available at `/dev/hidrawN` during this window**, the init chain stalls and the haptic gate (`[esi+0x17c]`) is never set
5. 0x8F commands are never dispatched → Steam-generated haptics don't work

The fix: when the gamepad CCCD is enabled, the server immediately sends a zero-notification to pre-fill the UHID queue (see `docs/att-server-implementation.md` "CCCD Timing Fix"). In-game rumble works because it takes a different code path (`SDL_RumbleJoystick` → `SDL_hid_write`) that doesn't depend on the init chain.

For the full init chain stall analysis, see `docs/findings-backlog.md`.

### Valve Custom Service

```
100F6C32-1735-4313-B402-38567131E5F3
```

This is a Valve custom UUID service. Steam reads from these characteristics directly.

| Characteristic | UUID | Properties | Description |
|---------------|------|------------|-------------|
| Input Report 1 | `100F6C7A-...` | Read, Notify | Input report (report ID 0x45) |
| Input Report 2 | `100F6C7C-...` | Read, Notify | Input report (report ID 0x47) |
| Report | `100F6C34-1735-4313-B402-38567131E5F3` | Read, Write, Write Without Response | Output/feature report |

## Input Report Formats

### Report 0x45 (45 bytes) — Primary Input

**Note**: Report ID (0x45) is NOT included in the ATT notification payload — BlueZ
hog-ll adds it from the Report Reference descriptor. The notification data is exactly
45 bytes starting at offset 0 = sequence number.

**Format verified against SDL3 `TritonMTUNoQuat_t` struct in `input_handler.py:263-284`.**

```
Offset  Size  Field                   Type
0       1     seq_num                 uint8
1       4     buttons                 uint32 LE
5       2     sTriggerLeft            int16 LE (0-32767)
7       2     sTriggerRight           int16 LE (0-32767)
9       2     sLeftStickX             int16 LE
11      2     sLeftStickY             int16 LE
13      2     sRightStickX            int16 LE
15      2     sRightStickY            int16 LE
17      2     sLeftPadX               int16 LE
19      2     sLeftPadY               int16 LE
21      2     unPressureLeft          uint16 LE
23      2     sRightPadX              int16 LE
25      2     sRightPadY              int16 LE
27      2     unPressureRight         uint16 LE
29      4     timestamp               uint32 LE (microseconds)
33      2     accel_x                 int16 LE
35      2     accel_y                 int16 LE
37      2     accel_z                 int16 LE
39      2     gyro_x                  int16 LE
41      2     gyro_y                  int16 LE
43      2     gyro_z                  int16 LE
                          Total: 45 bytes
```

Note: Triggers are 16-bit (0-32767), NOT 8-bit. The `unPressureLeft/Right` fields
are trackpad capacitive pressure values (unsigned). The 45-byte report does NOT
include quaternion data — the full 48-byte format (with Report ID + quaternion) is
used over USB (Report 0x42).

### Report 0x47 (47 bytes) — Extended Input

Same as 0x45 but adds 16-bit trackpad timestamp fields.

### Report 0x80 (9 bytes) — Haptic Output

**Format from `gatt_db.py:412-420` and `main_l2cap.py:267-292`.**

```
Offset  Size  Description
0       1     Type (0x80)
1       2     Intensity (uint16 LE)
3       2     Left motor speed (uint16 LE)
5       1     Left gain (int8)
6       2     Right motor speed (uint16 LE)
8       1     Right gain (int8)
```

This report is received via ATT Write Request (0x12) on the CHR_REPORT handle in
the HID Service. The hog-ll driver strips the Report ID prefix before delivering
to userspace — `_on_haptic_write()` receives the 9-byte payload without the 0x80
prefix. When the Report ID is present (10 bytes), it is parsed at `value[0]`.

The haptic rumble is forwarded to the Neptune controller as a `PackedRumbleReport`
(64-byte output report starting with `0xeb 0x09`).

### Report 0x42 (53 bytes) — USB Input

Same as 0x45 but adds full quaternion (4 × 16-bit) to the IMU data.

## Button Bitmask (32-bit)

**Verified from SDL3 `SDL_hidapi_steam_triton.c` TritonButtons enum.**

```
Bit 0:  A
Bit 1:  B
Bit 2:  X
Bit 3:  Y
Bit 4:  QAM (Quick Access Menu / ...)
Bit 5:  R3 (Right Stick Click)
Bit 6:  View (Options / Select)
Bit 7:  R4 (Right Paddle 1)
Bit 8:  R5 (Right Paddle 2)
Bit 9:  R (Right Bumper)
Bit 10: D-Pad Down
Bit 11: D-Pad Right
Bit 12: D-Pad Left
Bit 13: D-Pad Up
Bit 14: Menu
Bit 15: L3 (Left Stick Click)
Bit 16: Steam
Bit 17: L4 (Left Paddle 1)
Bit 18: L5 (Left Paddle 2)
Bit 19: L (Left Bumper)
Bit 20: Right Joystick Touch (capacitive)
Bit 21: Right Trackpad Touch
Bit 22: Right Trackpad Click
Bit 23: Right Trigger Click (binary threshold)
Bit 24: Left Joystick Touch (capacitive)
Bit 25: Left Trackpad Touch
Bit 26: Left Trackpad Click
Bit 27: Left Trigger Click (binary threshold)
Bit 28: Right Grip Touch (capacitive)
Bit 29: Left Grip Touch (capacitive)
Bit 30-31: Reserved
```

## HID Descriptor (Vendor Interface)

```
Usage Page (0xFF00)        ; Vendor-defined
Usage (0x01)               ; Vendor usage
Collection (Application)
  Report ID (0x00)
  Report Size (8)
  Report Count (64)
  Input (Data, Var, Abs)   ; 64-byte vendor input report
  Report ID (0x00)
  Report Size (8)
  Report Count (64)
  Output (Data, Var, Abs)  ; 64-byte vendor output report
End Collection
```

The vendor HID interface uses Usage Page 0xFF00 with 64-byte I/O reports. This is used for raw vendor communication (firmware updates, feature reports).

### SC2 BLE Report Map Summary

**From `gatt_db.py:290-452`.** The SC2 BLE Report Map contains:

| Report ID | Type | Size | Description |
|-----------|------|------|-------------|
| 0x01 | Input | 12 bytes | Gamepad (16 buttons + 4 × 16-bit axes + 2 × 8-bit triggers) |
| 0x02 | Output | 1 byte | Gamepad output (exists for hog-ll num_reports > 1) |
| 0x03 | Input | 4 bytes | Mouse (5 buttons + X + Y + Wheel, all 8-bit) |
| 0x04 | Input | 8 bytes | Keyboard (1 modifier + 1 reserved + 6 keycodes, all 8-bit) |
| 0x45 | Input | 45 bytes | SC2 Custom Input (see TritonMTUNoQuat_t above) |
| 0x47 | Input | 47 bytes | SC2 Custom Extended Input |
| 0x80 | Output | 9 bytes | Haptic Rumble Output (see Report 0x80 above) |
| 0x02 | Feature | 64 bytes | SC2 Command Channel (commands + responses) |
| 0x01 | Feature | 64 bytes | SC2 Capabilities |
| 0x85 | Feature | 64 bytes | SC2 Mode Switch (Lizard ↔ Steam Input) |

## Mode Switching

The SC2 starts in **Lizard Mode** (basic keyboard/mouse emulation):
- Left trackpad: mouse movement
- Right trackpad: scroll
- Buttons: keyboard keys
- Triggers: left/right mouse buttons

To switch to **Steam Input Mode** (full controller input):
1. Steam Client sends a feature report to the device
2. Device switches from lizard mode to Steam Input mode
3. Device begins sending full controller input reports

Key strings from Steam Client:
- `toggle_lizard` — toggle lizard mode
- `is_mode_switching_supported` — check if mode switching is available
- `CExitLizardModeWorkItem` — work item to exit lizard mode

## Feature Reports

| Report ID | Direction | Description |
|-----------|-----------|-------------|
| 0x01/0x02 | Device ↔ Host | SC2 command channel (GET_ATTRIBUTES, SET_SETTINGS, etc.) |
| 0x85 | Host → Device | Mode switch (lizard/Steam Input) |
| 0x86 | Host → Device | Configuration |
| 0x87 | Host → Device | Calibration data |

## SC2 Command Bytes (Feature Reports 0x01/0x02)

**Note**: The Feature Report protocol uses a write-then-read pattern — the host writes a command to FR 0x00, then reads FR 0x00 to get the response. This is non-standard BLE HID behavior. See `docs/att-server-implementation.md` "Feature Report Protocol" section for the full flow, including the dual response mechanism and lifecycle. For the Steam-side implementation, see `research/archive/steamclient-reverse-session/findings.md` (Feature Report handshake analysis).

### Firmware-Confirmed Command Table (100 commands)

The firmware's main command dispatch (`FUN_000383c4` at `0x000383c4`) uses a jump table at `0x00053f94` with **95 entries**. An additional 5 commands are handled outside the main table. **100 total commands** identified from firmware RE.

### Known Commands (handled by our spoofing code)

| Byte | Name | Direction | Description | In Firmware Table |
|------|------|-----------|-------------|:-----------------:|
| 0x81 | ID_CLEAR_DIGITAL_MAPPINGS | Host→Device | Clear mappings (exit lizard mode) | Yes |
| 0x83 | ID_GET_ATTRIBUTES_VALUES | Bidirectional | Get device attributes | Yes |
| 0x87 | ID_SET_SETTINGS_VALUES | Host→Device | Set controller settings | **No** (gap 0x86-0x8a) |
| 0x8F | ID_TRIGGER_HAPTIC_PULSE | Host→Device | Trigger haptic pulse | Yes (0x54368) |
| 0xAE | ID_GET_SERIAL | Bidirectional | Get serial number | **No** (handled at BLE co-processor level) |

### Commands Documented in steamclient.so RE

| Byte | Name | Direction | Description | In Firmware Table |
|------|------|-----------|-------------|:-----------------:|
| 0x80 | ID_SET_DIGITAL_MAPPINGS | Host→Device | Set button mappings (stored, returned by 0x82) | Yes |
| 0x82 | ID_GET_DIGITAL_MAPPINGS | Host→Device | Get current mappings (stored slice, `0xFF` if empty/past end) | Yes |
| 0x84 | ID_GET_ATTRIBUTE_LABEL | Host→Device | Get attribute label (no string response on this path) | Yes |
| 0x85 | ID_SET_DEFAULT_DIGITAL_MAPPINGS | Host→Device | Set default mappings | Yes |
| 0x86 | ID_FACTORY_RESET | Host→Device | Factory reset | Yes |
| 0x88 | ID_CLEAR_SETTINGS_VALUES | Host→Device | Clear settings | **No** (gap) |
| 0x89 | ID_GET_SETTINGS_VALUES | Bidirectional | Get current settings | **No** (gap) |
| 0x8A | ID_GET_SETTING_LABEL | Host→Device | Get setting label (no string response on this path) | Yes |
| 0x8B | ID_GET_SETTINGS_MAXS | Host→Device | Get max values | Yes |
| 0x8C | ID_GET_SETTINGS_DEFAULTS | Host→Device | Get default values | Yes |
| 0x8D | ID_SET_CONTROLLER_MODE | Host→Device | Mode switch (lizard ↔ Steam Input) | Yes |
| 0x8E | ID_LOAD_DEFAULT_SETTINGS | Host→Device | Load default settings | Yes |
| 0x9F | ID_TURN_OFF_CONTROLLER | Host→Device | Turn off controller | Yes |
| 0xA1 | ID_GET_DEVICE_INFO | Host→Device | Get device info | Yes |
| 0xBA | ID_GET_CHIP_ID | Bidirectional | Get chip ID | Yes |
| 0xBE | GET_BATTERY_DATA | Host→Device | Battery query (empty body on this path) | n/a (open firmware) |
| 0xC1 | ID_SET_AUDIO_MAPPING | Host→Device | Audio mapping (ACK-only, no audio HW) | n/a (open firmware) |
| 0xB4 | PROTOCOL_VERSION | Host→Device | Protocol version query | Yes |
| 0xB5 | PROTOCOL_COMMAND | Host→Device | Protocol command (generic ack) | Yes |
| 0xEE | FR_MSG_WRITE | Host→Device | Feature report message write | Yes |
| 0xEF | FR_MSG_READ | Host→Device | Feature report message read | Yes |
| 0x95 | ENTER_BOOTLOADER | Host→Device | Enter bootloader (ack, no reboot) | Yes |
| 0xF2 | GET_SYSTEM_INFO | Bidirectional | Version query (3 selector variants: build info / fixed blob / uptime). Distinct from the 6-byte mapping ACK `[01 00 00 00 00 F2]`, which is a firmware-IPC notification sent after mapping commands, not a feature response | Yes |

### Additional Commands in Firmware (not in steamclient.so RE)

The firmware handles ~80 additional commands across these categories:
- **System** (37 commands): power management, LED control,陀螺仪 calibration, factory reset variants
- **Configuration** (22 commands): settings read/write for trackpad, stick, trigger, IMU parameters
- **Calibration** (18 commands): stick/trigger/trackpad/IMU calibration data
- **Battery/Power** (6 commands): battery level, charging state, power management
- **Firmware** (6 commands): firmware update, bootloader entry, version queries
- **Input Report** (1 command): report mode switching
- **LED** (2 commands): RGB LED control

All of the above now have explicit handlers in `src/sc2_commands.py`
(stores where Steam reads them back: digital mappings, settings, LED,
user store; version-accurate responses: attributes, serial, device info,
chip ID, system info; safe ACKs for everything destructive or
hardware-bound). The full Steam-client opcode enum — including
dongle/radio (`0xAD`, `0xAF`–`0xB3`, `0xC4`), audio update
(`0xB6`–`0xB9`), extra calibration (`0xA7`, `0xA9`–`0xAC`, `0xBF`) and
Deck-only (`0xEA`, `0xEB`) codes — is vendored in sc26re's
`app/src/sdl/controller_constants.h`; the spoof ACKs them all without
acting. No firmware dump was needed for any of this: the open
firmware's handler semantics plus the SDL headers plus live captures
cover everything wire-visible (see
`research/firmware-dump-assessment.md` follow-up).

### Response Formatter

38 commands have detailed response formats in the firmware's response formatter (`FUN_0000c55c`). Commands 0x01-0x19 and 0x82/0x83 have specific response codes and payload sizes.

## Serial Validation (GET_SERIAL 0xAE)

The GET_SERIAL response has two validation code paths in steamclient.so:

### Path 1: V_strncmp (BLOCKING)
- Location: V_strncmp call at 0x10c29b3
- Checks: byte[2] must be 0x01 (success status), serial[0] must be 'F' (0x46)
- Failure: "Controller Serial# invalid" → identity slot not populated → zombie disconnect
- This is the PRIMARY validation that determines whether the controller registers

### Path 2: BLE PCB Serial (COSMETIC)
- Location: FUN_0122e4c0 at 0x122e4c0
- Checks: serial format against known patterns (Path A: status=0x00, serial[0]='M', serial[2]∈{B,C,D}; Path B: status=0x04, serial[0]='M', serial[1]='X', serial[2]='A')
- Failure: "Deck Controller PCB Serial# invalid: %s" — logged but does NOT block registration
- The V_strncmp check runs first; if it passes, the BLE PCB check also runs but is cosmetic only

### Required Format
- Response byte[0]: 0xAE (command echo)
- Response byte[1]: 0x15 (payload length, matches write command)
- Response byte[2]: 0x01 (success status — REQUIRED, 0x00/0x04 trigger blocking error)
- Response bytes[3-22]: serial string (20 bytes, first byte must be 'F')

## Settings Registers

| Register | Name | Values |
|----------|------|--------|
| 0 | SETTING_MOUSE_SENSITIVITY | |
| 1 | SETTING_MOUSE_ACCELERATION | |
| 2 | SETTING_TRACKBALL_ROTATION_ANGLE | |
| 3 | SETTING_HAPTIC_INTENSITY_UNUSED | |
| 4 | SETTING_LEFT_GAMEPAD_STICK_ENABLED | |
| 5 | SETTING_RIGHT_GAMEPAD_STICK_ENABLED | |
| 6 | SETTING_USB_DEBUG_MODE | |
| 7 | SETTING_LEFT_TRACKPAD_MODE | 7=None |
| 8 | SETTING_RIGHT_TRACKPAD_MODE | 7=None |
| 9 | SETTING_LIZARD_MODE | 0=OFF, 1=ON |
| 10 | SETTING_DPAD_DEADZONE | |
| 15 | SETTING_HAPTIC_INCREMENT | |
| 21 | SETTING_SENSITIVITY_SCALE_AMOUNT | |
| 24 | SETTING_SMOOTH_ABSOLUTE_MOUSE | |
| 48 | SETTING_IMU_MODE | |
| 70 | SETTING_HAPTICS_ENABLED | |
| 79 | SETTING_HAPTIC_INTENSITY | |

### GET_ATTRIBUTES (0x83) Response Format

The response contains 9 attributes, each as 1-byte tag + 4-byte LE uint32 value:

| Tag | Name | Real SC2 Value | Description |
|-----|------|----------------|-------------|
| 1 | ATTRIB_PRODUCT_ID | 0x1303 | SC2 BLE Product ID |
| 2 | ATTRIB_CAPABILITIES | 0x4169bfff | Feature capabilities bitmask |
| 4 | ATTRIB_FIRMWARE_BUILD_TIME | 0x65E4F1AD | Firmware build timestamp |
| 9 | ATTRIB_BOARD_REVISION | 46 | Hardware board revision |
| 10 | ATTRIB_BOOTLOADER_BUILD_TIME | varies | Bootloader build timestamp |
| 11 | ATTRIB_CONNECTION_INTERVAL_IN_US | 4000 | BLE connection interval |
| 12 | ATTRIB_12 | 0 | Unknown |
| 13 | ATTRIB_13 | 0 | Unknown |
| 14 | ATTRIB_14 | 0 | Unknown |

### Capabilities Bitmask (0x4169bfff)

The value 0x4169bfff is a 32-bit bitmask (bits 0–31 only).

```
Bit 0-9:   Buttons (A, B, X, Y, QAM, R3, View, R4, R5, R)
Bit 10-19: Triggers, D-Pad, Menu, L3, Steam, L4, L5, L
Bit 20-25: Joystick touch, Trackpad touch/click
Bit 26-29: Trigger clicks, Grip touch
Bit 30-31: IMU
```

The specific bit meanings should be verified against the firmware — the above is
based on the button bitmask layout and capabilities reported by the real device.

## Pairing

The SC2 uses standard BLE pairing:
1. Device advertises with SC2-specific service UUID
2. Host scans and connects
3. Pairs using Just Works or Passkey
4. Discovers GATT services
5. Subscribes to input report notifications
6. Sends feature report to switch out of lizard mode
7. Begins receiving controller input

## Comparison with Steam Deck (Neptune)

The Steam Deck controller ("Neptune") uses:
- VID: 0x28DE
- PID: 0x1205
- 5 USB interfaces (mouse, keyboard, vendor HID, audio, CDC)
- Separate motion sensor input device
- Virtual Xbox 360 pad via hid-steam driver

The SC2 BLE profile is significantly simpler, using a single GATT service with characteristics for input/output.

## Structured Protocol Logging

Set `SPOOFDECK_PROTO_LOG=1` to enable JSON logging to stderr for ATT opcodes, notifications, haptics, and SC2 commands. Parse with `scripts/extract_proto_trace.py`.

### Event Schema

| Event | Fields | Description |
|-------|--------|-------------|
| `att_mtu_req` | `opcode`, `client_mtu`, `server_mtu`, `negotiated` | MTU exchange |
| `att_read_req` | `opcode`, `handle`, `data`, `response`, `response_len` | Read Request/Response |
| `att_read_blob` | `opcode`, `handle`, `offset`, `response_len` | Read Blob Request |
| `att_write_req` | `opcode`, `handle`, `data`, `cmd`, `cmd_name`, `response`, `cb_invoked` | Write Request (+ SC2 command detection) |
| `att_write_cmd` | `opcode`, `handle`, `data`, `cmd`, `cmd_name` | Write Command |
| `att_notif` | `handle`, `len`, `sent` | Notification sent |
| `att_notif_dropped` | `handle`, `len` | Notification dropped (no CCCD) |
| `haptic_write` | `report_id`, `data`, `left_speed`, `right_speed` | Haptic output received |
| `sc2_cmd` | `cmd_byte`, `cmd_name`, `data`, `response_len` | SC2 command processed |
