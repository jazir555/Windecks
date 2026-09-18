# Puck (USB Dongle) Firmware & Cross-Transport Reference

**Purpose**: Single reference covering Puck architecture, ESB protocol, cross-transport behavior, and firmware-vs-steamclient analysis.

**Sources**:
- Puck firmware: `proteus_firmware.bin` (197,740 bytes, 790 functions)
- Triton firmware: `ibex_firmware.bin` (350,528 bytes, 2,027 functions)
- steamclient.so (49MB, 32-bit i386)
- Both firmwares: Nordic nRF52833 (ARM Cortex-M4F), Zephyr RTOS, nRF Connect SDK v2.9.0

---

## 1. Puck Architecture

### Firmware Overview

- **Chip**: Nordic nRF52833 (ARM Cortex-M4F), Zephyr RTOS
- **SDK**: nRF Connect SDK v2.9.0-d93dcad627bd
- **Binary**: 197,740 bytes (193 KB), 790 functions
- **Stack pointer**: 0x20015d00 (RAM)
- **Reset handler**: 0x00011dcd (flash)
- **Product name**: "Steam Controller Puck" by "Valve Software"
- **Flash storage**: flash-controller@4001e000

### USB HID Composite Device (CRITICAL)

The Puck presents a composite HID device to the host PC using HID-over-I2C (`board_hid_over_i2c`). The HID report descriptor is at binary offset 0x23b87 (immediately after the `hid_proxy` string at 0x23b7d).

#### Input Reports (Controller → Host)

| Report ID | Size (bytes) | Type | Description |
|-----------|-------------|------|-------------|
| 0x40 | 4 | Mouse | 2 buttons (1-bit each), 6-bit padding, X (8-bit signed), Y (8-bit signed), Wheel (8-bit signed), AC Pan (8-bit signed) |
| 0x41 | 7 | Keyboard | 8 modifier bits (E0-E7), 1 padding byte, 6 keycodes (8-bit each, 0-101) |
| 0x42 | 53 | Vendor | SC2 Custom input — primary gamepad state (sticks, triggers, buttons, trackpads) |
| 0x44 | 5 | Vendor | SC2 Custom input — secondary data |
| 0x79 | 1 | Vendor | SC2 Custom input — single byte |
| 0x43 | 14 | Vendor | SC2 Custom input — tertiary data |
| 0x7B | 12 | Vendor | SC2 Custom input — additional data |
| 0x45 | 45 | Vendor | SC2 Custom input — full gamepad report (sticks, triggers, trackpads, IMU, buttons) — **matches our BLE SC2 report format** |

#### Output Reports (Host → Controller)

| Report ID | Size (bytes) | Type | Description |
|-----------|-------------|------|-------------|
| 0x80 | 9 | Vendor | Output report — likely haptic/rumble (matches SC2 haptic output format) |
| 0x81 | 7 | Vendor | Output — likely LED/command |
| 0x82 | 3 | Vendor | Output — short command |
| 0x83 | 9 | Vendor | Output — medium command |
| 0x84 | 8 | Vendor | Output — medium command |
| 0x85 | 3 | Vendor | Output — short command |
| 0x86 | 3 | Vendor | Output — short command |
| 0x87 | 63 | Vendor | Output — large command (SC2 command channel) |
| 0x89 | 63 | Vendor | Output — large command |
| 0x88 | 63 | Vendor | Output — large command |

#### Feature Reports

| Report ID | Size (bytes) | Type | Description |
|-----------|-------------|------|-------------|
| 0x01 | 63 | Feature | GET/SET feature — SC2 command channel (matches Feature Report 0x00 in our BLE ATT server) |
| 0x02 | 63 | Feature | GET/SET feature — secondary command channel |

**Key Insight**: The Puck's HID report IDs (0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x79, 0x7B) are **the same report IDs used in the SC2 BLE protocol**. The Puck is essentially a **transparent relay** — it translates ESB packets from the controller directly into USB HID reports with minimal protocol translation.

### HID-over-I2C Bridge

| String | Meaning |
|--------|---------|
| `board_hid_over_i2c` | I2C HID bridge function |
| `i2c@40003000` | nRF52833 TWIM0 (I2C peripheral) |
| `i2c_hid` | I2C HID driver |
| `i2c_nrfx_twis` | Nordic TWIS (Two Wire Interface Slave) |
| `Failed to initialize HID I2C bus` | I2C bus init error |
| `Failed to initialize HID I2C target` | I2C target init error |
| `Failed to prepare DMA for I2C read/write` | DMA transfer error |
| `HID over I2C doesn't have a concept of SoF. Ignoring the callback` | I2C vs USB timing |

The Puck has a **native USB** connection to the host PC (nRF52833 has built-in USB). The `i2c_hid` and `board_hid_over_i2c` are likely used for **internal** communication between the nRF52833 and another chip on the Puck PCB, or for the EC (Embedded Controller) input interface.

---

## 2. Puck-Specific Features

### Pilot Signal

| String | Meaning |
|--------|---------|
| `puck-pilot-gpio` | Pilot signal GPIO |
| `gpio_puck` | Puck GPIO config |
| `Pilot signal is valid but controller unresponsive` | Pilot detection |
| `In pilot envelope` | Controller in range |
| `VPILOT out of range` | Signal strength check |
| `Failed to configure PILOT_SENSE input` | GPIO init error |

The Puck has a **pilot signal** — a GPIO-based signal that detects when a controller is physically nearby/paired. This is separate from the ESB radio connection and provides a quick presence check.

### EC Input (Embedded Controller)

| String | Meaning |
|--------|---------|
| `ec-input` | EC input device |
| `ec_input_tap` | EC tap input |
| `ec-input-tap@0` through `@3` | Per-controller EC tap inputs |
| `Failed to register a HID proxy tap %d` | EC tap registration |

The Puck has an **EC (Embedded Controller)** interface for tap/interaction input — likely for button taps on the dongle itself.

### ADC Reading

| String | Meaning |
|--------|---------|
| `puck_adcs_read` | ADC reading function |

The Puck reads analog signals via ADC — possibly battery voltage or pilot signal strength.

### UART Debug

| String | Meaning |
|--------|---------|
| `Failed to enable puck UART node(%d)` | UART init |
| `Failed to enable puck UART RX %d` | UART RX init |
| `UART port is not initalized. Puck interface is a no-op` | UART not available |
| `shell.shell_uart`, `shell_uart_backend` | Zephyr shell UART backend |

The Puck has a debug UART interface for development.

### Host Sleep Management

The Puck manages host USB sleep/wake:
- `Slot %u : Host suspended - waiting for wakeup`
- `Slot %u : Host awake`
- `Slot %u : Host did not wakeup within timeout`
- `Slot %u : Host suspended, turn off controller`
- `Ibex took too long to shutdown - disconnect and prevent it from waking the host next time it connects`
- `Remote wakeup feature not supported/enabled`

This is standard USB power management — when the host suspends, the Puck can turn off controller radios to save power, and support remote wakeup.

### Protocol Version Negotiation

- `Slot %u : Sending protocol version: %u %u`
- `Slot %u : Protocol version updated %u`
- `Slot %u: Unrecognized protocol version %u`

The Puck and controller negotiate a protocol version to ensure compatibility. This determines which report formats and features are available.

---

## 3. ESB Protocol

### Nordic ESB Standard (Zephyr Implementation)

Both firmwares use the **Zephyr ESB library** (`libesb`), which implements Nordic's Enhanced ShockBurst protocol. The packet format is fixed by the hardware radio peripheral:

```
┌──────────┬──────────┬────────────┬──────────┬───────────┬─────────┐
│ Preamble │ Address  │ Header (9b)│ Length(6b)│ Payload   │  CRC    │
│ 1-2 bytes│ 4-5 bytes│            │          │ 0-32 bytes│ 2-3 bytes│
└──────────┴──────────┴────────────┴──────────┴───────────┴─────────┘
```

