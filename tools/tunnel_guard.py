"""Keep the two Cloudflare quick tunnels alive for Tether and publish their URLs.

Spawns cloudflared for the bridge (8377) and the update server (8400), parses
the ephemeral trycloudflare.com URLs from cloudflared's logs, writes
tools/tunnels.json, and regenerates the pairing page so the REMOTE QR always
carries the live URLs. Restarts either tunnel when it dies and rewrites
tunnels.json + pairing.html on every (re)register.

Usage:
    python tools/tunnel_guard.py            # run in the foreground (or under the
                                             # same pattern as bridge_guard)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
CFD = Path(os.environ.get("CFD_PATH", r"C:\Users\yashs\tools\cloudflared.exe"))
LOGDIR = REPO / "tools"
STATE = LOGDIR / "tunnels.json"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

TARGETS = {
    "bridge": ("http://127.0.0.1:8377", LOGDIR / "cloudflared_bridge.log"),
    "upd": ("http://127.0.0.1:8400", LOGDIR / "cloudflared_upd.log"),
}


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOGDIR / "tunnel_guard.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def kill_existing() -> None:
    subprocess.run(
        ['powershell', '-NoProfile', '-Command',
         "(Get-CimInstance Win32_Process -Filter \"name='cloudflared.exe'\") | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True, timeout=30,
    )
    time.sleep(2)


def wait_url(path: Path, prev: str | None, timeout: int = 90) -> str | None:
    """Tail cloudflared log until a (new) quick-tunnel URL appears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        m = None
        try:
            txt = path.read_text(encoding="utf-8", errors="replace")
            for m in reversed(list(URL_RE.finditer(txt))):
                pass
        except FileNotFoundError:
            m = None
        if m and m.group(0) != prev:
            return m.group(0)
        time.sleep(3)
    return None


def spawn(key: str, url: str | None) -> str | None:
    """(Re)start cloudflared for TARGETS[key]; return the new quick-tunnel URL."""
    origin, logpath = TARGETS[key]
    logpath.write_text("", encoding="utf-8")  # fresh log so we parse the new URL
    subprocess.Popen(
        [str(CFD), "tunnel", "--url", origin, "--no-autoupdate"],
        stdout=open(logpath, "a", encoding="utf-8", errors="replace"),
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    log(f"spawned {key} -> {origin}")
    return wait_url(logpath, url)


def write_state(urls: dict) -> None:
    STATE.write_text(json.dumps(urls, indent=2), encoding="utf-8")
    log(f"state: {urls}")


def regen_pairing(urls: dict) -> None:
    """Regenerate the pairing page with live cloud URLs (best effort)."""
    wss = urls.get("bridge")
    upd = urls.get("upd")
    if not wss and not upd:
        return
    args = [sys.executable, str(REPO / "tools" / "pairing_serve.py")]
    if wss:
        args += ["--wss", f"wss://{wss.split('https://', 1)[1]}"]
    if upd:
        args += ["--upd", upd]
    try:
        subprocess.run(args, timeout=60, capture_output=True, text=True)
        log("pairing page regenerated with cloud URLs")
    except Exception as e:  # noqa: BLE001
        log(f"pairing regen failed: {e}")


def alive(origin: str) -> bool:
    """True if a cloudflared process exists whose command line serves [origin]."""
    pat = re.escape(origin)
    ps = (
        "(Get-CimInstance Win32_Process -Filter \"name='cloudflared.exe'\") | "
        "ForEach-Object { $_.CommandLine } | Where-Object { $_ -and $_ -match '"
        + pat
        + "' } | Measure-Object | Select-Object -ExpandProperty Count"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return int(r.stdout.strip().splitlines()[-1]) > 0
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    if not CFD.exists():
        log(f"cloudflared not found at {CFD} — set CFD_PATH")
        return 1
    kill_existing()
    urls: dict = {}
    for key in ("bridge", "upd"):
        u = spawn(key, None)
        if u:
            urls[key] = u
    write_state(urls)
    regen_pairing(urls)

    while True:
        time.sleep(30)
        for key, url in list(urls.items()):
            if alive(TARGETS[key][0]):
                continue
            log(f"{key} tunnel died — restarting")
            new = spawn(key, url)
            if new:
                urls[key] = new
                write_state(urls)
                regen_pairing(urls)
            else:
                log(f"{key} restart failed to register — retrying next loop")


if __name__ == "__main__":
    main()
