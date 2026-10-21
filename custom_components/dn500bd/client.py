"""Asyncio client for the Denon DN-500BD MKII serial/IP control protocol.

Protocol (Serial Command Protocol Guide v1.00, IP control on TCP port 9030):

* Packets: ``@`` + ID ``0`` + command + ``\\r``.
* The device answers every packet with ACK (0x06) or NACK (0x15).
  Real units (firmware 19xx and later) answer with the ASCII text ``ack`` /
  ``nack`` instead, and put a ``+`` in front of answer packets
  (``ack+@0STPP\r``). Both forms are accepted.
* A connection that is kept open for a long time can go stale: the unit
  still executes commands but no longer sends anything back. The client
  therefore closes the connection when idle and after every timeout, so each
  poll starts on a fresh connection.
  Status requests are answered with ACK followed by an ANSWER packet.
* Only one command may be outstanding; >= 30 ms between commands.
* The device may send unsolicited status notifications, which the host
  must acknowledge with ACK (otherwise the device repeats them once).

This module has no Home Assistant imports on purpose: it is shared by the
integration, ``tools/dn500bd_probe.py`` and the tests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)

ACK = 0x06
NACK = 0x15
START = 0x40  # '@'
END = 0x0D  # '\r'
DEVICE_ID = "0"
MAX_PACKET = 600

# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------


class DN500BDError(Exception):
    """Base error."""


class DN500BDConnectionError(DN500BDError):
    """TCP connection could not be established or was lost."""


class DN500BDNack(DN500BDError):
    """Device answered with NACK."""


class DN500BDTimeout(DN500BDError):
    """No ACK or no answer within the timeout."""


class DN500BDBusy(DN500BDError):
    """Device reported @0BDERBUSY (command buffer full)."""


# --------------------------------------------------------------------------
# Code tables
# --------------------------------------------------------------------------

TRANSPORT_CODES: dict[str, str] = {
    "PL": "play",
    "PP": "pause",
    "DVSF": "slow_forward",
    "DVSR": "slow_reverse",
    "DVFF": "scan_forward",
    "DVFR": "scan_reverse",
    "DVSP": "step",
    "DVFS": "fs_play",
    "ED": "menu",
    "DVSU": "setup",
    "DVTR": "track_menu",
    "DVHM": "home",
}

# Transport states that mean "picture is moving"
PLAYING_TRANSPORTS = {
    "play",
    "slow_forward",
    "slow_reverse",
    "scan_forward",
    "scan_reverse",
    "fs_play",
}
PAUSED_TRANSPORTS = {"pause", "step"}

DISC_CODES: dict[str, str] = {
    "NC": "no_disc",
    "CI": "disc_in",
    "UF": "unformatted",
    "TO": "tray_open",
    "TC": "tray_closed",
    "TE": "tray_error",
}

MEDIA_TYPES: dict[str, str | None] = {
    "DVV": "DVD-Video",
    "DVA": "DVD-Audio",
    "CDA": "CD-DA",
    "CDR": "CD-ROM",
    "SAC": "SACD",
    "DVR": "DVD-VR",
    "BDM": "BDMV",
    "BDA": "BDAV",
    "AVH": "AVCHD",
    "DLN": "DLNA",
    "EXT": "External Memory",
    "UKN": None,
}

AUDIO_FORMATS: dict[str, str | None] = {
    "DBD": "Dolby Digital",
    "DTS": "DTS",
    "MPG": "MPEG",
    "LPC": "LPCM",
    "PPC": "PPCM",
    "DSD": "DSD",
    "DD+": "Dolby Digital Plus",
    "DTH": "DTS-HD",
    "DLH": "Dolby TrueHD",
    "MP3": "MP3",
    "AAC": "AAC",
    "WMA": "WMA",
    "UKN": None,
}

AUDIO_CHANNELS: dict[str, str | None] = {
    "1CH": "1.0",
    "2CH": "2.0",
    "21C": "2.1",
    "3CH": "3.0",
    "31C": "3.1",
    "4CH": "4.0",
    "41C": "4.1",
    "5CH": "5.0",
    "51C": "5.1",
    "6CH": "6.0",
    "61C": "6.1",
    "7CH": "7.0",
    "71C": "7.1",
    "8CH": "8.0",
    "L/R": "L/R",
    "RCH": "R",
    "LCH": "L",
    "UKN": None,
}

# Status requests: key -> (request without "@0", expected answer prefixes)
# An answer prefix of None means "ACK only" (power status).
QUERIES: dict[str, tuple[str, tuple[str, ...] | None]] = {
    "power": ("?PW", None),
    "transport": ("?ST", ("ST",)),
    "disc": ("?CD", ("CD",)),
    "elapsed": ("?ET", ("ET",)),
    "remaining": ("?RM", ("RM",)),
    "chapter": ("?Tr", ("Tr",)),
    "chapters_total": ("?Tt", ("Tt",)),
    "title": ("?PCGp", ("PCGp",)),
    "titles_total": ("?PCTG", ("PCTG",)),
    "media_type": ("?PCTYP", ("PCTYP",)),
    "audio_format": ("?PCAFMT", ("PCAFMT",)),
    # Doc: request @0?PCACH, answer @0BDACHxxx (accept both spellings)
    "audio_channels": ("?PCACH", ("BDACH", "PCACH")),
    "version": ("?VN", ("VN",)),
    "mac": ("?MA", ("MA",)),
}

# --------------------------------------------------------------------------
# Value parsers
# --------------------------------------------------------------------------


def parse_number(value: str) -> int | None:
    """'0012' -> 12, 'UNKN' / garbage -> None."""
    value = value.strip()
    if not value or not value.isdigit():
        return None
    return int(value)


def parse_time(value: str) -> int | None:
    """'hhhmmss' (also tolerates hhmmss / separators) -> seconds."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 6:
        return None
    seconds = int(digits[-2:])
    minutes = int(digits[-4:-2])
    hours = int(digits[:-4] or "0")
    if seconds > 59 or minutes > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