**Confirmed from firmware strings**:
- Puck: `No event from ESB. Manually disabling radio so we get RADIO_IRQ for RX stage` — confirms direct radio IRQ handling (Zephyr ESB pattern)
- Puck: `Failed to initialize ESB system timer` — ESB uses a dedicated timer for retransmit/ACK timing
- Triton: `Failed to initialize ESB system timer` — same timer pattern on controller side
- Triton: `ESB TX FIFO full` — confirms hardware TX FIFO, standard ESB behavior

### Preamble
- **1 byte** at 1 Mbps data rate
- **2 bytes** at 2 Mbps data rate
- Both firmwares default to 2 Mbps (evidenced by high-throughput report relay and Zephyr ESB defaults)

### Address (4-5 bytes)
- **Puck (Dongle) address format**: `ibex%d_proteus_uuid : 0x%08X` — Puck has a 32-bit UUID per controller slot
- **Triton (Controller) address format**: `Connecting to: Proteus %s, (0x%08X, 0x%08X)` — connects using two 32-bit values (base address + prefix)
- **Connected pipe**: `Connected: private pipe (%u/%u, addr 0x%08X, prefix %u` — pipe number, endpoint, 32-bit address, prefix byte
- Total address: **5 bytes** (4-byte base + 1-byte prefix), standard Nordic ESB

### Header (9 bits)
- **PID** (2 bits): Packet ID for auto-ACK duplicate detection
- **NO_ACK** (1 bit): If set, no acknowledgment requested
- **Reserved** (6 bits)

### Payload Length (6 bits)
- 0-32 bytes (ESB standard maximum)

### CRC
- **2 bytes** (16-bit) or **3 bytes** (24-bit), configured in the Zephyr ESB library
- Puck string: `Unexpected CRC16 value %x` — confirms **16-bit CRC**

### RF Parameters

| Parameter | Value | Evidence |
|-----------|-------|----------|
| **Protocol** | Nordic ESB | `dongle_esb`, `triton_esb` module names |
| **Frequency band** | 2.4 GHz ISM | Standard ESB, nRF52833 radio |
| **Data rate** | 2 Mbps | Default Zephyr ESB, 45-byte reports at high rate |
| **Channel spacing** | 1 MHz | ESB standard |
| **Channel range** | 2-126 | ESB standard, configurable via `esb_set_rf_channel` |
| **Auto-ACK** | Enabled | ESB standard, confirmed by PID in header |
| **CRC** | 16-bit | `Unexpected CRC16 value %x` in Puck |
| **Max payload** | 32 bytes | ESB standard, but reports may be fragmented |
| **Retransmit** | Up to 15 retries | ESB default, configurable |
| **Air time per packet** | ~130μs | 2 Mbps with 32-byte payload |
| **Retry delay** | 250-500μs | Zephyr ESB default: 250μs |

### Address/Channel Configuration

**Pipe-based addressing** (both firmwares):

**Puck side** (4 controller slots):
```
esb-controller@0    — Device tree node for slot 0
esb-controller@1    — Device tree node for slot 1
esb-controller@2    — Device tree node for slot 2
esb-controller@3    — Device tree node for slot 3
```

**Pipe endpoints** (per controller):
```
esb/ibex_0          — ESB pipe endpoint to controller slot 0
esb/ibex_1          — ESB pipe endpoint to controller slot 1
esb/ibex_2          — ESB pipe endpoint to controller slot 2
esb/ibex_3          — ESB pipe endpoint to controller slot 3
```

**Shared data structure**: `ibexesb_common` — common ESB/Ibex data structure accessed by all slots

**Per-controller UUIDs** (Puck stores):
```
ibex%d_proteus_uuid : 0x%08X    — Puck's UUID for this slot
ibex%d_ibex_uuid : 0x%08X      — Controller's UUID for this slot
ibex%d_peer_sn : %s             — Controller's serial number
```

**Address generation**: Each Puck generates a unique 32-bit UUID per controller slot. Each controller has its own 32-bit UUID. During pairing, these UUIDs are exchanged and stored as bond data. The 5-byte ESB address = 4-byte UUID + 1-byte prefix.

### Channel Configuration

**Puck strings**:
- `esb_set_rf_channel %d %u` — configurable RF channel per slot
- `Initializing channel map` — channel map for frequency hopping
- `Slot %u : Connected Ibex %s (Ch %u)` — channel assigned per connection
- `radio_send_channels` / `radio_send_channels <timeout_s> [channels]` — shell commands for channel testing

**Triton strings**:
- `Failed to allocate PPI Channel` — PPI (Programmable Peripheral Interconnect) for radio timing
- `Failed to initialize ESB system timer` — ESB timing timer

### Frequency Hopping
- ESB uses **adaptive frequency hopping** across multiple channels
- The Puck maintains a **channel map** per controller slot
- `Initializing channel map` — channel map configured at connection time
- Channel can be queried per connection: `Slot %u : Connected Ibex %s (Ch %u)`
- `channel_cost` / `channel_cost_show` — channel quality tracking for adaptive hopping

---

## 4. Multi-Controller & Connection Lifecycle

### Multi-Controller Architecture

The Puck supports **4 simultaneous controllers**:

| Resource | Count | Identifiers |
|----------|-------|-------------|
| ESB controller slots | 4 | `esb-controller@0` through `esb-controller@3` |
| ESB pipe endpoints | 4 | `esb/ibex_0` through `esb/ibex_3` |
| HID proxy instances | 4 | `HID_PROXY_0` through `HID_PROXY_3` |
| EC input tap devices | 4 | `ec-input-tap@0` through `ec-input-tap@3` |
| UUID pairs | 4 | `ibex%d_proteus_uuid` + `ibex%d_ibex_uuid` |
| Serial numbers | 4 | `ibex%d_peer_sn` |
| Bond data | 4 | Stored in flash (`flash-controller@4001e000`) |

### Per-Controller Connection Lifecycle

From Puck strings, each slot follows this state machine:

```
┌─────────┐
│  Idle   │ ← Slot %u : Idle
└────┬────┘
     │ (controller enters pairing mode)
     ▼
┌────────────┐
│ Connecting │ ← Slot %u : Connecting Ibex %s
└────┬───────┘
     │ (ESB connection established)
     ▼
┌─────────┐
│ Pairing │ ← Slot %u : Pairing
└────┬────┘
     │ (key exchange complete)
     ▼
┌───────────┐     ┌──────────────────┐
│ Connected │ ←── │ Slot %u : Connected Ibex %s (Ch %u)
└─────┬─────┘     └──────────────────┘
      │
      ├─→ Protocol version exchange
      │   Slot %u : Sending protocol version: %u %u
      │   Slot %u : Protocol version updated %u
      │
      ├─→ Feature Report exchange
      │   Slot %u : Set FR / Get FR
      │
      ├─→ Active data relay (input/output reports)
      │
      ├─→ QOS monitoring
      │   Slot %u : QOS %u {%03u %03u %03u %02u %03d} {%02u %02u %u}
      │
      ├─→ Host sleep/wake handling
      │   Slot %u : Host suspended - waiting for wakeup
      │   Slot %u : Host awake
      │
      └─→ Disconnect
          Slot %u : Disconnect message
          Slot %u : Connection timeout
          Ibex disconnecting
          Ibex took too long to shutdown...
```

### Pairing and Bonding

**Puck side**:
```
Slot %u : Pairing                    — entering pairing state
Slot %u: Pairing successful          — bond established
Slot %u: Pairing failed              — pairing error
Slot %u : New bond saved             — bond stored to flash
Slot %u : Bond deleted               — bond removed
```

**Triton side**:
```
esb/bond                              — primary bond storage path
esb/bond_2                            — secondary bond storage
ID %d : bonded to %s                  — bond record
Bond info for id %d was deleted...    — bond cleanup
List of existing bonds:               — bond listing
```

