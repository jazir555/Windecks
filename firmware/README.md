# Firmware Binaries (Not Included)

The actual firmware binaries are not stored in this repository. They are extracted from the Steam client installation for reverse engineering purposes only.

## ibex_firmware.bin — Triton SC2 BLE Controller

| Field | Value |
|-------|-------|
| **Platform** | Nordic nRF52833, ARM Cortex-M4F (512 KB flash / 128 KB RAM) |
| **SDK** | nRF Connect SDK v2.9.0 |
| **Zephyr** | v3.7.99-af30fca7cecd |
| **Size** | 350,528 bytes |
| **SHA-256** | `e8954c24f0aae595d35324d4264d3abcf8fedcef379e59130d9742850c8f0a86` |
| **Steam timestamp** | 0x6941BF08 |
| **Steam filename** | `IBEX_FW_6941BF08.fw` |
| **Source** | `~/.steam/steam/bin/hardwareupdater/` |
| **Purpose** | Ghidra RE analysis — not loaded by project code |

## proteus_firmware.bin — Puck USB Dongle

| Field | Value |
|-------|-------|
| **Platform** | Nordic nRF52833, ARM Cortex-M4F (512 KB flash / 128 KB RAM) |
| **SDK** | nRF Connect SDK v2.9.0 |
| **Zephyr** | v3.7.99-af30fca7cecd |
| **Size** | 197,740 bytes |
| **SHA-256** | `d0afc5004ccd6144495e36019fc00ac8d365760fd81e13fffcf2924f909a0416` |
| **Steam timestamp** | 0x6941BF87 |
| **Steam filename** | `PROTEUS_FW_6941BF87.fw` |
| **Source** | `~/.steam/steam/bin/hardwareupdater/` |
| **Purpose** | Ghidra RE analysis — not loaded by project code |

## How to Obtain

1. Install Steam on a Linux system
2. Find the firmware files at `~/.steam/steam/bin/hardwareupdater/`
3. Copy them to this directory as `ibex_firmware.bin` and `proteus_firmware.bin`
4. Verify SHA-256 checksums match the table above
5. Delete the `.placeholder.txt` files once the real binaries are in place

## Why They're Not in the Repo

These are Valve's proprietary firmware binaries. They are used solely for reverse engineering analysis in Ghidra. The project's runtime code does not load or execute them — the Python ATT server and input handler work entirely from the live Neptune controller and BLE protocol.