def format_time(seconds: int | None) -> str | None:
    """Seconds -> 'H:MM:SS'."""
    if seconds is None:
        return None
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def normalize_body(raw: str) -> str:
    """Strip '@', device id and a leading '?' from a received packet.

    '@0Tr0003'   -> 'Tr0003'
    '@0?PCTG0004' -> 'PCTG0004'  (doc lists some answers with '?')
    """
    body = raw
    if body.startswith("@"):
        body = body[1:]
    if body.startswith(DEVICE_ID):
        body = body[1:]
    if body.startswith("?"):
        body = body[1:]
    return body.rstrip("\r")


def build_packet(command: str) -> bytes:
    """'02353' or '@02353' or '@02353\\r' -> b'@02353\\r'."""
    cmd = command.strip("\r\n")
    if not cmd.startswith("@"):
        cmd = "@" + cmd
    if len(cmd) < 2 or cmd[1] != DEVICE_ID:
        cmd = "@" + DEVICE_ID + cmd[1:]
    data = (cmd + "\r").encode("latin-1")
    if len(data) > MAX_PACKET:
        raise ValueError("packet too long")
    return data


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


@dataclass
class DN500BDState:
    """Last known device state."""

    power: bool | None = None
    transport: str | None = None
    transport_raw: str | None = None
    disc: str | None = None
    elapsed: int | None = None
    remaining: int | None = None
    elapsed_at: float | None = None  # time.time() of last elapsed update
    chapter: int | None = None
    chapters_total: int | None = None
    title: int | None = None
    titles_total: int | None = None
    media_type: str | None = None
    audio_format: str | None = None
    audio_channels: str | None = None
    firmware: str | None = None
    model: str | None = None
    mac: str | None = None
    unknown: dict[str, str] = field(default_factory=dict)

    def copy(self) -> DN500BDState:
        """Shallow copy (unknown dict copied)."""
        new = DN500BDState(**{k: v for k, v in self.__dict__.items() if k != "unknown"})
        new.unknown = dict(self.unknown)
        return new

    @property
    def duration(self) -> int | None:
        """Total length derived from elapsed + remaining."""
        if self.elapsed is None or self.remaining is None:
            return None
        return self.elapsed + self.remaining

    def clear_media(self) -> None:
        """Forget everything that belongs to a disc."""
        self.transport = None
        self.transport_raw = None
        self.elapsed = None
        self.remaining = None
        self.chapter = None
        self.chapters_total = None
        self.title = None
        self.titles_total = None
        self.media_type = None
        self.audio_format = None
        self.audio_channels = None


