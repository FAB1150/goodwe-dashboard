# GoodWe ES/EM protocol as used by SolarGo

Notes from an analysis of the SolarGo Android app (decompiled with jadx), done **only for
interoperability** with a GW5048-EM (ES family, "platform 105"), firmware
`0909A / 410-04000-11`. References in parentheses are to SolarGo classes.

## Transport

- UDP, port **8899** of the WiFi module. SolarGo opens one socket per request
  (`GoodUDP.writeSync`), with a **3 s** timeout and **3 attempts** (`UdpUnicast.sendForData`).
- The DTLS code in the app only applies to newer modules that advertise it; ES uses plain UDP.
- The portal does not use the automatic discovery (broadcast on port 48899): the IP is entered
  by hand, so the inverter can live in another VLAN.

## Passwords

- The installer password for the settings pages is checked **inside the app only**
  (`LoginSettingActivity`, `InstallerInputPwdActivity`) and is never sent to the inverter.
  The portal neither uses nor asks for it.
- There is a device login (`checkLoginPwd`, command `AA55 C07F 0201 10 …`), but SolarGo only
  uses it when the serial number advertises it (`Constant.SECURITY_AVAILABLE`, recent
  inverters). It is not used for ES.

## AA55 format

```
request:  AA 55 <src> 7F <ctrl> <func> <len> <data…> <sum_hi> <sum_lo>
response: AA 55 7F <src> <ctrl> <func|80> <len> <data…> <sum_hi> <sum_lo>
```

The checksum is the plain 16-bit sum of all preceding bytes. A write succeeded when the
response holds a single `06` byte (ACK); `15` means rejected.

**Source address: the main difference from Home Assistant.**

| Source | Used by SolarGo for | Used by the `goodwe` library (HA) |
|---|---|---|
| `C0` | "classic" `01xx` / `03xx` commands (`Constant.MONITOR_ADDRESS`) | **everything** |
| `B0` | register read `011A`, register write `0239` (`build105SettingPackage`, `build105ReadingPackage`) | never |

## Reads done by the portal (on page load or "Read from inverter")

| What | Frame | Notes |
|---|---|---|
| Device info | `AA55 C07F 0102 00` | reply `0182`: firmware [0:5], model [5:15], serial [31:47], software [51:63], safety country [63], extended ARM version [64:76] |
| Settings | `AA55 C07F 0109 00` | reply `0189`, table below (`DataCollectUtil.getSettingData1`) |
| Eco groups | `AA55 B07F 011A 03 0701 10` | 16 registers, 4 groups of 8 bytes (`READ_BATTERY_MODE_LIST`) |
| Off-grid DoD | `AA55 B07F 011A 03 05A1 01` | value = 100 − DoD (`READ_OFFLINE_DOD`) |
| Grid protection (on request only) | Modbus `F7 03 B158 0011` and `F7 03 B169 0010` | `CustomSaftyParamEnum`, **never written** |

### Settings block `0189` (byte offsets)

| Offset | Content |
|---|---|
| 0–3 | legacy charge window (h, m, h, m); 4–5 limit % |
| 6–9 | legacy discharge window; 10–11 limit % |
| 13 | backup output (0/1) |
| 15 | grid charging in off-grid mode |
| 17 | shadow scan |
| 18–19 | export limitation (1 = on) |
| 22–23 | capacity (Ah) |
| 24–25 | charge voltage (/10 V) |
| 26–27 | charge current (SolarGo: raw / 0.266 / 10 A) |
| 28–29 | discharge current (SolarGo: raw / 0.128 / 10 A, max 120) |
| 30–31 | discharge voltage (/10 V) |
| 32–33 | **on-grid minimum SoC = 100 − DoD** |
| 34–35 | battery activation |
| 42–43 | power factor |
| 44–51 | float charge parameters, battery model (255 = custom) |
| 52–53 | export limit (W) |
| 56–57 | **SoC protection: 1 = off**, anything else = on |
| 60–63 | equalisation charge |
| 66–67 | work mode: 0 general, 1 off-grid, 2 backup, 3 eco |
| 68–69 | grid quality check |

### Eco groups (registers `0701`–`0710`)

Each group is 8 bytes: `start_h start_m end_h end_m power(signed int16) enabled days`

- power in % of rated power: **negative = charge**, positive = discharge (`BatteryModeActivity`);
- enabled: `FF` yes, `00` no;
- days: bit 0 = Sunday … bit 6 = Saturday;
- no target SoC on this firmware (it comes with ARM ≥ 14, the "new eco mode", which this
  inverter does not support);
- SolarGo rejects start ≥ end and checks overlaps (opposite modes or different powers).

## Writes (same as SolarGo)

