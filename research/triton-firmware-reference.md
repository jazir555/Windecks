# Triton (SC2 BLE Controller) Firmware Reference

> **Platform**: Nordic nRF52833 (ARM Cortex-M4F), Zephyr OS v3.7.99-af30fca7cecd, nRF Connect SDK v2.9.0-d93dcad627bd, Nordic SoftDevice Controller  
> **Firmware**: `ibex_firmware.bin` (350,528 bytes), 2,027 functions, 73,705 lines of pseudocode  
> **Decompiler**: Ghidra 11.3.1 — analysis based on string cross-references, UUID values, and code pattern matching (no debug symbols)

---

## 1. Platform Overview

### Stack Architecture

```
┌─────────────────────────────────────────────────────┐
│ Application Layer (controller_state_machine_thread) │
├─────────────────────────────────────────────────────┤
│ Zephyr BLE Host (bt_gatt, bt_hids, bt_smp, bt_att) │
│   - bt_hids (HID Service)                           │
│   - bt_gatt_pool (attribute pool)                   │
│   - bt_gatt (GATT server)                           │
│   - bt_att (ATT protocol)                           │
│   - bt_smp (Security Manager)                       │
├─────────────────────────────────────────────────────┤
│ Nordic SoftDevice Controller                        │
│   - HCI driver (bt_hci)                             │
│   - Link layer                                      │
│   - Physical layer (2.4 GHz)                        │
└─────────────────────────────────────────────────────┘
```

### Initialization Strings (Order of Operations)

1. `*** Booting My Application v0.0.0-none ***`
2. `*** Using nRF Connect SDK v2.9.0-d93dcad627bd ***`
3. `*** Using Zephyr OS v3.7.99-af30fca7cecd ***`
4. `SoftDevice Controller build revision:` — SDC initialized
5. `HCI driver open failed` / `HCI driver close failed` — HCI transport to SDC
6. `Failed to set Bluetooth device name: %s` — Set device name
7. `HID report descriptor size: %zu` — HID descriptor loaded
8. `HIDS initialization failed (err %d)` — HIDS service registered
9. `controller_state_machine_thread` — Main thread starts
10. `Enable slot 0` / `Enable slot 1` — Advertising slots enabled

### I2C Device Names

| Device | Address | Description |
|--------|---------|-------------|
| `olympus@2c` | 0x2C | Trackpad controller (Olympus) |
| `mp2733@4b` | 0x4B | Battery charger IC |
| `slg4l48185@10` | 0x10 | GreenPAK programmable GPIO |
| `puck-pilot-gpio` | — | Puck/Gyro GPIO interface |

---

## 2. State Machine

The firmware uses **Zephyr's SMF (State Machine Framework)** (`smf_set_state` string found at runtime). The main thread is named `controller_state_machine_thread` (string at `0x48d9f`).

### All States (25 total)

| State | String Address | Entry Action |
|-------|---------------|--------------|
| **ST_INITIAL** | `0x48b19` | Boot initialization, BLE stack init |
| **ST_USB_WAIT_FOR_ENUMERATION** | `0x48b2a` | Wait for USB host enumeration |
| **ST_USB_DATA** | `0x48b4c` | USB HID data streaming active |
| **ST_USB_SUSPENDED** | `0x48b5e` | USB suspended (host sleep) |
| **ST_USB_WAIT_FOR_WAKEUP** | `0x48b75` | USB waiting for wakeup signal |
| **ST_USB_WIRELESS_ON** | `0x48b92` | USB connected + wireless enabled |
| **ST_USB_WIRELESS_OFF** | `0x48bab` | USB connected + wireless disabled |
| **ST_PUCK_OFF** | `0x48bc5` | Puck (wireless dongle) disconnected |
| **ST_PUCK_OFF_DEBOUNCE_DETACHMENT** | `0x48bd7` | Debouncing puck detach event |
| **ST_PUCK_KEYCHORD_CURRENT** | `0x48bfd` | Puck keychord (current mode) |
| **ST_PUCK_KEYCHORD_BT** | `0x48c1c` | Puck keychord (BT pairing) |
| **ST_PUCK_KEYCHORD_ESB** | `0x48c36` | Puck keychord (ESB mode) |
| **ST_PUCK_KEYCHORD_ESB_ALT** | `0x48c51` | Puck keychord (ESB alt mode) |
| **ST_PUCK_ON** | `0x48c70` | Puck connected, wireless active |
| **ST_BATTERY** | `0x48d00` | Battery monitoring state |
| **ST_BATTERY_KEYCHORD_CURRENT** | `0x48c81` | Battery keychord (current mode) |
| **ST_BATTERY_KEYCHORD_BT** | `0x48ca3` | Battery keychord (BT pairing) |
| **ST_BATTERY_KEYCHORD_ESB** | `0x48cc0` | Battery keychord (ESB mode) |
| **ST_BATTERY_KEYCHORD_ESB_ALT** | `0x48cde` | Battery keychord (ESB alt mode) |
| **ST_SHUTDOWN** | `0x48d11` | Normal shutdown |
| **ST_SHUTDOWN_SILENT** | `0x48d23` | Silent shutdown (no LED) |
| **ST_SHUTDOWN_LOW_BATT** | `0x48d3c` | Low battery forced shutdown |
| **ST_SHUTDOWN_KEYLOCK_ACTIVE** | `0x48d57` | Shutdown with keylock override |
| **ST_REBOOT** | `0x48d78` | Normal reboot |
| **ST_REBOOT_SILENT** | `0x48d88` | Silent reboot |

### State Transitions

```
ST_INITIAL
  ├─→ ST_USB_WAIT_FOR_ENUMERATION   (USB cable inserted)
  ├─→ ST_PUCK_OFF                   (No USB, start wireless)
  └─→ ST_BATTERY                    (Battery check at boot)

ST_PUCK_OFF
  ├─→ ST_PUCK_ON                    (Puck/dongle detected)
  ├─→ ST_PUCK_OFF_DEBOUNCE_DETACHMENT → ST_PUCK_OFF  (puck removed)
  ├─→ ST_PUCK_KEYCHORD_BT           (BT pairing keychord)
  ├─→ ST_PUCK_KEYCHORD_ESB          (ESB pairing keychord)
  └─→ ST_SHUTDOWN_LOW_BATT          (low battery)

ST_PUCK_ON
  ├─→ ST_PUCK_OFF                   (puck disconnected)
  └─→ ST_SHUTDOWN                   (power off)

ST_USB_WAIT_FOR_ENUMERATION
  ├─→ ST_USB_DATA                   (enumerated successfully)
  ├─→ ST_USB_WIRELESS_ON            (USB + wireless)
  └─→ ST_USB_WIRELESS_OFF           (USB only)

ST_USB_DATA
  ├─→ ST_USB_SUSPENDED              (host suspend)
  ├─→ ST_USB_WAIT_FOR_WAKEUP        (host sleep)
  └─→ ST_SHUTDOWN                   (power off while USB)

ST_BATTERY
  ├─→ ST_BATTERY_KEYCHORD_BT        (BT pairing from battery)
  ├─→ ST_BATTERY_KEYCHORD_ESB       (ESB pairing from battery)
  └─→ ST_SHUTDOWN_LOW_BATT          (critical battery)

ST_SHUTDOWN / ST_SHUTDOWN_LOW_BATT / ST_SHUTDOWN_SILENT / ST_SHUTDOWN_KEYLOCK_ACTIVE
  └─→ (power off / MCU reset)

ST_REBOOT / ST_REBOOT_SILENT
  └─→ (MCU reset → ST_INITIAL)
```

### State Machine Mechanism

- Uses `smf_set_state()` for transitions (string at runtime)
- Each state has an `_entry` function (logged when entering state)
- State table is located at approximately `0x53e4c` in firmware data section
- State entry functions are Thumb-mode code at addresses like `0x2b8ac` (ST_PUCK_OFF), `0x2c180` (ST_BATTERY_KEYCHORD_BT)
- States are logged via `failed to process state machine (%d)` on error

---

## 3. BLE Stack

### GATT Service Registration

The function `FUN_0001d8d0` (size 1520) is the master GATT service registration function. It constructs the complete GATT database by calling helper functions with specific BLE UUIDs:

#### GAP Service (0x1800)
```c
local_30 = param_1 + 0x81;           // Device Name attribute
local_2c = 0x40000;                   // Properties (Read)
// UUID 0x2a4a = HID Information
// UUID 0x2a4c = HID Control Point
```

#### HID Service (0x1812)
```c
local_3c = (undefined1 *)((uint)local_3c & 0xffffff00);  // UUID = 0x1812
FUN_0001ce7c(param_1, &local_3c);  // Register primary service
```

**HID Report Characteristics (up to 6 input reports):**
```c
// Loop: uVar11 = min(*(byte*)(param_1 + 0x46), 6)  — max 6 reports
for (uVar16 = 0; uVar16 < uVar11; uVar16++) {
    local_3e = 0x2a4d;    // UUID: HID Report Characteristic
    local_2c = uVar17 << 0x10;  // Properties from config
    FUN_0001cef4(param_1, 0x12, &local_3c);  // Register characteristic
    FUN_0001d09c(param_1, piVar8, uVar17 | uVar17 << 1);  // CCCD descriptor
    local_3e = 0x2908;    // UUID: Report Reference Descriptor
    FUN_0001cff0(param_1, &local_3c);  // Register descriptor
}
```

**HID Report Map (0x2a4b):**
```c
local_3e = 0x2a4b;       // UUID: Report Map
local_2c = 0x40000;      // Properties: Read
local_30 = param_1 + 0x7c;  // Report map data pointer
FUN_0001cef4(param_1, 2, &local_3c);
```

**Protocol Mode (0x2a4e):**
```c
local_3e = 0x2a4e;       // UUID: Protocol Mode
local_2c = 0x80000;      // Properties: Read + Write Without Response
FUN_0001cef4(param_1, 4);  // Register with Write
```

**Boot Keyboard Input Report (0x2a33) — optional:**
```c
if (*(char *)(param_2 + 0xdc) != '\0') {
    local_3e = 0x2a33;   // UUID: Boot Keyboard Input Report
    local_2c = 0x40000;  // Properties: Read (+ Notify via CCCD)
    FUN_0001cef4(param_1, 0x12, &local_3c);
    FUN_0001d09c(param_1, piVar8, 0xc);      // CCCD descriptor
}
```

