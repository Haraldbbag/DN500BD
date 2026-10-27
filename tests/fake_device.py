"""Minimal DN-500BD MKII emulator for tests (behaviour as documented)."""

from __future__ import annotations

import asyncio

ACK = b"\x06"
NACK = b"\x15"


class FakeDevice:
    """TCP server that speaks the documented protocol."""

    def __init__(self) -> None:
        self.power = True
        self.transport = "PL"
        self.disc = "CI"
        self.elapsed = "0012345"  # 1:23:45
        self.remaining = "0001015"  # 0:10:15
        self.chapter = 3
        self.chapters = 12
        self.title = 1
        self.titles = 4
        self.received: list[str] = []
        self.acks_from_host = 0
        self.drop_next_ack = 0  # simulate lost ACKs
        self.busy_next = False
        self.text_acks = False  # real units: "ack"/"nack" text, "+" before answers
        self.stale_first_connection = False  # first connection goes silent
        self.connections = 0
        self._server: asyncio.base_events.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for writer in self._writers:
            writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def notify(self, body: str) -> None:
        """Send an unsolicited status notification."""
        for writer in self._writers:
            writer.write(f"@0{body}\r".encode("latin-1"))
            await writer.drain()

    def _ack(self) -> bytes:
        return b"ack" if self.text_acks else ACK

    def _nack(self) -> bytes:
        return b"nack" if self.text_acks else NACK

    async def _client(self, reader, writer) -> None:
        self.connections += 1
        silent = self.stale_first_connection and self.connections == 1
        self._writers.append(writer)
        buf = b""
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                for byte in data:
                    ch = bytes([byte])
                    if ch == ACK and not buf:
                        self.acks_from_host += 1
                        continue
                    if ch == b"\r":
                        if buf:
                            await self._handle(buf.decode("latin-1"), writer, silent)
                        buf = b""
                        continue
                    buf += ch
        finally:
            if writer in self._writers:
                self._writers.remove(writer)

    async def _handle(self, packet: str, writer, silent: bool = False) -> None:
        self.received.append(packet)
        if silent:
            # stale connection: commands are executed, nothing is sent back
            class _Null:
                def write(self, data):
                    pass

                async def drain(self):
                    pass

            writer = _Null()
        if not packet.startswith("@0"):
            writer.write(self._nack())
            return
        cmd = packet[2:]

        if self.drop_next_ack:
            self.drop_next_ack -= 1
            return
        if self.busy_next:
            self.busy_next = False
            writer.write(b"@0BDERBUSY\r")
            return

        if not self.power and cmd != "PW00":
            return  # off: no response at all

        answer = None
        if cmd == "?PW":
            pass
        elif cmd == "PW00":
            self.power = True
        elif cmd == "PW01":
            self.power = False
        elif cmd == "2353":
            self.transport = "PL"
        elif cmd == "2348":
            self.transport = "PP"
        elif cmd == "2354":
            self.transport = "XX"
        elif cmd == "2332":
            self.chapter += 1
        elif cmd == "2333":
            self.chapter = max(1, self.chapter - 1)
        elif cmd.startswith("Tr") and cmd[2:].isdigit():
            self.chapter = int(cmd[2:])
        elif cmd.startswith("PCGp") and cmd[4:].isdigit():
            self.title = int(cmd[4:])
        elif cmd.startswith("PCSLS") and len(cmd) == 7:
            self.transport = "DVF" + cmd[5]
        elif cmd in ("PCDTRYOP", "PCDTRYCL"):
            self.disc = "TO" if cmd.endswith("OP") else "TC"
        elif cmd in (
            "PCCUSR1", "PCCUSR2", "PCCUSR3", "PCCUSR4", "PCENTR", "PCRTN",
            "DVTP", "DVPU", "PCHM", "PCSU", "DVOP",
        ):
            pass  # menu keys: ACK only
        elif cmd == "?ST":
            answer = f"ST{self.transport}"
        elif cmd == "?CD":
            answer = f"CD{self.disc}"
        elif cmd == "?ET":
            answer = f"ET{self.elapsed}"
        elif cmd == "?RM":
            answer = f"RM{self.remaining}"
        elif cmd == "?Tr":
            answer = f"Tr{self.chapter:04d}"
        elif cmd == "?Tt":
            answer = f"Tt{self.chapters:04d}"
        elif cmd == "?PCGp":
            answer = f"?PCGp{self.title:04d}"  # as written in the guide
        elif cmd == "?PCTG":
            answer = f"?PCTG{self.titles:04d}"  # as written in the guide
        elif cmd == "?PCTYP":
            answer = "PCTYPBDM"
        elif cmd == "?PCAFMT":
            answer = "PCAFMTDLH"
        elif cmd == "?PCACH":
            answer = "BDACH71C"
        elif cmd == "?VN":
            answer = "VN01020304DN-500BD"
        elif cmd == "?MA":
            answer = "MA0005CD123456"
        else:
            writer.write(self._nack())
            return

        writer.write(self._ack())
        if answer is not None:
            if self.text_acks:  # real units: "+" before, no '\r' after
                writer.write(f"+@0{answer}".encode("latin-1"))
            else:
                writer.write(f"@0{answer}\r".encode("latin-1"))
        await writer.drain()
