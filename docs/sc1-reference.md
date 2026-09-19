# SC1 Reference (What the 2015 Controller Teaches the Spoof)

The Steam Controller 1 (D0G, 2015) is far better documented by third
parties than the SC2, and several of its mechanisms are directly
reusable. Sources below are upstream projects, not this repo's claims.

## Sources

| Source | What it gives us |
|--------|------------------|
| Linux kernel `drivers/hid/hid-steam.c` (Rodrigo Rivas Costa + Valve) | SC1 USB IDs, lizard-mode handling, battery via `power_supply` |
| SDL3 `src/joystick/hidapi/steam/controller_structs.h` | `ValveInReport_t` shapes, Triton report IDs, status event layout |
| SDL commit `0b1c592f` (Sam Lantinga, Feb 2026) | Report `0x45` renamed `..._NO_QUATERNION` → `..._BLE` |
| `kozec/sc-controller`, Ynsta `steamcontroller` | SC1 host-driven haptics precedent, mapping without Steam |
| `research/` + `docs/sc2-protocol.md` | SC2-side firmware RE for contrast |

## Battery: power_supply, not feature reads

`hid-steam.c` registers `steam-controller-<serial>-battery` on the
kernel `power_supply` class with `POWER_SUPPLY_PROP_CAPACITY`,
converting the controller's reported **voltage (mV) to a 0-100 level**
(defaulting to 3000 mV while waiting for the first reading so userspace
never sees a bogus 0%). The SDL status event carries the same pair on
the wire:

```
SteamControllerStatusEvent_t:
    unPacketNum, sEventCode, unStateFlags,
    sBatteryVoltage (mV), ucBatteryLevel (0-100)
```

Adopted: battery travels via the **status/notification channel**, not
feature-report bodies. So the spoof keeps `0xBE GET_BATTERY_DATA` as the
open-firmware empty-body shape and puts the live level on the BLE
Battery notify (`win_ble.set_battery`) sourced from
`GetSystemPowerStatus` (`win_input.get_system_battery_percent`), with
unknown (desktop, no battery) meaning "don't send", exactly like the
kernel's avoid-bogus-zero rule.

## Device identity conventions (SC1 → SC2)

- SC1 USB: wired `28DE:1102`, wireless dongle `28DE:1142`
  (`hid-steam.c` id table); input name `"Steam Controller"` /
  `"Wireless Steam Controller"`, `uniq` = serial string.
- SC2 BLE keeps the same house style: manufacturer `Valve Software`,
  model `Steam Controller 2026`, serial starting with `F`, PnP
  `28DE:1303` — already our DIS values, now confirmed conventional
  rather than guessed.

## Report IDs (SDL, post-Feb-2026 rename)

| ID | Name | Note |
|----|------|------|
| `0x42` | `ID_TRITON_CONTROLLER_STATE` | Full state + quaternion (USB) |
| `0x43` | `ID_TRITON_BATTERY_STATUS` | Battery status input report |
| `0x45` | `ID_TRITON_CONTROLLER_STATE_BLE` | BLE state, no quaternion — **confirms our 45-byte layout is the BLE report** |
| `0x46` / `0x79` | Wireless status variants | Dongle-path only |
| BLE packet | `ValveControllerBLEStatePacket_t` | `ucGyroDataType` + 0-4 gyro shorts (dongle reconstitution) |

## Haptics: SC1 was host-driven, SC2-BLE is not

SC1 tooling (`sc-controller`: "Haptic Feedback and in-game Rumble
support") drives SC1 haptics from the host over USB — there was a
command path Steam actually used. On SC2-BLE there is none: Steam's
haptic scheduler is never entered for BLE controllers and the 0x8F gate
is architectural (see `docs/findings-backlog.md`; a real SC2 behaves
the same — USB Puck dongle only). Consequences for this project:

1. **Don't chase 0x8F on BLE.** No descriptor, capture, or patch will
   make Steam emit what its dispatcher never produces.
2. **Route what exists.** Game rumble (0x80 / XInput / HIDMaestro
   output events) is real and routable — see `src/win_haptics.py`.
3. **Synthesize locally (future, needs Deck hardware).** SC2 firmware
   plays trackpad/button haptic scripts locally without host traffic;
   a Deck-side software sequencer (short ERM pulses on trackpad
   touch/click, button press) would restore that *feel* without any
   Steam traffic. Design only — untested, not implemented.
