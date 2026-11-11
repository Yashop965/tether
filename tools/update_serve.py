"""Publish the latest Tether APK for in-app self-update.

Stages app-debug.apk into tools/apkserve/ (served by `python -m http.server 8400`)
and writes tether.json:
    {"version_code": N, "version_name": "x.y.z", "apk": "tether.apk",
     "sha256": "...", "size": 12345}

Usage:
    python tools/update_serve.py [--version-code N] [--version-name x.y.z]

Bump --version-code for every real release so installed apps can detect it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
APK = REPO / "app" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
SERVE = REPO / "tools" / "apkserve"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version-code", type=int, default=None)
    ap.add_argument("--version-name", default=None)
    args = ap.parse_args()

    if not APK.exists():
        print(f"APK not found at {APK} — run the gradle build first.", file=sys.stderr)
        return 1

    gradle = REPO / "app" / "app" / "build.gradle.kts"
    text = gradle.read_text()
    vc = args.version_code or int(re.search(r"versionCode\s*=\s*(\d+)", text).group(1))
    vn = args.version_name or re.search(r'versionName\s*=\s*"([^"]+)"', text).group(1)

    SERVE.mkdir(parents=True, exist_ok=True)
    dest = SERVE / "tether.apk"
    shutil.copy2(APK, dest)
    data = dest.read_bytes()
    manifest = {
        "version_code": vc,
        "version_name": vn,
        "apk": "tether.apk",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }
    (SERVE / "tether.json").write_text(json.dumps(manifest, indent=2))
    print(f"published v{vn} (code {vc}, {len(data)//1024} KB) -> {SERVE}/")
    print(f"serving:  http://<host>:8400/tether.apk   (run: python -m http.server 8400 in {SERVE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
