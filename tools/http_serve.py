"""Threaded static server for the Tether update/pairing dir (port 8400).

Plain ``python -m http.server`` is SINGLE-threaded: one APK download
(30+ MB) blocks every other request — including the tiny ``tether.json``
the app's CHECK button polls *while* the download is in flight. This
serves the same directory with ThreadingHTTPServer so downloads and
manifest/QR/pairing pages proceed in parallel. Same zero-auth posture
(the dir is deliberately public: apk + manifest + pairing page + QRs).

    python tools/http_serve.py [port] [directory]
"""
from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).parent.parent


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # one line per request would spam the guard
        pass


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8400
    directory = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "tools" / "apkserve"
    handler = partial(QuietHandler, directory=directory)
    srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
    srv.daemon_threads = True
    print(f"tether http: serving {directory} on 0.0.0.0:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