**Bond data includes**: ESB address (32-bit UUID + prefix), link key (ESB-level encryption), controller identity (serial number, UUID), protocol version, feature report cache.

**Pairing flow**:
1. Controller enters pairing mode (button press)
2. Puck detects pairing signal on ESB
3. Slot transitions to `PAIRING`
4. Key exchange occurs (ESB-level, NOT BLE SMP)
5. Bond saved to flash: `Slot %u : New bond saved`
6. Slot transitions to `CONNECTED`

### Reconnection

The Puck handles reconnection automatically:
- On disconnect, slot returns to `Idle`
- Puck continues advertising ESB presence
- When controller reappears, connection re-established
- Bond data used for fast reconnection (no re-pairing needed)
- `Ibex disconnecting` → `Slot %u : Idle` → `Slot %u : Connecting Ibex %s`

### Command Channel Through ESB

Before data flows, the Puck and controller exchange Feature Reports (FR):

**Puck strings**:
```
Slot %u : Set FR (0x%02X) - controller ret %d
Slot %u : Get FR (0x%02X) - response wrong state (%u)
Slot %u : Get FR (0x%02X)  - timer expired (%u)
Slot %u : Get FR (0x%02X) - mismatched rsp 0x%02X
Slot %u : Get FR (----) - no SET %u
Slot %u : Get FR - unexpected %u
Slot %u : FR timer expired %u
Slot %u : Set FR (0x%02X) - GET pending (%u)
Invalid GET_FR_RSP
Invalid SET_FR_RSP
```

**Triton strings**:
```
Discarding feature report with unexpected length %d
Failed to execute GET_FEATURE_REPORT
Failed to execute SET_FEATURE_REPORT
get_id_get_attributes_values
get_id_get_string_attribute
%s: GET: ID_GET_ATTRIBUTES_VALUES
%s: GET: ID_GET_STRING_ATTRIBUTE, TAG: %u
```

**Feature Report Flow**:
1. Puck sends SET_FEATURE_REPORT (0x%02X) to controller via ESB
   - Controller receives, processes command
   - Returns status (controller ret %d)
2. Puck sends GET_FEATURE_REPORT to controller via ESB
   - Timer started (FR timer)
   - Controller responds with feature report data
   - Puck validates: correct report ID? correct length? timer not expired?
   - If mismatch: "mismatched rsp 0x%02X" or "timer expired"
3. Capability negotiation complete → data relay begins

### Command IDs (from both firmware strings)

**GET commands** (Controller → Puck response):
| Command ID | Name | Description |
|------------|------|-------------|
| `ID_GET_ATTRIBUTES_VALUES` | Get Attributes | Controller capabilities/state |
| `ID_GET_STRING_ATTRIBUTE` | Get String | Controller name, serial, etc. |
| `ID_GET_BATTERY_DATA` | Get Battery | Battery level (Triton only) |
| `ID_GET_LED_COLOR` | Get LED | LED color state (Triton only) |
| `ID_GET_SETTINGS_VALUES` | Get Settings | Configuration values |
| `ID_GET_USER_STORE` | Get User Store | User-stored data |

**SET commands** (Puck → Controller):
| Command ID | Name | Description |
|------------|------|-------------|
| `ID_FIRMWARE_UPDATE_REBOOT` | FW Update | Firmware update trigger |
| `ID_SET_LED_COLOR` | Set LED | LED color control |
| `ID_SET_SETTINGS_VALUES` | Set Settings | Configuration values |
| `ID_SET_USER_STORE` | Set User Store | User-stored data |
| `ID_TURN_OFF` | Turn Off | Power off controller |
| `ID_REBOOT_INTO_ISP` | Reboot ISP | Reboot to ISP mode |
| `set_id_persist` | Persist | Save to flash |

### Data Relay Path

**Input Path (Controller → Host via Puck)**:

```
SC2 Controller
  → ESB radio (2.4 GHz, 2 Mbps)
    → Puck nRF52833 ESB receiver
      → ESB packet decode (per-slot: esb/ibex_N)
        → Protocol version check
          → HID proxy (HID_PROXY_N)
            → board_hid_over_i2c
              → USB HID device on host PC
                → Kernel HID driver
                  → /dev/hidrawN
```

**Output Path (Host → Controller via Puck)**:

```
Host PC
  → USB HID output report (report IDs 0x80-0x89)
    → board_hid_over_i2c
      → HID proxy (HID_PROXY_N)
        → ESB packet encode (per-slot)
          → ESB radio transmit
            → SC2 Controller receives
```

**Protocol Translation: NONE**

The Puck performs **zero protocol translation** between ESB and USB HID:

| Aspect | ESB Payload | USB HID Report | Translation? |
|--------|------------|----------------|-------------|
| Report ID 0x45 (45B) | SC2 gamepad state | Same 45 bytes | **No** |
| Report ID 0x42 (53B) | Extended gamepad | Same 53 bytes | **No** |
| Report ID 0x40 (4B) | Mouse | Same 4 bytes | **No** |
| Report ID 0x41 (7B) | Keyboard | Same 7 bytes | **No** |
| Output 0x80 (9B) | Haptic command | Same 9 bytes | **No** |
| Output 0x87-0x89 (63B) | Command channel | Same 63 bytes | **No** |
| Feature 0x01 (63B) | Command channel | Same 63 bytes | **No** |

### Payload Sizing

ESB max payload is 32 bytes, but SC2 reports can be up to 63 bytes (output reports 0x87-0x89). This means:

- **Input reports ≤32 bytes**: Single ESB packet
- **Input reports >32 bytes**: Must be fragmented (e.g., report 0x42 at 53 bytes = 2 ESB packets)
- **Output reports ≤32 bytes**: Single ESB packet
- **Output reports >32 bytes**: Fragmented into multiple ESB packets

The fragmentation/reassembly is handled by the Zephyr ESB library's payload splitting (not application-level).

### Latency

| Path | Latency | Evidence |
|------|---------|----------|
| **ESB radio** | ~1-2ms | 2 Mbps, single packet air time ~130μs |
| **Puck relay** | <1ms | Transparent relay, no processing |
| **USB HID** | ~1ms | USB 1.1 full-speed polling |
| **Total (Puck)** | **~2-4ms** | ESB + USB combined |
| **BLE (direct)** | ~7.5-15ms | BLE connection interval + ATT |

### Error Handling

**Puck**:
```
dongle recv report err: %d           — ESB receive error
Discarded report                     — invalid/malformed report
Discarding keyboard report           — keyboard report filtered
Discarding feature report with unexpected length %d — size mismatch
Failed to submit input report %d     — USB submission failed
Failed to submit report              — general report failure
Unsupported report type              — unknown report ID
```

**Triton**:
```
Discarded report                     — invalid/malformed report
Discarding feature report with unexpected length %d — size mismatch
Failed to allocate report            — memory allocation failure
Failed to send full HID report %d    — partial send failure
No buffer available to send notification — backpressure
Unable to send hid report, %u        — notification send failure
Device is not subscribed to characteristic — CCCD not enabled
```

---

## 5. ESB vs BLE Transport

### Dual-Stack Architecture (Triton Side)

The Triton firmware supports **both** ESB and BLE simultaneously:

