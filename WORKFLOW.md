# Tether — repo state & workflow

## Initial repo state (Day 0, Nov 5)

```
tether/                     ← Yashop965/tether, trunk-only, daily tags
├── README.md               what/why/install/demo (write last, it's a demo)
├── PRD.md                  requirements + acceptance criteria + risks
├── DESIGN.md               architecture, protocol, UI spec, safety
├── WORKFLOW.md             this file — the operating system of the project
├── plugin/                 ← the catalog unit (Python)
│   ├── plugin.yaml         manifest
│   ├── __init__.py         register(ctx): tool + /tether-pause + CLI + server start
│   ├── server.py           WS bridge: pad/tilt/click/scroll/audio, token gate
│   ├── mouse.py            pynput driver + armed/idle gate
│   ├── stt.py              faster-whisper transcriber
│   └── session.py          active-session resolution for injection
├── app/                    ← Android APK (Kotlin/Compose) — skeleton lands Day 1
├── design/                mockup.html + screenshots/ (feeds catalog yaml)
└── plugin-catalog/tether.yaml   draft entry for the NousResearch/hermes-agent PR
```

## Toolchain — deliberately minimal
| need | choice |
|---|---|
| Android build | JDK 21 ✓ + Android SDK 35 + `gradlew` (wrapper, no Android Studio, no IDE codegen) |
| Plugin | Hermes Python env (websockets, pynput, faster-whisper all present ✓) + `hermes plugins validate`/`doctor` |
| WS contract test | one script: `tools/ws_probe.py` — speaks the protocol, no framework |
| Versioning | trunk + tag at every ladder rung (`ladder-0`…`ladder-6`) |
| CI | none until submission day (optional APK build action) |

Rules: no new dependencies beyond the lists above. No test framework. No
issue tracker — the ladder below is the tracker.

## The MVP ladder (every rung = something demonstrable)

| rung | what turns on | risk killed | day |
|---|---|---|---|
| **L0** | hello-Compose APK installs + runs on the real phone | R1 | 1 |
| **L1** | plugin server up; Python WS probe moves the host cursor (pad/click/scroll) | R2 | 1 |
| **L2** | phone trackpad drives the laptop cursor end-to-end (M1+M2) | — | 2 |
| **L3** | tilt mode (M3) | R4 | 2 |
| **L4** | voice → whisper → transcript → injected into session (M4) | R3, R5 | 3 |
| **L5** | safety hardening + black/white UI pass (M5, M6, M7) | — | 4 |
| **L5+** | laptop side: status/control IPC + `hermes tether` commands (D1), one-shot installer (D2) | — | 4-5 |
| **L6** | demo video <90 s, catalog yaml, PR, Typeform + GitHub page (D3) | — | 5 (+ buffer) |

**Cut policy:** if a rung slips, cut SHOULDs, never MUSTs. Never start a new
rung with the previous one broken.

## Daily loop (each day ~2 h of work, ~30 min review)
1. morning: one 2-min look at yesterday's clip, pick the rung
2. build the rung, commit on trunk as it goes
3. evening: 30 s clip of the rung working → into `clips/` (final demo is just
   the best clips, re-shot on Day 5)
4. tag `ladder-N`

## Day plan (deadline Nov 11 5 pm EST = Nov 12 5:30 am IST)
- **Nov 5** (today): L0 + L1 + L2 start — all risk, no glory
- **Nov 6**: L2 finish + L3
- **Nov 7**: L4 (the magic moment) + L5 start
- **Nov 8**: L5 finish + S1/S2 polish
- **Nov 9**: S3 APK release + README + catalog yaml + validate
- **Nov 10**: PR open + demo video + Typeform (2-day buffer against deadline)
- **Nov 11**: contingency + judge-comment prep
