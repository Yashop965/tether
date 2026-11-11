"""Phone-simulator: speaks the Tether protocol against a running bridge.

    python tools/ws_probe.py --token T hello
    python tools/ws_probe.py --token T move --dx 0.02 --dy 0.01
    python tools/ws_probe.py --token T click --button left
    python tools/ws_probe.py --token T scroll --scroll -3
    python tools/ws_probe.py --token T tilt --dx 0.4 --dy 0.1
    python tools/ws_probe.py --token T pause | resume
    python tools/ws_probe.py --token T voice --file clip.webm
    python tools/ws_probe.py --token T hello --expect-reject     # A6: bad token refused

Defaults: host 127.0.0.1, port 8377.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import websockets


def parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8377)
    ap.add_argument("--token", required=True)
    ap.add_argument("--cmd", "-c", default="hello",
                    choices=["hello", "move", "click", "scroll", "tilt", "pause", "resume", "state", "voice"])
    ap.add_argument("--dx", type=float, default=0.02)
    ap.add_argument("--dy", type=float, default=0.0)
    ap.add_argument("--button", default="left", choices=["left", "right", "middle"])
    ap.add_argument("--scroll", type=int, default=-3)
    ap.add_argument("--file", default="")
    ap.add_argument("--expect-reject", action="store_true", help="assert a bad token is refused (A6)")
    return ap.parse_args()


async def run(args: argparse.Namespace, token: str) -> int:
    url = f"ws://{args.host}:{args.port}/?token={token}"
    async with websockets.connect(url, max_size=None) as ws:
        hello = await recv_json(ws)
        print("hello_ok:", json.dumps(hello))
        if args.cmd == "hello":
            return 0

        if args.cmd == "move":
            for _ in range(60):
                await ws.send(json.dumps({"t": "pad", "dx": args.dx, "dy": args.dy}))
                await asyncio.sleep(1 / 60)
            print(f"60 pad events sent (dx={args.dx}, dy={args.dy})")
        elif args.cmd == "click":
            await ws.send(json.dumps({"t": "click", "b": args.button}))
            print("click sent:", args.button)
        elif args.cmd == "scroll":
            await ws.send(json.dumps({"t": "scroll", "dy": args.scroll}))
            print("scroll sent:", args.scroll)
        elif args.cmd == "tilt":
            for i in range(30):
                x = args.dx + 0.4 * ((i % 10) - 5) / 5
                await ws.send(json.dumps({"t": "tilt", "x": x, "y": args.dy, "active": i < 25}))
                await asyncio.sleep(1 / 30)
            print("tilt burst sent")
        elif args.cmd in ("pause", "resume"):
            await ws.send(json.dumps({"t": args.cmd}))
            resp = await asyncio.wait_for(recv_json(ws), 3)
            print(resp)
        elif args.cmd == "state":
            await ws.send(json.dumps({"t": "state_req"}))
            print(await asyncio.wait_for(recv_json(ws), 3))
        elif args.cmd == "voice":
            data = Path(args.file).read_bytes()
            chunk = 65536
            for off in range(0, len(data), chunk):
                await ws.send(data[off:off + chunk])
            await ws.send(json.dumps({"t": "audio_done"}))
            print(f"audio sent: {len(data)} bytes, waiting for stt…")
            while True:
                msg = await asyncio.wait_for(recv_json(ws), 60)
                print(msg)
                if msg.get("t") in ("stt", "stt_error"):
                    break
    return 0


async def recv_json(ws):
    raw = await ws.recv()
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode()
    return json.loads(raw or "{}")


def main() -> None:
    args = parse()
    token = args.token if not args.expect_reject else "definitely-wrong"
    try:
        rc = asyncio.run(run(args, token))
    except websockets.exceptions.ConnectionClosed as e:
        if args.expect_reject:
            code = e.rcvd.code if e.rcvd else "?"
            print(f"rejected as expected (close code {code})")
            sys.exit(0)
        print("connection closed:", e)
        sys.exit(3)
    if args.expect_reject:
        print("FAIL: bad token was accepted")
        sys.exit(1)
    sys.exit(rc)


if __name__ == "__main__":
    main()