| Setting | Sequence | Origin in SolarGo |
|---|---|---|
| Eco group *n* | `C07F 0359 01 03` (eco mode), then `B07F 0239 0B <0701+4(n−1)> 04 <8 bytes>` | `BatteryModeActivity` → `setBatteryModeParam` |
| Enable/disable group *n* | `C07F 0359 01 03`, then `B07F 0239 05 <0704+4(n−1)> 01 <FF/00> <days>` | `BatteryModeListActivity` → `setBatteryModeSwitch` |
| SoC protection | `C07F 033B 03 00 <00 on / 01 off> 00` | `setSocProtect` |
| On-grid DoD | `B07F 0239 05 0560 01 <00> <100−DoD>` | `BatteryFunction105Activity.setBatteryDod` |
| Off-grid DoD | `B07F 0239 05 05A1 01 <00> <100−DoD>` | `setOfflineDod` |
| Export limit | `C07F 0335 02 <W hi> <W lo>` | `buildPackage(66)` |
| General mode | `032C 05 00 00 00 00 00`, `032D 05 00 00 00 00 00`, `B07F 0239 05 0700 01 0001`, `0359 01 00`, `0336 01 00` | `ChooseWorkModeActivity`, ARM ≥ 7 |
| Backup mode | `B07F 0239 05 0700 01 0001`, `0336 01 00`, `032C 05 00 00 17 3B 0A`, `0359 01 02` | `initESEMBackupModeDialog` |
| Eco mode | `0359 01 03` (then manage the groups) | |
| Export limit on/off | `C07F 0353 01 <01 on / 00 off>` | `switch_back_flow_limit` → `buildPackage(57)` (the app's variable name is inverted: switch on = `01`, matching the read value 1 at offset 18) |
| Shadow scan | `C07F 0328 02 80 <80 on / 00 off>` | `buildPackage(60)` |
| Backup output | `C07F 0327 02 00 <30 on / 10 off>` | `SetBackup` → `SetRelayControl` → `buildPackage(51)` |
| Battery activation | `C07F 0337 02 00 <0F on / 00 off>` | `buildPackage(70)` |
| Backup detection mode | `C07F 035B 01 <0 UPS full wave / 1 UPS half wave / 2 EPS, supports LVRT>` | `DetectionModeActivity` → `setDetectionMode` → `buildPackage(76)` |
| Clear overload history | `C07F 035E 01 0F` | `buildPackage(206)` |

### Why DoD writes from Home Assistant have no effect

1. **Source address.** HA writes the same register (`0x0560`) with the same value, but from
   `C0` (`aa55 c07f 0239 05 0560 01 0014 02f8`); SolarGo uses `B0`
   (`aa55 b07f 0239 05 0560 01 0014 02e8`). That is the only difference in the frame and the
   most likely cause; it still has to be confirmed on the real inverter.
2. **SoC protection.** SolarGo only lets you change the on-grid DoD while SoC protection is on:
   when it is off (offset 56 = 1) the app hides the field and re-sends the DoD unchanged. If
   SoC protection is off, a DoD written from HA may be ignored.

`AdvancedSettingNewActivity` also has a path that uses command `0352` (discharge voltage and
current + DoD), but for this model that section is hidden (`ll_battery_content` = `gone`): the
path actually used is the one above.

## What the portal NEVER writes

Safety country (`0301`, `023A`, `033C`), voltage and frequency thresholds and trip times,
connection parameters, anti-islanding, P(f)/Q(U)/cosφ curves (registers `0x0001`, `0x0100`,
`0x0579`–`0x0594`, `0x7150`–`0x7154`, Modbus `0xB158`–`0xB2A0`…). In `app/transport.py`
writes are restricted to an explicit allowlist of commands and registers: any other write
frame is blocked before it is sent, and the tests check this.

## Shown read-only on purpose

- **Power factor** (offset 42 of the 0x0109 block): SolarGo decodes it with a sign-extending read
  (`byte2ToIntNew`) and only maps raw values 0–20 to a negative factor, then divides by 100. The
  portal reproduces this exactly and shows `0xFFFF` as "not set".
- **First cold start (off-grid)**: SolarGo only offers it while the grid is missing.
- Charge/discharge voltage and current, capacity and battery model: installation parameters set
  by the battery model.

## Grid interface protection ("safety") parameters

Read as five Modbus blocks (same as SolarGo's `get_*` commands): `F7 03 B158 0011`,
`F7 03 B169 0010`, `F7 03 B179 0042`, `F7 03 B183 0043`, `F7 03 B1D6 000D`. The field list,
gains, units and ranges come from SolarGo's `CustomSaftyParamEnum` (generated into
`app/safety_catalog.py`).

Writes (only when the user has unlocked editing) use Modbus write-single, exactly as SolarGo's
`setCustomSaftyParam`: `F7 06 <reg> <value>` with `value = displayed × gain` as a two's-complement
16-bit register. **DRED** is the same kind of write: `F7 06 B7BC <1|0>`.

Kept read-only even when unlocked, because SolarGo writes them through a different / non-linear
path that is firmware-dependent:

- **trip times** (OV/UV/OF/UF stage 1/2/3 times): SolarGo writes these as `value × 1000` to a
  separate register block via `F7 10 B2xx …` (write-multiple) when the firmware supports it, or
  `value × 50` to the read register otherwise;
- **curve time constants** (`reactive_qu`, `active_pu`, `cos_qu`): non-linear encoding
  (`0.2 / (value + 0.2) × 4096`).

## Protection parameter units

Trip times (`0xB159`, `0xB15B`, …) are stored in grid periods: SolarGo shows them as
raw × 0.02 s (`ProtectParamFragment`). Voltages are raw / 10 V, frequencies raw / 100 Hz.
