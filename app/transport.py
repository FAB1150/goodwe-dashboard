"""UDP transport to the inverter's WiFi module.

Design choices:
* a fresh socket for every request (as SolarGo does), so a late reply to a previous
  request is never mistaken for the current one;
* one request at a time (lock), with a short gap between requests, to coexist with
  Home Assistant polling the inverter every 5 seconds;
* short timeout and few retries: the WiFi module sometimes goes silent for 10-30 s;
  in that case a clear error is returned instead of insisting;
* writes go through an allowlist of commands (`ensure_write_allowed`):
  grid/interface protection parameters (CEI 0-21) can never be written.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from . import protocol as p

log = logging.getLogger(__name__)


class InverterUnreachable(Exception):
    """No valid response after all retries."""


class WriteNotAllowed(Exception):
    """Attempt to send a write command that is not in the allowlist."""


# --- Allowlist of write commands --------------------------------------------
# "Classic" 0x03xx commands (source C0) the portal may send.
ALLOWED_CLASSIC_WRITES: dict[int, str] = {
    0x59: "work mode",
    0x36: "off-grid work mode flag",
    0x2C: "charge window (legacy mode)",
    0x2D: "discharge window (legacy mode)",
    0x3B: "SoC protection",
    0x35: "grid export limit",
    0x53: "export limit on/off",
    0x28: "shadow scan",
    0x27: "backup output (relay control)",
    0x37: "battery activation",
    0x5B: "backup detection mode",
    0x5E: "clear backup overload history",
}
# Registers writable with 0x0239 (source B0): (start, end inclusive, description)
ALLOWED_REGISTER_RANGES: list[tuple[int, int, str]] = [
    (0x0700, 0x0700, "reset battery mode parameters"),
    (0x0701, 0x0710, "eco groups (4 groups)"),
    (0x0560, 0x0560, "on-grid DoD"),
    (0x05A1, 0x05A1, "off-grid DoD"),
]


def is_write(req: p.Request) -> bool:
    if isinstance(req, p.ModbusReadRequest):
        return False
    return req.ctrl in (0x02, 0x03)


def ensure_write_allowed(req: p.Request) -> None:
    """Raise WriteNotAllowed if `req` is a write that is not in the allowlist."""
    if not is_write(req):
        return
    assert isinstance(req, p.Aa55Request)
    if req.ctrl == 0x03:
        if req.src == p.SRC_CLASSIC and req.func in ALLOWED_CLASSIC_WRITES:
            return
        raise WriteNotAllowed(f"command 03{req.func:02X} is not allowed")
    if req.ctrl == 0x02 and req.func == 0x39 and req.src == p.SRC_REGISTER:
        start = int.from_bytes(req.payload[0:2], "big")
        count = req.payload[2]
        end = start + count - 1
        for lo, hi, _ in ALLOWED_REGISTER_RANGES:
            if lo <= start and end <= hi:
                return
        raise WriteNotAllowed(f"writing registers 0x{start:04X}-0x{end:04X} is not allowed")
    raise WriteNotAllowed(f"command {req.ctrl:02X}{req.func:02X} is not allowed")


@dataclass
class Exchange:
    """Trace of one exchange, shown in the page (diagnostics / dry run)."""

    description: str
    request_hex: str
    response_hex: str | None = None
    sent: bool = True
    error: str | None = None
    attempts: int = 0
    elapsed_ms: int = 0


class _OneShot(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        self.queue.put_nowait(data)


@dataclass
class UdpClient:
    host: str
    port: int = 8899
    timeout: float = 2.0
    retries: int = 3
    gap: float = 0.25  # minimum pause between two requests
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _last: float = field(default=0.0, repr=False)

    async def _roundtrip(self, frame: bytes, validate) -> bytes:  # noqa: ANN001
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(_OneShot, remote_addr=(self.host, self.port))
        try:
            transport.sendto(frame)
            deadline = loop.time() + self.timeout
            last_err: Exception | None = None
            buf = b""
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError(str(last_err) if last_err else "timeout")
                chunk = await asyncio.wait_for(proto.queue.get(), remaining)
                buf = chunk if not buf else buf + chunk
                try:
                    validate(buf)
                    return buf
                except p.ProtocolError as e:
                    # fragmented or unrelated reply: keep waiting
                    last_err = e
                    if len(buf) > 2048 or (len(buf) >= 7 and buf[0:2] == p.HEADER and len(buf) > buf[6] + 9):
                        buf = b""
        finally:
            transport.close()

    async def request(self, req: p.Request, description: str, trace: list[Exchange] | None = None):
        """Send `req` and return the validated response payload."""
        ensure_write_allowed(req)
        frame = req.to_bytes()
        if isinstance(req, p.ModbusReadRequest):
            validate = lambda raw: p.parse_modbus_read(req, raw)  # noqa: E731
        else:
            validate = lambda raw: p.check_aa55_reply(req, raw)  # noqa: E731
        ex = Exchange(description=description, request_hex=frame.hex(" ").upper())
        start = time.monotonic()
        async with self._lock:
            errors: list[str] = []
            try:
                for attempt in range(1, self.retries + 1):
                    wait = self.gap - (time.monotonic() - self._last)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    ex.attempts = attempt
                    try:
                        raw = await self._roundtrip(frame, validate)
                        ex.response_hex = raw.hex(" ").upper()
                        return validate(raw)
                    except (TimeoutError, asyncio.TimeoutError) as e:
                        errors.append(f"attempt {attempt}: no response ({e})")
                    except OSError as e:
                        errors.append(f"attempt {attempt}: network error ({e})")
                    finally:
                        self._last = time.monotonic()
                ex.error = "; ".join(errors)
                raise InverterUnreachable(
                    f"The inverter at {self.host} did not answer '{description}' after {self.retries} attempts. "
                    "The WiFi module sometimes goes silent for 10-30 seconds: try again shortly."
                )
            finally:
                ex.elapsed_ms = int((time.monotonic() - start) * 1000)
                if trace is not None:
                    trace.append(ex)


def dry_run_exchange(req: p.Request, description: str) -> Exchange:
    """Build the frame without sending it (the allowlist is still enforced)."""
    ensure_write_allowed(req)
    return Exchange(
        description=f"{description} — {p.describe(req)}",
        request_hex=req.to_bytes().hex(" ").upper(),
        sent=False,
    )