**Consumer Control (0x2a22) — optional:**
```c
if (*(char *)((int)param_2 + 0x1b9) != '\0') {
    local_3e = 0x2a22;   // UUID: Consumer Control
    FUN_0001cef4(param_1, 0x12, &local_3c);
    FUN_0001d09c(param_1, piVar8, 0xc);      // CCCD descriptor
    local_3e = 0x2a32;   // UUID: Report Reference
    FUN_0001cef4(param_1, 0xe, &local_3c);  // Read descriptor
}
```

#### Battery Service (0x180F)
- Battery Level Characteristic (0x2a19)
- Referenced via `"BAS Notifications %s"` string
- Config: `CONFIG_BT_SETTINGS_CCC_STORE_MAX`

#### Device Information Service (0x180A) — stored in settings
- `"Failed to read device name from storage"`, `"Failed to read firmware revision from storage"`, `"Failed to read manufacturer from storage"`, `"Failed to read model from storage"`, `"Failed to read serial number from storage"`, `"Failed to read software revision from storage"`

#### Service Registration Order
1. Primary Service Declaration (0x2800) for each service
2. Characteristics with their declarations (0x2803)
3. Descriptors (CCCD 0x2902, Report Reference 0x2908, etc.)
4. Each attribute registered via `bt_gatt_pool` functions

### CCCD / Notification Logic

#### CCCD Write Handlers

**`FUN_00026894` (size 226) — CCCD Write Handler 1:**
- Checks `FUN_0003f76c` (verify connection state)
- Checks `param_1 + 0xd == 0x07` (ATT opcode check)
- Looks up characteristic via `FUN_000261ec`
- If CCCD write (`param_1 != 0`): sets `*(param_3 + 0x16) = 1` (notifications enabled)
- If CCCD clear (`param_1 == 0`): sets `*(param_3 + 0x16) = 0`
- UUID 0x2902 confirmed as CCCD

**`FUN_00026bb0` (size 218) — CCCD Write Handler 2:**
- Similar to `FUN_00026894` but for a different characteristic set
- Calls `FUN_00026b00` for CCCD processing

#### Notification Gate (`FUN_0003fc3e`, size 264)

Walks the GATT database looking for CCCD (0x2902) descriptors. For each characteristic, checks if CCCD value is non-zero. Returns: `(param_3 & *(ushort*)(puVar3 + 8)) != 0`.

UUID chain: 0x2803 (CharDecl) → 0x2902 (CCCD) → 0x2800/0x2801 (Service)

#### Notification Flow

```
1. Input event occurs (button, stick, trackpad, IMU)
2. Firmware checks: FUN_0003fc3e(conn, char, subscription_type)
   → Walks GATT DB to find CCCD for this characteristic
   → Checks if CCCD value & subscription_mask != 0
3. If subscribed:
   a. Allocate buffer: "Failed to allocate report" (on failure)
   b. Build ATT Handle Value Notification (0x1B)
   c. Send: "Unable to send hid report, %u" (on failure)
   d. Flow control: "No buffer available to send notification" (on backpressure)
4. If NOT subscribed:
   → "Device is not subscribed to characteristic" → skip
```

Relevant strings: `"No buffer available to send notification"`, `"Unable to send hid report, %u"`, `"Failed to send full HID report %d"`, `"Device is not subscribed to characteristic"`, `"BAS Notifications %s"`

### MTU / Data Length / PHY

**MTU:**
- `"ATT MTU exceeded, max %u, wanted %zu"` — enforces MTU limit
- `"No ATT channel for MTU %zu"` — channel allocation depends on negotiated MTU
- `"Report map size exceeds max ATT attribute length"` — Report Map limited by MTU
- `"Too small ATT PDU received"` — minimum PDU validation
- Expected MTU: 23 (default) or negotiated up to 517 (Zephyr default max)

**Data Length Extension (DLE):**
- `"DLE support: %s"` — logged during connection setup
- `"Failed to read DLE max data len"` / `"Failed to set data len (%d)"` — DLE negotiation
- `"ACL data length mismatch (%u != %u)"` — DLE enforcement

**PHY:**
- `"2M PHY support: %s"` — controller checks for 2M PHY support
- `"BLE PHY update"` / `"Tx PHY: %s"` / `"Rx PHY: %s"` — PHY negotiation logged
- `"Failed LE Set PHY (%d)"` — PHY switching attempted
- Controller supports 1M and 2M PHY (likely negotiates 2M for throughput)

### Advertising Configuration

**Advertising Slots:**
- `"Enable slot 0"` / `"Enable slot 1"` — Two advertising slots used
- `"Advertising (%s): id %d"` — Named advertising sets with IDs
- `"Advertising: %d"` — Advertising state logged

**Advertising Types:**
- `"adv-connectable"` — Connectable undirected advertising (primary)
- `"adv-dir-connectable"` — Connectable directed advertising (reconnection)
- `"Advertising timeout"` — Has a timeout (likely for directed ads)

**Advertising Data:**
- Device Name: `"Steam Deck Controller"` (string at `0x48b28`) — stored in settings
- Short Name: `"Steam Ctrl (BT) %s"` (string at `0x492d6`) — `%s` filled with MAC suffix
- Appearance: Likely 0x03C4 (Gamepad) based on HID usage
- Flags: LE General Discoverable + BR/EDR Not Supported
- Service UUIDs: HID Service (0x1812), Battery Service (0x180F)

**Advertising Lifecycle:**
```
1. ST_INITIAL → Enable slot 0 + slot 1
2. Bond updated - start advertising (new bond created)
3. Bond updated - restart advertising (bond changed)
4. Advertising timeout (directed ad timeout → switch to undirected)
5. Connected → Stop advertising
6. Disconnected → Restart advertising
7. "Controller cannot resume connectable advertising (%d)" — Error recovery
```

**Scan Response:**
- `"Cannot get scan response data: %s"` — Scan response data is used, likely full device name + additional service UUIDs

### Connection Management

**Connection Events:**
- `"Connected %s"` — Connection established (logged with address)
- `"Disconnected from %s (reason %u)"` — Disconnection with HCI reason code
- `"Lost connection"` — Unexpected disconnection (link supervision timeout)
- `"Connection event trigger failed"` — BLE event processing failure

**Connection Parameters:**
```
BLE connection parameter update
  connection interval: %u
  connection latency: %u
  connection timeout: %u
```
- `"Suboptimal connection interval"` — Firmware detects non-ideal intervals
- `"BLE connection parameter update request"` — Firmware requests parameter update
- `"Suboptimal connection interval requested"` — Requested interval is suboptimal
- `"Send auto LE param update failed (err %d)"` — Auto-update after connection
- `"Send LE param update failed (err %d)"` — Manual parameter update

**Connection Lifecycle:**
```
1. Advertising → Host scans → Connects
2. "Connected %s" → Notify HIDS about connection: "Failed to notify HIDS about connection %d"
3. SMP Pairing: "Pairing" → "Pairing timeout" (if no response)
4. Connection parameter negotiation (auto LE param update)
5. PHY negotiation: "BLE PHY update" → "Tx PHY: %s" / "Rx PHY: %s"
6. DLE negotiation: "DLE support: %s" → "Failed to set data len (%d)"
7. HID notifications start flowing
8. Disconnection: "Disconnected from %s (reason %u)" or "Lost connection"
9. "Failed to notify HIDS about disconnection %d" → Restart advertising
```

**Pairing and Bonding:**
- `"Pairing"` / `"Pairing timeout"` — Standard pairing with timeout
- `"Bond info for id %d was deleted. Resetting id %d"` — Bond management
- `"Bond updated - ignoring event"` — Duplicate bond events filtered
- `"Bond updated - start advertising"` / `"Bond updated - restart advertising"` — Bond triggers re-advertising
- `"Failed to save keys (err %d)"` — Key storage errors
- `"SMP does not allow a pairing failure at this point. Known issue. Disconnecting instead."` — Known SMP limitation
- `"SC Pair Only Mode selected but LE SC not supported"` — Secure Connections fallback
- `"Refusing new pairing. The old bond has more trust."` — Trust level enforcement
- `"bt/ccc"` — CCC values stored in settings
- `"esb/bond"` / `"esb/bond_2"` — ESB bond storage for wireless dongle

**Security:**
- `"Failed to set required security level"` — Security level enforcement
- `"Failed to set security for bonded peer (%d)"` — Bonded peer security setup
- `"No change to encryption state (encrypt 0x%02x)"` — Encryption monitoring
- `"Calculate LTK failed"` / `"Calculate local DHKey check failed"` / `"Calculate remote DHKey check failed"` — LE Secure Connections crypto

---

## 4. HID Input System

### HID Report Descriptor

Found at firmware offset `0x49a26` (duplicate at `0x49ecb`). Report IDs defined:

| Report ID | Direction | Size | Type | Description |
|-----------|-----------|------|------|-------------|
| 0x40 | Input | ~6B | Mouse | Buttons(2b), X/Y(8b signed relative), Hatswitch, AC Pan |
| 0x41 | Input | 7B | Keyboard | 8 modifier keys + 6 keycodes |
| 0x42 | Input | 53B | Vendor | Vendor-defined input |
| 0x43 | Input | 14B | Vendor | Vendor-defined input |
| 0x44 | Input | 5B | Vendor | Vendor-defined input |
| **0x45** | **Input** | **45B** | **Vendor** | **Main gamepad report** |
| 0x47 | Input | 47B | Vendor | Extended report (not found in descriptor, see notes) |
| 0x79 | Input | 1B | Vendor | Vendor-defined input |
| 0x7B | Input | 12B | Vendor | Vendor-defined input |
| 0x80 | Output | 9B | Vendor | Haptics (rumble) |
| 0x81 | Output | 7B | Vendor | Lizard mode clear |
| 0x82 | Output | 3B | Vendor | Vendor-defined output |
| 0x83 | Output | 9B | Vendor | Vendor-defined output |
| 0x84 | Output | 8B | Vendor | Vendor-defined output |
| 0x85 | Output | 3B | Vendor | Vendor-defined output |
| 0x86 | Output | 3B | Vendor | Vendor-defined output |
| 0x87-0x89 | Output | 63B | Vendor | Large output reports |
| 0x01 | Feature | 63B | Vendor | Command channel |
| 0x02 | Feature | 63B | Vendor | Command channel |

All 0x45 report data uses Usage Page `0xFF00` (vendor) with Usage `0x45`. The report data is entirely vendor-defined — Steam Client interprets the 45 raw bytes based on its own internal knowledge of the SC2 protocol.

**HID report system strings:**
- `"hid_custom"` — Custom HID report channel (separate from standard HID)
- `"hid_stream"` — Streaming HID data thread
- `"Discarded report"` — Invalid/malformed reports dropped
- `"Discarding feature report with unexpected length %d"` — Feature report validation

