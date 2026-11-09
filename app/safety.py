"""Grid interface protection ("safety") parameters: read, and write behind an explicit unlock.

These are the parameters of SolarGo's "Impostazioni dei parametri di sicurezza" page
(protection, connection, curves, ride-through, PQ). They are set by the grid code and in most
countries must not be changed by the end user. The portal shows them read-only by default and
only lets them be written when the user has explicitly unlocked editing (see app/main.py), which
is intended for a qualified installer.

Read:  Modbus block reads F7 03 <start> <count> (same blocks as SolarGo's get_* commands).
Write: Modbus write-single F7 06 <reg> <value>, value = displayed * gain (two's complement),
       exactly as SolarGo's setCustomSaftyParam. Trip-times and curve time-constants use a
       firmware-dependent / non-linear path in SolarGo, so they stay read-only here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import protocol as p
from . import safety_catalog as cat
from .es import ReadStep, WritePlan, WriteStep

# Registers the portal is allowed to write (writable catalogue entries). Used by the transport
# allowlist; a frame is still only ever built by plan_safety(), which requires the unlock token.
WRITABLE_REGISTERS: set[int] = {p_[0] for p_ in cat.SAFETY_PARAMS if p_[7]}
UNLOCK_PHRASE = "I UNDERSTAND"

_BY_REG: dict[int, tuple] = {p_[0]: p_ for p_ in cat.SAFETY_PARAMS}


def block_read_steps() -> list[ReadStep]:
    return [
        ReadStep(f"safety_{key}", f"Safety block {key} (Modbus 0x{start:04X}, {count} registers)",
                 p.ModbusReadRequest(start, count))
        for key, start, count in cat.BLOCKS
    ]


def _raw_at(blocks: dict[str, bytes], reg: int) -> int | None:
    for key, start, count in cat.BLOCKS:
        if start <= reg < start + count:
            data = blocks.get(f"safety_{key}")
            if data is None:
                return None
            off = (reg - start) * 2
            if off + 2 > len(data):
                return None
            return int.from_bytes(data[off:off + 2], "big")
    return None


def _decode(kind: str, gain: int, signed: bool, raw: int) -> Any:
    if kind == "switch":
        return raw != 0
    if kind == "time":
        return round(raw * 0.02, 2)  # SolarGo: trip time shown as raw * 0.02 s
    val = raw - 0x10000 if signed and raw >= 0x8000 else raw
    return round(val / gain, 3) if gain != 1 else val


def encode(kind: str, gain: int, value: Any) -> int:
    if kind == "switch":
        return 1 if value else 0
    return round(float(value) * gain)


def _display(kind: str, value: Any, unit: str) -> str:
    if kind == "switch":
        return "on" if value else "off"
    return f"{value} {unit}".strip()


def fields(blocks: dict[str, bytes]) -> dict[str, Any]:
    """Return the safety groups that were read, each field read-only but carrying `edit`
    metadata (kind/min/max/reg) when the parameter is writable. The client shows an edit
    control only after the user unlocks editing; writes are enforced server-side."""
    groups: dict[str, list[dict]] = {k: [] for k, _ in cat.GROUPS}
    for reg, gkey, label, kind, gain, unit, signed, writable, lo, hi, help_text in cat.SAFETY_PARAMS:
        raw = _raw_at(blocks, reg)
        if raw is None:
            continue
        value = _decode(kind, gain, signed, raw)
        note = f"register 0x{reg:04X}" + ("" if writable else " · set from SolarGo")
        groups[gkey].append({
            "key": f"safety_{reg:04X}",
            "label": label,
            "value": ("on" if value else "off") if kind == "switch" else value,
            "unit": "" if kind == "switch" else unit,
            "raw": raw,
            "note": note,
            "help": help_text,
            "protected": True,
            "edit": ({"setting": f"safety:{reg:04X}", "kind": "switch" if kind == "switch" else "number",
                      "on": bool(value) if kind == "switch" else None,
                      "min": lo, "max": hi} if writable else None),
        })
    titles = dict(cat.GROUPS)
    return {"groups": [{"key": k, "title": titles[k], "fields": groups[k]}
                       for k, _ in cat.GROUPS if groups[k]]}


def plan_safety(reg: int, value: Any) -> WritePlan:
    entry = _BY_REG.get(reg)
    if entry is None:
        raise ValueError(f"unknown safety register 0x{reg:04X}")
    _, gkey, label, kind, gain, unit, signed, writable, lo, hi, _ = entry
    if not writable:
        raise ValueError(f"{label} cannot be written from the portal (set it from SolarGo)")
    if kind == "switch":
        after: Any = bool(value)
        raw = 1 if after else 0
    else:
        v = float(value)
        if lo is not None and not (lo <= v <= hi):
            raise ValueError(f"{label}: value out of range [{lo}, {hi}] {unit}".strip())
        after = v
        raw = encode(kind, gain, v)
        if not -0x8000 <= raw <= 0xFFFF:
            raise ValueError(f"{label}: encoded value {raw} does not fit in one register")
    block = next((f"safety_{k}" for k, start, count in cat.BLOCKS if start <= reg < start + count), None)
    return WritePlan(
        setting=f"safety:{reg:04X}",
        label=f"[SAFETY] {label}",
        before=None,
        after=_display(kind, after, unit),
        steps=[WriteStep(f"write register 0x{reg:04X} = {raw & 0xFFFF} (0x{raw & 0xFFFF:04X})",
                         p.ModbusWriteRequest(reg, raw & 0xFFFF))],
        verify=[block] if block else [],
        warning="Grid interface protection parameter. Changing it can make the system non-compliant "
                "or unsafe, and in most countries only a qualified installer may do so.",
    )


def reread_value(reg: int, blocks: dict[str, bytes]) -> Any:
    entry = _BY_REG.get(reg)
    if entry is None:
        return None
    _, _, _, kind, gain, unit, signed, _, _, _, _ = entry
    raw = _raw_at(blocks, reg)
    if raw is None:
        return None
    return _display(kind, _decode(kind, gain, signed, raw), unit)
