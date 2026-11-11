"""UDP fast-channel probe for the Tether bridge (pairs with ws_probe.py).

    python tools/udp_probe.py --host 127.0.0.1 --token t-test-1 [--cmd ...]

Commands:
    auth      handshake only
    pad       60 pad-delta datagrams (~1 second of cursor travel)
    aim       30 aim datagrams to screen center
    click     one left click
    scroll    one scroll step
    rtt       10 RTT probes, prints min/avg
"""
from __future__ import annotations

import argparse
import socket
import struct
import time


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8378, help="UDP port (bridge port + 1)")
    ap.add_argument("--token", required=True)
    ap.add_argument("--cmd", choices=["auth", "pad", "aim", "click", "scroll", "rtt"], default="auth")
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3.0)
    dest = (args.host, args.port)

    # 1. handshake
    s.sendto(b"TTHR1" + args.token.encode(), dest)
    print(f"handshake sent to {dest}")

    if args.cmd == "auth":
        return

    # 2. input packets
    if args.cmd == "pad":
        for i in range(60):
            s.sendto(struct.pack("<Bff", 0x01, 0.02, 0.01), dest)
            time.sleep(0.016)
        print("60 pad datagrams sent")
    elif args.cmd == "aim":
        for _ in range(30):
            s.sendto(struct.pack("<Bff", 0x02, 0.0, 0.0), dest)
            time.sleep(0.016)
        print("30 aim datagrams sent")
    elif args.cmd == "click":
        s.sendto(b"\x03\x00", dest)
        print("click sent (left)")
    elif args.cmd == "scroll":
        s.sendto(struct.pack("<Bh", 0x04, -3), dest)
        print("scroll sent")
    elif args.cmd == "rtt":
        times = []
        for n in range(10):
            t0 = time.perf_counter()
            s.sendto(struct.pack("<Bi", 0x05, n), dest)
            try:
                data, _ = s.recvfrom(64)
                rtt = (time.perf_counter() - t0) * 1000.0
                if data.startswith(b"\x05"):
                    times.append(rtt)
            except socket.timeout:
                pass
            time.sleep(0.05)
        if times:
            print(f"RTT over {args.host}:{args.port}: min {min(times):.1f} ms, avg {sum(times) / len(times):.1f} ms ({len(times)}/10)")
        else:
            print("no RTT echoes received (UDP blocked?)")


if __name__ == "__main__":
    main()