### Report 0x45 Construction Pipeline

```
Neptune Controller Input (hidraw3)
        │
        ▼
┌──────────────────────────────────────────┐
│  FUN_000167d0 — Main Controller Loop     │
│  (processes trackpads, sticks, IMU)      │
│                                          │
│  ├─ FUN_00011498 (IMU processing)        │
│  │   ├─ FUN_0003ab58 (read IMU axes)     │
│  │   ├─ FUN_00011460 (scale IMU data)    │
│  │   ├─ FUN_00019598/00019530 (filter)   │
│  │   └─ FUN_00014064(local_5c, type=7)   │  ← writes IMU+trackpad data
│  │                                       │
│  ├─ FUN_0004373e (scale trackpad raw)    │
│  │   └─ DAT_00035d50 (calibration data)  │
│  │                                       │
│  ├─ FUN_0001672c(0, ...) — Left trackpad │
│  │   └─ FUN_00014064(local_34)          │  ← writes left trackpad
│  │                                       │
│  ├─ FUN_0001672c(1, ...) — Right trackpad│
│  │   └─ FUN_00014064(local_34)          │  ← writes right trackpad
│  │                                       │
│  ├─ FUN_0003a790(DAT_..., local_2c)      │
│  │   └─ FUN_00014064(local_2c)          │  ← writes stick/trigger data
│  │                                       │
│  └─ FUN_00013fe0() — SEND REPORT         │
│      ├─ FUN_00013858() — check ready     │
│      ├─ FUN_000138d0() — alloc buffer    │
│      ├─ Set report ID: *puVar5 = 0x45    │
│      ├─ Copy 45 bytes from state buffer  │
│      └─ FUN_00013980() — BLE notify     │
└──────────────────────────────────────────┘
```

**Note on offset 0x00**: The copy loop in `FUN_00013fe0` copies 45 bytes from `DAT_00014058` to the report buffer (after the Report ID byte). The first byte is a sequence counter that increments with each report.

### Report 0x45 — Byte Layout (45 Bytes)

Built by `FUN_00014064` which writes to a state structure at `DAT_00014200`. The report buffer is copied as-is by `FUN_00013fe0`.

```
Offset  Size  Field                    Source Type
──────  ────  ───────────────────────  ──────────────
0x00    1B    Sequence counter         Incremented each report send
0x01    4B    Flags + Button bitmask   Case 4: 20-bit buttons + 12-bit flags
0x05    2B    Left trigger             Case 2: uint16 (0-0xFFFF)
0x07    2B    Right trigger            Case 3: uint16 (0-0xFFFF)
0x09    2B    Left stick X             Case 0: int16 (signed)
0x0B    2B    Left stick Y             Case 0: int16 (signed)
0x0D    2B    Right stick X            Case 1: int16 (signed)
0x0F    2B    Right stick Y            Case 1: int16 (signed)
0x11    2B    Gyroscope X              Case 5: uint16
0x13    2B    Gyroscope Y              Case 5: uint16
0x15    2B    Gyroscope Z              Case 5: uint16
0x17    2B    Accelerometer X          Case 6: uint16
0x19    2B    Accelerometer Y          Case 6: uint16
0x1B    2B    Accelerometer Z          Case 6: uint16
0x1D    4B    Trackpad left X/Y        Case 7: 2x int16
0x21    4B    Trackpad left X2/Y2      Case 7: 2x int16
0x25    2B    Trackpad left touch      Case 7: uint16
0x27    4B    Trackpad right X/Y       Case 7: 2x int16
0x2B    2B    Trackpad right touch     Case 7: uint16
──────  ────
Total:  0x2D = 45 bytes
```

### Flags Word (Offset 0x01)

```
Bit  0-19:  Button bitmask (20 bits)
Bit 20:     Accelerometer active/touch flag
Bit 21:     Accelerometer secondary flag
Bit 22:     (unused or reserved)
Bit 23:     Right trigger active (set when trigger > 0)
Bit 24:     Gyroscope active/touch flag
Bit 25:     Gyroscope secondary flag
Bit 26:     (unused or reserved)
Bit 27:     Left trigger active (set when trigger > 0)
Bit 28:     Accelerometer mode flag
Bit 29:     Gyroscope mode flag
Bit 30-31:  (unused or reserved)
```

### Command Types (`FUN_00014064`)

`FUN_00014064` (470B) is the central state update function. Receives: `param_1[0]` = command type (0-7), `param_1[4..]` = payload.

**Case 0: Left Stick** — Writes int16 X/Y to offsets 0x09, 0x0B. Deadzone: if `max(|X|, |Y|) < 0xfa1 (4001)`, set deadzone flag = 0.

**Case 1: Right Stick** — Same as Case 0, offsets 0x0D, 0x0F.

**Case 2: Left Trigger** — Writes uint16 to offset 0x05. Sets bit 27 (`0x8000000`) in flags if trigger != 0.

**Case 3: Right Trigger** — Writes uint16 to offset 0x07. Sets bit 23 (`0x800000`) in flags if trigger != 0.

**Case 4: Buttons (20-bit bitmask)** — Lower 20 bits become button state:
```
Bit 0:   QAS (quick access)
Bit 1:   Dpad Up
Bit 2:   Dpad Down
Bit 3:   Dpad Left
Bit 4:   Dpad Right
Bit 5:   A (Right lower grip)
Bit 6:   B (Right upper grip)
Bit 7:   X (Left lower grip)
Bit 8:   Y (Left upper grip)
Bit 9:   Left Bumper
Bit 10:  Right Bumper
Bit 11:  Left View (Select/Back)
Bit 12:  Right View (Start)
Bit 13:  Left Thumbstick click
Bit 14:  Right Thumbstick click
Bit 15:  Steam button
Bit 16:  Left upper grip (L4)
Bit 17:  Left lower grip (L5)
Bit 18:  Right upper grip (R4)
Bit 19:  Right lower grip (R5)
```
*Inferred from firmware string order at `0x50d90` and SC2 protocol conventions.*

**Case 5: Gyroscope** — Writes 3x int16 (X, Y, Z) to offsets 0x11, 0x13, 0x15. Flags: bit 24 (gyro active), bit 29 (gyro mode), bit 25 (gyro secondary), bit 26 (gyro additional).

**Case 6: Accelerometer** — Writes 3x int16 (X, Y, Z) to offsets 0x17, 0x19, 0x1B. Flags: bit 20 (accel active), bit 28 (accel mode), bit 21 (accel secondary), bit 22 (accel additional).

**Case 7: Trackpad + IMU combined** — Writes multi-field data to offsets 0x1D-0x2C:
```c
*(undefined4 *)(DAT_00014200 + 0x1d) = *(undefined4 *)(param_1 + 4);  // 4B trackpad L X/Y
*(undefined4 *)(iVar5 + 0x21) = uVar9;                                  // 4B trackpad L X2/Y2
*(undefined2 *)(iVar6 + 4) = *(undefined2 *)(param_1 + 0xc);            // 2B trackpad L touch
*(undefined4 *)(iVar5 + 0x27) = *(undefined4 *)(param_1 + 0xe);         // 4B trackpad R X/Y
*(undefined2 *)(iVar6 + 10) = *(undefined2 *)(param_1 + 0x12);          // 2B trackpad R touch
```

### Analog Input Processing (Calibration/Scaling)

**`FUN_0004373e` — Trackpad Calibration (126 bytes):** Converts raw ADC/SPI sensor values to 0-0xFFFF unsigned range using FPU:
```c
uint FUN_0004373e(uint raw_value) {
    int max_raw = (1 << *(byte*)(DAT_00035d50 + 0x60)) - 1;
    int result = FUN_000436be(DAT_00035d50 + 0x50, &max_raw);
    if (result != 0) return 0;
    float scaled = raw_f / (max_f / range_f);
    if (scaled <= 0.0) return 0;
    if (scaled >= DAT_00035d54) return 0xFFFF;
    return VectorFloatToUnsigned(scaled, 3) & 0xFFFF;
}
```

**`FUN_00043746` — IMU/Secondary Calibration (8 bytes):** Same algorithm but reads calibration from `DAT_00035d50 + 0x10`.

**Calibration Data Structure (`DAT_00035d50`):**
- Offset `0x10`: Bit width for IMU ADC resolution
- Offset `0x50`: IMU calibration parameters
- Offset `0x60`: Bit width for trackpad ADC resolution
- Both functions use ARM Cortex-M4F FPU intrinsics (`VectorSignedToFloat`, `VectorFloatToUnsigned`)

### Trackpad Data Processing

**Trackpad Touch Pipeline (in `FUN_000167d0`):**
```
Trackpad state entries at DAT_00016a78:
  Entry 0 (left):  offset -8 (mode), -6 (X raw), -4 (Y raw), -2 (touch)
  Entry 1 (right): offset +0x30 (mode), +0x32 (X raw), +0x34 (Y raw), +0x36 (touch)

Processing:
1. Check mode == 2 or 0x81 (active touch)
2. Scale X/Y through FUN_0004373e (trackpad calibration)
3. Get deadzone threshold via FUN_00013c30(0x44)
4. Call FUN_00015170 for touch event generation
```

### Trackpad Calibration Offset (in `FUN_000167d0`)

For each trackpad touch point:
1. Read raw X/Y from sensor struct
2. Add calibration offset from psVar20[] (left) or psVar25[] (right)
3. Clamp to [1, 0xFFFF] range
4. Byte-swap via FUN_00043746
5. Validate via FUN_0003a596 (checks all 4 corners have valid range)

**`FUN_00015170` — Trackpad Touch Event (144 bytes):** Generates haptic/event trigger when trackpad is touched. If `|X - Y| > 99`, touch active, generates event via `FUN_0003347c` (haptic trigger on touch).

**`FUN_0001672c` — Individual Trackpad Handler (154 bytes):** Called for left (param_1=0) and right (param_1=1). Query mode via `FUN_00013c30(0x2e)`. Mode 1: raw passthrough via `FUN_00035ea8`. Mode 2: direct assignment. Otherwise: apply calibration via `FUN_0003a600`.

### IMU Data Processing

**IMU Processing Chain (`FUN_00011498`, 546 bytes):**
```
1. Read 3-axis raw data via FUN_0003ab58(device, 3, &data) → raw X, Y, Z as int32
2. Scale to angular rate: X_scaled = FUN_00011460(&raw_X) / 0x3d  (÷61)
3. Apply sensor fusion filter: FUN_00019598 (gyro calibration), FUN_00019530 (bias correction)
4. Read accelerometer via FUN_0003ab58(device, 7, &data), scale factor / 0x17d7 (÷6103)
5. Gyro drift compensation: fPrevGyro / fTimeDelta + fNewGyro → stored in pfVar4[0..2]
6. Read additional accel via FUN_0003ab58(device, 8, &data)
7. Read temp/secondary via FUN_0003ab58(device, 0x40, &data)
8. Build command struct (type=7, accel X/Y/Z, gyro, additional, temp) → FUN_00014064
```

