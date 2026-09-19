# Findings Backlog

> Current open problems, known facts, and what to try next.

---

## Registration Status: STABLE

Serial format `F0000-0000-00000000`. `BYieldingCompleteSteamControllerRegistration` completed. `CPulseHapticWorkItem` being created. Controller fully activated.

**"Deck Controller PCB Serial# invalid"** — cosmetic only. BLE PCB validation in `FUN_0122e4c0`; does NOT block registration.
**"Controller Serial# invalid"** — BLOCKING. `V_strncmp` check requires `serial[0]='F'` (0x46), `byte[2]=0x01` (success status).

**Working**: Gamepad, trackpads, gyro, back buttons, standard HID input, in-game rumble via SDL_RumbleJoystick().
**Not working**: Steam-generated haptics (trackpad clicks, UI feedback) do NOT produce rumble.

---

## Haptic Architecture

### Path 1: Firmware-Local Haptics (SC2 nRF52833 only)
Trackpad touch, button press, grip touch/detouch generate haptic feedback entirely within firmware.
- Modules: `haptics-sequencer-touchpad`, `haptics-sequencer-gri-v3`, `haptics_sequencer`
- Scripts are firmware-internal, selected by ID (host does NOT upload patterns)
- Triggered by firmware events (FUN_00015170 → FUN_0003347c)
- **Not available to SpoofDeck** — Deck's Neptune controller lacks SC2's haptic sequencer

### Path 2: Game Rumble via 0x80 (WORKS on any transport)
Games calling SDL_RumbleJoystick() → SDL_hid_write() → UHID → hog-ll → ATT Write Request → _on_haptic_write() → Neptune ERM motors
- Uses 0x80 output report (9 bytes)
- NOT gated by [esi+0x17c]
- Works on USB, Dongle, and BLE

### Path 3: Steam-Generated Haptics via 0x8F (USB/Dongle ONLY)
Steam UI → CPulseHapticWorkItem → 0x8F sub-command dispatcher → firmware haptic script trigger
- 0x8F is a multiplexed command envelope (sub-command byte selects haptic effect)
- Firmware receives 0x8F and plays the corresponding internal haptic script
- **BLOCKED on BLE by design** — see 0x8F Gate section below

### Confirmed: In-Game Rumble Pipeline
```
Game → SDL_RumbleJoystick(low_freq, high_freq)
  → SDL_hid_write(device, buffer, 10)
  → write("/dev/hidrawN") → kernel hidraw
  → uhid_hid_output_raw() → UHID_OUTPUT → BlueZ
  → forward_report() → gatt_write_char() [ATT 0x12] → handle 0x0019
  → _on_haptic_write() → _forward_haptic_to_neptune()
  → PackedRumbleReport to /dev/hidraw3 → Neptune ERM motors
```

### SC2 Haptic Format (Report ID 0x80)
```
Byte 0:   0x80 (report ID)
Byte 1:   type (uint8) — 0 = HAPTIC_TYPE_OFF
Byte 2-3: intensity (uint16 LE)
Byte 4-5: left.speed (uint16 LE) — low_frequency_rumble
Byte 6:   left.gain (int8) — 0
Byte 7-8: right.speed (uint16 LE) — high_frequency_rumble
Byte 9:   right.gain (int8) — 0
```

### Rumble Format (InputPlumber's PackedRumbleReport)
```
64-byte struct: [0xeb, 0x09, 0x00, 0x00, 0x00, left_lo, left_hi, right_lo, right_hi, ...]
```

### BlueZ hog-lib.c Analysis (Confirmed)
1. Haptics path is `UHID_OUTPUT` → `forward_report()` — NOT `UHID_SET_REPORT` → `set_report()`
2. `forward_report()` uses ATT Write Request (0x12), NOT Write Command (0x52)
3. `forward_report()` silently drops writes if report not found
4. `find_report()` bug: uses `hog->flags` instead of `hog->uhid_flags`
5. `bt_uhid_create()` doesn't set `ev.u.create2.flags`

---

## Init Chain Timing Fix

The init chain in CGetControllerInfoWorkItem::RunFunc stalls because SDL_hid_read_timeout gets 0 bytes. The fix sends multiple zero notifications when CCCDs are enabled to pre-fill the UHID queue:
- First notification is consumed by UHID device creation (UHID_CREATE2), not forwarded as UHID_INPUT2
- Subsequent notifications become UHID_INPUT2 and reach /dev/hidrawN
- 5 notifications sent with staggered delays (10-50ms) via threading to avoid blocking ATT thread
- Requires `import time as _time` and `import threading as _threading` (was broken when `time` was not imported at module level)

Note: Even with init chain completing, Steam haptics still don't work on BLE due to the 0x8F gate (see below).

---

## The 0x8F Gate — Multi-Layer Haptic Block (BLE Design Limitation)

