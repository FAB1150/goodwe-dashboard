"""Settings catalogue for the GoodWe ES/EM family (platform 105), derived from SolarGo.

Every read and write here reproduces the frames SolarGo sends to an EMU inverter
with ARM firmware 7..13 (tested target: GW5048-EM, 0909A / 410-04000-11).
See docs/PROTOCOL.md for details and references to the SolarGo code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import protocol as p

WORK_MODES = {
    0: "General (self-use)",
    1: "Off-grid",
    2: "Backup",
    3: "Eco (time schedule)",
}
# Work modes the portal is allowed to set (off-grid deliberately excluded)
WRITABLE_WORK_MODES = (0, 2, 3)

SAFETY_COUNTRIES = {0: "IT-CEI 0-21", 67: "IT-CEI 0-16"}

WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]  # bit 0 = Sunday

ECO_BASE = 0x0701
ECO_GROUPS = 4
ECO_REGS_PER_GROUP = 4
REG_ONGRID_DOD = 0x0560
REG_OFFGRID_DOD = 0x05A1
REG_CLEAR_BATTERY_MODE = 0x0700
REG_DRED = 0xB7BC  # read-only


# ---------------------------------------------------------------------------
# Decoding helpers
# ---------------------------------------------------------------------------


def _ascii(b: bytes) -> str:
    return b.replace(b"\x00", b" ").decode("ascii", errors="replace").strip()


def _u16(b: bytes, off: int) -> int:
    return int.from_bytes(b[off : off + 2], "big")


def _s16(b: bytes, off: int) -> int:
    return int.from_bytes(b[off : off + 2], "big", signed=True)


@dataclass
class Field:
    """A value shown in the page.

    `help` is the explanation shown by the "?" icon; `control` describes how the page lets
    the user change the value (None = read-only):
      {"type": "switch", "setting": ..., "on": bool, "warning": str}
      {"type": "number", "setting": ..., "min": .., "max": .., "step": ..}
      {"type": "select", "setting": ..., "options": [[value, label], ...], "current": value}
      {"type": "action", "setting": ..., "button": str, "warning": str}
    """

    key: str
    label: str
    value: Any
    unit: str = ""
    raw: Any = None
    note: str = ""
    help: str = ""
    control: dict | None = None
    protected: bool = False  # grid protection parameter: always read-only

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ---------------------------------------------------------------------------
# 0x0102 device info
# ---------------------------------------------------------------------------


@dataclass
class DeviceInfo:
    firmware: str
    model: str
    manufacturer: str
    serial: str
    software: str
    safety_country_code: int
    arm_string: str
    dsp_version: int | None
    arm_version: int | None

    @property
    def safety_country(self) -> str:
        return SAFETY_COUNTRIES.get(self.safety_country_code, f"code {self.safety_country_code}")

    @property
    def family_ok(self) -> bool:
        return any(t in self.serial for t in ("EMU", "ESU", "ESA", "BPS"))


def parse_device_info(data: bytes) -> DeviceInfo:
    if len(data) < 64:
        raise p.ProtocolError(f"0x0182 response too short ({len(data)} bytes)")
    firmware = _ascii(data[0:5])
    arm_string = _ascii(data[64:76]) if len(data) >= 76 else ""
    dsp = arm = None
    try:
        dsp = int(firmware[0:2])
    except ValueError:
        pass
    # Same as SolarGo: first the 5th firmware character (base 36: 'A' = 10),
    # then, if present, the third field of the "410-04000-11" string.
    try:
        arm = int(firmware[4], 36)
    except (ValueError, IndexError):
        pass
    parts = arm_string.split("-")
    if len(parts) >= 3:
        try:
            arm = int(parts[2])
        except ValueError:
            pass
    return DeviceInfo(
        firmware=firmware,
        model=_ascii(data[5:15]),
        manufacturer=_ascii(data[15:31]),
        serial=_ascii(data[31:47]),
        software=_ascii(data[51:63]),
        safety_country_code=data[63],
        arm_string=arm_string,
        dsp_version=dsp,
        arm_version=arm,
    )


def device_info_fields(info: DeviceInfo) -> list[Field]:
    return [
        Field("model", "Model", info.model, help="Model name reported by the inverter."),
        Field("serial", "Serial number", info.serial,
              help="Serial number. The letters EMU, ESU, ESA or BPS identify the ES family (\"platform 105\") "
                   "that this portal's commands are written for."),
        Field("firmware", "Firmware", info.firmware,
              help="Firmware code. The first two digits are the DSP version; the fifth character is the ARM "
                   "version in base 36 (A = 10, B = 11, …)."),
        Field("software", "Software version", info.software, help="ARM software version string."),
        Field("arm_string", "ARM version (extended)", info.arm_string or "—",
              help="Extended ARM version, e.g. 410-04000-11. The last number is the ARM version: SolarGo uses it "
                   "to decide which settings to show (e.g. eco windows with target SoC need ARM 14 or later)."),
        Field("dsp_version", "DSP version (from firmware)", info.dsp_version,
              help="Version of the DSP, the processor that runs the power conversion and grid protection."),
        Field("arm_version", "ARM version as used by SolarGo", info.arm_version,
              help="Version of the ARM processor firmware, which handles communication and settings."),
        Field(
            "safety_country",
            "Safety country",
            info.safety_country,
            raw=info.safety_country_code,
            protected=True,
            note="Byte 63 of the 0x0182 response, as in SolarGo; on some firmware this byte does not hold the "
                 "country (e.g. 32 = space).",
            help="The grid code the inverter follows (for a low-voltage connection in Italy: CEI 0-21). It loads "
                 "the whole set of grid protection thresholds and trip times shown at the bottom of the page. "
                 "Read-only: in Italy it must not be changed by the user.",
        ),
    ]


# ---------------------------------------------------------------------------
# 0x0109 settings block
# ---------------------------------------------------------------------------


def _hhmm(h: int, m: int) -> str:
    # SolarGo: a 0x30 byte ('0' in ASCII) in the hour means "not set"
    if h == 0x30:
        h, m = 0, 0
    return f"{h:02d}:{m:02d}"


def parse_settings(data: bytes) -> dict[str, Any]:
    if len(data) < 64:
        raise p.ProtocolError(f"0x0189 response too short ({len(data)} bytes)")
    s: dict[str, Any] = {}
    s["charge_window"] = f"{_hhmm(data[0], data[1])}-{_hhmm(data[2], data[3])}"
    s["charge_power_limit"] = _u16(data, 4)
    s["discharge_window"] = f"{_hhmm(data[6], data[7])}-{_hhmm(data[8], data[9])}"
    s["discharge_power_limit"] = _u16(data, 10)
    s["backup_supply"] = data[13]
    s["offgrid_charge"] = data[15]
    s["shadow_scan"] = data[17]
    s["export_limit_enabled_raw"] = _u16(data, 18)
    s["capacity_ah"] = _u16(data, 22)
    s["charge_v_raw"] = _u16(data, 24)
    s["charge_i_raw"] = _u16(data, 26)
    s["discharge_i_raw"] = _u16(data, 28)
    s["discharge_v_raw"] = _u16(data, 30)
    s["min_soc_raw"] = _u16(data, 32)  # = 100 - on-grid DoD
    s["battery_activated"] = _u16(data, 34)
    s["power_factor_raw"] = _u16(data, 42)
    s["float_v_raw"] = _u16(data, 44)
    s["float_i_raw"] = _u16(data, 46)
    s["float_time"] = _u16(data, 48)
    s["battery_model"] = _u16(data, 50)
    s["export_limit_w"] = _s16(data, 52)
    s["soc_protect_raw"] = _u16(data, 56)
    s["avg_v_raw"] = _u16(data, 60)
    s["avg_time"] = _u16(data, 62)
    s["work_mode"] = _u16(data, 66) if len(data) >= 68 else None
    s["grid_quality_check"] = _u16(data, 68) if len(data) >= 70 else None
    s["_len"] = len(data)
    return s


def soc_protect_on(settings: dict) -> bool:
    # SolarGo: value 1 = SoC protection off, anything else = on
    return settings["soc_protect_raw"] != 1


def ongrid_dod(settings: dict) -> int:
    return 100 - settings["min_soc_raw"]


DETECTION_MODES = {
    0: "UPS mode – full wave detection",
    1: "UPS mode – half wave detection",
    2: "EPS mode – supports LVRT",
}


def _onoff(v: bool) -> str:
    return "on" if v else "off"


def battery_activated_on(settings: dict) -> bool:
    return settings["battery_activated"] > 0


def power_factor(settings: dict) -> float | None:
    """Faithful to SolarGo's byte2ToIntNew: only a 0xFF high byte is sign-extended, then values
    0..20 map to a negative factor. 0xFFFF means "not set" and is shown as such."""
    raw = settings["power_factor_raw"]
    if raw == 0xFFFF:
        return None
    value = raw - 0x10000 if (raw >> 8) == 0xFF else raw
    if 0 <= value <= 20:
        value -= 100
    return round(value / 100, 2)


def settings_fields(s: dict, offgrid_dod_raw: int | None = None, dred: int | None = None) -> dict[str, list[Field]]:
    wm = s.get("work_mode")
    discharge_a = min(s["discharge_i_raw"] / 0.128 / 10, 120.0)
    charge_a = s["charge_i_raw"] / 0.266 / 10
    gq = s.get("grid_quality_check")

    mode = [
        Field(
            "work_mode", "Work mode",
            WORK_MODES.get(wm, f"unknown ({wm})") if wm is not None else "—", raw=wm,
            control={"type": "select", "setting": "work_mode", "current": wm,
                     "options": [[m, WORK_MODES[m]] for m in WRITABLE_WORK_MODES]},
            help="General (self-use): PV covers the house first, the surplus charges the battery, and the battery "
                 "discharges to cover the house load; the grid is used only for what is missing. "
                 "Backup: the battery is kept full (SolarGo sets a 00:00–23:59 grid charge window at 10 % power) and "
                 "is used only when the grid fails. "
                 "Eco: follows the charge/discharge windows below; outside the windows the inverter works as in "
                 "General mode. Off-grid is shown but cannot be set from the portal.",
        ),
        Field("charge_window", "Charge window (legacy command 0x032C)", s["charge_window"],
              note=f"limit {s['charge_power_limit']} %",
              help="Charge window of the older firmware scheme. With this firmware the eco windows are used "
                   "instead; SolarGo resets it to 00:00–00:00 when switching to General and sets 00:00–23:59 "
                   "(10 %) for Backup. Shown for reference only."),
        Field("discharge_window", "Discharge window (legacy command 0x032D)", s["discharge_window"],
              note=f"limit {s['discharge_power_limit']} %",
              help="Discharge window of the older firmware scheme, see above. Shown for reference only."),
    ]

    battery = [
        Field("soc_protect", "SoC protection", _onoff(soc_protect_on(s)), raw=s["soc_protect_raw"],
              control={"type": "switch", "setting": "soc_protect", "on": soc_protect_on(s)},
              help="When on, in on-grid operation the battery stops discharging when it reaches the minimum SoC "
                   "set by the on-grid DoD below (e.g. DoD 80 % = stops at 20 %). When off, that limit is not "
                   "applied and the battery is only protected by its own BMS limits; SolarGo then hides the DoD "
                   "field and does not let you change it."),
        Field("ongrid_dod", "On-grid depth of discharge (DoD)", ongrid_dod(s), "%", raw=s["min_soc_raw"],
              note=f"minimum SoC on-grid = {s['min_soc_raw']} %",
              control={"type": "number", "setting": "ongrid_dod", "min": 0, "max": 95, "step": 1,
                       "disabled": not soc_protect_on(s),
                       "disabled_reason": "Turn SoC protection on to change it (as in SolarGo)."},
              help="How much of the battery can be used while the grid is present. Minimum SoC = 100 − DoD: with "
                   "DoD 80 % the battery stops discharging at 20 %, keeping 20 % in reserve for outages. Only "
                   "applied when SoC protection is on."),
    ]
    if offgrid_dod_raw is not None:
        battery.append(
            Field("offgrid_dod", "Off-grid depth of discharge (DoD)", 100 - offgrid_dod_raw, "%", raw=offgrid_dod_raw,
                  note=f"minimum SoC off-grid = {offgrid_dod_raw} %",
                  control={"type": "number", "setting": "offgrid_dod", "min": 0, "max": 95, "step": 1},
                  help="Same as the on-grid DoD, but used when the grid is down and the backup (EPS) output runs "
                       "from the battery. It is usually set higher than the on-grid DoD, so that the reserve left "
                       "by the on-grid limit is available during an outage.")
        )
    battery += [
        Field("battery_activated", "Battery activation", _onoff(battery_activated_on(s)), raw=s["battery_activated"],
              control={"type": "switch", "setting": "battery_activated", "on": battery_activated_on(s),
                       "warning": "When on, the battery port outputs about 60 V to wake up the battery. Use it only "
                                  "for a lithium battery without automatic breaker that has shut down after an "
                                  "undervoltage protection."},
              help="Wakes up a lithium battery that switched itself off after an undervoltage protection: the "
                   "inverter puts about 60 V on the battery port so the BMS can restart. Only for lithium "
                   "batteries without an automatic breaker. Leave it off in normal operation."),
        Field("capacity_ah", "Battery capacity", s["capacity_ah"], "Ah",
              help="Battery capacity configured in the inverter, in ampere-hours (e.g. 7.2 kWh at 48 V ≈ 150 Ah). "
                   "For lithium batteries it comes from the battery model selected at installation."),
        Field("charge_v", "Charge voltage", s["charge_v_raw"] / 10, "V", raw=s["charge_v_raw"],
              help="Maximum charging voltage of the battery. Set by the battery model for lithium batteries "
                   "(configurable only for lead-acid). Read-only here."),
        Field("charge_i", "Charge current", round(charge_a, 1), "A", raw=s["charge_i_raw"],
              help="Maximum charging current. Converted as SolarGo does (raw ÷ 0.266 ÷ 10); the raw value is "
                   "shown too in case the conversion does not match SolarGo. Read-only here."),
        Field("discharge_v", "Discharge voltage", s["discharge_v_raw"] / 10, "V", raw=s["discharge_v_raw"],
              help="Voltage below which the battery is considered empty (cut-off). Set by the battery model for "
                   "lithium batteries. Read-only here."),
        Field("discharge_i", "Discharge current", round(discharge_a, 1), "A", raw=s["discharge_i_raw"],
              help="Maximum discharging current. Converted as SolarGo does (raw ÷ 0.128 ÷ 10, capped at 120 A). "
                   "Read-only here."),
        Field("battery_model", "Battery model", s["battery_model"],
              help="Index of the battery model selected at installation; 255 means custom (self-defined) "
                   "parameters. Changing it is part of the basic installation settings, not of this portal."),
        Field("offgrid_charge", "Off-grid charge flag", s["offgrid_charge"],
              help="Raw flag from the settings block (byte 15) related to charging in off-grid mode. Its exact "
                   "meaning is not documented; shown for comparison with SolarGo."),
    ]

    grid: list[Field] = []
    if dred is not None:
        grid.append(
            Field("dred", "DRED / Remote Shutdown / RCR", _onoff(dred == 1), raw=dred,
                  control={"type": "switch", "setting": "dred", "on": dred == 1,
                           "warning": "This tells the inverter to obey an external grid-operator control input "
                                      "(demand response / remote shutdown / ripple control) on its DRED port. "
                                      "Turning it ON without the right device connected can stop the inverter "
                                      "from working normally (SolarGo gives the same warning)."},
                  help="Lets an external signal limit or stop the inverter: a demand-response device (DRED), a "
                       "remote shutdown contact or a ripple-control receiver (RCR) wired to the inverter's DRED "
                       "port. With nothing connected it has no effect. Register 0xB7BC.")
        )
    grid += [
        Field("export_limit_enabled", "Export limit", _onoff(s["export_limit_enabled_raw"] == 1),
              raw=s["export_limit_enabled_raw"],
              control={"type": "switch", "setting": "export_limit_enabled", "on": s["export_limit_enabled_raw"] == 1},
              help="When on, the inverter reduces its output so that the power sent to the grid does not exceed "
                   "the export limit value below. It relies on the energy meter / CT: if that is not installed "
                   "or wired correctly the inverter cannot measure export and the limit does not work."),
        Field("export_limit_w", "Export limit value", s["export_limit_w"], "W",
              control={"type": "number", "setting": "export_limit_w", "min": 0, "max": 10000, "step": 10},
              help="Maximum power sent to the grid when the export limit is on, in watts. 0 = zero export: "
                   "once the house is covered and the battery is full, extra PV production is curtailed."),
        Field("shadow_scan", "Shadow scan", _onoff(s["shadow_scan"] == 1), raw=s["shadow_scan"],
              control={"type": "switch", "setting": "shadow_scan", "on": s["shadow_scan"] == 1},
              help="About every 60 minutes the inverter sweeps the whole PV voltage range to find the real "
                   "maximum power point, which can move when panels are partly shaded. Production drops briefly "
                   "during each scan, so leave it off unless shading is significant."),
        Field("backup_supply", "Backup output", _onoff(s["backup_supply"] == 1), raw=s["backup_supply"],
              control={"type": "switch", "setting": "backup_supply", "on": s["backup_supply"] == 1},
              help="Powers the BACKUP (EPS) port from battery and PV when the grid fails. When off, loads on the "
                   "backup port lose power during an outage. The detection mode below sets how fast it switches."),
        Field("detection_mode", "Detection mode", DETECTION_MODES.get(gq, f"unknown ({gq})") if gq is not None else "—",
              raw=gq,
              control={"type": "select", "setting": "detection_mode", "current": gq,
                       "options": [[k, v] for k, v in DETECTION_MODES.items()]} if gq is not None else None,
              help="How the inverter detects a grid failure to switch the backup output to the battery. "
                   "UPS full wave: fast detection of both overvoltage and undervoltage, shortest switch-over. "
                   "UPS half wave: fast detection of undervoltage only. "
                   "EPS (supports LVRT): fast waveform detection off, so short voltage dips are ridden through "
                   "instead of switching; switch-over is slower but there are fewer unnecessary switch-overs on a "
                   "disturbed grid."),
        Field("cold_start", "First cold start (off-grid)", "only when the grid is missing",
              help="Starts the inverter from the battery when there is no grid, to power the backup loads (Backup "
                   "output must be on). SolarGo only offers it while the grid is missing; the portal does not "
                   "send it."),
        Field("power_factor", "Power factor",
              power_factor(s) if power_factor(s) is not None else "not set",
              raw=s["power_factor_raw"], protected=True,
              note="0xFFFF = not set on this firmware" if power_factor(s) is None else "",
              help="Fixed power factor (cos φ) of the power fed to the grid: 1 = only active power, lower values "
                   "make the inverter also exchange reactive power. Decoded exactly as SolarGo does. "
                   "Reactive power behaviour is part of the grid code, so it is read-only here."),
        Field("overload_reset", "Clear overload history", "",
              control={"type": "action", "setting": "overload_reset", "button": "Clear",
                       "warning": "Resets the backup overload reconnection time to its default."},
              help="After repeated overloads of the backup output the inverter waits longer and longer before "
                   "turning it back on. This resets that waiting time to the default. It does not change any "
                   "setting."),
    ]
    return {"mode": mode, "battery": battery, "grid": grid}


# ---------------------------------------------------------------------------
# Eco groups (registers 0x0701-0x0710, 4 groups of 8 bytes)
# ---------------------------------------------------------------------------


@dataclass
class EcoGroup:
    index: int  # 0..3
    start: str  # "HH:MM"
    end: str
    power: int  # signed %: negative = charge, positive = discharge
    enabled: bool
    days: int  # bitmask, bit0 = Sunday

    @property
    def mode(self) -> str:
        return "charge" if self.power < 0 else "discharge"

    @property
    def days_list(self) -> list[str]:
        return [WEEKDAYS[i] for i in range(7) if self.days & (1 << i)]

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "power": self.power,
            "mode": self.mode,
            "power_abs": abs(self.power),
            "enabled": self.enabled,
            "days": self.days,
            "days_list": self.days_list,
        }

    def to_bytes(self, enabled: bool | None = None) -> bytes:
        sh, sm = (int(x) for x in self.start.split(":"))
        eh, em = (int(x) for x in self.end.split(":"))
        en = self.enabled if enabled is None else enabled
        return bytes([sh, sm, eh, em]) + self.power.to_bytes(2, "big", signed=True) + bytes(
            [0xFF if en else 0x00, self.days & 0x7F]
        )


def parse_eco_groups(data: bytes) -> list[EcoGroup]:
    if len(data) != 32:
        raise p.ProtocolError(f"eco groups: expected 32 bytes, received {len(data)}")
    groups = []
    for i in range(ECO_GROUPS):
        b = data[i * 8 : i * 8 + 8]
        groups.append(
            EcoGroup(
                index=i,
                start=f"{b[0]:02d}:{b[1]:02d}",
                end=f"{b[2]:02d}:{b[3]:02d}",
                power=int.from_bytes(b[4:6], "big", signed=True),
                enabled=b[6] == 0xFF,
                days=b[7],
            )
        )
    return groups


def _parse_hhmm(t: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in t.split(":"))
    except ValueError as e:
        raise ValueError(f"invalid time: {t!r}") from e
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"invalid time: {t!r}")
    return h, m


def validate_eco_group(g: EcoGroup) -> None:
    if not 0 <= g.index < ECO_GROUPS:
        raise ValueError("invalid group index")
    s = _parse_hhmm(g.start)
    e = _parse_hhmm(g.end)
    if s >= e:
        # Same check as SolarGo (BatteryModeActivity.compareTime)
        raise ValueError("start time must be before end time (groups cannot cross midnight)")
    if not -100 <= g.power <= 100:
        raise ValueError("power out of range [-100, 100] %")
    if not 0 <= g.days <= 0x7F:
        raise ValueError("invalid days")


def eco_conflicts(groups: list[EcoGroup], candidate: EcoGroup) -> list[str]:
    """Consistency checks SolarGo performs before saving/enabling a group."""
    problems = []
    cs, ce = _parse_hhmm(candidate.start), _parse_hhmm(candidate.end)
    for g in groups:
        if g.index == candidate.index or not g.enabled:
            continue
        gs, ge = _parse_hhmm(g.start), _parse_hhmm(g.end)
        overlap = cs < ge and gs < ce
        if not overlap or not (g.days & candidate.days):
            continue
        same_kind = (g.power < 0) == (candidate.power < 0)
        if not same_kind:
            problems.append(f"overlaps group {g.index + 1}, which has the opposite mode ({g.mode})")
        elif g.power != candidate.power:
            problems.append(f"overlaps group {g.index + 1}, which has a different power ({g.power} %)")
    return problems


# ---------------------------------------------------------------------------
# Reads: the sequence the portal runs when the page is opened / refreshed
# ---------------------------------------------------------------------------


@dataclass
class ReadStep:
    key: str
    description: str
    request: p.Request


def page_read_steps() -> list[ReadStep]:
    return [
        ReadStep("device_info", "Device info (0x0102)", p.read_device_info()),
        ReadStep("settings", "Settings (0x0109)", p.read_settings()),
        ReadStep("eco", "Eco groups (registers 0x0701-0x0710)", p.read_registers(ECO_BASE, ECO_GROUPS * ECO_REGS_PER_GROUP)),
        ReadStep("offgrid_dod", "Off-grid DoD (register 0x05A1)", p.read_registers(REG_OFFGRID_DOD, 1)),
        ReadStep("dred", "DRED / remote shutdown (Modbus 0xB7BC)", p.ModbusReadRequest(0xB7BC, 1)),
    ]


# ---------------------------------------------------------------------------
# Writes: command plans (reproduce SolarGo's sequences)
# ---------------------------------------------------------------------------


@dataclass
class WriteStep:
    description: str
    request: p.Request
    must_ack: bool = True  # SolarGo only treats an ACK (0x06) as success


@dataclass
class WritePlan:
    setting: str
    label: str
    before: Any
    after: Any
    steps: list[WriteStep]
    verify: list[str] = field(default_factory=list)  # read keys to repeat after the write
    warning: str = ""


def _work_mode_cmd(mode: int) -> WriteStep:
    return WriteStep(f"work mode = {mode} ({WORK_MODES[mode]})", p.classic_command(0x59, bytes([mode])))


def _offgrid_flag(v: int) -> WriteStep:
    return WriteStep(f"off-grid work mode flag = {v}", p.classic_command(0x36, bytes([v])))


def _legacy_window(func: int, start: str, end: str, limit: int, what: str) -> WriteStep:
    sh, sm = _parse_hhmm(start)
    eh, em = _parse_hhmm(end)
    return WriteStep(
        f"{what} window {start}-{end}, limit {limit} %",
        p.classic_command(func, bytes([sh, sm, eh, em, limit])),
    )


def _clear_battery_mode() -> WriteStep:
    return WriteStep(
        "reset battery mode parameters (register 0x0700 = 1)",
        p.write_registers(REG_CLEAR_BATTERY_MODE, (1).to_bytes(2, "big")),
    )


def plan_work_mode(current: int | None, mode: int) -> WritePlan:
    """ChooseWorkModeActivity sequences for ES/EM with ARM >= 7 and no "new eco mode"."""
    if mode not in WRITABLE_WORK_MODES:
        raise ValueError("work mode not allowed by the portal")
    if mode == 0:
        steps = [
            _legacy_window(0x2C, "00:00", "00:00", 0, "charge"),
            _legacy_window(0x2D, "00:00", "00:00", 0, "discharge"),
            _clear_battery_mode(),
            _work_mode_cmd(0),
            _offgrid_flag(0),
        ]
        warning = ""
    elif mode == 2:
        steps = [
            _clear_battery_mode(),
            _offgrid_flag(0),
            _legacy_window(0x2C, "00:00", "23:59", 10, "charge"),
            _work_mode_cmd(2),
        ]
        warning = "As in SolarGo, backup mode resets the battery mode parameters."
    else:  # eco
        steps = [_work_mode_cmd(3)]
        warning = "In eco mode the inverter follows the enabled time groups."
    return WritePlan(
        setting="work_mode",
        label="Work mode",
        before=WORK_MODES.get(current, current) if current is not None else None,
        after=WORK_MODES[mode],
        steps=steps,
        verify=["settings"],
        warning=warning,
    )


def plan_eco_group(groups: list[EcoGroup], new: EcoGroup) -> WritePlan:
    """BatteryModeActivity: eco work mode (0x0359=3) + write the group's 4 registers."""
    validate_eco_group(new)
    old = next((g for g in groups if g.index == new.index), None)
    reg = ECO_BASE + new.index * ECO_REGS_PER_GROUP
    return WritePlan(
        setting=f"eco_group_{new.index + 1}",
        label=f"Eco group {new.index + 1}",
        before=old.as_dict() if old else None,
        after=new.as_dict(),
        steps=[
            _work_mode_cmd(3),
            WriteStep(f"group {new.index + 1}: registers 0x{reg:04X}-0x{reg + 3:04X}", p.write_registers(reg, new.to_bytes())),
        ],
        verify=["eco", "settings"],
        warning="; ".join(eco_conflicts(groups, new)),
    )


