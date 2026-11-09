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
    """A value shown in the page."""

    key: str
    label: str
    value: Any
    unit: str = ""
    raw: Any = None
    note: str = ""
    editable: bool = False
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
        Field("model", "Model", info.model),
        Field("serial", "Serial number", info.serial),
        Field("firmware", "Firmware", info.firmware),
        Field("software", "Software version", info.software),
        Field("arm_string", "ARM version (extended)", info.arm_string or "—"),
        Field("dsp_version", "DSP version (from firmware)", info.dsp_version),
        Field("arm_version", "ARM version as used by SolarGo", info.arm_version),
        Field(
            "safety_country",
            "Safety country",
            info.safety_country,
            raw=info.safety_country_code,
            protected=True,
            note="Grid interface parameter: read-only. Byte 63 of the 0x0182 response, as in SolarGo; "
            "on some firmware this byte does not hold the country (e.g. 32 = space).",
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


def settings_fields(s: dict) -> dict[str, list[Field]]:
    wm = s.get("work_mode")
    pf_raw = s["power_factor_raw"]
    pf = pf_raw - 100 if 0 <= pf_raw <= 20 else pf_raw  # SolarGo's transformation
    discharge_a = min(s["discharge_i_raw"] / 0.128 / 10, 120.0)
    charge_a = s["charge_i_raw"] / 0.266 / 10
    return {
        "mode": [
            Field(
                "work_mode",
                "Work mode",
                WORK_MODES.get(wm, f"unknown ({wm})") if wm is not None else "—",
                raw=wm,
                editable=True,
            ),
            Field("charge_window", "Charge window (legacy command 0x032C)", s["charge_window"],
                  note=f"limit {s['charge_power_limit']} %"),
            Field("discharge_window", "Discharge window (legacy command 0x032D)", s["discharge_window"],
                  note=f"limit {s['discharge_power_limit']} %"),
        ],
        "battery": [
            Field("soc_protect", "SoC protection", "on" if soc_protect_on(s) else "off",
                  raw=s["soc_protect_raw"], editable=True,
                  note="SolarGo only lets you change the on-grid DoD while SoC protection is on."),
            Field("ongrid_dod", "On-grid depth of discharge (DoD)", ongrid_dod(s), "%",
                  raw=s["min_soc_raw"], editable=True,
                  note=f"on-grid minimum SoC = {s['min_soc_raw']} %"),
            Field("capacity_ah", "Battery capacity", s["capacity_ah"], "Ah"),
            Field("charge_v", "Charge voltage", s["charge_v_raw"] / 10, "V", raw=s["charge_v_raw"]),
            Field("charge_i", "Charge current (SolarGo conversion)", round(charge_a, 1), "A",
                  raw=s["charge_i_raw"]),
            Field("discharge_v", "Discharge voltage", s["discharge_v_raw"] / 10, "V", raw=s["discharge_v_raw"]),
            Field("discharge_i", "Discharge current (SolarGo conversion)", round(discharge_a, 1), "A",
                  raw=s["discharge_i_raw"]),
            Field("battery_model", "Battery model (255 = custom)", s["battery_model"]),
            Field("battery_activated", "Battery activation", s["battery_activated"]),
            Field("offgrid_charge", "Grid charging in off-grid mode", s["offgrid_charge"]),
        ],
        "grid": [
            Field("export_limit_w", "Grid export limit", s["export_limit_w"], "W", editable=True),
            Field("export_limit_enabled", "Export limitation (read state)",
                  "on" if s["export_limit_enabled_raw"] == 1 else "off",
                  raw=s["export_limit_enabled_raw"],
                  note="The switch cannot be changed from the portal yet (polarity to be verified)."),
            Field("backup_supply", "Backup output (EPS)", "on" if s["backup_supply"] else "off",
                  raw=s["backup_supply"]),
            Field("power_factor", "Power factor", pf, raw=pf_raw),
            Field("shadow_scan", "Shadow scan (MPPT)", "on" if s["shadow_scan"] else "off",
                  raw=s["shadow_scan"]),
            Field("grid_quality_check", "Grid quality check", s.get("grid_quality_check"),
                  raw=s.get("grid_quality_check")),
        ],
    }


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
# Grid protection parameters (read-only, Modbus 0xB158.. and 0xB169..)
# ---------------------------------------------------------------------------

# (register, label, unit, divisor)
PROTECTION_BLOCK = (0xB158, 17)
PROTECTION_FIELDS = [
    (0xB158, "Overvoltage stage 1", "V", 10),
    (0xB159, "Overvoltage stage 1 – time", "periods", 1),
    (0xB15A, "Undervoltage stage 1", "V", 10),
    (0xB15B, "Undervoltage stage 1 – time", "periods", 1),
    (0xB15C, "Overvoltage stage 2", "V", 10),
    (0xB15D, "Overvoltage stage 2 – time", "periods", 1),
    (0xB15E, "Undervoltage stage 2", "V", 10),
    (0xB15F, "Undervoltage stage 2 – time", "periods", 1),
    (0xB160, "10-minute average overvoltage", "V", 10),
    (0xB161, "Overfrequency stage 1", "Hz", 100),
    (0xB162, "Overfrequency stage 1 – time", "periods", 1),
    (0xB163, "Underfrequency stage 1", "Hz", 100),
    (0xB164, "Underfrequency stage 1 – time", "periods", 1),
    (0xB165, "Overfrequency stage 2", "Hz", 100),
    (0xB166, "Overfrequency stage 2 – time", "periods", 1),
    (0xB167, "Underfrequency stage 2", "Hz", 100),
    (0xB168, "Underfrequency stage 2 – time", "periods", 1),
]
CONNECT_BLOCK = (0xB169, 16)
CONNECT_FIELDS = [
    (0xB169, "Connection voltage – max", "V", 10),
    (0xB16A, "Connection voltage – min", "V", 10),
    (0xB16B, "Connection frequency – max", "Hz", 100),
    (0xB16C, "Connection frequency – min", "Hz", 100),
    (0xB16D, "Wait time before connecting", "s", 1),
    (0xB16E, "Reconnection after fault: voltage max", "V", 10),
    (0xB16F, "Reconnection after fault: voltage min", "V", 10),
    (0xB170, "Reconnection after fault: frequency max", "Hz", 100),
    (0xB171, "Reconnection after fault: frequency min", "Hz", 100),
    (0xB172, "Reconnection after fault: wait time", "s", 1),
    (0xB173, "Reconnection after frequency fault: wait time", "s", 1),
    (0xB174, "Power ramp on connection", "", 1),
    (0xB175, "Power ramp after fault", "", 1),
    (0xB178, "Connection ramp enabled", "", 1),
]


def parse_register_block(start: int, data: bytes, spec) -> list[Field]:  # noqa: ANN001
    out = []
    for reg, label, unit, div in spec:
        off = (reg - start) * 2
        if off + 2 > len(data):
            continue
        raw = _u16(data, off)
        value = raw / div if div != 1 else raw
        out.append(
            Field(f"reg_{reg:04X}", label, value, unit, raw=raw, protected=True,
                  note=f"register 0x{reg:04X} — read-only (CEI 0-21)")
        )
    return out


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
    ]


def protection_read_steps() -> list[ReadStep]:
    return [
        ReadStep("protection", "Protection parameters (Modbus 0xB158, 17 registers)",
                 p.ModbusReadRequest(*PROTECTION_BLOCK)),
        ReadStep("connect", "Connection parameters (Modbus 0xB169, 16 registers)",
                 p.ModbusReadRequest(*CONNECT_BLOCK)),
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