**IMU Sensor Access IDs:**

| ID  | Sensor | Description |
|-----|--------|-------------|
| 3   | Gyroscope | Raw 3-axis gyroscope data |
| 7   | Accelerometer | Primary 3-axis accelerometer data |
| 8   | Accelerometer | Secondary accelerometer (or temperature-compensated) |
| 0x3d | Bias | Gyroscope bias calibration data |
| 0x3e | Mode | IMU mode query |
| 0x40 | Secondary | Additional sensor data (temperature?) |

**IMU Calibration Strings:**
```
cal/sensors/gyroscope/bias       — Gyro bias calibration file
settings/sensors/imu             — IMU settings root
settings/sensors/imu/gyro_threshold  — Gyro deadzone threshold
settings/sensors/imu/mode        — IMU operating mode
settings/sensors/imu/mounting_matrix  — Board mounting orientation matrix
settings/sensors/imu/use_bias    — Whether to apply bias correction
gyro_dz_threshold               — Gyro deadzone threshold value
```

### Report Send Cycle (`FUN_000167d0`)

The main controller loop runs at a fixed rate (likely 100-200Hz):
```
Loop iteration:
1. Process left/right trackpad touch events (2 entries, stride 0x3c)
2. Process trackpad raw data (2 entries, stride 0xd4) with calibration
3. Wait for synchronization via FUN_00036a2c
4. Process timing synchronization via FUN_00036a2c
5. Read timing via thunk_FUN_00037978
6. Generate trackpad touch events via FUN_00043726
7. Send trackpad left/right via FUN_0001672c(0/1, ...)
8. Send stick/trigger data via FUN_0003a790 × 2
9. SEND REPORT: FUN_00013fe0()
10. Send haptic event: FUN_00013b68()
11. Measure cycle time, repeat (65 cycles per super-frame at 0x41)
```

### Architectural Notes

1. **Single state buffer pattern**: All input sources write to a single shared 45-byte state buffer at `DAT_00014200` via `FUN_00014064`. This buffer is copied atomically (with IRQ masking via `setBasePriority(0x40)`) to the BLE TX buffer in `FUN_00013fe0`.

2. **IRQ protection**: `FUN_00013fe0` masks interrupts to priority 0x40 during the 45-byte copy to prevent tearing from concurrent `FUN_00014064` writes.

3. **Sequence counter**: The first byte of the state buffer is a sequence counter incremented after each report send, allowing the host to detect missed reports.

4. **Observer pattern**: After state updates, `FUN_00014064` iterates an observer table (`DAT_00015284`-`DAT_00015288`) to notify registered callbacks of state changes. Each observer has an activation mask and start/stop callbacks.

5. **No Report ID in descriptor sub-fields**: The HID descriptor declares report 0x45 as a flat 45-byte vendor blob. Steam Client internally parses the bytes based on its own protocol knowledge.

6. **Calibration applied in firmware**: All analog values (sticks, triggers, IMU, trackpad) are calibrated and scaled to standard ranges (0-0xFFFF unsigned, signed int16 for sticks) before being placed in the report buffer.

---

## 5. Command Dispatch

### Overview

- **95 command codes** in the main dispatch switch (`FUN_000383c4`)
- **5 additional commands** handled outside the main table (0x81, 0x84, 0x85, 0x87, 0xAE)
- **100 total commands** identified from firmware RE
- **25 commands** have response format definitions in `FUN_0000c55c`
- **86 commands** (0x00–0x55) have size entries in the command size lookup table

### Key Functions

| Address | Size | Function |
|---------|------|----------|
| `0x000383c4` | 426 | Main command dispatch (TBH jump table) |
| `0x0000c55c` | 538 | Response formatter (BLE-side) |
| `0x00013c30` | 14 | Command size lookup (codes 0x00–0x55) |
| `0x00013d10` | — | Descriptor table pointer → `0x2000b070` (RAM, 12-byte entries × 86) |
| `0x00013c40` | — | Size table pointer → `0x2000d168` (RAM, short × 86 entries) |

### TBH Jump Table

The dispatch uses a Thumb `tbh [pc, r3, lsl 1]` instruction at `0x000383ce`. The 144-entry halfword table starts at `0x000383d2`. Each entry is a 2-byte offset from the table base (`target = 0x383d2 + entry * 2`).

| Code Range | Entries | Default Handler | Notes |
|------------|---------|-----------------|-------|
| 0x00–0x24 | Unique per command | — | System commands, all unique handlers |
| 0x25–0x2c | 0x014c → `0x03866a` | Default | Unhandled |
| 0x2d–0x2e | Unique | — | Config |
| 0x2f–0x3b | 0x014c → `0x03866a` | Default | Unhandled |
| 0x3c–0x47 | Unique | — | Config + input |
| 0x48–0x49 | 0x014c → `0x03866a` | Default | Unhandled |
| 0x4a | Unique | — | LED GET |
| 0x4b–0x4c | 0x014c → `0x03866a` | Default | Unhandled |
| 0x4d | Unique | — | LED SET |
| 0x4e–0x52 | 0x014c → `0x03866a` | Default | Unhandled |
| 0x53–0x58 | Unique | — | Config |
| 0x59 | 0x014c → `0x03866a` | Default | Unhandled |
| 0x5a–0x5c | Unique | — | Config |
| 0x5d–0x5e | 0x014c → `0x03866a` | Default | Unhandled |
| 0x5f | Unique (0x0144 → `0x03865a`) | — | Config |
| 0x60–0x67 | 0x014c → `0x03866a` | Default | Unhandled |
| 0x68–0x71 | Unique | — | Calibration |
| 0x72 | Unique (0x00ec → `0x0385aa`) | — | Calibration |
| 0x73–0x7e | Unique | — | Calibration + battery |
| 0x7f | Unique (0x013a → `0x038646`) | — | Battery |
| 0x80 | Unique (0x0136 → `0x03863e`) | — | **Haptic motor output** |
| 0x81–0x85 | 0x014c → `0x03866a` | Default | Handled outside dispatch |
| 0x86 | Unique (0x00d6 → `0x03857e`) | — | Config |
| 0x87–0x89 | 0x014c → `0x03866a` | Default | 0x87 handled outside dispatch |
| 0x8a | Unique (0x00e8 → `0x0385a2`) | — | Firmware |
| 0x8b–0x8e | Unique | — | Firmware |
| 0x8f | Unique (0x0142 → `0x038656`) | — | **Sub-command dispatcher** |

### Complete Command Table

#### System / Query (0x00–0x24)

| Code | Handler Descriptor | Category | Description | Response Format |
|------|-------------------|----------|-------------|----------------|
| `0x00` | `DAT_00038690` → `0x59b10` | system | **NOOP / Ping** — Returns default descriptor | No |
| `0x01` | `DAT_00038814` → `0x59b18` | system | **GET serial/info type 1** | ✅ Response code `0x0c`, 8 bytes |
| `0x02` | `DAT_00038694` → `0x59b22` | system | **GET device ID / version** | ✅ Response code `0x1a`, 1 byte |
| `0x03` | `DAT_00038698` → `0x59b3c` | system | **System query 3** | No |
| `0x04` | `DAT_0003869c` → `0x59b4c` | system | **GET settings value (subset)** | ✅ Response code `0x3e`, sub `0x04`, 20 bytes |
| `0x05` | `DAT_000386a0` → `0x59b64` | system | **GET settings value (short)** | ✅ Response code `0x3e`, sub `0x0c`, 4 bytes |
| `0x06` | `DAT_000386a4` → `0x59b6e` | system | **GET settings value (3-byte)** | ✅ Response code `0x3e`, sub `0x0a`, 3 bytes |
| `0x07` | `DAT_000386a8` → `0x59b88` | system | **GET settings value (5-byte)** | ✅ Response code `0x3e`, sub `0x0d`, 5 bytes |
| `0x08` | `DAT_000386ac` → `0x59b9a` | system | **GET controller mode/state** | ✅ Response code `0x05`, 4 bytes |
| `0x09` | `DAT_000386b4` → `0x59bc5` | system | **GET battery level** | ✅ Response code `0x08`, 4 bytes |
| `0x0a` | `DAT_000386b8` → `0x59bd5` | system | **GET firmware version** | ✅ Response code `0x30`, 3 bytes |
| `0x0b` | `DAT_000386c0` → `0x59bfe` | system | **GET multi-byte setting** | ✅ Response code `0x13`, variable (`count*4+1`) |
| `0x0c` | `DAT_000386c4` → `0x59c10` | system | **GET 2-byte config** | ✅ Response code `0x57`, 2 bytes |
| `0x0d` | `DAT_000386c8` → `0x59c21` | system | **GET controller type** — validates `param_2[3:5] == 0x2083` | ✅ Response code `0x0e`, 6 bytes |
| `0x0e` | `DAT_000386cc` → `0x59c33` | system | **System query 0x0e** | No |
| `0x0f` | `DAT_000386d0` → `0x59c3f` | system | **System query 0x0f** | No |
| `0x10` | `DAT_000386d4` → `0x59c55` | system | **GET controller state** — Complex, 19 or 31 bytes. Checks `FUN_00000d5c(0x29)` for variant | ✅ Complex, 19 or 31 bytes |
| `0x11` | `DAT_000386d8` → `0x59c6d` | system | **GET config type 11** | ✅ Response code `0x3e`, sub `0x07` |
| `0x12` | `DAT_000386dc` → `0x59c79` | system | **GET config type 12** | ✅ Response code `0x3e`, sub `0x0c` |
| `0x13` | `DAT_000386e0` → `0x59c8b` | system | **GET config type 13** | ✅ Response code `0x3e`, sub `0x12` |
| `0x14` | `DAT_000386e4` → `0x59c9a` | system | **GET config type 14** | ✅ Response code `0x3e`, sub `0x13` |
| `0x15` | `DAT_000386f0` → `0x59cd6` | system | **GET config type 15** | ✅ Response code `0x3e`, sub `0x20` |
| `0x16` | `DAT_000386f4` → `0x59ce5` | system | **GET config type 16** | ✅ Response code `0x3e`, sub `0x21` |
| `0x17` | `DAT_00038700` → `0x59d38` | system | **GET extended info 17** | ✅ Response code `0xff`, sub `0xa2`, 11 bytes |
| `0x18` | `DAT_00038704` → `0x59d56` | system | **GET extended info 18** | ✅ Response code `0xff`, sub `0xa3`, 5 bytes |
| `0x19` | `DAT_00038708` → `0x59d76` | system | **GET firmware/hardware info** | ✅ Response code `0xff`, sub `0x80`, 13 bytes |
| `0x1a` | `DAT_0003870c` → `0x59d8d` | system | **System query 0x1a** | No |
| `0x1b` | `DAT_00038710` → `0x59d9c` | system | **System query 0x1b** | No |
| `0x1c` | `DAT_00038718` → `0x59dbf` | system | **System query 0x1c** | No |
| `0x1d` | `DAT_00038720` → `0x59de5` | system | **System query 0x1d** | No |
| `0x1e` | `DAT_00038724` → `0x59df2` | system | **System query 0x1e** | No |
| `0x1f` | `DAT_00038728` → `0x59e08` | system | **System query 0x1f** | No |
| `0x20` | `DAT_0003872c` → `0x59e17` | system | **System query 0x20** | No |
| `0x21` | `DAT_00038730` → `0x59e23` | system | **System query 0x21** | No |
| `0x22` | `DAT_00038734` → `0x59e52` | system | **System query 0x22** | No |
| `0x23` | `DAT_00038738` → `0x59e63` | system | **System query 0x23** | No |
| `0x24` | `DAT_0003873c` → `0x59e7e` | system | **System query 0x24** | No |