def eco_schedule_conflicts(groups: list[EcoGroup]) -> list[str]:
    """Pairwise SolarGo consistency checks over all enabled groups."""
    problems: list[str] = []
    enabled = [g for g in groups if g.enabled]
    for i, a in enumerate(enabled):
        for b in enabled[i + 1 :]:
            for msg in eco_conflicts([b], a):
                problems.append(f"group {a.index + 1} {msg}")
    return problems


def plan_eco_schedule(current: list[EcoGroup], new: list[EcoGroup]) -> WritePlan:
    """Save several eco groups at once (timeline editor).

    Same frames as SolarGo's BatteryModeActivity: eco work mode (0x0359=3) once, then the
    4 registers of every group that changed (enable byte included, so a removed window is a
    group written with enable = 00).
    """
    by_index = {g.index: g for g in current}
    seen: set[int] = set()
    changed: list[EcoGroup] = []
    for g in new:
        if g.index in seen:
            raise ValueError(f"group {g.index + 1} given twice")
        seen.add(g.index)
        validate_eco_group(g)
        old = by_index.get(g.index)
        if old is None or old.to_bytes() != g.to_bytes():
            changed.append(g)
    if not changed:
        raise ValueError("no changes to save")
    final = [g for g in current if g.index not in seen] + list(new)
    final.sort(key=lambda g: g.index)
    problems = eco_schedule_conflicts(final)
    if problems:
        # SolarGo refuses these combinations: refuse them too instead of just warning
        raise ValueError("; ".join(problems))
    steps = [_work_mode_cmd(3)]
    for g in sorted(changed, key=lambda g: g.index):
        reg = ECO_BASE + g.index * ECO_REGS_PER_GROUP
        state = "enabled" if g.enabled else "disabled (removed)"
        steps.append(
            WriteStep(
                f"group {g.index + 1}: {g.start}-{g.end} {g.mode} {abs(g.power)} % {' '.join(g.days_list)}, {state}"
                f" — registers 0x{reg:04X}-0x{reg + 3:04X}",
                p.write_registers(reg, g.to_bytes()),
            )
        )
    return WritePlan(
        setting="eco_schedule",
        label="Eco schedule",
        before=[by_index[g.index].as_dict() for g in changed if g.index in by_index],
        after=[g.as_dict() for g in changed],
        steps=steps,
        verify=["eco", "settings"],
    )


