# Tether — PRD (v0.1, Nov 5)

## Problem
Hermes is most useful while your hands/eyes are off the laptop. Voice input
is faster than typing but needs a capture path, and cursor control needs the
mouse. The phone you always carry has all three: screen, gyro, mic.

## Product
A minimal black/white Android app + a Hermes plugin. The plugin runs a LAN
bridge: the phone drives the host cursor (touchpad + device tilt) and voice
notes are transcribed **locally** (Whisper, no cloud) and injected into the
active Hermes session as user turns.

## Requirements

### MUST (demo bar — all six or the entry doesn't work)
| id | requirement |
|----|-------------|
| M1 | **Pairing** — `hermes tether show` prints host:port + token; app connects with host/port/token; status dot white=connected |
| M2 | **Trackpad** — drag=move · tap=left click · two-finger tap=right click · two-finger vertical drag=scroll |
| M3 | **Tilt** — tilt phone → cursor drifts proportionally (TYPE_GAME sensor); flatten → stops; tap=click |
| M4 | **Voice** — mic button: tap record / tap send → local faster-whisper (base, int8, CPU) → transcript shown on phone → injected as a user message into the active Hermes session, flagged ✓/✗ |
| M5 | **Safety** — auto-pause after 25 s idle; in-app `■` stop; `/tether-pause`; while unarm every cursor op is a no-op even mid-drag |
| M6 | **LAN-only** — token-authenticated WebSocket, no cloud calls, no accounts |
| M7 | **UI** — 2 screens (Connect, Control), 3 modes (PAD/TILT/VOICE), black `#000` + white `#fff` only |

### SHOULD (polish, cuttable in order)
S1 desktop status pane (phone connected + last transcript) · S2 sensitivity
slider on phone · S3 install-friendly APK on GitHub Releases + README guide ·
S4 browser fallback pad page · S5 laptop-side settings CLI (`hermes tether
status/set/pause` — see "Laptop side" below) · **S6 fingerprint-sensor
gesture hook (on/off toggle, default OFF)** — on devices whose fingerprint
sensor doubles as a nav-gesture surface (swipe there = `KEYCODE_BACK` to the
focused app), the Tether app maps that gesture to *toggle the cursor pause
gate* (on → armed, off → frozen). No new protocol frames: the app sends the
existing `pause`/`resume` based on its last `state` message. Settings live in
the app (toggle row on the Connect screen) and can be defaulted by the
laptop via a one-line config key (`gesture_toggles_pause`).

### WON'T
iOS · multi-monitor coordinates · cloud STT · i18n · push/accounts · camera.

## Acceptance criteria (verifiable)
- A1 phone drag moves the host cursor; measured cursor pos changes on screen
- A2 tap on a desktop icon under the cursor opens it
- A3 45° tilt held 1 s moves cursor ≥10% of screen width; flatten → motion stops <300 ms
- A4 10 s voice note → transcript visible on phone and present in the session within 5 s of stop (CPU/base)
- A5 no input 25 s → state shows paused; tap moves nothing; `/tether-pause off` re-arms
- A6 wrong token → connection rejected (close 4401)

## Risks (ranked, unblock order = build order)
| risk | mitigated by |
|---|---|
| R1 Android toolchain on Windows (no SDK/Gradle yet) | L0: install hello-Compose APK on the phone first |
| R2 pynput cursor injection on this Windows host | L1: WS client probe moves cursor pre-app |
| R5 session-key resolution for desktop injection | L4 probe in this env |
| R3 Whisper latency on laptop CPU | measure L4; fallback: tiny model |
| R4 TYPE_GAME availability on the phone | fallback: accelerometer minus gravity |