#### Config (0x25–0x5f)

| Code | Handler Descriptor | Category | Description | Response Format |
|------|-------------------|----------|-------------|----------------|
| `0x2d` | `DAT_00038744` → `0x59ea7` | config | **Config 0x2d** | No |
| `0x2e` | `DAT_0003874c` → `0x59ec7` | config | **Config 0x2e** — Used by `FUN_00013c30(0x2e)` for controller state sizing | No |
| `0x3c` | `DAT_00038750` → `0x59ecf` | config | **Config 0x3c** | No |
| `0x3d` | `DAT_000387b0` → `0x5a113` | config | **Config 0x3d** | No |
| `0x3e` | `DAT_00038754` → `0x59edc` | config | **GET/SET settings values** — String refs: `ID_GET_SETTINGS_VALUES`, `ID_SET_SETTINGS_VALUES` | ✅ (response prefix `0x3e`) |
| `0x3f` | `DAT_00038758` → `0x59ef1` | config | **Config 0x3f** | No |
| `0x40` | `DAT_0003875c` → `0x59f05` | config | **Config 0x40** | No |
| `0x41` | `DAT_00038760` → `0x59f23` | config | **Config 0x41** | No |
| `0x42` | `DAT_00038764` → `0x59f2e` | config | **Config 0x42** | No |
| `0x43` | `DAT_00038768` → `0x59f41` | config | **Config 0x43** | No |
| `0x44` | `DAT_0003876c` → `0x59f59` | config | **Config 0x44** — Used by `FUN_00013c30(0x44)` | No |
| `0x53` | `DAT_00038788` → `0x59fca` | config | **Config 0x53** | No |
| `0x54` | `DAT_0003878c` → `0x59ff0` | config | **Config 0x54** | No |
| `0x55` | `DAT_00038790` → `0x5a015` | config | **Config 0x55** | No |
| `0x56` | `DAT_00038794` → `0x5a035` | config | **Config 0x56** | No |
| `0x57` | `DAT_00038798` → `0x5a073` | config | **Config 0x57** | No |
| `0x58` | `DAT_0003879c` → `0x5a099` | config | **Config 0x58** | No |
| `0x5a` | `DAT_000387a0` → `0x5a0b2` | config | **Config 0x5a** | No |
| `0x5b` | `DAT_000387a4` → `0x5a0c6` | config | **Config 0x5b** | No |
| `0x5c` | `DAT_000387a8` → `0x5a0e1` | config | **Config 0x5c** | No |
| `0x5f` | `DAT_000387f8` → `0x5a2d9` | config | **Config 0x5f** | No |

#### Input Reports (0x45–0x47)

| Code | Handler Descriptor | Category | Description |
|------|-------------------|----------|-------------|
| `0x45` | `DAT_00038770` → `0x59f69` | input | **Standard gamepad input report (12 bytes)** — Sticks, triggers, buttons. Primary HID input. |
| `0x46` | `DAT_00038774` → `0x59f77` | input | **Alternate input report** |
| `0x47` | `DAT_00038778` → `0x59f8b` | input | **Extended input report (45 bytes)** — SC2 custom with trackpads, IMU, force sensors. |

#### LED (0x4a, 0x4d)

| Code | Handler Descriptor | Description |
|------|-------------------|-------------|
| `0x4a` | `DAT_00038780` → `0x59fab` | **GET LED color** — String: `ID_GET_LED_COLOR` |
| `0x4d` | `DAT_00038784` → `0x59fbe` | **SET LED color** — String: `ID_SET_LED_COLOR` |

#### Calibration (0x68–0x79)

| Code | Handler Descriptor | Description |
|------|-------------------|-------------|
| `0x68` | `DAT_000387cc` → `0x5a1d4` | **Touch calibration (right)** — `cal/touch_r` |
| `0x69` | `DAT_000387ac` → `0x5a0f9` | **Touch calibration (left)** — `cal/touch_l` |
| `0x6a` | `DAT_000387b4` → `0x5a11b` | **Pressure calibration (right)** — `cal/prs_r` |
| `0x6b` | `DAT_000387b8` → `0x5a14b` | **Pressure calibration (left)** — `cal/prs_l` |
| `0x6c` | `DAT_000387bc` → `0x5a16a` | **Calibration 0x6c** |
| `0x6d` | `DAT_000387c0` → `0x5a189` | **Calibration 0x6d** |
| `0x6e` | `DAT_000387c4` → `0x5a1a0` | **Calibration 0x6e** |
| `0x6f` | `DAT_000387c8` → `0x5a1c1` | **Calibration 0x6f** |
| `0x70` | `DAT_000387d0` → `0x5a1ed` | **Calibration 0x70** |
| `0x71` | `DAT_000387d8` → `0x5a21a` | **Calibration 0x71** |
| `0x72` | `DAT_00038748` → `0x59eb0` | **Calibration 0x72** |
| `0x73` | `DAT_000386f8` → `0x59cf6` | **Calibration 0x73** |
| `0x74` | `DAT_00038804` → `0x5a332` | **Calibration 0x74** |
| `0x75` | `DAT_000386e8` → `0x59caa` | **Calibration 0x75** |
| `0x76` | `DAT_00038714` → `0x59dab` | **Calibration 0x76** |
| `0x77` | `DAT_000386ec` → `0x59cb7` | **Calibration 0x77** |
| `0x78` | `DAT_000386b0` → `0x59bac` | **Calibration 0x78** |
| `0x79` | `DAT_000386bc` → `0x59be1` | **Calibration 0x79** |

#### Battery / Power (0x7a–0x7f)

| Code | Handler Descriptor | Description |
|------|-------------------|-------------|
| `0x7a` | `DAT_00038800` → `0x5a321` | **Battery 0x7a** — Fuel gauge / battery status |
| `0x7b` | `DAT_0003877c` → `0x59f9a` | **Battery 0x7b** — Power management |
| `0x7c` | `DAT_000387e0` → `0x5a253` | **Battery 0x7c** |
| `0x7d` | `DAT_000387d4` → `0x5a204` | **Battery 0x7d** |
| `0x7e` | `DAT_000386fc` → `0x59d1a` | **Battery 0x7e** |
| `0x7f` | `DAT_000387e4` → `0x5a26d` | **Battery 0x7f** |

#### Haptic (0x80)

| Code | Handler Descriptor | Description |
|------|-------------------|-------------|
| `0x80` | `DAT_000387dc` → `0x5a23b` | **SET haptic/rumble output** — String: `Failed to set haptics master gain` nearby |

#### Config / Mapping (0x86)

| Code | Handler Descriptor | Description |
|------|-------------------|-------------|
| `0x86` | `DAT_0003871c` → `0x59dd7` | **Config / Mapping 0x86** |

#### Firmware / Bootloader (0x8a–0x8f)

| Code | Handler Descriptor | Description |
|------|-------------------|-------------|
| `0x8a` | `DAT_00038740` → `0x59e91` | **GET setting label** — `ID_GET_SETTING_LABEL`, string: `t/dis/fw` |
| `0x8b` | `DAT_000387fc` → `0x5a2fb` | **GET settings max values** — `ID_GET_SETTINGS_MAXS` |
| `0x8c` | `DAT_000387e8` → `0x5a289` | **GET default settings** — `ID_GET_SETTINGS_DEFAULTS` |
| `0x8d` | `DAT_000387ec` → `0x5a29c` | **SET controller mode** — `ID_SET_CONTROLLER_MODE` (lizard ↔ Steam Input) |
| `0x8e` | `DAT_000387f0` → `0x5a2b2` | **Load default settings** — `ID_LOAD_DEFAULT_SETTINGS` |
| `0x8f` | `DAT_000387f4` → `0x5a2c6` | **0x8F sub-command dispatcher** — Main haptic/mapping command router. Handler at `0x54368`. |

### Commands NOT in Main Dispatch Table

| Code | Name | Direction | Description | Source |
|------|------|-----------|-------------|--------|
| `0x81` | ID_CLEAR_DIGITAL_MAPPINGS | Host→Device | Clear mappings (exit lizard mode). Sent periodically. | Steam client RE |
| `0x84` | ID_GET_ATTRIBUTE_LABEL | Host→Device | Get attribute label/description string | steamclient.so |
| `0x85` | ID_SET_DEFAULT_DIGITAL_MAPPINGS | Host→Device | Set default button mappings | steamclient.so |
| `0x87` | ID_SET_SETTINGS_VALUES | Host→Device | Set controller settings (alternate path) | Steam client RE |
| `0xAE` | ID_GET_SERIAL | Bidirectional | Get controller serial number (BLE co-processor level) | Steam client RE |

### Response Formatter Details (`FUN_0000c55c`)

Handles **25 command codes** for BLE-to-controller IPC response construction:

