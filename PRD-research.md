# Tether — PRD research: competitive scan (12 Nov 2026)

## The space: "phone controls laptop"
| Project | Type | Transport | What it does | Notable |
|---|---|---|---|---|
| **Remote Mouse** (HungryBolo) | App + PC server | Wi-Fi/BT | Touchpad, gyro "air mouse", keyboard, media panels, voice input | Premium subscription for extras; "air mouse" = gyro tilt; most polished mainstream app |
| **Unified Remote** | App + PC server | Wi-Fi | Touchpad, keyboard, media, presentation remote, swipe typing | The incumbent; basic free, features paywalled |
| **MyPC Remote** | App + web portal | Wi-Fi/BT | QR-pairing server, 60 FPS screen mirror, macros, voice remote (volume/apps/shutdown) | Free, no Play Store app — scan QR |
| **Microsoft Phone Remote** | PC tray app | Wi-Fi | **Browser-only** remote: live screen, touchpad, keyboard, QR-pairing | No phone install; private LAN; system-tray resident |
| **TwinDesk** (OSS) | Multi-device agent | LAN (qWAVE DSCP, Wi-Fi low-latency mode) | One mouse/keyboard across Mac/PC/Linux/phones; tablet as 2nd screen; remote touchpad+keyboard+**voice typing**; camera/mic bridge | Free + OSS; phone as "full peer"; enterprise-grade transport tuning |
| **laptop-remote / "Remote Deck"** (PyPI) | Python server | LAN-only | Trackpad, D-pad, media, **presentation laser pointer**, headless terminal QR+PIN edition | Closest conceptual cousin: LAN-only, trusted-network, laser pointer; no phone app (web) |
| **Motion Mouse** | Win app + Android app | Wi-Fi/BT | **Gyro sub-pixel cursor**, virtual gamepad, dead-zone/sensitivity tuning | "The phone itself is the controller" — drift prevention, sensor fusion |
| **phoneview** (PyPI) | Python host | USB / Tailscale | **Reverse**: view+control the *phone* from the laptop browser | Opposite direction; Tailscale-published |
| BLE HOGP apps (Lykee, appmania, promox) | App only | Bluetooth HOGP | No PC software: phone emulates keyboard+mouse via BLE | Zero-install on PC; low-precision; no voice/agent |

## Where Tether is different (positioning for the PRD)
1. **Agent-first, not OS-first** — competitors move a cursor or type text. Tether's voice path injects commands into a *Hermes coding-agent session* (on-device STT → typed into the agent terminal). The laptop "keyboard" is an AI terminal, not Notepad.
2. **On-device speech** — every mainstream app uses Google/OS cloud dictation (or none). Tether transcribes locally (faster-whisper int8): offline, private, free.
3. **No phone-side install friction, and self-healing updates** — sideloaded APK + in-app updater + LAN/cloud relay discovery. Competitors either force a Play Store round-trip or a PC server install; none self-update the phone app.
4. **Gyro laser tuned for presentations** — Motion Mouse does gyro-cursor; laptop-remote does laser; nobody cleanly does "wave the phone like a wand, cursor glides" with a tilt-velocity model + neutral-pose calibration.
5. **Three transports, one token** — LAN WS, laptop-hotspot auto-pair, Cloudflare tunnel "from anywhere." Competitors pick one (usually Wi-Fi).
6. **Hermes-native surface** — the whole thing is a Hermes plugin (`hermes tether show/hotspot/tunnel`), so judges see an AI-agent product, not a mouse app.

## Use cases to write into the PRD
- **Hackathon demo / presentation**: laser + voice commands from across the room
- **Couch/bed dev**: trackpad + voice-to-agent, no keyboard
- **Accessibility**: switch-control style coarse pointer (gyro wand), voice-driven
- **Kiosk/terminal mode**: headless pairing (QR+PIN) like laptop-remote's terminal edition
- **Remote (no LAN)**: Cloudflare "from anywhere" mode (Tailscale-pub like phoneview)

## Latency reference points (from product claims/reviews)
- Usable wireless mouse: "low, not quite physical-mouse" (Remote Mouse review)
- TwinDesk: voice-class DSCP + Wi-Fi low-latency mode = they treat latency as first-class
- Our target: <50 ms perceived LAN pad latency (16 ms batching + 60 Hz pump), audio chunk 64 KB
- Motion Mouse claims "ultra-low latency" gyro over UDP/TCP dual channel — we have UDP fast channel 8378 (verify it's actually used by the app!)

## Open questions for PRD
- Is the UDP fast channel (8378) actually wired end-to-end, or vestigial?
- Does the in-app updater survive a phone with no LAN + dead relay (full offline)?
- Security posture: single shared token, no per-connection mTLS — acceptable for LAN? Document it.