The haptic scheduler function at `0x0123e5d0` (called via vtable from the controller update loop) has **five layers of defense** preventing BLE controllers from receiving Steam haptics. Even with LD_AUDIT/LD_PRELOAD patching all known gates, the scheduler's vtable integrity checks reject the BLE controller object.

### Layer 1: Primary Gate — `[esi+0x17c]` ✅ PATCHABLE
- `cmp byte [esi+0x17c], 0; jne +0x290` at vaddr `0x0123e5fb`/`0x0123e602`
- BLE: gate=0 → fall-through (skip haptic dispatch)
- USB/Dongle: gate=1 → jump to dispatch
- **Patch**: `jne` → `jmp` (unconditional)

### Layer 2: param_4 Active Flag ✅ PATCHABLE
- `test al, al; je` at vaddr `0x0123e89a`
- param_4 is the haptic pulse active byte passed by the caller
- When gate was NOT set (BLE fall-through), param_4=0 → immediate return
- **Patch**: `je` → `nop nop`

### Layer 3: Transport Type `[esi+0x10c]` ✅ PATCHABLE
- `cmp byte [esi+0x10c], 0; je` at vaddr `0x0123e8b6`
- BLE: `[esi+0x10c]=0` → takes alt toggle path (may not process haptics)
- USB/Dongle: `[esi+0x10c]=1` → takes main path
- **Patch**: `je` → `jmp` (force main path)

### Layer 4: Secondary Transport Check `[esi+0x10c]` ✅ PATCHABLE
- `cmp byte [esi+0x10c], 0; je +0x11b` at vaddr `0x0123e6df`
- After effect submission attempt, re-checks transport type
- **Patch**: `je` → `nop nop nop nop nop nop`

### Layer 5: Vtable Integrity Checks ❌ NOT PRACTICAL TO PATCH
- `cmp [edx+0x74], edi` at `0x0123e640` and `cmp [edx+0x84], edx` at `0x0123e65a`
- Validates the controller object's vtable entries against expected function pointers
- BLE controller vtables differ from USB/Dongle vtables (set during `CGetControllerInfoWorkItem::RunFunc`)
- Even if patched, deeper calls (`FUN_0129ce50` and beyond) may have additional checks
- **Not patchable in practice** — each patched check reveals another layer; the controller object's internal state is fundamentally different for BLE connections

### LD_AUDIT Implementation (SLSsteam pattern)
- Library: `patches/sc2_gate_audit.c` — 32-bit, uses `la_objopen` callback when `steamclient.so` loads
- Strips itself from `LD_AUDIT` in `la_preinit` to prevent re-injection
- Only activates in `steam` process (checks `/proc/self/comm`)
- Wrapper: prepend `export LD_AUDIT=...` to `steam.sh`
- **Confirmed**: All 4 patches applied and verified in running process memory via `/proc/PID/mem`
- **Result**: CPulseHapticWorkItem still runs 0.0ms. Zero 0x8F commands on Deck.

### Why It's Not Fixable via Patching
The vtable checks validate the controller object's class hierarchy, which is set during Steam's initialization based on transport type (BLE vs USB). The controller struct at `esi` has fundamentally different vtable pointers, state fields (`[esi+0x10c]`, `[esi+0x17c]`, `[esi+0x6c]`), and function pointers depending on whether it was initialized as a BLE or USB controller. Patching the scheduler's checks doesn't change the underlying controller object — it just skips validation, which may cause crashes in downstream code that assumes USB-like behavior.

**Bottom line**: The haptic path has 5+ layers of defense. Even if all were patched, the controller object's state is set by Steam's BLE initialization code, which doesn't configure the haptic pipeline fields. This is a fundamental architectural limitation — not a bug to fix.

### What Works Today
- ✅ Game rumble via `SDL_RumbleJoystick` → 0x80 (games using SDL rumble API)
- ✅ Full controller input (gamepad, trackpads, gyro, back buttons, SC2 custom reports)
- ✅ Steam recognizes it as Steam Controller 2026 with full Steam Input
- ❌ Steam-generated haptics (trackpad clicks, UI feedback) — blocked by 5-layer defense in steamclient.so
- ❌ Real SC2 also doesn't get these over BLE — only via USB Puck dongle

---

## Previously Open Questions (Resolved)

1. ~~Why does Steam retry GET_SERIAL 19+ times?~~ — GET_SERIAL byte[2] must be 0x01 (success status). Values 0x00 or 0x04 trigger "Controller Serial# invalid" (BLOCKING). Serial[0] must be 'F' (0x46) per V_strncmp with count=1 at 0x10c29b3.
2. ~~What does [esi+0x160] hold for BLE?~~ — Confirmed: BLE controllers get state 3-4 at [rdi+0x1d8], which routes to the 16-byte allocation path that never opens the haptic gate.
3. What triggers the controller message dispatcher (0x015675a8)? — Invoked through vtable dispatch; vtable is runtime-constructed.
4. Why is handler+0x08 BLE flag never read? — BLE vs USB distinction made elsewhere in PID dispatch table.

