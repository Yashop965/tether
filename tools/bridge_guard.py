"""Tether bridge watchdog — keeps 8377 (WS/UDP bridge) and 8400 (update/pairing
server) alive. The bridge itself has no self-healing; this guard restarts it
on crash and keeps the token stable across restarts so paired phones keep working.

    python tools/bridge_guard.py          # run once, stays alive
    (Ctrl-C stops it; it also reaps strays on startup)

Logs everything to tools/bridge_guard.log so instability has a timeline.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).parent.parent
APKSERVE = REPO / "tools" / "apkserve"
TOKEN_FILE = REPO / "tools" / "bridge.token"
PORT = int(os.environ.get("TETHER_PORT", "8377"))
HTTP_PORT = 8400  # update/pairing server (NOT PORT+1 — that's the UDP fast port)
LOG = REPO / "tools" / "bridge_guard.log"

VENV_PY = (
    r"C:\Users\yashs\AppData\Local\hermes\installs\9b28cca942036e39"
    r"\environments\1ad68c34cd30470bb12cb2c33975aa1d\venv\Scripts\python.exe"
)
PYTHON = sys.executable if (sys.executable and "python" in sys.executable.lower()) else VENV_PY


def get_logger() -> logging.Logger:
    lg = logging.getLogger("bridge_guard")
    lg.setLevel(logging.INFO)
    lg.handlers.clear()
    h = logging.FileHandler(LOG, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    lg.addHandler(h)
    return lg


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stable_token() -> str:
    """One token per machine; survives bridge restarts so paired phones never orphan."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    import secrets
    tok = secrets.token_urlsafe(12)
    TOKEN_FILE.write_text(tok)
    return tok


def reap_strays(lg: logging.Logger) -> None:
    """Kill python processes from earlier guard runs that still hold the ports.

    Matches only the CHILD command lines (bridge + update server) — never the
    guards themselves, whose paths also contain 'tether'."""
    try:
        out = subprocess.run(
            ["tasklist", "/v", "/nh"],
            capture_output=True, text=True, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        pids = set()
        for line in out.stdout.splitlines():
            cols = line.split()
            # tasklist /v columns: image PID session name session# mem ... description
            if len(cols) < 3:
                continue
            if ("_local_run" in line or "http.server" in line) and "guard" not in line \
                    and cols[0].lower().endswith((".exe", "python")) and cols[1].isdigit():
                pids.add(int(cols[1]))
        for pid in sorted(pids):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lg.info("reaped stray pid %s", pid)
    except Exception as exc:  # noqa: BLE001 - best effort
        lg.warning("reap failed: %s", exc)


def start_bridge(token: str, lg: logging.Logger) -> subprocess.Popen | None:
    cmd = [PYTHON, str(REPO / "plugin" / "_local_run.py"),
           "--port", str(PORT), "--token", token]
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc = subprocess.Popen(cmd, cwd=str(REPO),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)
    lg.info("bridge spawned pid=%s", proc.pid)
    return proc


def start_http(lg: logging.Logger) -> None:
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(HTTP_PORT),
                             "--bind", "0.0.0.0"],
                           cwd=str(APKSERVE),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=flags)
    lg.info("http spawned pid=%s", proc.pid)


def main() -> int:
    lg = get_logger()
    lg.info("=== bridge_guard start (python=%s) ===", PYTHON)
    # singleton: a second bridge_guard would fight the first over ports 8377/8400
    lock = REPO / "tools" / ".bridge_guard.pid"

    def _release_lock() -> None:
        # never leave a stale lock behind — Windows recycles PIDs fast and a
        # name-only check against a reused pid would scare off the next guard
        try:
            if lock.exists() and lock.read_text().strip() == str(os.getpid()):
                lock.unlink()
        except Exception:  # noqa: BLE001
            pass

    import atexit
    atexit.register(_release_lock)
    me = os.getpid()
    try:
        if lock.exists():
            old = int(lock.read_text().strip() or "0")
            if old and old != me:
                # PID reuse makes name-only checks lie: confirm the OLD pid
                # is actually a live bridge_guard (command line check).
                chk = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_Process -Filter 'ProcessId=" + str(old)
                     + "') | Where-Object { $_.CommandLine -match 'bridge_guard' } | Select-Object -First 1"],
                    capture_output=True, text=True, timeout=25)
                if chk.stdout.strip():
                    lg.info("another bridge_guard (pid %d) is running — exiting", old)
                    return 0
                lg.info("stale guard lock (pid %d gone) — taking over", old)
    except Exception:  # noqa: BLE001
        lg.warning("singleton check failed — continuing")
    lock.write_text(str(me))
    token = stable_token()
    lg.info("stable token=%s (file)", token[:6] + "…")
    reap_strays(lg)
    time.sleep(1)

    bridge_pid = start_bridge(token, lg) if not port_open(PORT) else None
    if not port_open(HTTP_PORT):
        start_http(lg)

    restarts = 0
    last_check = time.time()
    last_http_try = 0.0
    while True:
        try:
            if not port_open(PORT):
                restarts += 1
                lg.warning("bridge DOWN (restart #%d) -> restarting", restarts)
                bridge_pid = start_bridge(token, lg)
                # give the websockets server a moment to bind
                for _ in range(10):
                    if port_open(PORT):
                        break
                    time.sleep(0.5)
                if port_open(PORT):
                    lg.info("bridge back UP on :%d", PORT)
                else:
                    lg.error("bridge FAILED to come up; retrying in 30s")
                    time.sleep(30)
            if not port_open(HTTP_PORT) and time.time() - last_http_try > 30:
                last_http_try = time.time()
                lg.warning("http DOWN -> restarting (reaping strays first)")
                try:
                    reap_strays(lg)
                    time.sleep(1)
                except Exception:  # noqa: BLE001
                    pass
                start_http(lg)
            # heartbeat line every ~10 min so a log gap is itself a signal
            if time.time() - last_check > 600:
                last_check = time.time()
                lg.info("heartbeat — bridge:%s http:%s restarts:%d",
                        port_open(PORT), port_open(HTTP_PORT), restarts)
        except Exception as exc:  # noqa: BLE001 - guard must never die silently
            lg.exception("guard tick failed: %s", exc)
        time.sleep(3)


if __name__ == "__main__":
    main()