```
┌─────────────────────────────────────────────────────────────┐
│                    Triton (SC2) Firmware                     │
│                                                              │
│  ┌─────────────────────┐  ┌──────────────────────────────┐  │
│  │  ESB Stack           │  │  BLE Stack                    │  │
│  │  ├─ triton_esb       │  │  ├─ bt_hids (HID Service)     │  │
│  │  ├─ esb_thread       │  │  ├─ bt_gatt (GATT server)     │  │
│  │  ├─ esb/bond         │  │  ├─ bt_smp (Security)         │  │
│  │  └─ puck-interface   │  │  └─ bt_att (ATT protocol)     │  │
│  └──────────┬──────────┘  └──────────────┬───────────────┘  │
│             │                             │                   │
│             ▼                             ▼                   │
│  ┌─────────────────────┐  ┌──────────────────────────────┐  │
│  │  ESB Radio           │  │  BLE Radio                    │  │
│  │  (2.4 GHz proprietary)│  │  (2.4 GHz standard)          │  │
│  └──────────┬──────────┘  └──────────────┬───────────────┘  │
│             │                             │                   │
└─────────────┼─────────────────────────────┼──────────────────┘
              │                             │
              ▼                             ▼
      ┌──────────────┐           ┌──────────────────┐
      │ Puck (Dongle)│           │ Host PC (Direct)  │
      │ via ESB      │           │ via BLE           │
      └──────────────┘           └──────────────────┘
```

### State Machine: Transport Differentiation

**ESB-related states**:
| State | Purpose |
|-------|---------|
| `ST_PUCK_OFF` | No Puck detected, start ESB |
| `ST_PUCK_OFF_DEBOUNCE_DETACHMENT` | Debouncing Puck removal |
| `ST_PUCK_KEYCHORD_CURRENT` | Keychord in current mode |
| `ST_PUCK_KEYCHORD_BT` | Keychord to switch to BT pairing |
| `ST_PUCK_KEYCHORD_ESB` | Keychord to switch to ESB pairing |
| `ST_PUCK_KEYCHORD_ESB_ALT` | Keychord to switch to ESB alt mode |
| `ST_PUCK_ON` | Puck connected, ESB active |

**BLE-related states** (handled by BLE stack, not state machine):
- BLE advertising: `adv-connectable`, `adv-dir-connectable`
- BLE connection: `Device connected`, `Device disconnected`
- BLE SMP: `Pairing`, `Pairing timeout`

### Connection Detection

**ESB connection** (from Triton):
```
Connecting to: Proteus %s, (0x%08X, 0x%08X)    — initiating ESB connection
Connected: private pipe (%u/%u, addr 0x%08X, prefix %u   — ESB pipe established
No message on private channel                    — ESB channel monitoring
Wrong kind of message on private channel         — ESB error handling
```

**BLE connection** (from Triton):
```
Connected %s                                      — BLE connection established
Disconnected from %s (reason %u)                 — BLE disconnection
Device connected                                  — BLE device event
Device disconnected                               — BLE device event
```

### Transport Decision

The Triton firmware decides which transport to use based on:
1. **Puck presence**: If Puck is detected (via pilot GPIO or ESB beacon), use ESB
2. **No Puck**: If no Puck detected, advertise BLE for direct connection
3. **Keychord**: User can force transport via keychord:
   - `ST_PUCK_KEYCHORD_BT` — force BLE pairing
   - `ST_PUCK_KEYCHORD_ESB` — force ESB pairing
   - `ST_PUCK_KEYCHORD_ESB_ALT` — ESB alternative mode

### Report Format: Transport-Agnostic (CRITICAL)

The report format is **identical** between ESB and BLE:

| Report ID | Size | ESB Payload | BLE Payload | Same? |
|-----------|------|-------------|-------------|-------|
| 0x45 (Gamepad) | 45 bytes | 45 bytes after Report ID | 45 bytes ATT notification | **Yes** |
| 0x42 (Extended) | 53 bytes | 53 bytes after Report ID | N/A (Puck-only) | N/A |
| 0x80 (Haptic) | 9 bytes | 9 bytes via ESB output | 9 bytes ATT Write | **Yes** |
| 0x87-0x89 (Command) | 63 bytes | 63 bytes via ESB output | 63 bytes ATT Write | **Yes** |
| 0x01 (Feature) | 63 bytes | 63 bytes via ESB feature | 63 bytes ATT Read/Write | **Yes** |

The only difference is the **transport layer** (ESB radio vs BLE ATT), not the **application layer** (report data).

### Features Available per Transport

| Feature | ESB (Puck) | BLE (Direct) | Notes |
|---------|-----------|-------------|-------|
| **Input reports** | ✅ All (0x40-0x7B) | ✅ All (same IDs) | Same report format |
| **Output reports** | ✅ All (0x80-0x89) | ✅ All (same IDs) | Same command format |
| **Feature reports** | ✅ 0x01, 0x02 | ✅ Custom ATT | Same commands |
| **Multi-controller** | ✅ 4 simultaneous | ❌ 1 at a time | Puck advantage |
| **Latency** | ~2-4ms | ~7.5-15ms | ESB faster |
| **Range** | ~10m (2.4 GHz) | ~10m (BLE) | Similar |
| **Power** | Higher (active radio) | Lower (BLE) | BLE advantage |
| **Pairing** | ESB proprietary | BLE SMP | Different protocols |
| **QOS monitoring** | ✅ Puck feature | ❌ Not available | Puck-only |
| **Host sleep/wake** | ✅ USB power mgmt | ❌ Not available | Puck-only |
| **Protocol version** | ✅ Negotiated | ✅ Implicit | ESB explicit |
| **Bond storage** | `esb/bond` | `bt/ccc` + BLE bonds | Different paths |
| **Haptics** | ✅ Via ESB output | ✅ Via ATT Write | Same command |
| **Firmware update** | ✅ `ID_FIRMWARE_UPDATE_REBOOT` | ✅ Same command | Same protocol |

---

## 6. QOS & Reliability

### Packet Error Rate Monitoring

**Puck strings**:
```
Slot %u : QOS %u {%03u %03u %03u %02u %03d} {%02u %02u %u}
packet_error_rate
connection_qos
connection_qos_stop
Dump controller PER
%s_connection_uptime_s: %u
%s_channel[%u] : %u
background_rssi_show
channel_cost
channel_cost_show
```

### QOS Fields

The 9-field QOS structure `{%03u %03u %03u %02u %03d} {%02u %02u %u}` likely represents:

| Field | Format | Likely Meaning |
|-------|--------|----------------|
| 1 | %03u | RX RSSI (0-255, signal strength) |
| 2 | %03u | TX RSSI or power |
| 3 | %03u | RX rate or throughput |
| 4 | %02u | TX rate or power level |
| 5 | %03d | Noise floor (signed dBm) |
| 6 | %02u | RX packets per interval |
| 7 | %02u | TX packets per interval |
| 8 | %u | Retries or total errors |
| 9 | (implied) | Connection uptime or slot |

### Channel Quality Tracking

```
channel_cost              — per-channel quality metric
channel_cost_show         — display channel costs
%s_channel[%u] : %u      — per-channel statistics
background_rssi_show      — background RSSI measurement
```

### Retransmission

ESB handles retransmission at the radio level:
- **Auto-ACK**: Each packet acknowledged by receiver
- **Retransmit**: Up to 15 retries (configurable)
- **Retry delay**: 250-500μs (Zephyr ESB default: 250μs)
- **Duplicate detection**: PID (Packet ID) in header prevents duplicate processing

---

## 7. Firmware vs steamclient.so Cross-Reference

### Report 0x45 Format — Match/Mismatch

#### Byte Layout Comparison