---

## Next Steps

### 1. ~~0xbc Classification Patch~~ COMPLETED — Not the vtable selector

**Result**: Patched `0x121ba9c` (BLE class 2 → USB class 1). No `0x8F` haptics. `+0xbc` is a post-construction state field, not the vtable/class constructor selector. The controller object is already constructed with vtable A when `+0xbc` is written.

### 2. ~~Vtable Entry Patch~~ COMPLETED — A→C globally crashes Steam

**Vtable layouts found** (from memory scan):
- **Vtable A** (`0x02e6ce2c`): BLE controller vtable, most common. vt[0x74]=`0xd3d59920`, vt[0x84]=`0xd3d59930`
- **Vtable B** (`0x02e6dbe4`): Alternate class. vt[0x74]=`0xd3dc73f0`, vt[0x84]=`0xd3dc3c40`
- **Vtable C** (`0x02e6c940`): Scheduler-expected entries. vt[0x74]=`0xd3d59c50`, vt[0x84]=`0xd3d59160`

**Vtable C is NOT in the binary as a static definition** — it exists only at runtime in the r--p (rodata) segment. Its entries are the scheduler's expected function pointers.

**Patch attempt**: Modified vtable A entries at `+0x74` and `+0x84` to match vtable C. Patches applied successfully but Steam **segfaulted immediately**.

**Diagnosis**: Vtable A is shared by MANY objects (not just the target controller). Vtable C methods expect a different object layout (field offsets, constructor-initialized state). Globally replacing A→C entries corrupts non-controller objects that also use vtable A.

**Key finding**: Vtable identity alone is insufficient — constructor/layout/init state matters. The controller object's C++ class layout must match the vtable's method expectations.

### 3. ATT Server Spec Compliance (Not Blocking)

These are correctness improvements, not blockers. Fix one at a time, test between each.

1. Read Blob error code (0x01 → 0x07)
2. MTU caps on Read/Notify PDUs
3. PDU length validation
4. ATT permission checking
5. Diagnostic handle labels

### 4. Full Firmware Dump — DOWNGRADED (no longer blocking protocol work)

`ibex_firmware.bin` is 66.9% of nRF52833's 512KB flash. Command descriptors at 0x59b10–0x5a332 beyond the dump. J-Link/SWD needed for full flash dump and further firmware RE.

Update 2026-09-19: the missing descriptors are firmware-internal dispatch metadata in a factory partition no DFU has ever carried — a spoof does not need them. All wire-visible behavior has since been implemented from sc26re handler semantics + the SDL opcode enum (`src/sc2_commands.py`: 0x80/0x82 mapping store, 0xF2 version variants, 0x84/0x8A/0xBE, dongle/audio/calibration/Deck-only ACKs). The dump is now interesting only for firmware-internal questions. See `research/firmware-dump-assessment.md` follow-up.

### 5. Steam Haptics — ABANDONED

**Final conclusion**: Steam-generated haptics (trackpad clicks, UI feedback via 0x8F) cannot flow to a BLE-spoofed controller. The block is architectural, not a patchable bug.

**Evidence**:
- The haptic scheduler at `0x123e5d0` is **never called** for BLE controllers (GDB breakpoints never fire despite active Deck connection and input flow)
- `CPulseHapticWorkItem(0)` fires with `running 0.0ms` — the work item completes instantly because it never enters the scheduler
- The haptic block happens BEFORE the scheduler — upstream in controller setup or work item dispatch
- A real SC2 also doesn't get Steam-generated haptics over BLE (only game rumble via 0x80 works, which uses SDL_RumbleJoystick → hidraw → UHID → ATT)

**What was tried** (all failed to produce 0x8F):
1. ~~0xbc classification patch (0x121ba9c)~~ — post-construction field, no effect
2. ~~Global vtable A entry patch (A→C)~~ — crashed Steam (vtable A shared by many objects, C methods incompatible with A layout)
3. ~~Targeted vtable pointer redirect (single object)~~ — couldn't identify the correct object (152 candidates with gate=1,transport=1)
4. ~~GDB capture of esi at scheduler~~ — scheduler never called for BLE

**What works**: Game rumble via 0x80 (SDL_RumbleJoystick) — this is the existing haptic path and doesn't require Steam-generated 0x8F commands.

**Infrastructure preserved** (for potential future use):
- LD_AUDIT injection: 64-bit no-op stub + 32-bit patcher
- SLSsteam-style PATH wrapper: `~/.local/share/SLSsteam/path/steam`
- Vtable definitions: A at `g_code_base + 0x02e6ce2c`, C at `g_code_base + 0x02e6c940`
- ELF address rule: `runtime = ELF_load_base + vaddr`

---

*Last updated: 2026-07-03*