| Input Code | Response Code | Sub/Length | Notes |
|:----------:|:-------------:|:----------:|-------|
| `0x01` | `0x0c` | 8 bytes | Fixed payload |
| `0x02` | `0x1a` | 1 byte | Single byte response |
| `0x04` | `0x3e` | sub `0x04`, 20 bytes | Settings data |
| `0x05` | `0x3e` | sub `0x0c`, 4 bytes | Settings data |
| `0x06` | `0x3e` | sub `0x0a`, 3 bytes | Settings data |
| `0x07` | `0x3e` | sub `0x0d`, 5 bytes | Settings data |
| `0x08` | `0x05` | 4 bytes | Controller state |
| `0x09` | `0x08` | 4 bytes | Battery/status |
| `0x0a` | `0x30` | 3 bytes | Version info |
| `0x0b` | `0x13` | variable (`count*4+1`) | Multi-value settings |
| `0x0c` | `0x57` | 2 bytes | 16-bit config |
| `0x0d` | `0x0e` | 6 bytes | Validates magic `0x2083` |
| `0x10` | `0x3e` | 19 or 31 bytes | Complex controller state |
| `0x11`–`0x16` | `0x3e` | sub `0x07`–`0x21` | Config data |
| `0x17` | `0xff` | sub `0xa2`, 11 bytes | Extended info |
| `0x18` | `0xff` | sub `0xa3`, 5 bytes | Extended info |
| `0x19` | `0xff` | sub `0x80`, 13 bytes | Firmware/hardware info |
| `0x82` | `0xff` | error `0x0d` | GET_DIGITAL_MAPPINGS error |
| `0x83` | `0xff` | error `0x02` | GET_ATTRIBUTES error |

### 0xf2 ACK Format

The 0xf2 response is a minimal 6-byte ACK sent after 0xe7 (mapping) commands:
```
[0x01, 0x00, 0x00, 0x00, 0x00, 0xf2]
 count   (zeroed)              type
```

Built by `FUN_00042132` at `0x00042132`. Response family:

| Function | Type Byte | Payload | Purpose |
|----------|-----------|---------|---------|
| `FUN_000420ae` | 0xf0 | MAC + UUID (20B) | Identity response |
| `FUN_00042132` | 0xf2 | None | **Mapping ACK** |
| `FUN_0004214a` | 0xf3 | Mode byte (1B) | Mode notification |
| `FUN_00042108` | 0xf4 | Timestamp + model (20B) | Status response |

### Dispatch Callers

The dispatch at `0x000383c4` is called by 1 tail-call wrapper at `0x445f2` (sets r1=0, r2=0, branches to dispatch). Two higher-level callers:

| Caller | Address | Call Sites | Purpose |
|--------|---------|------------|---------|
| `fcn.000180a8` | `0x180c8` | 1 | Controller command processing |
| `fcn.00018320` | `0x183ac`, `0x1841a` | 2 | Controller command processing |

Both follow: lookup command type → negate → call dispatch via wrapper → build 0x20-byte message structure `[flags=0x1000003, msg_id, descriptor_ptr, buf_size=0x200]` → submit to event system via `fcn.0001b07c`.

### String References (Command Identification)

| String | Address | Likely Command | Category |
|--------|---------|----------------|----------|
| `CLEAR DIGITAL MAPPINGS` | `0x49028` | `0x81` | Mapping |
| `%s: GET: ID_GET_LED_COLOR` | `0x48e95` | `0x4a` | LED |
| `%s: SET: ID_SET_LED_COLOR` | `0x48eaf` | `0x4d` | LED |
| `%s: SET: ID_TURN_OFF` | `0x48f16` | (not in table) | Power |
| `%s: SET: ID_REBOOT_INTO_ISP` | `0x48f2b` | (not in table) | Firmware |
| `%s: SET: ID_FIRMWARE_UPDATE_REBOOT` | `0x48f47` | (not in table) | Firmware |
| `%s: SET: ID_SET_USER_STORE` | `0x48faf` | (not in table) | Config |
| `%s: GET: ID_GET_USER_STORE` | `0x48fe1` | (not in table) | Config |
| `%s: GET: ID_LOAD_DEFAULT_SETTINGS_VALUES` | `0x4903f` | `0x8e` | Config |
| `%s: GET: ID_GET_SETTINGS_VALUES` | `0x49068` | `0x3e` | Config |
| `%s: SET: ID_SET_SETTINGS_VALUES` | `0x49088` | `0x3e` | Config |
| `%s: GET: ID_GET_ATTRIBUTES_VALUES` | `0x50f62` | `0x8f` sub | Firmware |
| `%s: GET: ID_GET_STRING_ATTRIBUTE, TAG: %u` | `0x50fa4` | `0x8f` sub | Firmware |
| `settings/haptics/haptic_master_gain_db` | `0x4861e` | Haptic config | Haptic |
| `settings/haptics/enabled` | `0x48667` | Haptic config | Haptic |
| `settings/sensors/imu/mode` | `0x490c1` | IMU config | Config |
| `cal/touch_r` | `0x489a7` | `0x68` | Calibration |
| `cal/touch_l` | `0x489b3` | `0x69` | Calibration |
| `cal/prs_r` | `0x489bf` | `0x6a` | Calibration |
| `cal/prs_l` | `0x489c9` | `0x6b` | Calibration |
| `esb/bond` | `0x49177` | ESB bonding | System |
| `controller_settings` | `0x48810` | Settings namespace | Config |

### Gaps in the Dispatch Table

| Range | Count | Notes |
|-------|:-----:|-------|
| `0x25–0x2c` | 8 | Config gap |
| `0x2f–0x3b` | 13 | Config gap |
| `0x48–0x49` | 2 | Input gap |
| `0x4b–0x4c` | 2 | LED gap |
| `0x4e–0x52` | 5 | Config gap |
| `0x59` | 1 | Config gap |
| `0x5d–0x5e` | 2 | Config gap |
| `0x60–0x67` | 8 | Calibration gap |
| `0x81–0x85` | 5 | Firmware/mapping gap (some handled outside table) |
| `0x87–0x89` | 3 | Firmware gap (0x87 handled outside table) |
| `0x90–0xff` | 112 | Upper range (some documented: 0x9F, 0xA1, 0xAE, 0xBA, 0xF2) |

### Architecture Notes

1. **DAT_ values are NOT handler function pointers** — They point to descriptor structures in flash/RAM beyond the binary extract (addresses `0x59b10`–`0x5a332`). These descriptors contain: handler function pointer, min/max packet size, flags, and name string pointer.

2. **The dispatch function is a pure lookup table** — It takes a command code, returns the corresponding DAT_ descriptor pointer. The caller then uses the descriptor to invoke the actual handler.

3. **Response formatter is BLE-side** — `FUN_0000c55c` formats responses for commands that travel between the BLE stack and the main controller logic within the same nRF52833 chip.

4. **Command size table is runtime** — `FUN_00013c30` reads from RAM (`0x2000d168`), so the size data is initialized at boot from flash configuration. Covers codes `0x00`–`0x55` only.

5. **0x8F is the master dispatcher** — Sub-command router for haptic pulses, attribute queries, and mapping operations. The case `0x8f` handler at `0x54368` (in the truncated region) processes the sub-command byte.

6. **Upper range (0x90+)** — Several commands documented from steamclient.so: `0x9F` (turn off), `0xA1` (get device info), `0xAE` (get serial), `0xBA` (get chip ID), `0xF2` (capability query). Likely handled at a different code path within the same firmware.

---

## 6. Haptic System — Three-Path Architecture

### Path 1: Firmware-Local Haptics (SC2 nRF52833 only)

The SC2 firmware has a complete, self-contained haptic sequencer that generates feedback **independently of the host**. The host does NOT upload haptic patterns — scripts are firmware-internal, selected by ID.

**Haptic Architecture Strings:**
- `haptic_script` — Named haptic script sequences (pre-programmed, firmware-internal)
- `haptics_sequencer` — Haptic sequencer module
- `haptics-sequencer-gri-v3` — Grip haptic sequencer
- `haptics-sequencer-touchpad` — Touchpad haptic sequencer
- `channel-left` / `channel-right` — Motor channels

**How scripts are triggered:**
- `"Haptic script ID: %d gain %d"` — Script selection with gain (host sends script ID via 0x8F sub-command, firmware executes the corresponding internal script)
- `"Haptics script already active - ignoring new script"` — Mutual exclusion (only one script runs at a time)
- `"sequence init: %d"` — Sequence initialization
- `"Inappropriate trigger (%d/%d), active stream(s): %d"` — Trigger validation

**Trackpad touch → local haptic:**
- `FUN_00015170` (Trackpad Touch Event, 144 bytes) detects finger movement
- If `|X - Y| > 99` (movement threshold), calls `FUN_0003347c` (haptic trigger on touch)
- The haptics-sequencer-touchpad module plays a basic click script

**Haptic Settings (stored in flash):**
```
settings/haptics/haptic_master_gain_db  — Gain in dB
settings/haptics/enabled               — Enable/disable
settings/haptics/amplifier_mode        — Amplifier mode
user/haptic_boot_level                 — Boot level
```

Configured via host SET_SETTINGS (0x87) registers 70, 79, 3, 15. These are scalar parameters, not script data.

**Grip Haptics:**
- `"Left lower grip"` / `"Left upper grip"` — Left grip motor control
- `"R_LOWER_GRIP"` / `"R_UPPER_GRIP"` — Right grip motor references
- `"grip de-touch threshold"` / `"grip touch threshold"` — Touch detection thresholds

**Not available to SpoofDeck** — Deck's Neptune controller lacks the SC2's haptic sequencer. The Neptune has raw ERM motors driven by InputPlumber's PackedRumbleReport, not the SC2's script-based system.

### Path 2: Game Rumble via 0x80 (works on any transport)

The actual motor output command. Games call `SDL_RumbleJoystick()` which writes a 0x80 output report to `/dev/hidrawN`. This bypasses the haptic sequencer entirely and drives the motors directly.

**Command 0x80 — SET haptic/rumble output:** The haptic motor output command in firmware dispatch. String: `"Failed to set haptics master gain"` nearby.

**Output Report 0x80 (9 bytes):**
```
From host → ATT Write Request → _on_haptic_write()
Format: [0x80, cmd_type, 0, 0, 0, left_speed, left_hi, right_speed, right_hi]

Forwarded to Neptune as PackedRumbleReport:
[0xeb, 0x09, 0x00, 0x00, 0x00, left_lo, left_hi, right_lo, right_hi] (64 bytes)
```

**Haptic Pipeline (Verified from BlueZ 5.86 + Ghidra):**
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

This path is NOT gated by `[esi+0x17c]`. Works on USB, Dongle, and BLE.

### Path 3: Steam-Generated Haptics via 0x8F (USB/Dongle ONLY)

Steam sends 0x8F sub-commands to trigger firmware haptic scripts (UI feedback, trackpad click sounds, etc.). 0x8F is a **multiplexed command envelope** — the byte after 0x8F selects a sub-command (e.g., `ID_TRIGGER_HAPTIC_PULSE`, `ID_GET_ATTRIBUTES_VALUES`).

