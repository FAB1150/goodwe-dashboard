"""Framing dei protocolli GoodWe usati dalla famiglia ES/EM (piattaforma "105").

Due formati convivono sulla stessa porta UDP 8899:

* AA55 (protocollo storico della famiglia ES):
    richiesta:  AA 55 <src> 7F <ctrl> <func> <len> <dati...> <chk_hi> <chk_lo>
    risposta:   AA 55 7F <src> <ctrl> <func|0x80> <len> <dati...> <chk_hi> <chk_lo>
  dove il checksum è la somma semplice (16 bit) di tutti i byte precedenti.
  SolarGo usa due indirizzi sorgente diversi:
    - 0xC0 per i comandi "classici" (lettura info 0x0102, lettura impostazioni 0x0109,
      modalità di lavoro 0x0359, protezione SoC 0x033B, limite immissione 0x0335, ...);
    - 0xB0 per l'accesso diretto ai registri (lettura 0x011A, scrittura 0x0239):
      fasce eco 0x0701.., DoD in rete 0x0560, DoD fuori rete 0x05A1, ...
  La libreria `goodwe` usata da Home Assistant usa sempre 0xC0.

* Modbus RTU incapsulato (usato da SolarGo su ES solo per alcuni registri "alti",
  es. parametri di protezione rete 0xB158.., ricarica forzata 0xB9B9):
    richiesta:  F7 03 <reg_hi> <reg_lo> <n_hi> <n_lo> <crc_lo> <crc_hi>
    risposta:   AA 55 F7 03 <len> <dati...> <crc_lo> <crc_hi>
"""

from __future__ import annotations

from dataclasses import dataclass

HEADER = b"\xaa\x55"
INVERTER_ADDR = 0x7F
SRC_CLASSIC = 0xC0  # comandi "classici" (come HA)
SRC_REGISTER = 0xB0  # accesso a registri (come SolarGo)
MODBUS_ADDR = 0xF7

ACK = 0x06
NAK = 0x15


class ProtocolError(Exception):
    """Risposta non valida o inattesa."""


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
            raise ValueError("payload troppo lungo")
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
        raise ProtocolError(f"risposta troppo corta ({len(raw)} byte)")
    if raw[0:2] != HEADER:
        raise ProtocolError("intestazione AA55 mancante")
    length = raw[6]
    if len(raw) != length + 9:
        raise ProtocolError(f"lunghezza incoerente: dichiarati {length} byte di dati, ricevuti {len(raw)} byte totali")
    if checksum16(raw[:-2]) != raw[-2:]:
        raise ProtocolError("checksum AA55 errato")
    return Aa55Response(src=raw[3], ctrl=raw[4], func=raw[5], data=raw[7:-2])


def check_aa55_reply(req: Aa55Request, raw: bytes) -> Aa55Response:
    resp = parse_aa55(raw)
    if resp.ctrl != req.ctrl or resp.func != req.expected_func:
        raise ProtocolError(
            f"risposta di tipo {resp.ctrl:02X}{resp.func:02X}, attesa {req.ctrl:02X}{req.expected_func:02X}"
        )
    return resp


# Comandi AA55 di uso comune --------------------------------------------------


def read_device_info() -> Aa55Request:
    """0x0102: informazioni dispositivo (modello, SN, firmware, paese di sicurezza)."""
    return Aa55Request(SRC_CLASSIC, 0x01, 0x02)


def read_running_data() -> Aa55Request:
    """0x0106: dati di funzionamento (non usato in automatico: niente polling)."""
    return Aa55Request(SRC_CLASSIC, 0x01, 0x06)


def read_settings() -> Aa55Request:
    """0x0109: blocco impostazioni (batteria, modalità, limite immissione, ...)."""
    return Aa55Request(SRC_CLASSIC, 0x01, 0x09)


def read_registers(start: int, count: int) -> Aa55Request:
    """0x011A: lettura di `count` registri a 16 bit da `start` (indirizzo sorgente B0 come SolarGo)."""
    if not 1 <= count <= 0x7F:
        raise ValueError("numero di registri non valido")
    return Aa55Request(SRC_REGISTER, 0x01, 0x1A, start.to_bytes(2, "big") + bytes([count]))


def write_registers(start: int, values: bytes) -> Aa55Request:
    """0x0239: scrittura di registri a 16 bit da `start` (indirizzo sorgente B0 come SolarGo)."""
    if len(values) == 0 or len(values) % 2:
        raise ValueError("i valori devono essere un multiplo di 2 byte")
    return Aa55Request(SRC_REGISTER, 0x02, 0x39, start.to_bytes(2, "big") + bytes([len(values) // 2]) + values)


def classic_command(func: int, payload: bytes) -> Aa55Request:
    """Comando di controllo 0x03xx con indirizzo sorgente C0."""
    return Aa55Request(SRC_CLASSIC, 0x03, func, payload)


# ---------------------------------------------------------------------------
# Modbus RTU incapsulato
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
        raise ProtocolError("risposta Modbus non valida")
    if raw[3] != 0x03:
        if raw[3] == 0x83:
            raise ProtocolError(f"l'inverter ha rifiutato la lettura Modbus (codice {raw[4]:02X})")
        raise ProtocolError(f"funzione Modbus inattesa {raw[3]:02X}")
    length = raw[4]
    if len(raw) != length + 7:
        raise ProtocolError("lunghezza Modbus incoerente")
    if crc16_modbus(raw[2:-2]).to_bytes(2, "little") != raw[-2:]:
        raise ProtocolError("CRC Modbus errato")
    if length != req.count * 2:
        raise ProtocolError(f"attesi {req.count * 2} byte, ricevuti {length}")
    return raw[5:-2]


Request = Aa55Request | ModbusReadRequest


def describe(req: Request) -> str:
    """Descrizione leggibile di un frame, per la modalità "prova a secco"."""
    if isinstance(req, ModbusReadRequest):
        return f"Modbus lettura {req.count} registri da 0x{req.start:04X}"
    code = f"{req.ctrl:02X}{req.func:02X}"
    src = f"src {req.src:02X}"
    if req.ctrl == 0x01 and req.func == 0x1A:
        start = int.from_bytes(req.payload[0:2], "big")
        return f"AA55 {code} ({src}) lettura {req.payload[2]} registri da 0x{start:04X}"
    if req.ctrl == 0x02 and req.func == 0x39:
        start = int.from_bytes(req.payload[0:2], "big")
        return f"AA55 {code} ({src}) scrittura {req.payload[2]} registri da 0x{start:04X} = {req.payload[3:].hex(' ').upper()}"
    return f"AA55 {code} ({src}) dati: {req.payload.hex(' ').upper() or '-'}"
