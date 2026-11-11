"""Run the Tether bridge standalone (no Hermes) for protocol testing.

    python plugin/_local_run.py            # listen on 8377, token shown
    python plugin/_local_run.py --token X --port 8377

Injects are printed (nothing to queue them into outside Hermes).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))
from plugin.server import TetherServer  # noqa: E402


class FakeCtx:
    plugin_id = "tether"
    injections: list[str] = []

    def register_tool(self, *a, **k):
        print("[stub] register_tool", a[0] if a else k.get("name"))

    def register_command(self, *a, **k):
        print("[stub] register_command", a[0] if a else k.get("name"))

    def register_cli_command(self, *a, **k):
        print("[stub] register_cli_command", a[0] if a else k.get("name"))

    def plugin_data_dir(self) -> Path:
        return Path(__file__).parent / "_data"

    def inject_message(self, content: str, role: str = "user", *, session_key=None, origin=None) -> bool:
        self.injections.append(content)
        print(f"[inject role={role} session_key={session_key}] {content}")
        return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8377)
    ap.add_argument("--token", default=None)
    ap.add_argument("--stt-model", default="tiny", help="keep standalone tests light; use 'base' in prod")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.v else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    import secrets
    ctx = FakeCtx()
    settings = {
        "port": args.port,
        "stt_model": args.stt_model,
        "idle_seconds": 25,
        "session_key": "local",
    }
    token = args.token or secrets.token_urlsafe(12)
    print(f"token: {token}")
    print(f"connect: ws://127.0.0.1:{args.port}/?token={token}")

    server = TetherServer(ctx, settings, token)
    server.register_with_ctx(ctx)
    server.start()

    import time
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
