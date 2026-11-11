# Tether

Use your phone as a **remote trackpad, laser-tilt pointer, and voice terminal** for a
Hermes Agent host. Built for the Hermes Agent Plugin Hackathon.

- **Host (laptop)** — a small Hermes plugin runs a LAN/Tailscale WebSocket + UDP bridge.
  It drives the host cursor (60 Hz interpolated) and injects transcribed voice notes
  into a Hermes session. Voice runs **fully local** (faster-whisper int8 on CPU).
- **Phone (Android)** — a lightweight Kotlin/Compose app. On-screen trackpad,
  tilt-as-laser-pointer, and push-to-talk voice that lands in Hermes as text.
- **Pairing** — scan a QR on the laptop (in-app camera, on-device MLKit) or tap a
  shared `tether://` deep link. No typing hosts or tokens.
- **Self-update** — the app downloads and installs newer builds from the bridge's
  host (port 8400, SHA-256 verified). No Play Store, no cloud.

```
phone ── ws:// (voice + control) ──┐
      └─ udp:// (fast pad/tilt) ───┤  Tether bridge  ── pynput ── host cursor
                                    │  (Hermes plugin)── faster-whisper ── session inject
```

## Layout

| Path | What it is |
|---|---|
| `plugin/` | The Hermes plugin (bridge, cursor, local STT, session injection). This is the catalog-submittable unit. |
| `app/` | Android app (Kotlin + Compose, CameraX + MLKit QR, in-app updater). |
| `tools/` | Bridge-side helpers: `update_serve.py` (publish APK), `pairing_serve.py` (QR page), probes. |
| `PRD.md`, `PRD-laptop.md`, `DESIGN.md` | Design docs and protocol spec. |

## Quickstart (host side)

1. Install the plugin into Hermes: `hermes plugins install Yashop965/tether` (catalog entry)
   or point Hermes at `plugin/` in a local profile.
2. Start the bridge (auto on plugin enable). `hermes tether show` prints the pairing
   host(s) + token; `python tools/pairing_serve.py` builds the scannable QR page served
   on port 8400 next to the update files.
3. On the phone: open Tether → **SCAN QR** (or LINK panel: host + port `8377` + token)
   → the pad, tilt, and voice surfaces come alive.

### Build the phone app

```
gradle :app:assembleDebug        # inside app/
adb install -r app/app/build/outputs/apk/debug/app-debug.apk
```

Dependencies: `com.google.mlkit:barcode-scanning` (on-device QR), CameraX 1.3.4,
OkHttp. No cloud APIs anywhere in the app.

### Publishing an update

```
python tools/update_serve.py    # stages APK + tether.json into tools/apkserve/
```

The bridge's host serves `tools/apkserve/` on port 8400. Installed apps nudge,
download, verify SHA-256, and self-install.

## Protocol (L1)

- `ws://<host>:8377/?token=<token>` — control channel: `hello`, `pause`, `resume`,
  `voice:base64` frames → server STT → `stt`/`stt_pending` replies; cursor gate state.
- `udp://<host>:8378` — fast input channel: one-line auth handshake (`a <token>` →
  `ok`), then `pad <dx> <dy>`, `aim <x> <y>` (-1..1 laser aim), `click <btn>`,
  `scroll <dy>`. Bypasses TCP head-of-line blocking for sub-50 ms feel on LAN.
- Host side interpolates 30 Hz network arrivals to 60 Hz cursor updates.

## Security notes

- Token-auth on both channels; the bridge binds to LAN/Tailscale interfaces, not a
  public endpoint.
- The pairing QR embeds the token — anyone who scans it can drive that host while
  the bridge runs. Rotate the token (`hermes tether show` regenerates) when pairing
  in shared spaces.
- All voice stays on the host machine; nothing is sent to a cloud.