**Output Report 0x81 (7 bytes):**
```
Direct command: [0x81, ...] — clears digital mappings
Must be re-sent periodically (~2 seconds) as lizard mode auto-re-enables
```

**Haptic pipeline config variables:** `haptic_new`, `haptic_intensity`, `haptic_intensity_old`, `haptic_off_divisor`, `ibex_rumble_deadzone`, `g_RumbleRepeatAfterDelaySeconds` (0.050), `g_RumbleSustainTimeSeconds`.

---

## 7. ESB Interface

The SC2 has a secondary wireless protocol for the Steam Wireless Receiver (dongle):

- `esb_thread` — Dedicated ESB processing thread
- `esb/bond` / `esb/bond_2` — ESB bond storage
- `puck-interface` — Puck (dongle) communication interface
- `puck-pilot-gpio` — Puck GPIO control
- `"Connecting to: Proteus %s, (0x%08X, 0x%08X)"` — ESB connection to dongle
- `"Connected: private pipe (%u/%u, addr 0x%08X, prefix %u"` — ESB pipe setup
- `"No message on private channel"` — ESB channel monitoring

The ESB protocol runs alongside BLE — the controller can simultaneously:
1. Act as BLE peripheral (connected to host PC)
2. Communicate with Steam Wireless Receiver via ESB (2.4 GHz proprietary)

Puck is a transparent relay. Report IDs identical between ESB, USB, BLE ATT (Puck uses ESB, not BLE).

---

## 8. USB Mode & RGB LED

### USB Mode

When connected via USB:
- `"USB connected operation"` — USB mode active
- `"USB device support already enabled"` — Single USB initialization
- `"Failed to enable USB"` — USB initialization error
- `"Cannot get USB HID 0 Device"` — USB HID device access
- `"neptune_usb"` — Neptune USB interface access

USB states:
- `ST_USB_WAIT_FOR_ENUMERATION` → `ST_USB_DATA` (enumerated)
- `ST_USB_SUSPENDED` ↔ `ST_USB_WAIT_FOR_WAKEUP` (power management)
- `ST_USB_WIRELESS_ON` / `ST_USB_WIRELESS_OFF` (USB + wireless combo)

### RGB LED System

- `rgbled` — RGB LED module
- `rgbled_test_thread` — LED test thread
- `pwmrgbleds` — PWM-controlled RGB LEDs
- `pwm_nrfx` — nRF PWM driver for LEDs

---

## 9. Function Address Reference

### Core BLE Functions

| Address | Size | Function |
|---------|------|----------|
| `0x0001d8d0` | 1520 | GATT service registration (HID+GAP+GATT+BAS+DIS) |
| `0x0001ce7c` | 306 | Register primary service (bt_gatt_pool) |
| `0x0001cef4` | — | Register characteristic |
| `0x0001cff0` | — | Register descriptor |
| `0x0001d09c` | — | Register CCCD descriptor |
| `0x00026894` | 226 | CCCD write handler 1 |
| `0x00026bb0` | 218 | CCCD write handler 2 |
| `0x0003fc3e` | 264 | Notification subscription check (gate) |
| `0x000246b0` | 344 | Primary/Secondary service helper |
| `0x00024eb0` | 234 | Primary/Secondary service helper |
| `0x00025260` | 188 | Primary service helper |
| `0x0003fac6` | 58 | Characteristic declaration helper |

### State Machine Functions

| Address | Size | Function |
|---------|------|----------|
| `0x00028ce4` | 754 | State event processor (0-4 cases) |
| `0x00023304` | 598 | BLE connection state handler (0-8 cases) |
| `0x0001bc50` | 784 | BLE command processor (1-9 cases) |
| `0x00009ff0` | 1176 | BLE connection event handler |

### Advertising Functions

| Address | Size | Function |
|---------|------|----------|
| `0x00022298` | 338 | Advertising management |
| `0x000235e8` | 544 | Advertising configuration |

### HID Report Functions

| Address | Size | Function |
|---------|------|----------|
| `0x00013fe0` | 118 | Report sender — alloc buffer, set Report ID 0x45, copy 45 bytes, BLE notify |
| `0x00014064` | 470 | State updater — typed input commands (0-7) → 45-byte report buffer |
| `0x000167d0` | 1014 | Main controller loop — trackpads, IMU, sticks, calls report sender |
| `0x00013858` | 6 | Check if report sending is allowed |
| `0x000138d0` | 30 | Allocate report buffer from pool |
| `0x00013980` | — | Send report via BLE GATT notification |
| `0x00011498` | 546 | IMU processing chain |
| `0x00011460` | — | Scale IMU data |
| `0x0001672c` | 154 | Individual trackpad handler (left/right) |
| `0x00015170` | 144 | Trackpad touch event generator |
| `0x0004373e` | 126 | Trackpad calibration (FPU) |
| `0x00043746` | 8 | IMU/secondary calibration (FPU) |
| `0x0003a790` | — | Stick/trigger data processor |
| `0x000127cc` | — | Button bitfield assembly |

### Key DAT_ References

| DAT_ Address | Type | Description |
|-------------|------|-------------|
| `DAT_00013860` | byte* | Report-send-ready flag |
| `DAT_000138f0` | void* | Report buffer pool |
| `DAT_00013c40` | short* | Mode/state lookup table (56 entries × 2 bytes) |
| `DAT_00014058` | byte* | Report data source buffer (45 bytes) |
| `DAT_00014200` | byte* | Controller state structure |
| `DAT_00014204` | byte* | Controller state + 4 alias |
| `DAT_00015284` | uint* | State change observer table |
| `DAT_00015288` | uint* | Observer table end |
| `DAT_00035d50` | void* | Calibration data structure |
| `DAT_00035d54` | float | Max scaling constant |

### Host-Side Function Address Table (32-bit steamclient.so)

| Address | Function | Description |
|---------|----------|-------------|
| `0x011b3a60` | CHIDIOThread_Main | Main HID I/O thread — creates worker threads, registers HID device callbacks |
| `0x011d5850` | CHIDIOThread_CWorkItem | CWorkItemThread — processes HID read/write work items |
| `0x011d8c40` | CHIDIOThread constructor | Allocates and initializes 0xBB78-byte controller manager object. 16 controller slots at stride 0xDC |
| `0x011e9be0` | Master controller constructor | Allocates and initializes 0xC34-byte master controller object |
| `0x011e9350` | PID-to-transport mapper | Maps PID to transport type: 0x1303→0 (BLE), 0x1302→3 (USB), 0x1304→1 (Dongle) |
| `0x01218840` | CGetControllerInfoWorkItem::RunFunc | Reads controller info via HID feature reports. Logs "Read failure" on error. Retries up to 51 times with 100ms sleep |
| `0x011cee30` | EYldWaitForControllerDetails | Blocks until controller details are ready. Calls `FUN_02ae47e0("EYldWaitForControllerDetails", 2000000, ...)` with 2-second timeout |
| `0x011f7630` | Zombie_Controller_Check | Detects zombie controllers (state=3, flag=0, connection!=1&&!=4). Logs "Zombie Controller" |
| `0x01219bf0` | BYieldingRegisterSteamController (identity path) | Checks controller identity before registration. Logs "couldn't get controller identity" on failure |
| `0x0121a690` | BYieldingRegisterSteamController | Full registration flow. Calls `AccountHardware.RegisterSteamController#1` API |
| `0x012191a0` | Per-controller slot initializer | Initializes 0xDC-byte controller slots. Sets offsets 0x1b0 (deadzone), 0x160 (graphics API), 0x1e8 (connection state) |
| `0x01202e70` | ControllerPersonalization | Loads personalization settings (guide brightness, sounds, antidrift). Not a rumble handler despite our naming |
| `0x012042d0` | Rumble_Handler_2 | Second rumble-related function (needs further analysis) |
| `0x011cbae0` | Controller_Activity_Update | Updates controller activity. Checks `unControllerIndex < MAX_STEAM_CONTROLLERS` |
| `0x011e45d0` | SDL_JOYSTICK_HIDAPI_STEAM_Setup | Loads `libSDL3.so.0`, sets SDL env vars for HIDAPI Steam controller support |
| `0x011beca0` | QueryAccountsRegisteredToController | Queries which accounts are registered to a controller via `AccountHardware.QueryAccountsRegisteredToController#1` |
| `0x01217a30` | SET_SETTINGS dispatch | Sends settings to controller. Fire-and-forget — no response read. Uses vtable[0x50] for write-only dispatch |
| `0x0123e1e0` | Gate_CHECK_Parent_Function | Initializes controller XInput subsystem. Sets up 16 controller slots with 0x800-byte pipe buffers |
| `0x0123e5da` | Gate CHECK function | 2103-byte function containing gate CHECK at 0x0123e5fb. Ghidra incorrectly merges this into FUN_0123e1e0 |
| `0x0173ce00` | Gate CLEAR — RecvMsgAppStatus | Only function that clears gate to 0. Called after successful controller status loop processing |
| `0x01789c00` | YRT_Parent_Function (ShaderCacheManager) | Contains gate SET at offset +0x540. Handles shader cache management and backend hit cache generation |
| `0x019aec80` | Graphics API type writer | Writes 1-4 to `[eax+0x160]` based on detected graphics API (GL/Vulkan/D3D12) |
| `0x00ec1330` | 0x8F_Dispatcher_1 | First command dispatcher with jump table. `cmp eax, 0x8f` at +0x74 |
| `0x00eed350` | 0x8F_Dispatcher_2 | Second command dispatcher with jump table. `cmp eax, 0x8f` at +0x74 |

### Command Dispatch Functions

| Address | Size | Function |
|---------|------|----------|
| `0x000383c4` | 426 | Main command dispatch (TBH jump table) |
| `0x0000c55c` | 538 | Response formatter (BLE-side) |
| `0x00013c30` | 14 | Command size lookup (0x00–0x55) |
| `0x00010d90` | — | BLE command loop — handles 0xe2-0xe7, sends 0xf2 ACK after 0xe7 |
| `0x000445f2` | — | Dispatch tail-call wrapper |
| `0x0001b07c` | — | Message submit to event bus |

### Response / ACK Functions

| Address | Function |
|---------|----------|
| `0x000420ae` | 0xf0 identity response — MAC + UUID (20B) |
| `0x00042132` | 0xf2 mapping ACK — 6-byte response |
| `0x0004214a` | 0xf3 mode notification — 1B mode |
| `0x00042108` | 0xf4 status response — timestamp + model (20B) |

---