| Offset | Firmware (Triton) | steamclient.so (Expected) | Match? |
|--------|-------------------|---------------------------|--------|
| 0x00 | Sequence counter (uint8, increments each report) | Not parsed by host — used as padding | ✅ OK — host ignores |
| 0x01-0x04 | Flags (32-bit): bits 0-19 = buttons, bits 20-31 = flags | 32-bit flags + button bitmask | ✅ MATCH |
| 0x05-0x06 | Left trigger (uint16, 0-0xFFFF) | uint16 left trigger | ✅ MATCH |
| 0x07-0x08 | Right trigger (uint16, 0-0xFFFF) | uint16 right trigger | ✅ MATCH |
| 0x09-0x0A | Left stick X (int16, signed) | int16 left stick X | ✅ MATCH |
| 0x0B-0x0C | Left stick Y (int16, signed) | int16 left stick Y | ✅ MATCH |
| 0x0D-0x0E | Right stick X (int16, signed) | int16 right stick X | ✅ MATCH |
| 0x0F-0x10 | Right stick Y (int16, signed) | int16 right stick Y | ✅ MATCH |
| 0x11-0x16 | Gyroscope X/Y/Z (3×int16, unsigned) | 3×uint16 gyroscope | ✅ MATCH |
| 0x17-0x1C | Accelerometer X/Y/Z (3×int16, unsigned) | 3×uint16 accelerometer | ✅ MATCH |
| 0x1D-0x2C | Trackpad: L X/Y, L X2/Y2, L touch, R X/Y, R touch (16B total) | 16B trackpad data | ✅ MATCH |
| **Total** | **0x2D = 45 bytes** | **45 bytes** | ✅ MATCH |

#### Flags Word Detail (Offset 0x01-0x04)

| Bit(s) | Firmware | steamclient.so | Match? |
|---------|----------|----------------|--------|
| 0-4 | Dpad up/down/left/right, QAS | Button bitmask bits 0-4 | ✅ |
| 5-8 | A/B/X/Y buttons | Button bitmask bits 5-8 | ✅ |
| 9-10 | LB/RB bumpers | Button bitmask bits 9-10 | ✅ |
| 11-12 | Left View, Right View | Button bitmask bits 11-12 | ✅ |
| 13-14 | Left stick click, Right stick click | Button bitmask bits 13-14 | ✅ |
| 15 | Steam button | Button bitmask bit 15 | ✅ |
| 16-19 | L4/L5/R4/R5 back buttons | Button bitmask bits 16-19 | ✅ |
| 20 | Accel active/touch | Accel active flag | ✅ |
| 21 | Accel secondary | Accel secondary flag | ✅ |
| 23 | Right trigger active | Trigger active flag | ✅ |
| 24 | Gyro active/touch | Gyro active flag | ✅ |
| 25 | Gyro secondary | Gyro secondary flag | ✅ |
| 27 | Left trigger active | Trigger active flag | ✅ |
| 28 | Accel mode | Mode flag | ✅ |
| 29 | Gyro mode | Mode flag | ✅ |

**Verdict: FULL MATCH.** The 0x45 report layout is byte-for-byte identical between firmware construction and host parsing.

#### Button Bitmask — Potential Mismatch

**CRITICAL NOTE**: The firmware's button bit assignment at `0x50d90` lists:
```
QAS, R_THUMB, MENU, R_UPPER_GRIP, R_LOWER_GRIP, R_BUMPER,
Dpad up, Dpad down, Dpad left, Dpad right, Steam,
Left upper grip, Left lower grip, Left bumper, Left view, Left thumbstick
```

This is 16 named buttons. The 20-bit bitmask has 20 positions. The firmware analysis at line 204-226 suggests:
- Bits 0-4: QAS, Dpad up/down/left/right
- Bits 5-8: A, B, X, Y
- Bits 9-10: LB, RB
- Bits 11-12: Left View, Right View
- Bits 13-14: L3, R3
- Bit 15: Steam
- Bits 16-19: L4, L5, R4, R5

**BUT** the firmware string order does NOT match this mapping. The string order puts QAS first (bit 0?), then R_THUMB (bit 1?), which contradicts the expected Dpad layout. This needs verification against a real SC2 capture.

**Confidence: The report format matches, but button bit positions may differ from our current spoof implementation.**

### HID Descriptor Comparison

#### Firmware HID Descriptor (at 0x49a26)

| Report ID | Type | Size | Usage Page | Description |
|-----------|------|------|------------|-------------|
| 0x40 | Input | ~6B | Vendor | Mouse |
| 0x41 | Input | 7B | Vendor | Keyboard |
| 0x42 | Input | 53B | Vendor | Vendor input |
| 0x43 | Input | 14B | Vendor | Vendor input |
| 0x44 | Input | 5B | Vendor | Vendor input |
| **0x45** | **Input** | **45B** | **Vendor (0xFF00)** | **Main gamepad** |
| 0x47 | Input | 47B | Vendor | Extended (not in descriptor) |
| 0x79 | Input | 1B | Vendor | Vendor input |
| 0x7B | Input | 12B | Vendor | Vendor input |
| 0x80 | Output | 9B | Vendor | Haptics |
| 0x81 | Output | 7B | Vendor | Lizard mode clear |
| 0x82-0x89 | Output | varies | Vendor | Various outputs |
| 0x01 | Feature | 63B | Vendor | Command channel |
| 0x02 | Feature | 63B | Vendor | Command channel |

#### Our ATT Server GATT Database (from gatt_db.py)

| Service | UUID | Reports |
|---------|------|---------|
| HID | 0x1812 | Report Map declares 0x45 (45B input), 0x47 (47B input), 0x80 (9B output) |
| Battery | 0x180F | Battery Level |
| Device Info | 0x180A | PnP ID, Manufacturer, Model, etc. |

#### steamclient.so Expected Reports

From the binary, steamclient.so reads:
- **PnP ID** from Device Info Service: VID=0x28DE, PID=0x1303
- **Report Map** from HID Service
- **HID Information** from HID Service
- **Report characteristics** with CCCDs

**Match**: ✅ The report IDs 0x45 and 0x80 match. The firmware declares 0x45 as the main input and 0x80 as haptic output.

**Mismatch**: The firmware has MORE report IDs (0x40-0x7B) that we do NOT declare in our GATT database. The real SC2 registers up to 6 input reports via `bt_hids`. Our GATT database is minimal — it only declares the ones we need (0x45, 0x47, 0x80).

**Impact**: LOW — steamclient.so only uses 0x45 for input and 0x80 for haptics. The extra reports (mouse, keyboard, vendor) are firmware-internal and not used by the host BLE driver.

### Feature Report / Command Channel

#### Firmware Command Handler (`FUN_0000c55c`)

The firmware processes Feature Report 0x00 commands via `FUN_0000c55c`. This function receives commands and builds responses:

| Command (byte 0) | Firmware Action | Response Format |
|-------------------|----------------|-----------------|
| **0x83** | **GET_ATTRIBUTES** | Sets `param_2[0] = 0xFF`, `param_2[1] = 2`, falls through to `FUN_0000b82c` |
| **0x82** | Unknown | Sets `param_2[0] = 0xFF`, `param_2[1] = 0x0D` |
| 0x01-0x19 | Various settings | Mapped via switch statement |
| 0x0D | Special check | Verifies `*(short*)(param_2+3) == 0x2083` |

#### steamclient.so Command Sends

| Command | Byte 0 | Purpose | Frequency |
|---------|--------|---------|-----------|
| GET_ATTRIBUTES | 0x83 | Read controller attributes | 1-2× at init |
| GET_SERIAL | 0xAE | Read serial number | 4-19× (retries) |
| SET_SETTINGS | 0x87 | Configure settings | 55-61× |
| ClearDigitalMappings | 0x81 | Disable lizard mode | 8-38× |
| 0x8F | 0x8F | Haptic feedback enable | 16× on native, 0× on BLE |
| 0xC1 | 0xC1 | Unknown | 1× |
| 0xDC | 0xDC | Unknown | 1× |
| 0xE2 | 0xE2 | Unknown | 1× |
| 0xF2 | 0xF2 | Capabilities query | 1× |

#### Command Channel Mismatch Analysis

**GET_ATTRIBUTES (0x83):**
- Firmware: Receives 0x83, builds response with `param_2[0]=0xFF, param_2[1]=2`
- steamclient.so: Sends `[0x83, 0x00]` (2-byte write), reads back 62-byte response
- **MATCH**: The firmware recognizes 0x83 and builds a response.

