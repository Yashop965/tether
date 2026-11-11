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
HTTP_PORT = PORT + 1
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
    """Kill python processes from earlier guard runs that still hold the ports."""
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-Process python* -ErrorAction SilentlyContinue | Where-Object { "
                "($_.CommandLine -or '') -match 'tether|_local_run|http.server' } | ForEach-Object { $_.Id }",
            ],
            capture_output=True, text=True, timeout=30,
        )
        pids = [p for p in out.stdout.split() if p.strip().isdigit()]
        for pid in pids:
            if int(pid) != os.getpid():
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True, text=True)
                lg.info("reaped stray pid %s", pid)
    except Exception as exc:  # noqa: BLE001 - best effort
        lg.warning("reap failed: %s", exc)


def start_bridge(token: str, lg: logging.Logger) -> subprocess.Popen | None:
    cmd = [PYTHON, str(REPO / "plugin" / "_local_run.py"),
           "--port", str(PORT), "--token", token]
    proc = subprocess.Popen(cmd, cwd=str(REPO),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    lg.info("bridge spawned pid=%s", proc.pid)
    return proc


def start_http(lg: logging.Logger) -> None:
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(HTTP_PORT),
                             "--bind", "0.0.0.0"],
                           cwd=str(APKSERVE),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    lg.info("http spawned pid=%s", proc.pid)


def main() -> int:
    lg = get_logger()
    lg.info("=== bridge_guard start (python=%s) ===", PYTHON)
    token = stable_token()
    lg.info("stable token=%s (file)", token[:6] + "…")
    reap_strays(lg)
    time.sleep(1)

    bridge_pid = start_bridge(token, lg) if not port_open(PORT) else None
    if not port_open(HTTP_PORT):
        start_http(lg)

    restarts = 0
    last_check = time.time()
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
                    lg.error("bridge FAILED to come up; retrying in 5s")
                    time.sleep(5)
            if not port_open(HTTP_PORT) and restarts % 5 == 0:
                lg.warning("http DOWN -> restarting")
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
