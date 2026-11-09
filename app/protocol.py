"""Framing of the GoodWe protocols used by the ES/EM family ("platform 105").

Two formats share the same UDP port 8899:

* AA55 (legacy protocol of the ES family):
    request:   AA 55 <src> 7F <ctrl> <func> <len> <data...> <sum_hi> <sum_lo>
    response:  AA 55 7F <src> <ctrl> <func|0x80> <len> <data...> <sum_hi> <sum_lo>
  where the checksum is the plain 16-bit sum of all preceding bytes.
  SolarGo uses two different source addresses:
    - 0xC0 for the "classic" commands (device info 0x0102, settings 0x0109,
      work mode 0x0359, SoC protection 0x033B, export limit 0x0335, ...);
    - 0xB0 for direct register access (read 0x011A, write 0x0239):
      eco groups 0x0701.., on-grid DoD 0x0560, off-grid DoD 0x05A1, ...
  The `goodwe` library used by Home Assistant always uses 0xC0.

* Encapsulated Modbus RTU (used by SolarGo on ES only for a few "high" registers,
  e.g. grid protection parameters 0xB158.., forced charge 0xB9B9):
    request:   F7 03 <reg_hi> <reg_lo> <n_hi> <n_lo> <crc_lo> <crc_hi>
    response:  AA 55 F7 03 <len> <data...> <crc_lo> <crc_hi>
"""

from __future__ import annotations

from dataclasses import dataclass

HEADER = b"\xaa\x55"
INVERTER_ADDR = 0x7F
SRC_CLASSIC = 0xC0  # "classic" commands (same as Home Assistant)
SRC_REGISTER = 0xB0  # register access (same as SolarGo)
MODBUS_ADDR = 0xF7

ACK = 0x06
NAK = 0x15


class ProtocolError(Exception):
    """Invalid or unexpected response."""


def checksum16(data: bytes) -> bytes:
    return (sum(data) & 0xFFFF).to_bytes(2, "big")


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


# ---------------------------------------------------------------------------
# AA55
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Aa55Request:
    src: int
    ctrl: int
    func: int
    payload: bytes = b""

    def to_bytes(self) -> bytes:
        if len(self.payload) > 255:
            raise ValueError("payload too long")
        body = HEADER + bytes([self.src, INVERTER_ADDR, self.ctrl, self.func, len(self.payload)]) + self.payload
        return body + checksum16(body)

    @property
    def expected_func(self) -> int:
        return self.func | 0x80


@dataclass(frozen=True)
class Aa55Response:
    src: int
    ctrl: int
    func: int
    data: bytes

    @property
    def is_ack(self) -> bool:
        return len(self.data) >= 1 and self.data[0] == ACK


def parse_aa55(raw: bytes) -> Aa55Response:
    if len(raw) < 9:
        raise ProtocolError(f"response too short ({len(raw)} bytes)")
    if raw[0:2] != HEADER:
        raise ProtocolError("missing AA55 header")
    length = raw[6]
    if len(raw) != length + 9:
        raise ProtocolError(f"inconsistent length: {length} data bytes declared, {len(raw)} bytes received")
    if checksum16(raw[:-2]) != raw[-2:]:
        raise ProtocolError("bad AA55 checksum")
    return Aa55Response(src=raw[3], ctrl=raw[4], func=raw[5], data=raw[7:-2])


def check_aa55_reply(req: Aa55Request, raw: bytes) -> Aa55Response:
    resp = parse_aa55(raw)
    if resp.ctrl != req.ctrl or resp.func != req.expected_func:
        raise ProtocolError(
            f"response type {resp.ctrl:02X}{resp.func:02X}, expected {req.ctrl:02X}{req.expected_func:02X}"
        )
    return resp


# Common AA55 commands --------------------------------------------------------


def read_device_info() -> Aa55Request:
    """0x0102: device info (model, serial number, firmware, safety country)."""
    return Aa55Request(SRC_CLASSIC, 0x01, 0x02)


def read_running_data() -> Aa55Request:
    """0x0106: running data (never used automatically: no polling)."""
    return Aa55Request(SRC_CLASSIC, 0x01, 0x06)


def read_settings() -> Aa55Request:
    """0x0109: settings block (battery, work mode, export limit, ...)."""
    return Aa55Request(SRC_CLASSIC, 0x01, 0x09)


def read_registers(start: int, count: int) -> Aa55Request:
    """0x011A: read `count` 16-bit registers from `start` (source B0, like SolarGo)."""
    if not 1 <= count <= 0x7F:
        raise ValueError("invalid register count")
    return Aa55Request(SRC_REGISTER, 0x01, 0x1A, start.to_bytes(2, "big") + bytes([count]))


def write_registers(start: int, values: bytes) -> Aa55Request:
    """0x0239: write 16-bit registers from `start` (source B0, like SolarGo)."""
    if len(values) == 0 or len(values) % 2:
        raise ValueError("values must be a multiple of 2 bytes")
    return Aa55Request(SRC_REGISTER, 0x02, 0x39, start.to_bytes(2, "big") + bytes([len(values) // 2]) + values)


def classic_command(func: int, payload: bytes) -> Aa55Request:
    """0x03xx control command with source address C0."""
    return Aa55Request(SRC_CLASSIC, 0x03, func, payload)


# ---------------------------------------------------------------------------
# Encapsulated Modbus RTU
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModbusReadRequest:
    start: int
    count: int
    addr: int = MODBUS_ADDR

    def to_bytes(self) -> bytes:
        body = bytes([self.addr, 0x03]) + self.start.to_bytes(2, "big") + self.count.to_bytes(2, "big")
        return body + crc16_modbus(body).to_bytes(2, "little")


def parse_modbus_read(req: ModbusReadRequest, raw: bytes) -> bytes:
    if len(raw) < 7 or raw[0:2] != HEADER:
        raise ProtocolError("invalid Modbus response")
    if raw[3] != 0x03:
        if raw[3] == 0x83:
            raise ProtocolError(f"the inverter rejected the Modbus read (code {raw[4]:02X})")
        raise ProtocolError(f"unexpected Modbus function {raw[3]:02X}")
    length = raw[4]
    if len(raw) != length + 7:
        raise ProtocolError("inconsistent Modbus length")
    if crc16_modbus(raw[2:-2]).to_bytes(2, "little") != raw[-2:]:
        raise ProtocolError("bad Modbus CRC")
    if length != req.count * 2:
        raise ProtocolError(f"expected {req.count * 2} bytes, received {length}")
    return raw[5:-2]


Request = Aa55Request | ModbusReadRequest


def describe(req: Request) -> str:
    """Human-readable description of a frame, used by the dry-run mode."""
    if isinstance(req, ModbusReadRequest):
        return f"Modbus read {req.count} registers from 0x{req.start:04X}"
    code = f"{req.ctrl:02X}{req.func:02X}"
    src = f"src {req.src:02X}"
    if req.ctrl == 0x01 and req.func == 0x1A:
        start = int.from_bytes(req.payload[0:2], "big")
        return f"AA55 {code} ({src}) read {req.payload[2]} registers from 0x{start:04X}"
    if req.ctrl == 0x02 and req.func == 0x39:
        start = int.from_bytes(req.payload[0:2], "big")
        return f"AA55 {code} ({src}) write {req.payload[2]} registers from 0x{start:04X} = {req.payload[3:].hex(' ').upper()}"
    return f"AA55 {code} ({src}) data: {req.payload.hex(' ').upper() or '-'}"
