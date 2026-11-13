"""Tether LAN bridge: WebSocket control channel + voice-to-session pipeline.

Protocol (JSON text frames + binary audio frames), phone -> host:
  {"t":"hello"}                      -> {"t":"hello_ok","port":...,"paused":...}
  {"t":"pad","dx":0.02,"dy":-0.01}  relative cursor move (normalized deltas)
  {"t":"click","b":"left"}          click (left/right/middle)
  {"t":"scroll","dy":-3}            scroll
  {"t":"audio_done"}                end of a binary audio stream
  {"t":"pause"} / {"t":"resume"}    manual cursor gate
Host -> phone:
  {"t":"stt","text":"...","sent":true}
  {"t":"stt_pending"}
  {"t":"stt_error","error":"..."}
  {"t":"state","paused":true,"idle_paused":true}
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from .mouse import MouseDriver
from .session import resolve_session_key
from .stt import Transcriber

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 30 * 1024 * 1024  # 30 MB cap per clip
TRANSCRIPT_RING = 20


class _Client:
    __slots__ = ("ws", "audio", "audio_bytes", "audio_started", "addr")

    def __init__(self, ws: Any, addr: Any) -> None:
        self.ws = ws
        self.addr = addr
        self.audio = bytearray()
        self.audio_bytes = 0
        self.audio_started = False


class TetherServer:
    def __init__(self, ctx: Any, settings: Mapping[str, Any], token: str) -> None:
        self.ctx = ctx
        self.settings = dict(settings)
        self.token = token
        self.port = int(settings.get("port") or 8377)
        self.host = str(settings.get("host") or "0.0.0.0")
        self.mouse = MouseDriver(
            sensitivity=float(settings.get("sensitivity", 1.0)),
            idle_seconds=int(settings.get("idle_seconds", 25)),
        )
        self.stt = Transcriber(
            model_size=str(settings.get("stt_model", "base")),
            device=str(settings.get("stt_device", "cpu")),
            compute_type=str(settings.get("stt_compute", "int8")),
        )
        self.clients: dict[Any, _Client] = {}
        self.transcripts: list[dict[str, Any]] = []
        self._loop: Any = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._started_at = time.time()

    # ------------------------------------------------------------------ ctx
    def register_with_ctx(self, ctx: Any) -> None:
        ctx.register_tool(
            name="tether_status",
            toolset="tether",
            schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=self._tool_status,
            description=(
                "Tether remote-control bridge status: connected phones, cursor "
                "gate state, and recent voice-note transcripts."
            ),
        )
        ctx.register_command(
            "tether-pause",
            self._cmd_pause,
            description="Pause / resume / check the Tether cursor gate",
            args_hint="[on|off|status]",
            argument_mode="text",
        )
        ctx.register_cli_command(
            "tether",
            "Tether remote-control bridge (phone -> host cursor + voice)",
            self._cli_setup,
            self._cli_show,
            description="Show the pairing URL + token for the Tether phone app",
        )

    def _tool_status(self, _args: Mapping[str, Any] = None) -> str:
        paused = not self.mouse.armed
        out = {
            "port": self.port,
            "bound_host": self.host,
            "phone_clients": len(self.clients),
            "cursor_paused": paused,
            "idle_seconds": self.mouse.idle_seconds,
            "recent_transcripts": self.transcripts[-5:],
        }
        return json.dumps(out, ensure_ascii=False)

    async def _cmd_pause(self, raw_args: str = "") -> str | None:
        arg = (raw_args or "").strip().lower()
        if arg in ("", "status", "check"):
            state = "paused" if not self.mouse.armed else "armed"
            return f"tether cursor gate: {state} ({len(self.clients)} phone(s) connected)"
        if arg in ("on", "pause", "stop"):
            self.mouse.pause()
            self._broadcast({"t": "state", "paused": True})
            return "tether: cursor control paused"
        if arg in ("off", "resume", "go"):
            self.mouse.resume()
            self._broadcast({"t": "state", "paused": False})
            return "tether: cursor control resumed"
        return f"tether: unknown argument {arg!r} (on|off|status)"

    def _cli_setup(self, parser: Any) -> None:
        parser.add_argument(
            "sub",
            choices=["show", "hotspot", "tunnel"],
            help=(
                "show = pairing targets + QR; "
                "hotspot = start the Windows Wi-Fi hotspot the phone auto-joins; "
                "tunnel = Cloudflare quick tunnels (remote mode, works from any network)"
            ),
        )

    # ------------------------------------------------------------ candidates
    @staticmethod
    def _classify_ip(ip: str) -> str | None:
        if ip.startswith("127.") or ip.startswith("169.254."):
            return None
        if ip.startswith("192.168.43."):
            return "tether"   # phone's hotspot / USB-tethering client
        if ip.startswith("100."):
            return "tailnet"
        if ip.startswith("10.") or ip.startswith("172.") or ip.startswith("192.168."):
            return "lan"
        return "lan"

    def _candidates(self) -> list[dict[str, str]]:
        """Reachable pairing targets, best (lowest-latency) first:
        tether > lan > tailnet, plus the Tailscale MagicDNS name when present."""
        import re
        import shutil
        import subprocess

        out: list[dict[str, str]] = []
        if shutil.which("ipconfig"):
            try:
                raw = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=15).stdout
            except Exception:
                raw = ""
            for line in raw.splitlines():
                s = line.strip()
                m = re.match(r"^(?:Ethernet|Wireless LAN) adapter\s+(.+):$", s)
                if m:
                    adapter = m.group(1)
                    continue
                m2 = re.match(r"IPv4 Address[^\s:]*:\s*(\d+\.\d+\.\d+\.\d+)", s)
                if m2:
                    ip = m2.group(1)
                    kind = self._classify_ip(ip)
                    if kind:
                        out.append({"kind": kind, "ip": ip, "name": adapter or "nic"})
        # Tailscale: 100.x IP + stable MagicDNS name
        if shutil.which("tailscale"):
            try:
                ip = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                                    text=True, timeout=10).stdout.strip()
                if ip:
                    out.append({"kind": "tailnet", "ip": ip, "name": "tailscale"})
                st = json.loads(subprocess.run(["tailscale", "status", "--json"],
                                               capture_output=True, text=True, timeout=10).stdout)
                me = st.get("Self", {})
                if me.get("DNSName"):
                    out.append({"kind": "tailnet-dns", "ip": me["DNSName"], "name": "tailscale"})
            except Exception:
                pass
        order = {"tether": 0, "lan": 1, "tailnet": 2, "tailnet-dns": 3}
        out.sort(key=lambda c: order.get(c["kind"], 9))
        seen: set[str] = set()
        ded = [c for c in out if not (c["ip"] in seen or seen.add(c["ip"]))]
        return ded

    def _cli_show(self, args: Any) -> None:
        sub = getattr(args, "sub", "show")
        if sub == "hotspot":
            self._cli_hotspot(args)
            return
        if sub == "tunnel":
            self._cli_tunnel(args)
            return
        cands = self._candidates()
        if not cands:
            cands = [{"kind": "local", "ip": "127.0.0.1", "name": "loopback"}]
        print(f"Tether pairing — port {self.port}, token {self.token}")
        print("Fastest first: laptop hotspot / USB-tether beats same-Wi-Fi LAN; the")
        print("tailnet IP (100.x) is the stable pairing target when networks keep changing.")
        print()
        for c in cands:
            print(f"  [{c['kind']:>11}] {c['ip']}   ({c['name']})")
        primary = cands[0]
        payload = f"tether://{primary['ip']}?port={self.port}&token={self.token}"
        print()
        print(f"Pairing URL:  {payload}")
        try:
            import qrcode

            # qrcode.make() returns a PIL image (no print_ascii) when Pillow
            # is installed — use the QRCode object directly so the terminal
            # QR prints regardless of the image backend.
            qr = qrcode.QRCode()
            qr.add_data(payload)
            qr.make()
            qr.print_ascii(invert=True)
            print("\nScan with the Tether app's camera to pair in one tap.")
        except ImportError:
            print("(install 'qrcode' for QR output: pip install qrcode)")
        # live cloud tunnels (if `hermes tether tunnel` was run) — show remote
        try:
            tun = json.loads(Path(self._data_dir() + "/tunnels.json").read_text(encoding="utf-8"))
            if tun.get("bridge"):
                print(f"\nREMOTE (any network) pairing: wss://{tun['bridge'].split('https://', 1)[-1]}")
            if tun.get("upd"):
                print(f"REMOTE app updates:           {tun['upd']}")
        except Exception:
            pass

    # ---------------------------------------------------------- data dir
    def _data_dir(self) -> str:
        try:
            return str(self.ctx.plugin_data_dir())
        except Exception:
            import tempfile

            return tempfile.gettempdir()

    # ------------------------------------------------------------ hotspot
    def _cli_hotspot(self, args: Any) -> None:
        """Start the laptop's Wi-Fi hotspot (UAC), print SSID/PSK, and refresh
        the pairing page so the phone's Tether app auto-joins Wi-Fi + bridge."""
        import secrets
        import string
        import subprocess

        check = subprocess.run(
            ["netsh", "wlan", "show", "drivers"], capture_output=True, text=True
        ).stdout.lower()
        if "hosted network: yes" not in check and "hosted network supported" not in check:
            print("this Wi-Fi adapter does not support a hosted network — use `hermes tether tunnel` instead")
            return

        ssid = f"Tether-{secrets.token_hex(2).upper()}"
        # stronger PSK (~62 bits vs 33) but still human-typeable: 12 chars from
        # the full alnum set (WPA2-PSK allows any 8-63 printable ASCII, but the
        # demo flow hands this to people — keep it free of -_@)
        _ALNUM = string.ascii_letters + string.digits
        psk = "".join(secrets.choice(_ALNUM) for _ in range(12))
        print(f"configuring hotspot SSID={ssid} — accept the UAC prompt(s) below")

        def netsh_admin(argv: list[str]) -> str:
            import os
            import time as _time

            tag = secrets.token_hex(4)
            out = os.path.join(os.environ.get("TEMP", "."), f"tether_netsh_{tag}.txt")
            inner = " ".join(argv) + ' > "' + out + '" 2>&1'
            ps = "Start-Process powershell -ArgumentList '-NoProfile','-Command','" + inner + "' -Verb RunAs -Wait"
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=120, check=False)
            _time.sleep(1.5)
            if os.path.exists(out):
                raw = open(out, "rb").read()
                try:
                    return raw.decode("utf-16").strip()
                except UnicodeDecodeError:
                    return raw.decode("utf-8", errors="replace").strip()
            return ""

        out1 = netsh_admin(
            ["netsh", "wlan", "set", "hostednetwork", f"ssid={ssid}", f"key={psk}", "keyUsage=temporary"]
        )
        if out1 and "not a valid" in out1.lower():
            print(f"netsh configure failed: {out1[:200]}")
            return
        out2 = netsh_admin(["netsh", "wlan", "start", "hostednetwork"])
        if "already" in out2.lower():
            out2 = "already running"
        print(f"hotspot up: SSID={ssid}  PSK={psk}")
        print("refreshing the pairing page with the HOTSPOT QR…")
        self._regen_pairing_page(ssid=ssid, psk=psk)
        print("on the phone: Tether app → SCAN QR → HOTSPOT card → one tap (Wi-Fi + bridge).")

    def _regen_pairing_page(self, ssid: str | None = None, psk: str | None = None) -> None:
        """Run tools/pairing_serve.py when present (repo dev layout)."""
        import subprocess
        import sys as _sys

        here = Path(__file__).resolve().parent
        script = here.parent / "tools" / "pairing_serve.py"
        if not script.exists():
            print("(pairing-page tool not found next to the plugin — skipped)")
            return
        cmd = [_sys.executable, str(script)]
        if ssid and psk:
            cmd += ["--ssid", ssid, "--psk", psk]
        try:
            tun = json.loads(Path(self._data_dir() + "/tunnels.json").read_text(encoding="utf-8"))
            if tun.get("bridge"):
                cmd += ["--wss", "wss://" + tun["bridge"].split("https://", 1)[-1]]
            if tun.get("upd"):
                cmd += ["--upd", tun["upd"]]
        except Exception:
            pass
        try:
            subprocess.run(cmd, timeout=60, check=False)
        except Exception as e:  # noqa: BLE001
            print(f"pairing page regen failed: {e}")

    # ------------------------------------------------------------- tunnel
    def _cli_tunnel(self, args: Any) -> None:
        """Cloudflare quick tunnels: bridge (WS) + update server; print the
        remote URLs and refresh the pairing page. URLs are ephemeral — rerun
        any time, and `hermes tether show` will pick them up automatically."""
        import re
        import shutil
        import subprocess
        import sys as _sys

        cfd = None
        for cand in (Path.home() / "tools" / "cloudflared.exe", shutil.which("cloudflared")):
            if cand and Path(str(cand)).exists():
                cfd = Path(str(cand))
                break
        if cfd is None:
            print("cloudflared not found — download from https://github.com/cloudflare/cloudflared/releases")
            print(f"(expected at {Path.home() / 'tools' / 'cloudflared.exe'})")
            return

        def wait_url(logpath: Path, prev: str, timeout: int = 90) -> str | None:
            import time as _time

            deadline = _time.time() + timeout
            url = None
            while _time.time() < deadline:
                try:
                    txt = logpath.read_text(encoding="utf-8", errors="replace")
                    for m in reversed(list(re.finditer(r"https://[a-z0-9-]+\.trycloudflare\.com", txt))):
                        url = m.group(0)
                except FileNotFoundError:
                    pass
                if url and url != prev:
                    return url
                _time.sleep(3)
            return None

        logdir = Path(self._data_dir())
        urls: dict[str, str] = {}
        for label, origin, logf in (
            ("bridge", f"http://127.0.0.1:{self.port}", logdir / "cloudflared_bridge.log"),
            ("upd", "http://127.0.0.1:8400", logdir / "cloudflared_upd.log"),
        ):
            try:
                logf.write_text("", encoding="utf-8")
            except OSError:
                continue
            # bind the log handle to a named var so it isn't left dangling
            # (and so a second tunnel spawn in the same loop gets a fresh one)
            fh = open(logf, "a", encoding="utf-8", errors="replace")
            subprocess.Popen(
                [str(cfd), "tunnel", "--url", origin, "--no-autoupdate"],
                stdout=fh,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            u = wait_url(logf, "")
            if u:
                urls[label] = u
        if not urls:
            print("tunnel(s) failed to register — check internet connectivity")
            return
        (logdir / "tunnels.json").write_text(json.dumps(urls, indent=2), encoding="utf-8")
        for k, v in urls.items():
            print(f"{k:>6} tunnel: {v}")
        if "bridge" in urls:
            print(f"\nREMOTE pairing:  wss://{urls['bridge'].split('https://', 1)[-1]}")
        self._regen_pairing_page()
        print("tunnels keep running in the background — rerun to get fresh URLs (they are ephemeral).")

    # ---------------------------------------------------------------- start
    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_loop, name="tether-server", daemon=True,
        )
        self._thread.start()
        # preload the whisper model in the background — first voice note
        # shouldn't pay the 2-5 s model-load spike (and the demo won't
        # stumble on it either)
        try:
            self.stt.warmup()
        except Exception:  # noqa: BLE001
            pass
        logger.info("tether: bridge listening on %s:%s", self.host, self.port)

    def stop(self) -> None:
        """Graceful teardown: stop the pump, close the WS server loop."""
        self.mouse.stop_pump()
        if self._loop and self._thread:
            try:
                self._loop.call_soon_threadsafe(lambda: None)
            except RuntimeError:
                pass
        self._thread = None

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            import websockets

            async def _serve() -> None:
                # websockets>=13 builds the server inside the running loop
                async with websockets.serve(self._handle, self.host, self.port):
                    await asyncio.Future()  # run until the process dies

            self._loop.run_until_complete(_serve())
        except Exception:
            logger.exception("tether: server thread crashed")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- conn
    async def _handle(self, ws: Any) -> None:
        # token gate: phone connects to ws://host:port/?token=***
        from urllib.parse import parse_qs, urlparse
        try:
            path = getattr(getattr(ws, "request", None), "path", "") or ""
            token = parse_qs(urlparse(path).query).get("token", [""])[0]
        except Exception:
            token = ""
        if token != self.token:
            logger.warning("tether: rejected unauthenticated phone from %s", ws.remote_address)
            await ws.close(code=4401, reason="bad token")
            return
        client = _Client(ws, ws.remote_address)
        self.clients[ws] = client
        logger.info("tether: phone connected from %s", ws.remote_address)
        try:
            await ws.send(json.dumps({
                "t": "hello_ok",
                "port": self.port,
                "token_ok": True,
                # sync the cursor gate state so a re-opened app (or a second
                # phone) immediately knows if the cursor is currently paused
                "paused": self.mouse._paused,
            }))
            async for message in ws:
                if isinstance(message, bytes):
                    self._on_audio(client, message)
                else:
                    await self._on_json(ws, client, message)
        except Exception:
            logger.debug("tether: client handler ended", exc_info=True)
        finally:
            self.clients.pop(ws, None)
            logger.info("tether: phone disconnected (%s)", ws.remote_address)

    # ------------------------------------------------------------ dispatch
    async def _on_json(self, ws: Any, client: _Client, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        kind = msg.get("t")

        if kind == "pad":
            self.mouse.queue_move(float(msg.get("dx", 0.0)), float(msg.get("dy", 0.0)))
        elif kind == "click":
            self.mouse.click(str(msg.get("b", "left")), swallow_wedge_clear=True)
        elif kind == "scroll":
            self.mouse.scroll(int(msg.get("dy", 0)))
        elif kind == "pause":
            self.mouse.pause()
            self._broadcast({"t": "state", "paused": True})
        elif kind == "resume":
            self.mouse.resume()
            self._broadcast({"t": "state", "paused": False})
        elif kind == "state_req":
            await self._send(ws, {
                "t": "state",
                "paused": not self.mouse.armed,
                "manual": self.mouse._paused,
                "clients": len(self.clients),
            })
        elif kind == "audio_done":
            audio = bytes(client.audio)
            client.audio = bytearray()
            client.audio_bytes = 0
            client.audio_started = False
            if not audio:
                await self._send(ws, {"t": "stt_error", "error": "no audio received"})
                return
            await self._send(ws, {"t": "stt_pending"})
            text = await asyncio.to_thread(self.stt.transcribe, audio)
            if not text:
                await self._send(ws, {
                    "t": "stt_error",
                    "error": f"heard nothing ({len(audio)} bytes of audio) — "
                             "speak a bit longer / closer to the mic and try again",
                })
                return
            key = resolve_session_key(self.settings, self.ctx)
            try:
                sent = bool(self.ctx.inject_message(text, "user", session_key=key))
            except Exception as exc:
                logger.warning("tether: injection failed: %s", exc)
                sent = False
            self.transcripts.append({
                "at": int(time.time()), "text": text, "sent": sent, "session_key": key,
            })
            self.transcripts[:] = self.transcripts[-TRANSCRIPT_RING:]
            await self._send(ws, {"t": "stt", "text": text, "sent": sent})
            logger.info("tether: voice note -> %s (%s)", text[:60], "sent" if sent else "not delivered")

    def _on_audio(self, client: _Client, frame: bytes) -> None:
        if client.audio_bytes + len(frame) > MAX_AUDIO_BYTES:
            return
        client.audio.extend(frame)
        client.audio_bytes += len(frame)
        client.audio_started = True

    # ------------------------------------------------------------- helpers
    @staticmethod
    async def _send(ws: Any, payload: Mapping[str, Any]) -> None:
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass

    def _broadcast(self, payload: Mapping[str, Any]) -> None:
        if not self._loop or not self.clients:
            return
        async def _do() -> None:
            for ws in list(self.clients):
                await self._send(ws, payload)
        asyncio.run_coroutine_threadsafe(_do(), self._loop)
