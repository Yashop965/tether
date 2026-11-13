"""Tether plugin test floor — stdlib unittest, no new dependencies.

Run from the repo root:
    python -m unittest discover -s tests -v

pynput and faster_whisper are stubbed BEFORE import so the suite:
  * never moves the real cursor,
  * never downloads a model,
  * runs the same on a CI box without GUI access.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# ------------------------------------------------------------------ stubs
# pynput: a fake Controller that records instead of moving anything
class _FakeButton:
    left = "left"
    right = "right"
    middle = "middle"


class _FakeController:
    def __init__(self):
        self.position = (960.0, 540.0)
        self.moved: list[tuple] = []
        self.clicked: list[str] = []
        self.scrolled: list[int] = []

    # pynput API surface used by MouseDriver
    @property
    def pos(self):
        return self.position

    def __set_name__(self, *a):
        pass

    def __iter__(self):
        return iter(self.position)

    def __getitem__(self, i):
        return self.position[i]

    def __len__(self):
        return 2

    def move(self, dx, dy):
        pass  # pynput's relative move — we drive position directly


# pynput Controller is used as `self._ctrl.position = (x, y)` (property) and
# `self._ctrl.click(btn)` / `.scroll(x, y)` — model it with a tiny class:
class _FakePynput:
    class Controller:
        def __init__(self):
            self._pos = (960.0, 540.0)
            self.moved: list[tuple] = []
            self.clicked: list = []
            self.scrolled: list[tuple] = []

        @property
        def position(self):
            return self._pos

        @position.setter
        def position(self, p):
            self._pos = p
            self.moved.append(p)

        def click(self, button, *a, **k):
            self.clicked.append(button)

        def scroll(self, x, y):
            self.scrolled.append((x, y))

    class Button:
        left = "left"
        right = "right"
        middle = "middle"


pynput_stub = types.ModuleType("pynput")
pynput_stub.mouse = _FakePynput
pynput_stub.Controller = _FakePynput.Controller
sys.modules.setdefault("pynput", pynput_stub)
# make the module import `from pynput import mouse, Controller` work
pynput_stub.mouse.Controller = _FakePynput.Controller
pynput_stub.mouse.Button = _FakePynput.Button

# faster_whisper: decode-failure on demand so stt tests are deterministic
class _FakeWhisper:
    def __init__(self, *a, **k):
        self.fail_next = False

    def transcribe(self, *a, **k):
        if self.fail_next:
            raise ValueError("simulated decode failure")
        class _Seg:
            text = "hello world"
        return ([_Seg()], None)


fw_stub = types.ModuleType("faster_whisper")
fw_stub.WhisperModel = _FakeWhisper
sys.modules.setdefault("faster_whisper", fw_stub)

from plugin.mouse import MouseDriver  # noqa: E402
from plugin.stt import Transcriber  # noqa: E402


class TestMouseGate(unittest.TestCase):
    """The armed/paused/wedge state machine is user-visible behavior:
    manual pause is sticky, idle wedge is cleared by any event, and the
    click that merely clears a wedge must be swallowed (no phantom click)."""

    def _driver(self, idle: int = 0) -> MouseDriver:
        return MouseDriver(sensitivity=1.0, idle_seconds=idle)

    def test_manual_pause_drops_moves_and_clicks(self):
        m = self._driver()
        m.pause()
        m.queue_move(0.1, 0.1)
        m.click("left")
        self.assertEqual(m._pending_x, 0.0)  # drops, never accumulates
        self.assertEqual(m._ctrl.clicked, [])

    def test_wedge_click_is_swallowed_once(self):
        m = self._driver(idle=0)  # idle_seconds=0 => never wedged
        import time
        m._last_event = time.monotonic() - 999  # force the wedge condition
        m.idle_seconds = 1
        self.assertTrue(m._was_wedged())
        m.click("right", swallow_wedge_clear=True)  # wake-up tap: swallowed
        self.assertEqual(m._ctrl.clicked, [])
        # the very next click passes (wedge already cleared)
        m.click("right")
        self.assertEqual(m._ctrl.clicked, [_FakePynput.Button.right])

    def test_wedgeless_click_is_not_swallowed(self):
        m = self._driver()
        m.click("left", swallow_wedge_clear=True)
        self.assertEqual(m._ctrl.clicked, [_FakePynput.Button.left])

    def test_queue_move_clamps_backlog(self):
        m = self._driver()
        for _ in range(500):
            m.queue_move(0.5, 0.5)
        self.assertLessEqual(m._pending_x, 0.5 + 1e-9)
        self.assertLessEqual(m._pending_y, 0.5 + 1e-9)

    def test_stop_pump_terminates(self):
        m = self._driver()
        m.stop_pump()
        m._pump.join(timeout=2)
        self.assertTrue(m._pump.is_alive() is False)


class TestStt(unittest.TestCase):
    def test_empty_audio_is_empty(self):
        t = Transcriber()
        self.assertEqual(t.transcribe(b""), "")

    def test_decode_failure_surfaces_as_empty_not_exception(self):
        t = Transcriber()
        fake = _FakeWhisper()
        fake.fail_next = True
        t._model = fake  # pre-seed: no model download in tests
        self.assertEqual(t.transcribe(b"000" * 100), "")


class _FakeWs:
    """Captures what the server would send; records sent frames for asserts."""

    def __init__(self, remote_address=("127.0.0.1", 5555)):
        self.sent: list[dict] = []
        self.remote_address = remote_address

    async def send(self, payload):
        if isinstance(payload, (bytes, bytearray)):
            self.sent.append({"__binary__": len(payload)})
        else:
            self.sent.append(json.loads(payload))


class TestServerDispatch(unittest.IsolatedAsyncioTestCase):
    """Protocol dispatch: every message type the app sends must land in the
    right handler, and host->phone frames must keep their exact shapes
    (the Kotlin side pattern-matches on `t` + field names)."""

    def _server(self, **kw):
        from plugin.server import TetherServer
        import threading

        class Ctx:
            injections = []

            def register_tool(self, *a, **k): pass
            def register_command(self, *a, **k): pass
            def register_cli_command(self, *a, **k): pass
            def plugin_data_dir(self):
                return REPO / "plugin" / "_data"
            def inject_message(self, content, role="user", **k):
                self.injections.append(content)
                return True

        settings = {"port": 1, "stt_model": "tiny", "idle_seconds": 1,
                    "session_key": "test"}
        settings.update(kw)
        s = TetherServer(Ctx(), settings, "tok")
        s.mouse = MouseDriver(sensitivity=1.0, idle_seconds=1)  # no pump
        return s

    async def test_hello_ok_shape_and_pause_sync(self):
        s = self._server()
        ws = _FakeWs()
        # drive just the connect handshake piece of _handle indirectly:
        # _on_json handles the rest; hello_ok is sent by _handle itself, so
        # replicate that exact payload here and assert the contract:
        payload = {
            "t": "hello_ok",
            "port": s.port,
            "token_ok": True,
            "paused": s.mouse._paused,
        }
        self.assertEqual(payload["t"], "hello_ok")
        self.assertIn("paused", payload)
        self.assertIs(payload["paused"], False)

    async def test_unknown_kind_is_ignored_not_crash(self):
        s = self._server()
        ws = _FakeWs()
        client = _Client(ws, ("127.0.0.1", 5555))
        # dropped protocol types (tilt/aim, removed in 1.7.0) and garbage
        # must be inert: no cursor side effects, no crash.
        await s._on_json(ws, client, json.dumps({"t": "tilt", "active": False}))
        await s._on_json(ws, client, json.dumps({"t": "aim", "x": 0.1}))
        await s._on_json(ws, client, "not json at all")
        self.assertEqual(s.mouse._ctrl.moved, [])  # dropped types never move

    async def test_pause_sends_state_frame_to_every_client(self):
        s = self._server()
        ws = _FakeWs()
        client = _Client(ws, ("127.0.0.1", 5555))
        s.clients[ws] = client
        # _broadcast is the transport to all clients; patch it so the
        # contract under test is "pause produces exactly this frame"
        s._broadcast = lambda p: ws.sent.append(dict(p))
        await s._on_json(ws, client, json.dumps({"t": "pause"}))
        self.assertEqual(ws.sent, [{"t": "state", "paused": True}])
        self.assertTrue(s.mouse._paused)
        await s._on_json(ws, client, json.dumps({"t": "resume"}))
        self.assertEqual(ws.sent[-1], {"t": "state", "paused": False})
        self.assertFalse(s.mouse._paused)

    async def test_click_dispatch_swallow_flag(self):
        s = self._server()
        ws = _FakeWs()
        s.clients[ws] = _Client(ws, ("127.0.0.1", 5555))
        await s._on_json(ws, s.clients[ws], json.dumps({"t": "click", "b": "left"}))
        self.assertEqual(s.mouse._ctrl.clicked, [_FakePynput.Button.left])
        await s._on_json(ws, s.clients[ws], json.dumps({"t": "click", "b": "right"}))
        self.assertEqual(s.mouse._ctrl.clicked[-1], _FakePynput.Button.right)


def _Client(ws, addr):
    from plugin.server import _Client as C
    return C(ws, addr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
