# Firmware Dump Assessment (2026-09-18)

Question: is a full SC2 flash dump already available online, filling the gap
in `triton-firmware-reference.md` (command descriptors at `0x59b10`–`0x5a332`
beyond the 350,528-byte `ibex_firmware.bin`)?

## Verdict

**No full dump exists publicly. No download substitutes for an SWD dump.**
But the search produced (a) the newest firmware with analysis, (b) proof the
descriptors were *never* in any DFU, (c) two high-value reference repos, and
(d) a correction to our own SoC identification.

## Sources pulled

| Repo | Location | License | Value |
|------|----------|---------|-------|
| `OpenSteamController/Ibex-Firmware` | web (catalog + blobs) | n/a (Valve blobs) | Every Valve-shipped `.fw` with `index.json` (sizes, SHA-256, CRC32, Steam versions) |
| `CouchTurtle/sc2-research` | `~/Documents/sc2-research` | MIT | `analyze_fw.py`, `attr_query.py`, `fw_changelog.py`; `docs/FIRMWARE_PROTOCOL.md` (update protocol, opcodes, settings, HW map) |
| `mwdmwd/sc26re` | `~/Documents/sc26re` | AGPL-3.0 | Working open Zephyr firmware for the controller; `valve_feature.h` opcode enum, 83-entry settings registry, `flash.py` update tooling |
| `ArthFink/nrf52840-OpenThread` | `~/Documents/nrf52840-OpenThread` | — | **Not useful**: Thread-networking firmware builder for generic dongles; no BLE HID, no Valve code |

Raw Valve `.fw` blobs are kept in Temp only (proprietary, never committed).
`sc2-research` tools are MIT; `sc26re` is AGPL — read and learn from it, do
not copy its code into this MIT repo.

## Newest firmware analysis (IBEX_FW_6AA43B55, 2026-09-17)

- File 395,172 B = 32-byte header + 395,140 B payload. Header independently
  verified: `magic=0xD2D86467`, `payload_size` matches, CRC32 at `0x08`
  matches payload (`0x2A7A3350`, same value the Ibex-Firmware catalog lists).
- Payload covers flash `0x00000–0x60783` (load base `0x0`: vector table at
  offset 0, `SP=0x20016900`, `Reset=0x25DB1`).
- `sc2-research/tools/analyze_fw.py` runs on it (needs `PYTHONUTF8=1` on
  Windows — it emits `≈` and crashes under cp1252 otherwise): ~2,217
  function prologs, 7,727 BL sites, 17,951-byte rodata block, 565 strings.

## The descriptors were never in any DFU

- Our build (`6941BF08`): 94-entry dispatch pointer table at file offset
  `0x38690`, targets `0x59b10, 0x59b22, 0x59b3c, …` — all past the
  350,528-byte end. Raw pointers recovered, structs unreadable (known gap).
- New build (`6AA43B55`): table relocated to file offset `0x396a0` (still 94
  entries, same stride shape `0x645b0, 0x645c2, 0x645dc, …`) — but every
  target sits at flash `0x64500+` while the payload ends at `0x60783`.
- All 30 archived builds are 347–395 KB payloads. Conclusion: descriptor
  structs live in a flash region no DFU has ever carried (likely a
  factory-programmed partition). **SWD dump of a real controller is the only
  way to read them.**
- Secondary signal: the new firmware repacked descriptors into one
  contiguous array (all strides positive, 8–62 B) where the old table
  scattered across 14 backward jumps and strides up to 1856 B. Per-command
  code→descriptor mapping still needs dispatch-function RE (table order is
  not proven identical across versions — 90/93 stride positions differ).

See `research/dispatch-table-diff.json`: 94 rows of
`{index, code, category, old_desc, new_desc, old_stride, new_stride}` joining
the recovered `ibex_command_table.json` with both images. Index↔code mapping
is from our build; do not assume it holds for the new build.

## SoC correction: nRF52833, not nRF52840

Controller (Ibex/Triton) SoC is **Nordic nRF52833** (512 KB flash, 128 KB
RAM), not nRF52840 (1 MB / 256 KB). Evidence, strongest first:

1. `sc26re` builds `steam_controller_ibex/nrf52833` (512 KB / 128 KB,
   J-Link `nRF52833_xxAA`) and flashes it onto **real controllers** —
   working hardware proof.
2. iFixit / PC Gamer teardowns read nRF52833 chip markings; sc2-research
   documents its own retraction of an earlier nRF52840 claim with
   methodology notes (present peripherals prove family, absent QSPI/CC310
   prove the part).
3. Our images fit: `SP=0x20016D80` (ours) / `0x20016900` (new) both land
   inside 128 KB SRAM; payloads fit 512 KB flash.
4. `usbd@40027000` DT refs appear on the 52833 map — USB presence does not
   contradict the 52833 (the earlier "USB ⇒ 52840" inference was the error).

Consequences: our blob is ~67% of flash, not "33.4% of 1 MB". Live docs
corrected (archive/ left as historical record). Puck (Proteus) SoC keeps its
existing attribution pending the same level of proof.

## What this unlocked in code (this round)

`src/sc2_commands.py`, sourced from sc26re `valve_feature.h` + settings
registry (defaults only, no copied code):

- Real 83-register settings defaults (`SC2_SETTING_DEFAULTS`): 0x89 reads
  current-or-default, new 0x8B (maxs) and fixed 0x8C (defaults) mirror it,
  0x87 clamps to max, 0x86/0x88/0x8E restore defaults.
- New safe handlers: 0xA1 GET_DEVICE_INFO (sc26re 18-byte shape),
  0xC5/E9 LED RGBW store/return, 0xDB/DC user-store echo, 0xED/EE/EF/F0
  read/stage/commit/delete-setting lifecycle.
- Explicit ACK-only set (`ACK_ONLY_OPCODES`): 0x90/0x95 reboot, 0x9F
  power-off, calibration (0xB5/C0/C2/C3/CE/D8/E2/A2), 0xFE provisioning —
  success bytes returned, never acted on.
- 16/16 tests in `tests/test_windows_port.py`.

## Still needs hardware

1. SWD full-flash dump (descriptors at `0x64500+` in current builds).
2. Over-the-air `--mode ble` validation against Steam (advertising STARTED
   locally; HOGP interop untested).
