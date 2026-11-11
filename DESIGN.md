# Tether — design

Phone as remote input + voice terminal for Hermes. Built for the Hermes
plugin hackathon (entry: `tether`).

## One-line pitch

Point your phone at nothing, tilt it to move the cursor, tap to click, and
talk — your voice lands in Hermes as a typed message. No cloud, no accounts.

## Architecture

```
 Android APK "Tether" (Kotlin/Compose)          Hermes host (Python plugin)
─────────────────────────────────────           ─────────────────────────────
 Connect screen (host/port/token)               WS server, port 8377, token
   │ OkHttp WebSocket                              auth on connect
   ├─ PAD   pointerInput deltas ────────JSON────> pynput cursor driver
   ├─ TILT  SensorManager (TYPE_GAME) ──────────>  (armed gate: idle 25 s
   ├─ VOICE MediaRecorder (opus/aac) ─binary───>  + manual pause + /tether-pause)
   │                                            faster-whisper base (int8, CPU)
   └─ STOP  pause event ─────────────────────>    ctx.inject_message()
                                                        │
                                                        ▼
                                              active Hermes session
                                              (voice note = a user turn)
```

- **App → host**: one WebSocket. JSON text frames for control, binary frames
  for audio. Host → app: JSON (stt results, state, errors).
- **Plugin**: starts the server in a daemon thread when enabled; exposes
  `tether_status` tool, `/tether-pause` command, `hermes tether show` CLI.
- **No cloud**: Whisper runs locally on the host. Nothing leaves the LAN.

## Protocol

| dir | frame | meaning |
|---|---|---|
| → | `{"t":"hello"}` | connect handshake |
| → | `{"t":"pad","dx":0.02,"dy":-0.01}` | relative cursor move (normalized screen fraction × sensitivity) |
| → | `{"t":"click","b":"left"|"right"|"middle"}` | click |
| → | `{"t":"scroll","dy":-3}` | scroll |
| → | `{"t":"tilt","x":0.12,"y":-0.05}` | drift while tilt active (~30 Hz, ∓1.0) |
| → | `{"t":"tilt","active":false}` | release |
| → | binary (repeating) | audio chunks (webm/opus ≥API 29, m4a/aac <29) |
| → | `{"t":"audio_done"}` | end clip → transcribe → inject |
| → | `{"t":"pause"}` / `{"t":"resume"}` | manual cursor gate |
| ← | `{"t":"hello_ok"}` | accepted |
| ← | `{"t":"stt","text":"...","sent":true}` | transcript + delivery flag |
| ← | `{"t":"stt_pending"}` / `{"t":"stt_error","error":"..."}` | progress / failure |
| ← | `{"t":"state","paused":true}` | gate state changed |

## Phone UI (black `#000`, white `#fff`, nothing else)

Two screens, zero clutter:

1. **Connect** — host, port, token fields (remembered) + one `CONNECT` button.
2. **Control** — one bar on top, one surface below:
   - top: status dot · mode pill `[ PAD │ TILT │ VOICE ]` · big `■` stop
   - **PAD**: full-bleed touch zone. Drag = move, tap = click,
     two-finger tap = right-click, two-finger drag = scroll.
   - **TILT**: centered crosshair with a live tilt vector line; tap = click,
     two-finger = right-click. Phone flat → cursor holds; tilt → drifts.
   - **VOICE**: one big circular mic button (tap to record, tap to send),
     pulsing ring while recording, then the transcript with a ✓ when sent.
   - bottom strip: last note + ✓/✗.

Mockups: `design/mockup.html` (open in browser, interactive tabs).

## Safety model

- Cursor is **disarmed by default after 25 s** of no phone input.
- In-app `■` and `/tether-pause` pause immediately; every mouse op is a
  no-op while unarm — even mid-drag.
- WS is token-authenticated; server binds LAN with the token as the only
  secret; 30 MB audio cap per clip.
- `tether_status` tool surfaces the gate state to the agent so Hermes knows
  when the cursor is live.

## Languages & deps (deliberately minimal)

| piece | language | why |
|---|---|---|
| Android app | **Kotlin + Jetpack Compose** | native, first-class sensor/mic APIs, smallest runtime footprint, no extra SDKs beyond Android SDK + Gradle |
| Plugin | **Python** | Hermes plugin system is Python; deps: `websockets`, `pynput`, `faster-whisper` |

## Build & test plan

- App: Gradle 8 + AGP 8.5, minSdk 26, target 35, debug APK (JDK 21 +
  Android SDK 35 already being installed).
- Plugin: `hermes plugins validate ./plugin --install-deps`,
  `hermes plugins doctor ./plugin`; WS contract tested with a Python client
  before the phone exists.
- E2E: real Android phone on LAN; demo recorded < 90 s.

## Demo script (≤ 90 s)

1. `hermes tether show` → pairing URL (8 s)
2. phone connects, status dot goes white (12 s)
3. PAD: drag across screen, open a file with a tap (18 s)
4. TILT: hold phone, cursor drifts, flatten → stops (15 s)
5. VOICE: "open the sprint board and find the design review ticket"
   → transcript appears in Hermes, Hermes acts (25 s)
6. `■` freezes the cursor; `/tether-pause status` shows paused (12 s)

## Catalog submission

- Repo: `Yashop965/tether` (plugin + app + docs + APK release).
- PR to `NousResearch/hermes-agent`: one file `plugin-catalog/tether.yaml`
  pinning the repo sha; `screenshots:` from `design/`.
- Then: demo video on X (tag @NousResearch) + Typeform with both links.

## Scope cuts if the clock gets tight

Browser pad page · desktop status pane · iOS support · cloud STT fallback ·
multi-monitor coordinates. Core is: pad + tilt + voice + safety.
