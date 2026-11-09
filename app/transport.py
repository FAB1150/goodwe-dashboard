"""Trasporto UDP verso il modulo WiFi dell'inverter.

Scelte:
* un socket nuovo per ogni richiesta (come fa SolarGo), così nessuna risposta tardiva
  di una richiesta precedente viene scambiata per quella corrente;
* una sola richiesta alla volta (lock), con una piccola pausa tra una e l'altra,
  per convivere con Home Assistant che interroga l'inverter ogni 5 secondi;
* timeout breve e pochi tentativi: il modulo WiFi a volte resta muto per 10-30 s;
  in quel caso si restituisce un errore chiaro invece di insistere;
* le scritture passano da una lista di comandi ammessi (`ensure_write_allowed`):
  i parametri di protezione di interfaccia/rete (CEI 0-21) non sono scrivibili.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from . import protocol as p

log = logging.getLogger(__name__)


class InverterUnreachable(Exception):
    """Nessuna risposta valida dopo tutti i tentativi."""


class WriteNotAllowed(Exception):
    """Tentativo di inviare un comando di scrittura fuori dalla lista ammessa."""


# --- Lista dei comandi di scrittura ammessi ---------------------------------
# Comandi "classici" 0x03xx (sorgente C0) che il portale può inviare.
ALLOWED_CLASSIC_WRITES: dict[int, str] = {
    0x59: "modalità di lavoro",
    0x36: "modalità lavoro fuori rete (flag)",
    0x2C: "finestra di carica (modalità legacy)",
    0x2D: "finestra di scarica (modalità legacy)",
    0x3B: "protezione SoC",
    0x35: "limite di immissione in rete",
}
# Registri scrivibili con 0x0239 (sorgente B0): (inizio, fine inclusa)
ALLOWED_REGISTER_RANGES: list[tuple[int, int, str]] = [
    (0x0700, 0x0700, "azzeramento parametri modalità batteria"),
    (0x0701, 0x0710, "fasce eco (4 gruppi)"),
    (0x0560, 0x0560, "DoD in rete"),
    (0x05A1, 0x05A1, "DoD fuori rete"),
]


def is_write(req: p.Request) -> bool:
    if isinstance(req, p.ModbusReadRequest):
        return False
    return req.ctrl in (0x02, 0x03)


def ensure_write_allowed(req: p.Request) -> None:
    """Solleva WriteNotAllowed se `req` è una scrittura non presente nella lista ammessa."""
    if not is_write(req):
        return
    assert isinstance(req, p.Aa55Request)
    if req.ctrl == 0x03:
        if req.src == p.SRC_CLASSIC and req.func in ALLOWED_CLASSIC_WRITES:
            return
        raise WriteNotAllowed(f"comando 03{req.func:02X} non ammesso")
    if req.ctrl == 0x02 and req.func == 0x39 and req.src == p.SRC_REGISTER:
        start = int.from_bytes(req.payload[0:2], "big")
        count = req.payload[2]
        end = start + count - 1
        for lo, hi, _ in ALLOWED_REGISTER_RANGES:
            if lo <= start and end <= hi:
                return
        raise WriteNotAllowed(f"scrittura registri 0x{start:04X}-0x{end:04X} non ammessa")
    raise WriteNotAllowed(f"comando {req.ctrl:02X}{req.func:02X} non ammesso")


@dataclass
class Exchange:
    """Traccia di uno scambio, mostrata nella pagina (modalità diagnostica / prova a secco)."""

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
    gap: float = 0.25  # pausa minima tra due richieste
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
                    # risposta frammentata o estranea (es. destinata ad altri): continua ad aspettare
                    last_err = e
                    if len(buf) > 2048 or (len(buf) >= 7 and buf[0:2] == p.HEADER and len(buf) > buf[6] + 9):
                        buf = b""
        finally:
            transport.close()

    async def request(self, req: p.Request, description: str, trace: list[Exchange] | None = None):
        """Invia `req` e restituisce i dati utili della risposta (validata)."""
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
                        errors.append(f"tentativo {attempt}: nessuna risposta ({e})")
                    except OSError as e:
                        errors.append(f"tentativo {attempt}: errore di rete ({e})")
                    finally:
                        self._last = time.monotonic()
                ex.error = "; ".join(errors)
                raise InverterUnreachable(
                    f"L'inverter {self.host} non ha risposto a «{description}» dopo {self.retries} tentativi. "
                    "Il modulo WiFi a volte resta muto per 10-30 secondi: riprova tra poco."
                )
            finally:
                ex.elapsed_ms = int((time.monotonic() - start) * 1000)
                if trace is not None:
                    trace.append(ex)


def dry_run_exchange(req: p.Request, description: str) -> Exchange:
    """Prepara il frame senza inviarlo (verifica comunque la lista dei comandi ammessi)."""
    ensure_write_allowed(req)
    return Exchange(
        description=f"{description} — {p.describe(req)}",
        request_hex=req.to_bytes().hex(" ").upper(),
        sent=False,
    )
