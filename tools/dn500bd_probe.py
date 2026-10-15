#!/usr/bin/env python3
"""On-site probe for the Denon DN-500BD MKII (no Home Assistant needed).

Answers the open questions from the protocol guide against a real player
and writes everything to a log file that can be handed back for tuning.

Usage:
    python3 tools/dn500bd_probe.py 192.168.x.y
        -> read-only: all status requests + 10 s listening for notifications

    python3 tools/dn500bd_probe.py 192.168.x.y --actions
        -> additionally tests transport (play/pause/stop, chapter skip,
           scan forward/reverse, slow). CHANGES PLAYBACK – a disc with
           several chapters should be playing, no show running.

    python3 tools/dn500bd_probe.py 192.168.x.y --raw "@0?ST" --raw "@0PCSLSRf"
        -> send individual commands and show what comes back

Only Python 3.11+ standard library is required.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import importlib.util
import logging
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "dn500bd_client", ROOT / "custom_components" / "dn500bd" / "client.py"
)
client_mod = importlib.util.module_from_spec(_spec)
sys.modules["dn500bd_client"] = client_mod
_spec.loader.exec_module(client_mod)

LOG: list[str] = []


def out(line: str = "") -> None:
    print(line)
    LOG.append(line)


async def raw(cli, cmd: str, window: float = 0.5) -> dict:
    res = await cli.raw(cmd, window)
    flag = "ACK " if res["ack"] else ("NACK" if res["nack"] else "----")
    pkts = [p for p in res["received"] if not p.startswith("<")]
    out(f"  {res['sent']:<16} {flag}  {' | '.join(pkts) if pkts else ''}")
    return res


async def status_block(cli) -> None:
    for key, (req, _expect) in client_mod.QUERIES.items():
        await raw(cli, req)


async def et(cli) -> str:
    res = await cli.raw("?ET", 0.4)
    pkts = [p for p in res["received"] if p.startswith("@")]
    return pkts[-1] if pkts else "-"


async def actions(cli, wait: float) -> None:
    out("\n== Transport ==")
    for label, cmd in [
        ("Play", "2353"),
        ("Pause", "2348"),
        ("Play", "2353"),
    ]:
        out(f"{label}:")
        await raw(cli, cmd)
        await asyncio.sleep(wait)
        await raw(cli, "?ST")
        await raw(cli, "?ET")

    out("\n== ET bezieht sich auf Titel oder Kapitel? ==")
    out(f"  vor Kapitelsprung: ET={await et(cli)}")
    await raw(cli, "2332")
    await asyncio.sleep(wait)
    out(f"  nach Next Chapter: ET={await et(cli)}   (nahe 0 => kapitelbezogen)")
    await raw(cli, "?Tr")
    await raw(cli, "2333")
    await asyncio.sleep(wait)
    await raw(cli, "?Tr")

    out("\n== Time Mode (PCTMD) Einfluss auf ET ==")
    await raw(cli, "?PCTMD")
    for mode in ("TL", "EL"):
        await raw(cli, f"PCTMD{mode}")
        await asyncio.sleep(0.5)
        out(f"  Mode {mode}: ET={await et(cli)}")

    out("\n== Scan ==")
    for code in ("PCSLSFf", "PCSLSFs", "PCSLSRf", "PCSLSRs", "PCSLSBf", "PCSLSrf"):
        out(f"{code}:")
        res = await raw(cli, code)
        if res["ack"]:
            await asyncio.sleep(wait)
            await raw(cli, "?ST")
            await raw(cli, "?ET")
            await raw(cli, "?PCSLS")
        await raw(cli, "2353")  # back to play
        await asyncio.sleep(1)

    out("\n== Wiederholtes Scan-Kommando (Geschwindigkeitsstufen?) ==")
    for _ in range(3):
        await raw(cli, "PCSLSFf")
        await asyncio.sleep(1.5)
        out(f"  ET={await et(cli)}")
        await raw(cli, "?ST")
    await raw(cli, "2353")

    out("\n== Stop-Status ==")
    await raw(cli, "2354")
    await asyncio.sleep(wait)
    await raw(cli, "?ST", 1.0)
    await raw(cli, "?ET")
    await raw(cli, "2353")
    await asyncio.sleep(wait)
    await raw(cli, "?ST")


async def listen(cli, seconds: float) -> None:
    out(f"\n== {seconds:.0f} s auf unaufgeforderte Statusmeldungen hören ==")
    out("   (jetzt am Gerät/Fernbedienung Play/Pause/Kapitel drücken)")
    seen: list[str] = []
    cli.on_packet = lambda body, unsol: seen.append(body) if unsol else None
    await asyncio.sleep(seconds)
    cli.on_packet = None
    if seen:
        for body in seen:
            out(f"  NOTIFY @0{body}")
    else:
        out("  keine Meldungen")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=9030)
    ap.add_argument("--actions", action="store_true", help="Transport testen (ändert Wiedergabe!)")
    ap.add_argument("--raw", action="append", default=[], help="einzelnen Befehl senden")
    ap.add_argument("--listen", type=float, default=10.0, help="Sekunden auf Notifications hören")
    ap.add_argument("--wait", type=float, default=2.0, help="Pause nach Aktionen (s)")
    ap.add_argument("--log", default=None, help="Logdatei (Standard: dn500bd_<host>_<zeit>.log)")
    ap.add_argument("--debug", action="store_true", help="Rohverkehr ausgeben")
    args = ap.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(message)s")

    cli = client_mod.DN500BDClient(args.host, args.port, ack_timeout=0.5, answer_timeout=1.0)
    out(f"DN-500BD Probe {dt.datetime.now():%Y-%m-%d %H:%M:%S}  {args.host}:{args.port}")
    try:
        await cli.connect()
    except client_mod.DN500BDError as err:
        out(f"FEHLER: {err}")
        return 1
    out("TCP verbunden.")

    try:
        if args.raw:
            out("\n== Rohbefehle ==")
            for cmd in args.raw:
                await raw(cli, cmd, 1.0)
        else:
            out("\n== Statusabfragen ==")
            await status_block(cli)
            await listen(cli, args.listen)
            if args.actions:
                await actions(cli, args.wait)
                out("\n== Status danach ==")
                await status_block(cli)
    finally:
        await cli.close()

    log = args.log or f"dn500bd_{args.host.replace('.', '-')}_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    pathlib.Path(log).write_text("\n".join(LOG) + "\n", encoding="utf-8")
    print(f"\nLog geschrieben: {log}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
