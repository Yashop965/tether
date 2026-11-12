"""Tether LAN bridge: WebSocket control channel + voice-to-session pipeline.

Protocol (JSON text frames + binary audio frames), phone -> host:
  {"t":"hello"}                      -> {"t":"hello_ok","port":...,"session":...}
  {"t":"pad","dx":0.02,"dy":-0.01}  relative cursor move (normalized deltas)
  {"t":"click","b":"left"}          click (left/right/middle)
  {"t":"scroll","dy":-3}            scroll
  {"t":"tilt","x":0.12,"y":-0.05}   continuous drift while tilt is active
  {"t":"tilt","active":false}       release
  {"t":"aim","x":0.5,"y":-0.3}     laser pointer: absolute screen aim (-1..1, 0,0=center)
  {"t":"audio_done"}                end of a binary audio stream
  {"t":"pause"} / {"t":"resume"}    manual cursor gate
Host -> phone:
  {"t":"stt","text":"...","sent":true}
  {"t":"stt_pending"}
  {"t":"stt_error","error":"..."}
  {"t":"state","paused":true,"idle_paused":true}

Fast input channel (UDP, port+1) — same semantics, ~9-byte packets, no TCP
head-of-line blocking; best over Wi-Fi/hotspot/WireGuard. Handshake first
datagram: "TTHR1" + token (text, UTF-8). Then, from that peer only:
  0x01 <ff> dx dy     pad relative move (normalized, same scale as WS)
  0x02 <ff> x y       aim absolute (-1..1, 0,0 = screen center)
  0x03 <B> b          click: 0=left 1=right 2=middle
  0x04 <h> dy        scroll
  0x05 <i> nonce     RTT probe — server echoes the packet back
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
        self.stt = Transcriber(model_size=str(settings.get("stt_model", "base")))
        self.clients: dict[Any, _Client] = {}
        self.transcripts: list[dict[str, Any]] = []
        self._loop: Any = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._tilt_active = False
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
        print("Fastest first: phone hotspot/USB-tether beats same LAN, which beats Tailscale.")
        print()
        for c in cands:
            print(f"  [{c['kind']:>11}] {c['ip']}   ({c['name']})")
        primary = cands[0]
        payload = f"tether://{primary['ip']}?port={self.port}&token={self.token}"
        print()
        print(f"Pairing URL:  {payload}")
        try:
            import qrcode

            img = qrcode.make(payload)
            img.print_ascii(invert=True)
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
        import subprocess

        check = subprocess.run(
            ["netsh", "wlan", "show", "drivers"], capture_output=True, text=True
        ).stdout.lower()
        if "hosted network: yes" not in check and "hosted network supported" not in check:
            print("this Wi-Fi adapter does not support a hosted network — use `hermes tether tunnel` instead")
            return

        ssid = f"Tether-{secrets.token_hex(2).upper()}"
        psk = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))
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
            subprocess.Popen(
                [str(cfd), "tunnel", "--url", origin, "--no-autoupdate"],
                stdout=open(logf, "a", encoding="utf-8", errors="replace"),
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
        try:
            self._udp_socket = self._make_udp_socket()
        except OSError as exc:
            logger.warning("tether: UDP fast channel unavailable (%s:8378)", exc)
            self._udp_socket = None
        if self._udp_socket is not None:
            threading.Thread(target=self._udp_loop, name="tether-udp", daemon=True).start()
        logger.info("tether: bridge listening on %s:%s (UDP fast channel on %s:%s)",
                    self.host, self.port, self.host, self.port + 1)

    # -------------------------------------------------------------- udp fast
    def _make_udp_socket(self):
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port + 1))
        sock.settimeout(1.0)
        return sock

    def _udp_loop(self) -> None:
        import socket
        import struct
        authed: dict = {}  # peer -> auth time
        UDP_TTL = 300.0
        while True:
            try:
                data, peer = self._udp_socket.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            now = time.time()
            for k in [k for k, t in authed.items() if now - t > UDP_TTL]:
                authed.pop(k, None)
            if data.startswith(b"TTHR1"):
                tok = data[5:].decode("utf-8", "replace").strip()
                if tok == self.token:
                    authed[peer] = now
                    logger.info("tether: udp auth from %s", peer)
                continue
            if peer not in authed or len(data) < 2:
                continue
            t, payload = data[0], data[1:]
            if t == 1 and len(payload) >= 8:
                dx, dy = struct.unpack("<ff", payload[:8])
                self.mouse.queue_move(dx, dy)
            elif t == 2 and len(payload) >= 8:
                x, y = struct.unpack("<ff", payload[:8])
                self.mouse.aim(x, y)
            elif t == 3:
                self.mouse.click(("left", "right", "middle")[payload[0] % 3])
            elif t == 4 and len(payload) >= 2:
                (dy,) = struct.unpack("<h", payload[:2])
                self.mouse.scroll(dy)
            elif t == 5:
                try:
                    self._udp_socket.sendto(data, peer)  # RTT echo
                except OSError:
                    pass

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
            self.mouse.click(str(msg.get("b", "left")))
        elif kind == "scroll":
            self.mouse.scroll(int(msg.get("dy", 0)))
        elif kind == "tilt":
            if msg.get("active") is False:
                self._tilt_active = False
            else:
                self._tilt_active = True
                self.mouse.queue_move(
                    float(msg.get("x", 0.0)) * 0.02 * self.mouse.sensitivity,
                    float(msg.get("y", 0.0)) * 0.02 * self.mouse.sensitivity,
                )
        elif kind == "aim":
            self.mouse.aim(float(msg.get("x", 0.0)), float(msg.get("y", 0.0)))
        elif kind == "pause":
            self.mouse.pause()
            await self._send(ws, {"t": "state", "paused": True})
        elif kind == "resume":
            self.mouse.resume()
            await self._send(ws, {"t": "state", "paused": False})
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