def plan_eco_switch(groups: list[EcoGroup], index: int, enabled: bool) -> WritePlan:
    """BatteryModeListActivity: eco work mode + the group's "switch" register (enable byte + days)."""
    g = next(g for g in groups if g.index == index)
    reg = ECO_BASE + index * ECO_REGS_PER_GROUP + 3
    candidate = EcoGroup(g.index, g.start, g.end, g.power, enabled, g.days)
    return WritePlan(
        setting=f"eco_group_{index + 1}_switch",
        label=f"Eco group {index + 1}: {'enable' if enabled else 'disable'}",
        before="enabled" if g.enabled else "disabled",
        after="enabled" if enabled else "disabled",
        steps=[
            _work_mode_cmd(3),
            WriteStep(
                f"group {index + 1}: register 0x{reg:04X} = {'FF' if enabled else '00'} + days",
                p.write_registers(reg, bytes([0xFF if enabled else 0x00, g.days & 0x7F])),
            ),
        ],
        verify=["eco", "settings"],
        warning="; ".join(eco_conflicts(groups, candidate)) if enabled else "",
    )


def plan_soc_protect(settings: dict, enabled: bool) -> WritePlan:
    """Command 0x033B: 00 <0 = on, 1 = off> 00 (source C0)."""
    return WritePlan(
        setting="soc_protect",
        label="SoC protection",
        before="on" if soc_protect_on(settings) else "off",
        after="on" if enabled else "off",
        steps=[WriteStep("SoC protection", p.classic_command(0x3B, bytes([0, 0 if enabled else 1, 0])))],
        verify=["settings"],
    )


