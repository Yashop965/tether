# Tether — laptop side & packaging (PRD addendum)

## Goal
After the phone is paired, a user should be able to manage the whole thing
from the laptop without touching the phone: check status, tweak settings,
pause the cursor. And the whole stack should install in one action, with a
GitHub page present enough that someone can judge it in 30 seconds.

## D-requirements

### D1 — `hermes tether` CLI (settings + status, light)
The plugin already registers one CLI entry point; extend it to:
- `hermes tether show`          — pairing URL + token (today)
- `hermes tether status`       — read `<data_dir>/status.json`: port,
  token (redacted), phones connected, cursor gate (armed / manual-pause /
  idle-paused), last 3 voice notes
- `hermes tether set <k> <v>`   — write `plugins.entries.tether.settings`:
  `sensitivity`, `idle_seconds`, `stt_model`, `session_key`, `port` (warns:
  restart required), `token` (regenerates; phones must reconnect)
- `hermes tether pause|resume` — write `<data_dir>/control.json` =
  `{"cmd":"pause"}`; the running server's 1 s poller picks it up
- `hermes tether help`

Cross-process design: the CLI and the running server are different processes,
so they share two tiny files in the plugin data dir:
```
<plugin_data_dir>/status.json    server -> CLI   (server rewrites on change + every 2 s)
<plugin_data_dir>/control.json   CLI -> server   (CLI writes cmd; server consumes + clears)
```
Both JSON, stdlib only, no sockets, no daemon.

### D2 — one-shot install ("pkg exe or cmd")
`install/TetherSetup.exe` (Windows) and `install/tether-setup.sh` (macOS/Linux)
— both thin wrappers over one Python `install/setup.py`:
1. detect Hermes home (`$HERMES_HOME` or default path)
2. `pip install websockets pynput faster-whisper`
3. copy `plugin/` -> `<home>/plugins/tether/`
4. `hermes plugins enable tether`
5. mint + persist the token, print the pairing URL + next steps
   (install APK from Releases, open Connect screen)
Windows: `TetherSetup.exe` = PyInstaller one-file build of setup.py
(stdlib-only core keeps the exe small, ~10 MB). macOS/Linux: `.sh` calls
the same setup.py via system python3. One double-click / one line = installed.

### D3 — GitHub page (the repo IS the page)
`README.md` front = landing:
- hero: one-line pitch + 3 feature glyphs (PAD / TILT / VOICE)
- screenshot strip (mockup.html rendered + real app shots in `design/screenshots/`)
- **Install**: one-click TetherSetup.exe / `./tether-setup.sh` / manual 3 lines
- **Use it**: `hermes tether show` -> phone connects -> drag / tilt / talk
- **Safety**: idle 25 s, `■` stop, `/tether-pause`, LAN-only + token
- **Protocol**: the JSON frame table (judges like this)
- badges: python 3.10+ · Android 8.0+ · no cloud · hackathon entry
Plus `design/screenshots/` PNGs that feed the catalog yaml `screenshots:`
field. A one-page `gh-pages` index (same README + hero video embed) is a
stretch, not required.

## Acceptance (laptop side)
- D1a: after `hermes tether set sensitivity 2.0`, the running bridge applies
  it within 3 s without a restart (measured via probe moves)
- D1b: `hermes tether pause` -> `status` shows `cursor_gate: paused`
- D2: on a clean machine, double-click TetherSetup.exe -> ends with pairing
  URL printed, `hermes plugins list` shows tether enabled
- D3: README renders on GitHub with screenshots; no dead links

## Cut order if short
D1 (status.json/control.json are ~40 lines) -> D2 exe (if no time, ship
`setup.py` + a 3-line bat) -> D3 gh-pages index (README alone is enough).
