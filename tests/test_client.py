"""Tests for the protocol client against the fake device.

Run:  python tests/test_client.py      (or: python -m pytest tests)
No Home Assistant needed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

_spec = importlib.util.spec_from_file_location(
    "dn500bd_client", ROOT / "custom_components" / "dn500bd" / "client.py"
)
client_mod = importlib.util.module_from_spec(_spec)
sys.modules["dn500bd_client"] = client_mod
_spec.loader.exec_module(client_mod)

from fake_device import FakeDevice  # noqa: E402

DN500BDClient = client_mod.DN500BDClient


def run(coro):
    return asyncio.run(coro)


async def _setup(**kwargs):
    dev = FakeDevice()
    await dev.start()
    cli = DN500BDClient(
        "127.0.0.1", dev.port, ack_timeout=0.1, answer_timeout=0.2, **kwargs
    )
    await cli.connect()
    return dev, cli


# ---------------------------------------------------------------- parsers


def test_parsers():
    assert client_mod.parse_time("0012345") == 5025
    assert client_mod.parse_time("012345") == 5025
    assert client_mod.parse_time("UNKN") is None
    assert client_mod.parse_number("0007") == 7
    assert client_mod.parse_number("UNKN") is None
    assert client_mod.format_time(5025) == "1:23:45"
    assert client_mod.normalize_body("@0?PCTG0004") == "PCTG0004"
    assert client_mod.normalize_body("@0Tr0003") == "Tr0003"
    assert client_mod.build_packet("2353") == b"@02353\r"
    assert client_mod.build_packet("@02353") == b"@02353\r"
    assert client_mod.build_packet("@02353\r") == b"@02353\r"


def test_apply_packet():
    st = client_mod.DN500BDState()
    client_mod.apply_packet(st, "STDVFR")
    assert st.transport == "scan_reverse"
    client_mod.apply_packet(st, "STPL")
    assert st.transport == "play"
    client_mod.apply_packet(st, "BDACH51C")
    assert st.audio_channels == "5.1"
    client_mod.apply_packet(st, "VN01020304DN-500BD")
    assert st.firmware == "01020304" and st.model == "DN-500BD"
    client_mod.apply_packet(st, "MA0005CD123456")
    assert st.mac == "00:05:cd:12:34:56"
    client_mod.apply_packet(st, "Tr0005")
    client_mod.apply_packet(st, "CDTO")
    assert st.disc == "tray_open" and st.chapter is None


# ----------------------------------------------------------- transactions


def test_full_poll():
    async def go():
        dev, cli = await _setup()
        try:
            assert await cli.query_power() is True
            errors = await cli.poll(
                [
                    "transport", "disc", "elapsed", "remaining", "chapter",
                    "chapters_total", "title", "titles_total", "media_type",
                    "audio_format", "audio_channels", "version", "mac",
                ]
            )
            assert errors == {}
            s = cli.state
            assert s.transport == "play"
            assert s.disc == "disc_in"
            assert s.elapsed == 5025 and s.remaining == 615 and s.duration == 5640
            assert (s.chapter, s.chapters_total) == (3, 12)
            assert (s.title, s.titles_total) == (1, 4)
            assert s.media_type == "BDMV"
            assert s.audio_format == "Dolby TrueHD"
            assert s.audio_channels == "7.1"
            assert s.mac == "00:05:cd:12:34:56"
            # solicited answers must NOT be ACKed by the host
            assert dev.acks_from_host == 0
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_controls():
    async def go():
        dev, cli = await _setup()
        try:
            await cli.pause()
            await cli.query("transport")
            assert cli.state.transport == "pause"
            await cli.next_chapter()
            await cli.goto_chapter(9)
            await cli.query("chapter")
            assert cli.state.chapter == 9
            await cli.goto_title(2)
            await cli.query("title")
            assert cli.state.title == 2
            await cli.scan("reverse", "fast")
            assert dev.received[-1] == "@0PCSLSRf"
            await cli.scan("forward", "slow")
            assert dev.received[-1] == "@0PCSLSFs"
            await cli.tray(True)
            assert dev.received[-1] == "@0PCDTRYOP"
            for key, code in (
                ("up", "PCCUSR3"),
                ("down", "PCCUSR4"),
                ("left", "PCCUSR1"),
                ("right", "PCCUSR2"),
                ("enter", "PCENTR"),
                ("return", "PCRTN"),
                ("top_menu", "DVTP"),
                ("popup_menu", "DVPU"),
            ):
                await cli.send_key(key)
                assert dev.received[-1] == "@0" + code
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_nack():
    async def go():
        dev, cli = await _setup()
        try:
            try:
                await cli.command("FOOBAR")
            except client_mod.DN500BDNack:
                pass
            else:
                raise AssertionError("expected NACK")
            # connection still usable
            await cli.query("chapter")
            assert cli.state.chapter == 3
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_control_not_retried():
    async def go():
        dev, cli = await _setup()
        try:
            dev.drop_next_ack = 5
            try:
                await cli.next_chapter()
            except client_mod.DN500BDTimeout:
                pass
            else:
                raise AssertionError("expected timeout")
            # sent exactly once, then '\r'
            assert dev.received.count("@02332") == 1
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_query_retry():
    async def go():
        dev, cli = await _setup()
        try:
            dev.drop_next_ack = 1
            await cli.query("chapter")
            assert cli.state.chapter == 3
            assert dev.received.count("@0?Tr") == 2
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_busy():
    async def go():
        dev, cli = await _setup()
        try:
            dev.busy_next = True
            try:
                await cli.play()
            except client_mod.DN500BDBusy:
                pass
            else:
                raise AssertionError("expected busy")
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_power_off():
    async def go():
        dev, cli = await _setup()
        try:
            await cli.power_off()
            assert await cli.query_power() is False
            await cli.power_on()
            assert await cli.query_power() is True
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_notification_is_acked():
    async def go():
        seen = []
        dev, cli = await _setup(on_packet=lambda body, unsol: seen.append((body, unsol)))
        try:
            await dev.notify("STPP")
            await asyncio.sleep(0.1)
            assert cli.state.transport == "pause"
            assert seen[-1] == ("STPP", True)
            assert dev.acks_from_host == 1
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_raw():
    async def go():
        dev, cli = await _setup()
        try:
            res = await cli.raw("?Tr", window=0.1)
            assert res["ack"] and not res["nack"]
            assert "@0Tr0003" in res["received"]
            res = await cli.raw("@0XYZ", window=0.1)
            assert res["nack"]
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_reconnect_after_server_drop():
    async def go():
        dev, cli = await _setup()
        port = dev.port
        await dev.stop()
        await asyncio.sleep(0.05)
        assert not cli.connected
        dev2 = FakeDevice()
        dev2._server = await asyncio.start_server(dev2._client, "127.0.0.1", port)
        try:
            await cli.query("chapter")
            assert cli.state.chapter == 3
        finally:
            await cli.close()
            await dev2.stop()

    run(go())


def test_text_acks():
    """Real units answer 'ack'/'nack' as text and prefix answers with '+'."""
    async def go():
        dev, cli = await _setup()
        dev.text_acks = True
        try:
            assert await cli.query_power() is True
            await cli.query("transport")
            assert cli.state.transport_raw == "PL"
            await cli.pause()
            await cli.query("transport")
            assert cli.state.transport_raw == "PP"
            await cli.query("chapter")
            assert cli.state.chapter == 3
            try:
                await cli.command("ZZZZ")
            except client_mod.DN500BDNack:
                pass
            else:
                raise AssertionError("expected NACK")
            res = await cli.raw("?ST", window=0.1)
            assert res["ack"] and not res["nack"], res
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_bare_time_answers():
    """Real units answer ?ET/?RM with bare digits after 'ack'."""
    async def go():
        dev, cli = await _setup()
        dev.text_acks = True
        dev.bare_time = True
        try:
            await cli.query("elapsed")
            await cli.query("remaining")
            assert cli.state.elapsed == dev_seconds(dev.elapsed), cli.state
            assert cli.state.remaining == dev_seconds(dev.remaining), cli.state
            assert cli.state.duration == cli.state.elapsed + cli.state.remaining
            # other answers still work afterwards
            await cli.query("transport")
            assert cli.state.transport_raw == "PL"
            res = await cli.raw("?ET", window=0.1)
            assert res["ack"] and dev.elapsed in res["received"], res
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def dev_seconds(value: str) -> int:
    return client_mod.parse_time(value)


def test_idle_close():
    """The connection is closed when idle and reopened for the next request."""
    async def go():
        dev = FakeDevice()
        await dev.start()
        cli = DN500BDClient(
            "127.0.0.1", dev.port, ack_timeout=0.1, answer_timeout=0.2, idle_close=0.1
        )
        try:
            await cli.query("transport")
            assert cli.connected
            await asyncio.sleep(0.25)
            assert not cli.connected
            await cli.query("chapter")
            assert cli.state.chapter == 3 and dev.connections == 2
        finally:
            await cli.close()
            await dev.stop()

    run(go())


def test_stale_connection_recovers():
    """A connection that went silent is dropped; the next poll answers again."""
    async def go():
        dev = FakeDevice()
        dev.text_acks = True
        dev.stale_first_connection = True
        await dev.start()
        cli = DN500BDClient(
            "127.0.0.1", dev.port, ack_timeout=0.1, answer_timeout=0.2, idle_close=None
        )
        try:
            # executed but silent -> looks off, connection is dropped
            assert await cli.query_power() is False
            assert not cli.connected
            assert await cli.query_power() is True
            assert dev.connections == 2
        finally:
            await cli.close()
            await dev.stop()

    run(go())


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"{len(tests)} tests passed")