def plan_ongrid_dod(settings: dict, dod: int) -> WritePlan:
    """BatteryFunction105Activity: register 0x0560 = 100 - DoD, source B0."""
    if not 0 <= dod <= 95:
        raise ValueError("DoD out of range [0, 95] %")
    warning = ""
    if not soc_protect_on(settings):
        warning = (
            "SoC protection is off: in this case SolarGo does not let you change the DoD "
            "(it re-sends it unchanged). Turn SoC protection on first."
        )
    return WritePlan(
        setting="ongrid_dod",
        label="On-grid DoD",
        before=ongrid_dod(settings),
        after=dod,
        steps=[WriteStep(f"register 0x0560 = {100 - dod} (100 − DoD)", p.write_registers(REG_ONGRID_DOD, (100 - dod).to_bytes(2, "big")))],
        verify=["settings"],
        warning=warning,
    )


def plan_offgrid_dod(current: int | None, dod: int) -> WritePlan:
    """Register 0x05A1 = 100 - DoD, source B0 (BatteryFunction105Activity)."""
    if not 0 <= dod <= 95:
        raise ValueError("DoD out of range [0, 95] %")
    return WritePlan(
        setting="offgrid_dod",
        label="Off-grid DoD",
        before=current,
        after=dod,
        steps=[WriteStep(f"register 0x05A1 = {100 - dod} (100 − DoD)", p.write_registers(REG_OFFGRID_DOD, (100 - dod).to_bytes(2, "big")))],
        verify=["offgrid_dod"],
    )


