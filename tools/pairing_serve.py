"""Generate the pairing page for Tether QR-pairing.

Writes into tools/apkserve/ (served on port 8400 next to the update files):
    pairing.html  — black/white page with one QR per reachable host
    qr_<label>.png— the QR images themselves

QR payload: tether://pair?h=<host>&p=<port>&t=<token>
The Tether app handles the tether:// scheme: scan (in-app) or tap a shared
link -> fields fill -> auto-connect. No typing on the phone.

Usage:
    python tools/pairing_serve.py [--token TOKEN] [--port 8377]
Hosts are auto-detected: Tailscale IP (if the tailnet is up) + first LAN IP.
"""
from __future__ import annotations

import argparse
import html
import socket
import urllib.request
from pathlib import Path

import qrcode
import qrcode.image.pil

REPO = Path(__file__).parent.parent
OUT = REPO / "tools" / "apkserve"


def tailscale_ip() -> str | None:
    try:
        import subprocess
        out = subprocess.run(
            ["tailscale", "ip"], capture_output=True, text=True, timeout=8
        ).stdout.split()
        for line in out:  # first line = v4 if present; skip v6
            if line.count(".") == 3:
                return line
        return None
    except Exception:
        return None


def lan_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=None, help="bridge token (default: read from bridge config or env TETHER_TOKEN)")
    ap.add_argument("--port", default="8377")
    args = ap.parse_args()

    import os
    token = args.token or os.environ.get("TETHER_TOKEN") or "785b68b042cb4842"

    hosts = [h for h in [tailscale_ip(), lan_ip()] if h]
    if not hosts:
        print("no candidate host found", flush=True)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    cards = []
    for i, host in enumerate(hosts):
        label = "TAILNET" if i == 0 and host.startswith("100.") else "LAN"
        payload = f"tether://pair?h={host}&p={args.port}&t={token}"
        qr = qrcode.QRCode(box_size=16, border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#000000", back_color="#ffffff").convert("L")
        img.save(OUT / f"qr_{label}.png")
        cards.append((label, host, payload))

    html_out = f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tether pairing</title>
<style>
 body {{ background:#0a0a0a; color:#fff; font-family:ui-monospace,Consolas,monospace;
        display:flex; flex-direction:column; align-items:center; min-height:100vh; padding:48px 16px; }}
 h1 {{ font-size:15px; letter-spacing:6px; font-weight:600; margin:0 0 6px; }}
 .sub {{ color:#666; font-size:11px; letter-spacing:2px; margin-bottom:36px; }}
 .card {{ background:#111; border:1px solid #2a2a2a; border-radius:14px; padding:22px 26px;
         display:flex; flex-direction:column; align-items:center; gap:12px; }}
 .card img {{ width:230px; height:230px; background:#fff; padding:10px; border-radius:8px; }}
 .lbl {{ color:#888; font-size:10px; letter-spacing:3px; }}
 .host {{ font-size:13px; }}
 .url {{ color:#555; font-size:10px; word-break:break-all; max-width:300px; text-align:center; }}
 .row {{ display:flex; gap:22px; flex-wrap:wrap; justify-content:center; }}
</style></head>
<body>
<h1>TETHER</h1>
<div class="sub">SCAN WITH THE PHONE'S TETHER APP &middot; SCAN QR</div>
<div class="row">
{chr(10).join(f'''<div class="card">
 <div class="lbl">{label}</div>
 <img src="qr_{label}.png" alt="{label}">
 <div class="host">{html.escape(host)}</div>
 <div class="url">{html.escape(payload)}</div>
 <div class="lbl">tap the link on the phone, or SCAN QR</div>
</div>''' for label, host, payload in cards)}
</div>
</body></html>
"""
    (OUT / "pairing.html").write_text(html_out)
    print(f"pairing page -> {OUT / 'pairing.html'}  (hosts: {', '.join(h for h, _, _ in cards)})")
    print(f"open on the laptop:  http://<laptop>:8400/pairing.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
