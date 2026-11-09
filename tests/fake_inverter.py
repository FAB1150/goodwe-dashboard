"""Fake ES inverter over UDP, for tests and for trying the page without the real inverter.

Answers with frames recorded from a real GW5048-EM (samples folder, taken from the tests of the
`goodwe` library, MIT licence) and simulates registers/settings in memory.

Manual use:  python -m tests.fake_inverter 18899
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app import protocol as p

SAMPLES = Path(__file__).parent / "samples"


def _sample_data(name: str) -> bytes:
    raw = bytes.fromhex(SAMPLES.joinpath(name).read_text().strip())
    return p.parse_aa55(raw).data


class FakeInverter(asyncio.DatagramProtocol):
    def __init__(self, mute_for: int = 0) -> None:
        self.device_info = _sample_data("GW5048-EM_device_info.hex")
        self.settings = bytearray(_sample_data("GW5048D-ES_settings_data.hex"))
        self.registers: dict[int, int] = {}
        # eco groups: group 1 charges 01:00-05:00 at 50 % every day, enabled
        eco = bytes([1, 0, 5, 0]) + (-50).to_bytes(2, "big", signed=True) + bytes([0xFF, 0x7F])
        eco += bytes([17, 0, 21, 0, 0, 100, 0x00, 0x3E])  # group 2 discharges, disabled, Mon-Fri
        eco += bytes(16)
        for i in range(16):
            self.registers[0x0701 + i] = int.from_bytes(eco[i * 2 : i * 2 + 2], "big")
        self.registers[0x05A1] = 80
        self.registers[0x0560] = 100 - self.ongrid_dod
        for i in range(17):
            self.registers[0xB158 + i] = 2645 + i
        for i in range(16):
            self.registers[0xB169 + i] = 5010 - i
        self.received: list[bytes] = []
        self.mute_for = mute_for  # number of requests to ignore (simulates a silent WiFi module)
        self.transport = None
        self.reject_ack = False

    @property
    def ongrid_dod(self) -> int:
        return 100 - int.from_bytes(self.settings[32:34], "big")

    def connection_made(self, transport) -> None:  # noqa: ANN001
        self.transport = transport

    def _reply(self, src: int, ctrl: int, func: int, data: bytes) -> bytes:
        body = p.HEADER + bytes([0x7F, src, ctrl, func | 0x80, len(data)]) + data
        return body + p.checksum16(body)

    def handle(self, frame: bytes) -> bytes | None:
        if frame[0] == p.MODBUS_ADDR and frame[1] == 0x03:
            start = int.from_bytes(frame[2:4], "big")
            count = int.from_bytes(frame[4:6], "big")
            data = b"".join(self.registers.get(start + i, 0).to_bytes(2, "big") for i in range(count))
            body = bytes([p.MODBUS_ADDR, 0x03, len(data)]) + data
            return p.HEADER + body + p.crc16_modbus(body).to_bytes(2, "little")
        req = p.parse_aa55(frame)  # a request has the same structure as a response
        src, ctrl, func, data = frame[2], req.ctrl, req.func, req.data
        ack = bytes([p.NAK if self.reject_ack else p.ACK])
        if (ctrl, func) == (0x01, 0x02):
            return self._reply(src, ctrl, func, self.device_info)
        if (ctrl, func) == (0x01, 0x09):
            return self._reply(src, ctrl, func, bytes(self.settings))
        if (ctrl, func) == (0x01, 0x1A):
            start = int.from_bytes(data[0:2], "big")
            out = b"".join(self.registers.get(start + i, 0).to_bytes(2, "big") for i in range(data[2]))
            return self._reply(src, ctrl, func, out)
        if (ctrl, func) == (0x02, 0x39):
            start = int.from_bytes(data[0:2], "big")
            values = data[3:]
            if not self.reject_ack:
                for i in range(data[2]):
                    self.registers[start + i] = int.from_bytes(values[i * 2 : i * 2 + 2], "big")
                if start == 0x0560:
                    self.settings[32:34] = values[0:2]
            return self._reply(src, ctrl, func, ack)
        if ctrl == 0x03:
            if not self.reject_ack:
                if func == 0x59:
                    self.settings[66:68] = bytes([0, data[0]])
                elif func == 0x3B:
                    self.settings[56:58] = bytes([0, data[1]])
                elif func == 0x35:
                    self.settings[52:54] = data[0:2]
                elif func == 0x53:
                    self.settings[18:20] = bytes([0, data[0]])
                elif func == 0x28:
                    self.settings[16:18] = bytes([0, 1 if data[1] else 0])
                elif func == 0x27:
                    self.settings[12:14] = bytes([0, 1 if data[1] == 0x30 else 0])
                elif func == 0x37:
                    self.settings[34:36] = data[0:2]
                elif func == 0x5B:
                    self.settings[68:70] = bytes([0, data[0]])
            return self._reply(src, ctrl, func, ack)
        return None

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        self.received.append(data)
        if self.mute_for > 0:
            self.mute_for -= 1
            return
        reply = self.handle(data)
        if reply:
            self.transport.sendto(reply, addr)


async def start(port: int = 0, **kw) -> tuple[asyncio.DatagramTransport, FakeInverter, int]:  # noqa: ANN003
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(lambda: FakeInverter(**kw), local_addr=("127.0.0.1", port))
    return transport, proto, transport.get_extra_info("sockname")[1]


if __name__ == "__main__":
    async def _main() -> None:
        port = int(sys.argv[1]) if len(sys.argv) > 1 else 18899
        await start(port)
        print(f"Fake inverter listening on 127.0.0.1:{port} (UDP)")
        await asyncio.Event().wait()

    asyncio.run(_main())