def apply_packet(state: DN500BDState, body: str) -> str | None:
    """Apply a normalized packet body to state. Returns the state key or None."""
    # Longest prefixes first (e.g. PCTYP before PCT..., BDACH before BD...)
    if body.startswith("BDERBUSY"):
        return "busy"
    if body.startswith("PCTYP"):
        code = body[5:].strip()
        state.media_type = MEDIA_TYPES.get(code, code or None)
        return "media_type"
    if body.startswith("PCAFMT"):
        code = body[6:].strip()
        state.audio_format = AUDIO_FORMATS.get(code, code or None)
        return "audio_format"
    if body.startswith(("BDACH", "PCACH")):
        code = body[5:].strip()
        state.audio_channels = AUDIO_CHANNELS.get(code, code or None)
        return "audio_channels"
    if body.startswith("PCTG"):
        state.titles_total = parse_number(body[4:])
        return "titles_total"
    if body.startswith("PCGp"):
        state.title = parse_number(body[4:])
        return "title"
    if body.startswith("Tt"):
        state.chapters_total = parse_number(body[2:])
        return "chapters_total"
    if body.startswith("Tr"):
        state.chapter = parse_number(body[2:])
        return "chapter"
    if body.startswith("ET"):
        state.elapsed = parse_time(body[2:])
        state.elapsed_at = time.time()
        return "elapsed"
    if body.startswith("RM"):
        state.remaining = parse_time(body[2:])
        return "remaining"
    if body.startswith("ST"):
        code = body[2:].strip()
        state.transport_raw = code
        state.transport = TRANSPORT_CODES.get(code, code.lower() or None)
        return "transport"
    if body.startswith("CD"):
        code = body[2:].strip()
        state.disc = DISC_CODES.get(code, code.lower() or None)
        if state.disc in ("no_disc", "tray_open"):
            state.clear_media()
        return "disc"
    if body.startswith("VN"):
        value = body[2:]
        state.firmware = value[:8].strip() or None
        state.model = value[8:].strip() or None
        return "version"
    if body.startswith("MA"):
        value = body[2:].strip().lower()
        if len(value) == 12:
            value = ":".join(value[i : i + 2] for i in range(0, 12, 2))
        state.mac = value or None
        return "mac"
    if body.startswith("PW"):
        # Not documented as an answer, but be tolerant
        state.power = body[2:4] == "00"
        return "power"
    state.unknown[body[:6]] = body
    return None


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class DN500BDClient:
    """TCP client for a DN-500BD MKII (short-lived connections by default)."""

    def __init__(
        self,
        host: str,
        port: int = 9030,
        *,
        ack_timeout: float = 0.3,
        answer_timeout: float = 0.5,
        command_gap: float = 0.04,
        connect_timeout: float = 5.0,
        ack_notifications: bool = True,
        reverse_code: str = "R",
        on_packet: Callable[[str, bool], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        idle_close: float | None = 1.0,
    ) -> None:
        """Init. on_packet(body, unsolicited) is called for every packet."""
        self.host = host
        self.port = port
        self.ack_timeout = ack_timeout
        self.answer_timeout = answer_timeout
        self.command_gap = command_gap
        self.connect_timeout = connect_timeout
        self.ack_notifications = ack_notifications
        self.reverse_code = reverse_code or "R"
        self.on_packet = on_packet
        self.on_disconnect = on_disconnect
        # Close the connection after this many idle seconds (None = keep open)
        self.idle_close = idle_close
        self._idle_handle: asyncio.TimerHandle | None = None

        self.state = DN500BDState()

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._last_tx = 0.0

        # pending transaction
        self._ack_fut: asyncio.Future[bool] | None = None
        self._answer_fut: asyncio.Future[str] | None = None
        self._expect: tuple[str, ...] | None = None
        self._capture: list[str] | None = None  # for raw()

    # ------------------------------------------------------------ connection

    @property
    def connected(self) -> bool:
        """True if the TCP connection is up."""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Open the TCP connection (no-op if already connected)."""
        if self.connected:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.connect_timeout
            )
        except (TimeoutError, OSError) as err:
            self._reader = self._writer = None
            raise DN500BDConnectionError(
                f"cannot connect to {self.host}:{self.port}: {err}"
            ) from err
        _LOGGER.debug("Connected to %s:%s", self.host, self.port)
        self._reader_task = asyncio.get_running_loop().create_task(
            self._reader_loop(), name=f"dn500bd-reader-{self.host}"
        )

    async def close(self) -> None:
        """Close the connection."""
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._drop_connection(notify=False)

    def _drop_connection(self, notify: bool = True) -> None:
        writer, self._writer = self._writer, None
        self._reader = None
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
        err = DN500BDConnectionError("connection lost")
        for fut in (self._ack_fut, self._answer_fut):
            if fut is not None and not fut.done():
                fut.set_exception(err)
        if notify and self.on_disconnect is not None:
            self.on_disconnect()

    async def _reader_loop(self) -> None:
        assert self._reader is not None
        reader = self._reader
        buf = bytearray()
        in_packet = False
        text = ""  # ASCII outside packets ("ack" / "nack" from real units)
        try:
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    _LOGGER.debug("Connection closed by %s", self.host)
                    break
                for byte in chunk:
                    if in_packet:
                        if byte == END:
                            self._handle_packet(bytes(buf).decode("latin-1"))
                            buf.clear()
                            in_packet = False
                        elif len(buf) < MAX_PACKET:
                            buf.append(byte)
                        continue
                    if byte == START:
                        in_packet = True
                        text = ""
                        buf.clear()
                        buf.append(byte)
                    elif 0x41 <= byte <= 0x5A or 0x61 <= byte <= 0x7A:
                        text = (text + chr(byte).lower())[-4:]
                        if text.endswith("nack"):
                            _LOGGER.debug("RX nack (text)")
                            text = ""
                            self._resolve_ack(False)
                        elif text.endswith("ack"):
                            _LOGGER.debug("RX ack (text)")
                            text = ""
                            self._resolve_ack(True)
                    elif byte in (0x2B, 0x0D, 0x0A, 0x20):
                        text = ""  # '+' before answers, line ends
                    elif byte == ACK:
                        _LOGGER.debug("RX ACK")
                        self._resolve_ack(True)
                    elif byte == NACK:
                        _LOGGER.debug("RX NACK")
                        self._resolve_ack(False)
                    else:
                        _LOGGER.debug("RX stray byte 0x%02x", byte)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Reader error on %s: %s", self.host, err)
        if self._reader is reader:
            self._drop_connection()

    def _resolve_ack(self, ok: bool) -> None:
        if self._capture is not None:
            self._capture.append("<ACK>" if ok else "<NACK>")
        fut = self._ack_fut
        if fut is not None and not fut.done():
            fut.set_result(ok)

    def _handle_packet(self, raw: str) -> None:
        _LOGGER.debug("RX %r", raw)
        body = normalize_body(raw)
        if self._capture is not None:
            self._capture.append(raw)

        solicited = False
        if body.startswith("BDERBUSY"):
            for fut in (self._ack_fut, self._answer_fut):
                if fut is not None and not fut.done():
                    fut.set_exception(DN500BDBusy("device busy"))
                    solicited = True
        elif (
            self._expect is not None
            and self._answer_fut is not None
            and not self._answer_fut.done()
            and body.startswith(self._expect)
        ):
            self._answer_fut.set_result(body)
            solicited = True

        if not solicited and self.ack_notifications and self._capture is None:
            # Unsolicited status notification -> host must ACK
            self._write(bytes([ACK]))

        apply_packet(self.state, body)
        if self.on_packet is not None:
            try:
                self.on_packet(body, not solicited)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("on_packet callback failed")

    def _write(self, data: bytes) -> None:
        if not self.connected:
            raise DN500BDConnectionError("not connected")
        assert self._writer is not None
        _LOGGER.debug("TX %r", data)
        self._writer.write(data)

    # ----------------------------------------------------------- transaction

    async def _transact(
        self,
        command: str,
        expect: tuple[str, ...] | None,
        *,
        retries: int,
    ) -> str | None:
        """Send one packet, wait for ACK (and answer if expect is set).

        retries: how often the packet is re-sent when no ACK arrives.
        Control commands use 0 so nothing is ever executed twice.
        """
        packet = build_packet(command)
        async with self._lock:
            await self.connect()
            loop = asyncio.get_running_loop()
            try:
                for attempt in range(retries + 1):
                    wait = self.command_gap - (time.monotonic() - self._last_tx)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self._ack_fut = loop.create_future()
                    self._answer_fut = loop.create_future() if expect else None
                    self._expect = expect
                    self._write(packet)
                    assert self._writer is not None
                    await self._writer.drain()
                    self._last_tx = time.monotonic()
                    try:
                        ok = await asyncio.wait_for(
                            asyncio.shield(self._ack_fut), self.ack_timeout
                        )
                    except TimeoutError:
                        if self._answer_fut is not None and self._answer_fut.done():
                            # ACK lost but answer arrived -> fine
                            return self._answer_fut.result()
                        if attempt < retries:
                            _LOGGER.debug("No ACK for %r, retry %d", packet, attempt + 1)
                            continue
                        # Protocol: after 3 failed attempts send '\r'
                        self._write(b"\r")
                        # A unit that stops answering on an open connection
                        # usually answers again on a fresh one.
                        self._drop_connection(notify=False)
                        raise DN500BDTimeout(f"no ACK for {command!r}") from None
                    if not ok:
                        raise DN500BDNack(f"NACK for {command!r}")
                    if self._answer_fut is None:
                        return None
                    try:
                        return await asyncio.wait_for(
                            asyncio.shield(self._answer_fut), self.answer_timeout
                        )
                    except TimeoutError:
                        self._drop_connection(notify=False)
                        raise DN500BDTimeout(f"no answer for {command!r}") from None
                return None
            finally:
                for fut in (self._ack_fut, self._answer_fut):
                    if fut is not None and not fut.done():
                        fut.cancel()
                    elif fut is not None and not fut.cancelled():
                        fut.exception()  # mark retrieved
                self._ack_fut = None
                self._answer_fut = None
                self._expect = None
                self._last_tx = time.monotonic()
                self._schedule_idle_close()

    def _schedule_idle_close(self) -> None:
        """Close the connection once nothing has been sent for idle_close s."""
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None
        if self.idle_close is None or not self.connected:
            return
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(
            self.idle_close, lambda: loop.create_task(self._close_if_idle())
        )

    async def _close_if_idle(self) -> None:
        self._idle_handle = None
        if self._lock.locked():
            return  # a transaction is running; it reschedules when done
        if time.monotonic() - self._last_tx < (self.idle_close or 0) - 0.01:
            return
        _LOGGER.debug("Closing idle connection to %s", self.host)
        await self.close()

    async def command(self, command: str) -> None:
        """Send a control command (never retried)."""
        await self._transact(command, None, retries=0)

    async def query(self, key: str) -> str | None:
        """Run a status request from QUERIES; updates self.state."""
        request, expect = QUERIES[key]
        return await self._transact(request, expect, retries=2)

    async def raw(self, command: str, window: float = 0.6) -> dict:
        """Send anything and record everything that comes back (for testing)."""
        packet = build_packet(command)
        async with self._lock:
            await self.connect()
            wait = self.command_gap - (time.monotonic() - self._last_tx)
            if wait > 0:
                await asyncio.sleep(wait)
            self._capture = []
            try:
                self._write(packet)
                assert self._writer is not None
                await self._writer.drain()
                await asyncio.sleep(window)
                received = list(self._capture)
            finally:
                self._capture = None
                self._last_tx = time.monotonic()
                self._schedule_idle_close()
        return {
            "sent": packet.decode("latin-1").rstrip("\r"),
            "ack": "<ACK>" in received,
            "nack": "<NACK>" in received,
            "received": received,
        }

    # -------------------------------------------------------------- queries

    async def query_power(self) -> bool:
        """Power status: ACK = on, no response = off."""
        try:
            await self._transact(QUERIES["power"][0], None, retries=1)
        except DN500BDTimeout:
            self.state.power = False
            return False
        except DN500BDNack:
            # Device answered, so it is alive
            self.state.power = True
            return True
        self.state.power = True
        return True

    async def poll(self, keys: list[str]) -> dict[str, Exception]:
        """Query several keys. Failing keys are reset to None and returned."""
        errors: dict[str, Exception] = {}
        for key in keys:
            try:
                await self.query(key)
            except (DN500BDTimeout, DN500BDNack, DN500BDBusy) as err:
                errors[key] = err
                _LOGGER.debug("Query %s failed: %s", key, err)
                if key == "transport":
                    self.state.transport = None
                    self.state.transport_raw = None
                elif hasattr(self.state, key) and key not in ("version", "mac"):
                    setattr(self.state, key, None)
        return errors

    # ------------------------------------------------------------- controls

    async def power_on(self) -> None:
        """Power on."""
        await self.command("PW00")

    async def power_off(self) -> None:
        """Power off."""
        await self.command("PW01")

    async def play(self) -> None:
        """Play."""
        await self.command("2353")

    async def pause(self) -> None:
        """Pause."""
        await self.command("2348")

    async def stop(self) -> None:
        """Stop."""
        await self.command("2354")

    async def next_chapter(self) -> None:
        """Track/chapter skip forward."""
        await self.command("2332")

    async def previous_chapter(self) -> None:
        """Track/chapter skip reverse."""
        await self.command("2333")

    async def goto_chapter(self, chapter: int) -> None:
        """Jump to track/chapter 1-2000."""
        if not 1 <= chapter <= 2000:
            raise ValueError("chapter must be 1-2000")
        await self.command(f"Tr{chapter:04d}")

    async def goto_title(self, title: int) -> None:
        """Jump to group/title 1-2000."""
        if not 1 <= title <= 2000:
            raise ValueError("title must be 1-2000")
        await self.command(f"PCGp{title:04d}")

    async def next_title(self) -> None:
        """Group/title skip forward."""
        await self.command("PCGPNX")

    async def previous_title(self) -> None:
        """Group/title skip reverse."""
        await self.command("PCGPPV")

    async def scan(self, direction: str, speed: str = "fast") -> None:
        """Slow/search. direction 'forward'|'reverse', speed 'fast'|'slow'.

        The guide only documents 'F' as direction; reverse uses
        self.reverse_code (default 'R', taken from the @0STDVFR status).
        """
        dir_code = "F" if direction == "forward" else self.reverse_code
        speed_code = "f" if speed == "fast" else "s"
        await self.command(f"PCSLS{dir_code}{speed_code}")

    async def tray(self, open_tray: bool) -> None:
        """Open/close disc tray."""
        await self.command("PCDTRYOP" if open_tray else "PCDTRYCL")
