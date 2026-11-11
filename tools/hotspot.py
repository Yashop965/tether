"""Start the laptop's Windows hosted-network Wi-Fi hotspot for Tether.

Windows' "Mobile hotspot" feature via raw netsh (the UI version needs
Windows 10 1803+ settings plumbing; netsh works headless). Steps:

    1. netsh wlan set hostednetwork set=ssid=<SSID> key=<PSK>
    2. netsh wlan start hostednetwork
    3. poll the virtual adapter's IP (typically 192.168.137.1)

Both netsh calls need an elevated token. This script detects that and, if
not already admin, relaunches *just those two commands* through PowerShell
`Start-Process -Verb RunAs` — a single UAC prompt on the user's screen.

Prints at the end (parse or eyeball):
    SSID=<...> PSK=<...> IP=<192.168.137.1>

Usage:
    python tools/hotspot.py [--ssid Tether-XXXX] [--psk SECRET]
                            [--wait]     # also poll until the IP appears
"""
from __future__ import annotations

import argparse
import ctypes
import ipaddress
import re
import secrets
import socket
import subprocess
import sys
import time


def is_admin() -> bool:
    try:
        return ctypes.windll.kernel32.IsTokenElevated() == 1
    except Exception:
        return False


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return (r.stdout or "") + (r.stderr or "")


def run_netsh_admin(argv: list[str]) -> str:
    """Run `netsh <argv>` elevated (UAC prompt); capture output via temp file."""
    if is_admin():
        return run(["netsh"] + argv)
    import os
    tag = secrets.token_hex(4)
    out = os.path.join(os.environ.get("TEMP", r"C:\Users\yashs\AppData\Local\Temp"), f"tether_netsh_{tag}.txt")
    inner = " ".join(map(str, argv)) + ' > "' + out + '" 2>&1'
    ps = "Start-Process powershell -ArgumentList '-NoProfile','-Command','" + inner + "' -Verb RunAs -Wait"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False, timeout=120)
    time.sleep(1.5)
    if os.path.exists(out):
        with open(out, encoding="utf-8", errors="replace") as f:
            txt = f.read().strip()
        try:
            os.remove(out)
        except OSError:
            pass
        return txt
    return ""


def hosted_status() -> str:
    return run(["netsh", "wlan", "show", "hostednetwork"]).strip()


def hotspot_ip() -> str | None:
    """IP of the hosted-network interface (the phone will see the bridge here)."""
    for line in run(["ipconfig"]).splitlines():
        line = line.strip()
        m = re.match(r"(?i)^IPv4 Address.*:\s*([\d.]+)", line)
        if not m:
            continue
        ip = m.group(1)
        if ipaddress.ip_address(ip) in ipaddress.ip_network("192.168.137.0/24"):
            return ip
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssid", default=None, help="default: Tether-<4 hex>")
    ap.add_argument("--psk", default=None, help="default: 8 random chars")
    ap.add_argument("--wait", action="store_true", help="poll up to 60 s for the hotspot IP")
    args = ap.parse_args()

    ssid = args.ssid or f"Tether-{secrets.token_hex(2).upper()}"
    psk = args.psk or "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))

    print(f"[1/3] status: {hosted_status()[:120]}", flush=True)
    print(f"[2/3] configuring SSID={ssid} PSK=***")
    print(run_netsh_admin(["wlan", "set", "hostednetwork", "set", f"ssid={ssid}", f"key={psk}"]) or "(elevated netsh done)")
    print(f"[3/3] starting hosted network…")
    out = run_netsh_admin(["wlan", "start", "hostednetwork"])
    print(out or "(elevated netsh done)")
    if "not" in out.lower() and "already" in out.lower():
        pass  # already up
    elif "denied" in out.lower() or "administrative" in out.lower():
        print("!! netsh wanted elevation — did you accept the UAC prompt? retry this command.", file=sys.stderr)
        return 2

    if args.wait:
        for _ in range(12):
            ip = hotspot_ip()
            if ip:
                break
            time.sleep(5)
    else:
        ip = hotspot_ip()
    print(f"SSID={ssid} PSK={psk} IP={ip or 'not up yet'}", flush=True)
    print("next:  python tools/pairing_serve.py --ssid " + ssid + " --psk " + psk, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