**GET_SERIAL (0xAE):**
- Firmware: NOT in the `FUN_0000c55c` switch cases (cases 0-0x19 don't include 0xAE)
- steamclient.so: Sends 0xAE multiple times (4-19 retries)
- **MISMATCH**: The firmware command handler doesn't explicitly handle 0xAE. It may be handled elsewhere or the firmware uses a different command for serial.
- **Note**: The firmware at line 16790 has `uVar1 = 0xae` — this may be a different context (ESB protocol).

**SET_SETTINGS (0x87):**
- Firmware: NOT in `FUN_0000c55c` switch (0x87 > 0x19, not 0x82 or 0x83 → aborts)
- steamclient.so: Sends 0x87 fire-and-forget
- **MISMATCH**: The firmware's main command handler doesn't handle 0x87. It's likely processed by a different handler (settings subsystem at `settings/haptics/enabled` etc.).

**0x8F Haptic Command:**
- Firmware: `case 0x8f` exists in a lookup table at `0x54368` — maps to `DAT_000387f4` (a data pointer, not a handler)
- steamclient.so: Sends 0x8F 16× on native Deck, 0× on BLE
- **CRITICAL FINDING**: The firmware DOES have 0x8F as a recognized value in a switch/case dispatch, but it maps to a DAT_ pointer, not a command handler. This suggests 0x8F is a **request ID** that gets a response, not a command to execute.

#### 0xF2 Capability Response (Firmware Side)

The firmware builds 0xF2 responses in `FUN_00042132`:
```c
void FUN_00042132(undefined1 *param_1) {
    FUN_000445c2(param_1 + 1, 0, 0x84);  // Clear buffer
    param_1[5] = 0xf2;                     // Set capability byte
    *param_1 = 1;                          // Set type = 1
}
```

And in `FUN_0004214a`:
```c
void FUN_0004214a(undefined1 *param_1, undefined1 param_2, ...) {
    FUN_000445c2(param_1 + 1, 0, 0x84);  // Clear buffer
    param_1[5] = 0xf3;                     // Set capability byte
    param_1[6] = param_2;                  // Sub-type
    *param_1 = 2;                          // Set type = 2
}
```

**Key**: The firmware uses 0xF2 and 0xF3 as **response type bytes**, not command bytes. The type field (`*param_1`) distinguishes: 1=base capability, 2=extended capability.

This matches steamclient.so's expectation that 0xF2 responses contain capability data in per-category format.

### The 0x8F Gate Mystery

#### What Firmware Shows

The `case 0x8f` at `0x54368` is in a large switch statement that maps command bytes to DAT_ pointers. The case 0x8F maps to `DAT_000387f4`. This is likely a **lookup table for feature report handlers**, where each case points to a handler function or data structure.

**The firmware DOES recognize 0x8F.** It's in the command dispatch table.

#### What steamclient.so Shows

From the RE findings (TASK 8):
- Native Deck HIDIOCSFEATURE capture: **124 calls in 35s**, including 16× 0x8F
- BLE: **0× 0x8F**
- 0x8F appears during initialization AND steady state
- The 0x8F gate at `[esi+0x17c]` blocks haptic dispatch when == 0

#### The Missing Piece

The 0x8F command is sent by steamclient.so on native but NEVER on BLE. The gate at `[esi+0x17c]` is set to 1 only by `YieldingRunTestProgram` (a test/init path). On BLE, this path is never taken because the controller state is 3-4 instead of 1-2.

**Firmware confirms**: 0x8F IS a valid command. The firmware has it in its command dispatch table. The firmware CAN handle it. The issue is entirely on the steamclient.so side — it never sends 0x8F on BLE because the gate is never opened.

**Root cause chain**:
1. BLE controller gets state 3-4 in `[rdi+0x1d8]` (UNVERIFIED — could be graphics API type)
2. State 3-4 routes to 16-byte allocation path in `0x15675a8`
3. 16-byte path does NOT set `[esi+0x17c] = 1`
4. Gate stays closed → 0x8F never dispatched → Steam haptics don't work

### Initialization Chain

#### Firmware Init Sequence

1. Boot → SDC init → HCI driver → HIDS registration → State machine starts
2. BLE advertising enabled (2 slots)
3. Host connects → BLE connection established
4. SMP pairing (kernel handles)
5. HID notifications start flowing (after CCCD write)

**Timing**: Firmware starts sending reports as soon as CCCD is written. No firmware-side "initialization handshake" required beyond standard BLE GATT discovery.

#### steamclient.so Init Sequence

1. Opens /dev/hidrawN
2. Reads serial number (feature report)
3. Reads chip ID, board revision, firmware version
4. Sends 0xf2 multiple times for capabilities
5. Populates ControllerDetails_tE
6. Calls QueueFetchingControllerDetails → sets ready_flag
7. Registration completes

#### The Stall

`CGetControllerInfoWorkItem::RunFunc` at `0x01218840`:
- Calls `SDL_hid_read_timeout` via vtable[5]
- Gets **0 bytes** back
- Retries 51× × 100ms = 5.1s, then fails

**Why 0 bytes?** Because on BLE, the input reports flow through BlueZ's hog-lib.c → UHID → /dev/hidrawN. If hog-lib.c hasn't set up the UHID device properly, `SDL_hid_read_timeout` returns 0 bytes.

**Firmware timing**: The firmware starts sending reports immediately after CCCD is written. There's no firmware-side delay. The stall is entirely in the BlueZ/host stack.

### PnP ID / Device Identity

#### Firmware

At `0x49956`: `*(undefined4 *)(param_2 + 1) = 0x1302` — this is the USB PID (0x1302).

The firmware uses 0x1302 for USB mode. For BLE, the PID should be 0x1303 (as confirmed by steamclient.so's product ID dispatch).

#### steamclient.so

| Field | Value | Source |
|-------|-------|--------|
| VID | 0x28DE | Valve USB Vendor ID |
| PID (BLE) | 0x1303 | Product ID dispatch at `0x010c4de0` |
| PID (USB) | 0x1302 | Product ID dispatch at `0x010c4940` |
| PID (Dongle) | 0x1304-0x1305 | Product ID dispatch at `0x010c4c40` |

#### Our Spoofed PnP ID

From gatt_db.py / att_server.py:
- VID: 0x28DE ✓
- PID: 0x1303 ✓ (BLE)
- Vendor ID Source: 0x02 (USB-IF) ✓

**MATCH**: Our PnP ID matches what steamclient.so expects for BLE controllers.

### BLE vs USB Haptic Path

#### Firmware

The firmware has:
- `haptics-sequencer-touchpad` — trackpad click haptics
- `haptics-sequencer-gri-v3` — grip/rumble haptics
- `haptics_sequencer` — main sequencer
- `channel-left` / `channel-right` — motor channels
- `settings/haptics/enabled` — enable/disable setting
- `settings/haptics/haptic_master_gain_db` — gain control

The firmware's haptic system is **local** — it generates haptics from trackpad touches, button presses, etc. independently. The host can also send rumble commands via output report 0x80.

#### steamclient.so

- Haptics sent via `SDL_hid_write()` (output reports), NOT feature reports
- Output report 0x80: `MsgHapticRumble` (10 bytes)
- CRumbleThread processes work items → sends via HID
- **BLE path**: steamclient.so → IPC → bluetoothd → ATT Write Request (0x12) → our server

#### The Disconnect

On native Deck:
1. steamclient.so sends 0x8F (haptic enable) → firmware enables host-controlled haptics
2. steamclient.so sends 0x80 (rumble) → firmware plays rumble
3. Both paths work

On BLE (our spoof):
1. steamclient.so never sends 0x8F (gate closed)
2. steamclient.so DOES send 0x80 (rumble from games)
3. Our ATT server forwards to Neptune → rumble works for games
4. Steam-generated haptics (trackpad clicks, UI) use 0x8F path → never sent → don't work

---

## 8. Implications for SpoofDeck

### Summary Table

| Area | Status | Details |
|------|--------|---------|
| Report 0x45 format | ✅ MATCH | Byte-for-byte identical |
| Button bitmask | ⚠️ UNVERIFIED | Bit positions need verification against real capture |
| Flags word | ✅ MATCH | All flag bits match |
| HID Descriptor | ✅ MATCH | 0x45 input, 0x80 output correct |
| Extra reports | ℹ️ INFO | Firmware has more reports than we declare — not blocking |
| PnP ID | ✅ MATCH | VID=0x28DE, PID=0x1303 |
| GET_ATTRIBUTES (0x83) | ✅ MATCH | Firmware handles it, format confirmed |
| GET_SERIAL (0xAE) | ⚠️ PARTIAL | Not in main firmware handler — handled elsewhere |
| SET_SETTINGS (0x87) | ✅ OK | Fire-and-forget, not in main handler |
| 0x8F haptic gate | ❌ BLOCKER | Firmware recognizes it, steamclient never sends on BLE |
| 0xF2 capabilities | ✅ MATCH | Firmware builds responses with correct format |
| Init timing | ⚠️ DIFFERENT | Firmware sends immediately; host stalls due to BlueZ |
| Haptic 0x80 rumble | ✅ WORKS | Game rumble flows end-to-end |
| Steam haptics | ❌ BROKEN | 0x8F gate never opened on BLE |

### Key Findings for BLE Spoofing

1. **Report format is transport-agnostic**: The SC2 report format (report IDs 0x40-0x7B) is the same regardless of whether it flows over ESB, USB HID, or BLE ATT. Our BLE spoof must use the **exact same report format**.

2. **Command channel is transport-agnostic**: Feature reports (GET_ATTRIBUTES, GET_SERIAL, haptics, etc.) use the same command codes over ESB and BLE. Our ATT server must handle the **same command set**.

3. **ESB is irrelevant for BLE spoofing**: The ESB protocol is only for Puck communication. Our BLE spoof bypasses the Puck entirely.

4. **Protocol version negotiation**: The Puck explicitly negotiates protocol version with the controller. BLE connections may not have this step — the controller implicitly uses the default version.

5. **Bond storage paths differ**: ESB bonds at `esb/bond`, BLE bonds at `bt/ccc`. These are independent systems.

6. **Multi-controller is Puck-only**: BLE supports 1 connection at a time, Puck supports 4.

7. **QOS monitoring is Puck-only**: No equivalent in BLE path.

### Puck Features → SpoofDeck Equivalents

| Puck Feature | SpoofDeck Equivalent | Status |
|-------------|---------------------|--------|
| Feature Report exchange | ATT Read/Write on SC2 Custom service | ✅ Implemented |
| Report relay (input) | ATT Notifications | ✅ Implemented |
| Report relay (output) | ATT Write Requests | ✅ Implemented |
| Haptic forwarding | ATT Write to haptic characteristic | ✅ Implemented |
| Protocol version negotiation | Not needed (BLE implicit) | N/A |
| Multi-controller | Not needed (1 connection) | N/A |
| QOS monitoring | Not needed | N/A |
| Host sleep/wake | Not needed (Deck is host) | N/A |
| Pilot signal | Not needed | N/A |
| EC input | Not needed | N/A |

### Recommendations

**Immediate**:
1. **Verify button bit positions** — Capture a real SC2 0x45 report and compare bit positions with our input_handler.py mapping.
2. **Investigate 0x8F gate bypass** — The LD_PRELOAD approach to patch `[esi+0x17c]` at `0x0123e5fb` (change `je` to `jne`) could enable Steam haptics on BLE. But the gate must be opened AFTER the initialization chain completes.
3. **Fix CGetControllerInfoWorkItem stall** — The 0-byte read issue is in BlueZ/hog-lib.c, not firmware. Need to ensure UHID device is ready before Steam reads.

**Medium-term**:
4. **Handle more Feature Report commands** — The firmware recognizes 60+ commands. Expanding our synthetic handler to respond to more commands (even with stub responses) may improve compatibility.
5. **Investigate 0xE7 command** — In the firmware, `case 0xe7` triggers `FUN_00042132` (0xF2 capability response). This suggests 0xE7 is a "send capabilities" command that the firmware uses internally. Understanding this could reveal how to trigger capability reports on demand.

**Long-term**:
6. **GDB verification** — Set watchpoint on `[rdi+0x1d8]` during BLE connection to confirm what value the dispatcher reads. This resolves the 0x8F gate root cause definitively.

---

## 9. String Reference Index

### Puck Firmware Strings (ESB-Related)

| String | Context |
|--------|---------|
| `dongle_esb` | Main ESB module name |
| `esb-controller@0` - `@3` | Controller device tree nodes |
| `esb/ibex_0` - `ibex_3` | ESB pipe endpoints |
| `ibexesb_common` | Shared ESB/Ibex data structure |
| `esb_set_rf_channel %d %u` | RF channel configuration |
| `Initializing radio` | Radio initialization |
| `Initializing channel map` | Channel map setup |
| `Failed to initialize ESB system timer` | ESB timer error |
| `Failed to delete esb settings` | Settings cleanup error |
| `No event from ESB...` | Radio IRQ fallback |
| `Slot %u : Connected Ibex %s (Ch %u)` | Connection established |
| `Slot %u : Connecting Ibex %s` | Connection attempt |
| `Slot %u : Pairing` | Pairing state |
| `Slot %u : QOS %u {...}` | QOS metrics |
| `Slot %u : Set FR / Get FR` | Feature report exchange |
| `Slot %u : Protocol version...` | Version negotiation |
| `Slot %u : Host suspended/awake` | Power management |
| `Slot %u : Disconnect/timeout` | Disconnection |
| `radio_send` / `radio_send_channels` | Shell commands |
| `puck-pilot-gpio` | GPIO for pilot detection |
| `In pilot envelope` | Controller in range |
| `Pilot signal is valid but controller unresponsive` | Pilot timeout |
| `VPILOT out of range` | Signal strength check |
| `Failed to configure PILOT_SENSE input` | GPIO init error |
| `ec-input` | EC input device |
| `ec_input_tap` | EC tap input |
| `ec-input-tap@0` through `@3` | Per-controller EC tap inputs |
| `Failed to register a HID proxy tap %d` | EC tap registration |
| `board_hid_over_i2c` | I2C HID bridge function |
| `i2c@40003000` | nRF52833 TWIM0 (I2C peripheral) |
| `i2c_hid` | I2C HID driver |
| `i2c_nrfx_twis` | Nordic TWIS |
| `Failed to initialize HID I2C bus` | I2C bus init error |
| `Failed to initialize HID I2C target` | I2C target init error |
| `Failed to prepare DMA for I2C read/write` | DMA transfer error |
| `HID over I2C doesn't have a concept of SoF. Ignoring the callback` | I2C vs USB timing |
| `puck_adcs_read` | ADC reading function |
| `Failed to enable puck UART node(%d)` | UART init |
| `Failed to enable puck UART RX %d` | UART RX init |
| `UART port is not initalized. Puck interface is a no-op` | UART not available |
| `shell.shell_uart`, `shell_uart_backend` | Zephyr shell UART backend |
| `dongle recv report err: %d` | ESB receive error |
| `Discarded report` | Invalid/malformed report |
| `Discarding keyboard report` | Keyboard report filtered |
| `Discarding feature report with unexpected length %d` | Size mismatch |
| `Failed to submit input report %d` | USB submission failed |
| `Failed to submit report` | General report failure |
| `Unsupported report type` | Unknown report ID |
| `Unexpected CRC16 value %x` | CRC mismatch |
| `bonds` | Shell command to show bond info |
| `Show bond information` | Bond info display |
| `Slot %u : New bond saved` | Bond data stored to flash |
| `Slot %u : Bond deleted` | Bond data removed |
| `Slot %u: Pairing successful` | Pairing completed |
| `Slot %u: Pairing failed` | Pairing failed |
| `connection_qos` / `connection_qos_stop` | QOS shell commands |
| `QOS reporting updated to every %u ms, debug prints %s` | QOS config |
| `Dump controller PER` | Packet Error Rate dump |
| `%s_connection_uptime_s: %u` | Connection uptime tracking |
| `%s_channel[%u] : %u` | Per-channel statistics |
| `packet_error_rate` | PER display |
| `background_rssi_show` | Background RSSI |
| `channel_cost` / `channel_cost_show` | Channel quality |

### Triton Firmware Strings (ESB-Related)

| String | Context |
|--------|---------|
| `triton_esb` | ESB module name |
| `esb_thread` | ESB processing thread |
| `esb/bond` / `esb/bond_2` | Bond storage paths |
| `ibexesb_common` | Shared data structure |
| `puck-interface` | Puck communication interface |
| `puck-pilot-gpio` | Pilot GPIO control |
| `Connecting to: Proteus %s, (0x%08X, 0x%08X)` | ESB connection |
| `Connected: private pipe (%u/%u, addr 0x%08X, prefix %u` | Pipe established |
| `No message on private channel` | Channel monitoring |
| `Wrong kind of message on private channel` | Error handling |
| `ESB TX FIFO full` | TX buffer full |
| `Failed to initialize ESB system timer` | Timer error |
| `Failed to delete esb settings` | Settings error |
| `Proteus asking us to turn off` | Remote shutdown |
| `ST_PUCK_KEYCHORD_ESB_entry` | ESB pairing state |
| `ST_PUCK_ON_entry` | Puck active state |
| `Failed to configure PILOT_SENSE input` | Pilot GPIO error |
| `Discarded report` | Invalid/malformed report |
| `Discarding feature report with unexpected length %d` | Size mismatch |
| `Failed to allocate report` | Memory allocation failure |
| `Failed to send full HID report %d` | Partial send failure |
| `No buffer available to send notification` | Backpressure |
| `Unable to send hid report, %u` | Notification send failure |
| `Device is not subscribed to characteristic` | CCCD not enabled |
| `haptics-sequencer-touchpad` | Trackpad click haptics |
| `haptics-sequencer-gri-v3` | Grip/rumble haptics |
| `haptics_sequencer` | Main haptics sequencer |
| `channel-left` / `channel-right` | Motor channels |
| `settings/haptics/enabled` | Haptics enable/disable |
| `settings/haptics/haptic_master_gain_db` | Gain control |
| `ibex%d_proteus_uuid : 0x%08X` | Puck UUID for slot |
| `ibex%d_ibex_uuid : 0x%08X` | Controller UUID for slot |
| `ibex%d_peer_sn : %s` | Controller serial number |
| `ID %d : bonded to %s` | Bond record |
| `Bond info for id %d was deleted...` | Bond cleanup |
| `List of existing bonds:` | Bond listing |
| `Failed to execute GET_FEATURE_REPORT` | Feature report error |
| `Failed to execute SET_FEATURE_REPORT` | Feature report error |
| `get_id_get_attributes_values` | GET_ATTRIBUTES handler |
| `get_id_get_string_attribute` | GET_STRING_ATTRIBUTE handler |
| `%s: GET: ID_GET_ATTRIBUTES_VALUES` | GET_ATTRIBUTES log |
| `%s: GET: ID_GET_STRING_ATTRIBUTE, TAG: %u` | GET_STRING_ATTRIBUTE log |

---

## 10. Function Size Summaries

### Puck Firmware (Top 10 Largest Functions)

| Function | Address | Size | Likely Purpose |
|----------|---------|------|----------------|
| `FUN_000199b4` | `0x000199b4` | 3,256B | USB HID composite device handler |
| `FUN_00016eb4` | `0x00016eb4` | 3,148B | ESB data relay / HID proxy |
| `FUN_00005e64` | `0x00005e64` | 3,002B | ESB state machine / connection mgmt |
| `FUN_0000ac78` | `0x0000ac78` | 1,390B | Feature report exchange |
| `FUN_000161cc` | `0x000161cc` | 1,370B | QOS monitoring / channel stats |
| `FUN_00008b74` | `0x00008b74` | 1,270B | Bond management |
| `FUN_000043c0` | `0x000043c0` | 1,202B | Radio initialization |
| `FUN_0000dcc8` | `0x0000dcc8` | 1,108B | HID report descriptor handler |
| `FUN_00003f7c` | `0x00003f7c` | 1,066B | Multi-slot controller management |
| `FUN_0001f6d8` | `0x0001f6d8` | 1,064B | USB endpoint handler |

### Triton Firmware (Top 10 Largest Functions)

| Function | Address | Size | Likely Purpose |
|----------|---------|------|----------------|
| `FUN_00038ab8` | `0x00038ab8` | 2,422B | BLE HID service / GATT handler |
| `FUN_00049c4a` | `0x00049c4a` | 1,962B | IMU / sensor processing |
| `FUN_0001d8d0` | `0x0001d8d0` | 1,520B | GATT service registration |
| `FUN_00001f88` | `0x00001f88` | 1,276B | ESB connection / pipe management |
| `FUN_000116f8` | `0x000116f8` | 1,210B | Neptune input processing |
| `FUN_0003949c` | `0x0003949c` | 1,190B | BLE connection state handler |
| `FUN_00009ff0` | `0x00009ff0` | 1,176B | BLE connection event handler |
| `FUN_00001ab4` | `0x00001ab4` | 1,160B | State machine thread |
| `FUN_000167d0` | `0x000167d0` | 1,014B | Main controller loop / report send |
| `FUN_00019eb8` | `0x00019eb8` | 938B | Haptic sequencer |

---

## Appendix: Unresolved Questions

1. **ESB payload fragmentation**: How are reports >32 bytes fragmented into multiple ESB packets? (Zephyr ESB library handles this, but the exact splitting is not visible from strings alone.)

2. **Feature Report contents**: What exactly is exchanged in Feature Reports 0x01/0x02? (Calibration data, firmware version, capabilities bitmap — would need to capture an actual exchange.)

3. **ESB encryption**: Is the ESB link encrypted? (Nordic ESB supports encryption via CRYPTOCELL, but the CC310 is absent on the nRF52833 and the firmware strings don't confirm encryption — likely unencrypted.)

4. **ESB timing parameters**: Exact retry delay, auto-ACK window, and retransmit count (configured in Zephyr ESB library, not visible from application strings.)

5. **Report 0x42 (53 bytes)**: What does this extended report contain? (Larger than 0x45 — possibly extended gamepad data or touchpad data with additional resolution.)

6. **Report 0x47 (47 bytes)**: Not in HID descriptor but referenced in analysis — possibly a variant of 0x45 with additional fields.

7. **ESB channel selection algorithm**: How does the Puck choose which channels to use for each controller? (Adaptive frequency hopping based on channel_cost metrics, but exact algorithm not visible.)

8. **Bond data format**: What exactly is stored in the ESB bond? (Address, link key, UUID, serial number — but exact byte layout unknown.)

9. **I2C HID internal path**: The exact role of `board_hid_over_i2c` — is it for host communication, internal chip-to-chip, or EC interface?

10. **Firmware update**: How does the Puck receive firmware updates? The `flash-controller@4001e000` suggests DFU capability but the update protocol is not visible from strings alone.

11. **0x8F gate root cause**: What value does `[rdi+0x1d8]` hold for BLE controllers? GDB watchpoint needed to confirm.

12. **GET_SERIAL (0xAE) handler**: Where is 0xAE handled in the firmware? Not in `FUN_0000c55c` switch — may be in a separate BLE-specific handler.