def plan_export_limit(settings: dict, watts: int) -> WritePlan:
    """Command 0x0335 with the limit in W (2 bytes), source C0."""
    if not 0 <= watts <= 10000:
        raise ValueError("limit out of range [0, 10000] W")
    return WritePlan(
        setting="export_limit_w",
        label="Grid export limit",
        before=settings["export_limit_w"],
        after=watts,
        steps=[WriteStep(f"export limit = {watts} W", p.classic_command(0x35, watts.to_bytes(2, "big")))],
        verify=["settings"],
    )


def _switch_plan(setting: str, label: str, before: bool, after: bool, func: int, payload: bytes, desc: str,
                 warning: str = "") -> WritePlan:
    return WritePlan(
        setting=setting,
        label=label,
        before=_onoff(before),
        after=_onoff(after),
        steps=[WriteStep(desc, p.classic_command(func, payload))],
        verify=["settings"],
        warning=warning,
    )


def plan_export_limit_enabled(settings: dict, enabled: bool) -> WritePlan:
    """Command 0x0353: 01 = export limit on, 00 = off (AdvancedSettingNewActivity, switch_back_flow_limit)."""
    return _switch_plan("export_limit_enabled", "Export limit", settings["export_limit_enabled_raw"] == 1, enabled,
                        0x53, bytes([1 if enabled else 0]), f"export limit {_onoff(enabled)}")