## 10. Key Offset Map (Controller Object)

| Offset | Size | Description |
|--------|------|-------------|
| `+0x17c` | byte | **Haptic gate flag** (0=blocked, 1=enabled). SET by 5 functions, CLEAR only by `RecvMsgAppStatus` (0x0173ce00) |
| `+0x160` | dword | **Graphics API type** (1=GL, 2=Vulkan, 3=D3D12A, 4=D3D12B). Written by `FUN_019aec80`. NOTE: 32-bit offset is 0x160, NOT 0x1d8 (which is 64-bit) |
| `+0xbc` | int | **Protocol/transport type** (1=USB, 2=BLE, 3=other). Set during controller init based on PID |
| `+0x48c` | int | **Product ID** (0x1302=USB, 0x1303=BLE, 0x1304=Dongle) |
| `+0x1b0` | dword | Scale/deadzone factor (default 1.0f = 0x3f800000). Set by per-slot initializer |
| `+0xa0` | byte | Haptic active flag (set to 1 when haptic processing begins) |
| `+0x10c` | byte | Secondary haptic enable flag |
| `+0x144` | int | Haptic intensity integer |
| `+0x140` | ptr | Object pointer (used in many vtable calls) |
| `+0x4b0` | byte | "Is not dongle" flag (1=true for SC2, 0=false for dongle) |

### Gate Mechanism (0x17c) — Full Interaction Map

| # | Function | Address | Operation | Context |
|---|----------|---------|-----------|---------|
| 1 | `FUN_0173ce00` (RecvMsgAppStatus) | 0x0173ce00 | **WRITE = 1** | Error/abnormal exit from message processing |
| 2 | `FUN_0173ce00` (RecvMsgAppStatus) | 0x0173ce00 | **WRITE = 0** | Normal path — after controller status loop completes |
| 3 | `FUN_0173fbb0` | 0x0173fbb0 | **WRITE = 1** | Conditional SET in a different message processing path |
| 4 | `FUN_016d5780` | 0x016d5780 | **READ `& 1`** | Bitmask check — gate is a multi-bit field, bit 0 = "enabled" |
| 5 | `FUN_01721710` | 0x01721710 | **READ** full byte | Used as BST lookup result — feeds into controller status |
| 6 | `FUN_02b92130` | 0x02b92130 | **READ == 0** check | Conditional gate check for a specific operation |
| 7 | `FUN_0247de42` | 0x0247de42 | **COPY** into message | Gate state is embedded in outgoing protocol messages |

### Input Path vs Command Path — Separation

**Input path (NOT gated):**
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

**Command path (GATED):**
```
Gate CHECK at 0x0123e5fb: cmp byte [esi+0x17c], 0
  → If 0: skip entire command pipeline
  → If 1: proceed with commands
    → 0x80 (Rumble), 0x81 (Clear Mappings), 0x83 (Get Attributes)
    → 0x85 (Set Mode), 0xB4 (Protocol Version), 0xEE/0xEF (Feature Messages)
    → Gyro enable/disable (0x50/0x30) via vtable[0x50]
    → Mode switch (0x08/0x09) via vtable[0x50]
```

---

## 11. SDL Configuration

Steam loads SDL3 and sets these environment variables at startup (from `FUN_011e45d0`):

| Variable | Value | Effect |
|----------|-------|--------|
| `SDL_JOYSTICK_HIDAPI_STEAM` | `"1"` | **Enables Steam HIDAPI driver** — this is the path that talks to SC2 |
| `SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS` | `"1"` | Allows input even when Steam window not focused |
| `SDL_JOYSTICK_ENHANCED_REPORTS` | `"1"` | Uses enhanced report format |
| `SDL_AUTO_UPDATE_JOYSTICKS` | `"1"` | Auto-discovers new controllers |
| `SDL_JOYSTICK_RAWINPUT` | `"1"` | Enables raw input path |
| `SDL_JOYSTICK_HIDAPI_STEAMXBOX` | `"0"` | Disables SteamXbox HIDAPI |
| `SDL_JOYSTICK_LINUX_DEADZONES` | `"0"` | Disables Linux deadzone handling |

**Critical insight**: `SDL_JOYSTICK_HIDAPI_STEAM=1` means Steam uses SDL3's HIDAPI driver for Steam controllers. This bypasses the kernel's `hid-steam` driver and communicates directly via HID feature/output reports.

### BLE vs USB Transport Logic

The controller init function `FUN_0122ba20` (0x0122ba20, 9543 bytes) reads PID from `controller+0x48c` and sets transport at `controller+0xbc`:

| PID | Transport | `controller+0xbc` |
|-----|-----------|-------------------|
| 0x1303 (BLE SC2) | BLE | 2 |
| 0x1302 (USB SC2) | USB | 1 |
| 0x1220 (USB SC1) | USB | 1 |
| 0x1304 (Puck Dongle) | Dongle | 1 |
| Other | Unknown | 3 |

Key behavioral differences:
- **BLE** (type 2): Gets sensor-derived IMU polling rate via `FUN_01269650`
- **USB** (type 1): Gets config-forced IMU polling rate
- **BLE**: Gets timestamp-based timing via `FUN_01236770` when `0xbc > 1`
- **Both**: Skip extended initialization (only type 3 unknown PIDs get that)
- **Report to Steam**: Protocol type sent at report offset 0x51 (1=USB, 2=BLE)

### Controller Registration Flow

```
1. BYieldingRegisterSteamController (0x0121a690)
   → QueryAccountsRegisteredToController (0x011beca0)
   → AccountHardware.RegisterSteamController#1 API

2. BYieldingCompleteSteamControllerRegistration (0x01219bf0)
   → Checks controller identity
   → EYldWaitForControllerDetails (0x011cee30) with 2s timeout
   → AccountHardware.CompleteSteamControllerRegistration#1 API

3. Zombie detection: FUN_011f7630
   → Checks slot state == 3, flag == 0, connection state != 1 && != 4
```

---

## 12. Implications for SpoofDeck

1. **Report format**: The SC2 BLE HID report format is defined by the HID Report Descriptor in the firmware. Our spoofed GATT database must match this exactly.

2. **MTU and report size**: The firmware handles reports up to the negotiated MTU. Our ATT server must handle MTU exchange correctly.

3. **CCCD behavior**: The firmware checks CCCD before every notification send. Our host-side driver (hog-ll) must write CCCD to enable notifications — which it already does.

4. **Notification gating**: The `FUN_0003fc3e` function is the notification gate. If our spoofed controller doesn't implement this correctly, notifications won't flow.

5. **Multiple report types**: Up to 6 input reports, plus optional Boot Keyboard and Consumer Control. The Report Map must list all of these.

6. **Connection parameters**: The firmware requests specific connection parameters. Our ATT server should handle LE Connection Parameter Update requests.

7. **ESB is irrelevant for BLE spoofing**: The ESB protocol is for the wireless dongle, not BLE. We can ignore it.

8. **State machine is firmware-internal**: The state machine handles mode switching (USB/Puck/BLE/Battery). For BLE spoofing, we only care about the BLE path.

---

## 13. Key Findings Summary

### Matches Expectations
1. GATT services: HID (0x1812) + Battery (0x180F) + Device Info (0x180A)
2. CCCD handling: Standard Zephyr bt_hids CCCD management
3. Report format: Up to 6 input reports with Report Reference descriptors
4. BLE advertising: Connectable + directed advertising with timeout
5. SMP pairing: Standard BLE pairing with bond storage

### Surprising Findings
1. **ESB (Enhanced ShockBurst)**: Complete secondary wireless protocol for the Steam Wireless Receiver
2. **State machine complexity**: 25 states covering USB/Puck/ESB/Battery/Shutdown scenarios
3. **Two advertising slots**: One for BLE, one likely for directed reconnection
4. **Grip haptics**: Dedicated grip motors (left upper/lower, right upper/lower) with touch detection
5. **Haptic scripts**: Named script sequences with gain control, not just simple rumble
6. **2M PHY support**: Supports and likely negotiates 2M PHY for higher throughput
7. **Consumer Control (0x2a22)**: Optional consumer control characteristic for media keys
8. **Boot Keyboard Input (0x2a33)**: Optional boot protocol support
9. **GATT layout**: Only HID Service (0x1812) explicitly registered in firmware; Battery/Device Info NOT in firmware (stored in settings)

### Firmware Binary Limitation

`ibex_firmware.bin` is 350,528 bytes (66.9% of nRF52833's 512 KB flash). Command descriptor structures at `0x59b10`–`0x5a332` (~15 KB beyond the dump) are unreadable — and per `research/firmware-dump-assessment.md` they sit outside every Valve DFU ever shipped. Full flash dump via J-Link/SWD needed to read:
- 94 command descriptor structures (8-62 bytes each, variable-length)
- Haptic motor speed calculation code (addresses ≥ `0x55940`)
- 0x8F sub-command dispatcher implementation at `0x54368`

---

## 14. Next Steps for Further Research

1. **Capture Steam controller.txt logs on host** — These contain the exact error messages from CGetControllerInfoWorkItem and the registration flow. Zero-effort, immediate answers.
2. **Capture btmon during BLE connection** — Shows the ATT traffic timing: when CCCDs are written, when our first notification arrives, when UHID_START fires. Identifies the timing gap.
3. **Verify CCCD write + notification flow** — Check if our `_notification_handles` set is populated when CGetControllerInfoWorkItem starts reading. Add logging to confirm CCCD writes arrive on the correct handles.
4. **Test with pre-sent notifications** — Modify our ATT server to send a burst of notifications immediately on connection (before waiting for Neptune input). This would pre-fill the UHID queue and ensure CGetControllerInfoWorkItem gets data.
5. **Native SC2 comparison** — Capture btmon + Steam logs with a real SC2 to establish the baseline timing (requires hardware we don't have).
6. **GDB on host Steam process** — Breakpoint on `CGetControllerInfoWorkItem::RunFunc` (0x01218840) to see what `hid_read` returns and how many retries it takes.

---

## 15. Files

| File | Content |
|------|---------|
| `exports/32bit/functions.csv` | 141,351 functions |
| `exports/32bit/strings.csv` | 56,317 strings |
| `exports/32bit/call_graph.csv` | 16,494 call edges |
| `exports/32bit/controller_decompiled_32bit.txt` | Decompiled C for 14 key functions |
| `exports/32bit/controller_xrefs_32bit.txt` | Xrefs to 12 controller-related strings |
| `exports/32bit/key_disassembly.txt` | Assembly for 9 known addresses |
| `exports/64bit/decompiled_64bit.txt` | Decompiled C for 18 key addresses (64-bit reference) |
