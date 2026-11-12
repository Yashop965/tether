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
        creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                       | getattr(subprocess, "CREATE_NO_WINDOW", 0)),
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


def alive(origin: str, url: str) -> bool:
    """Process alive AND the quick-tunnel URL actually answers.

    Quick tunnels get kicked by Cloudflare while the cloudflared process
    keeps running (zombie) — an answer-check is the only real liveness."""
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
        proc_alive = int(r.stdout.strip().splitlines()[-1]) > 0
    except Exception:  # noqa: BLE001
        proc_alive = False
    if not proc_alive or not url:
        return False
    import urllib.error
    import urllib.request
    try:
        code = urllib.request.urlopen(url, timeout=10).getcode()
        return code is not None
    except urllib.error.HTTPError as e:
        # 4xx/4xx-ish from Cloudflare still means the tunnel registered
        return e.code < 500 or e.code in (426, 404)
    except Exception:  # noqa: BLE001
        return False


def kill_for_origin(origin: str) -> None:
    pat = re.escape(origin)
    ps = (
        "(Get-CimInstance Win32_Process -Filter \"name='cloudflared.exe'\") | "
        "ForEach-Object { $_.CommandLine } | Where-Object { $_ -and $_ -match '"
        + pat
        + "' } | ForEach-Object { "
        "Get-CimInstance Win32_Process -Filter \"name='cloudflared.exe'\" | "
        "Where-Object { $_.CommandLine -match '" + pat + "' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force } }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, timeout=30, check=False)


def main() -> int:
    if not CFD.exists():
        log(f"cloudflared not found at {CFD} — set CFD_PATH")
        return 1
    # singleton: never run two tunnel_guards (they fight over cloudflared)
    lock = REPO / "tools" / ".tunnel_guard.pid"
    me = os.getpid()
    try:
        if lock.exists():
            old = int(lock.read_text().strip() or "0")
            if old and old != me:
                chk = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {old}", "/NH"],
                    capture_output=True, text=True, timeout=15)
                if "python" in chk.stdout.lower() or "Info:" not in chk.stdout:
                    log(f"another tunnel_guard (pid {old}) is running — exiting")
                    return 0
    except Exception:  # noqa: BLE001
        pass
    lock.write_text(str(me))
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
        for key in list(urls):
            if alive(TARGETS[key][0], urls[key]):
                continue
            log(f"{key} tunnel dead (process or URL) — force restarting")
            kill_for_origin(TARGETS[key][0])
            time.sleep(2)
            new = spawn(key, urls[key])
            if new:
                urls[key] = new
                write_state(urls)
                regen_pairing(urls)
            else:
                log(f"{key} restart failed to register — retrying next loop")


if __name__ == "__main__":
    main()