def plan_shadow_scan(settings: dict, enabled: bool) -> WritePlan:
    """Command 0x0328: 80 <80 = on / 00 = off> (buildPackage(60))."""
    return _switch_plan("shadow_scan", "Shadow scan", settings["shadow_scan"] == 1, enabled,
                        0x28, bytes([0x80, 0x80 if enabled else 0x00]), f"shadow scan {_onoff(enabled)}")


def plan_backup_supply(settings: dict, enabled: bool) -> WritePlan:
    """Command 0x0327 (relay control): 00 <30 = backup on / 10 = off> (buildPackage(51))."""
    return _switch_plan("backup_supply", "Backup output", settings["backup_supply"] == 1, enabled,
                        0x27, bytes([0x00, 0x30 if enabled else 0x10]), f"backup output {_onoff(enabled)}",
                        "" if enabled else "Loads on the backup port will lose power during a grid outage.")


def plan_battery_activated(settings: dict, enabled: bool) -> WritePlan:
    """Command 0x0337: 00 0F = on, 00 00 = off (buildPackage(70))."""
    return _switch_plan("battery_activated", "Battery activation", battery_activated_on(settings), enabled,
                        0x37, bytes([0x00, 0x0F if enabled else 0x00]), f"battery activation {_onoff(enabled)}",
                        "The battery port will output about 60 V to wake up the battery." if enabled else "")


def plan_detection_mode(settings: dict, mode: int) -> WritePlan:
    """Command 0x035B: 0 full wave, 1 half wave, 2 EPS/LVRT (DetectionModeActivity -> buildPackage(76))."""
    if mode not in DETECTION_MODES:
        raise ValueError("unknown detection mode")
    cur = settings.get("grid_quality_check")
    return WritePlan(
        setting="detection_mode",
        label="Detection mode",
        before=DETECTION_MODES.get(cur, cur),
        after=DETECTION_MODES[mode],
        steps=[WriteStep(f"detection mode = {DETECTION_MODES[mode]}", p.classic_command(0x5B, bytes([mode])))],
        verify=["settings"],
    )


def plan_overload_reset() -> WritePlan:
    """Command 0x035E 01 0F: clear the backup overload history (buildPackage(206))."""
    return WritePlan(
        setting="overload_reset",
        label="Clear overload history",
        before=None,
        after="cleared",
        steps=[WriteStep("clear overload history", p.classic_command(0x5E, bytes([0x0F])))],
        verify=[],
    )


REG_DRED_WRITE = 0xB7BC


def plan_dred(current: int | None, enabled: bool) -> WritePlan:
    """SolarGo setDred: Modbus write-single 0xB7BC = 1 (on) / 0 (off)."""
    return WritePlan(
        setting="dred",
        label="DRED / Remote Shutdown / RCR",
        before=_onoff(current == 1) if current is not None else None,
        after=_onoff(enabled),
        steps=[WriteStep(f"DRED {_onoff(enabled)} (register 0xB7BC = {1 if enabled else 0})",
                         p.ModbusWriteRequest(REG_DRED_WRITE, 1 if enabled else 0))],
        verify=["dred"],
        warning="" if not enabled else
                "Turning DRED on tells the inverter to obey an external control input. Only do this with the "
                "right device connected; otherwise it can stop the inverter from operating normally.",
    )
