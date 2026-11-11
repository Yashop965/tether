"""Host cursor control with safety gating (idle auto-pause, manual pause)."""
from __future__ import annotations

import sys
import threading
import time

try:
    from pynput import mouse as _pm
    _HAVE_PINPUT = True
except Exception:  # pynput missing (e.g. bare CI) — cursor moves become no-ops
    _HAVE_PINPUT = False

_IS_WIN = sys.platform == "win32"


def _virtual_screen_size() -> tuple[int, int]:
    """Virtual screen size in pixels (Windows multi-monitor aware)."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetSystemMetrics.restype = ctypes.c_int
        return user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)  # VSM_XSIZE/YSIZE
    except Exception:
        return 1920, 1080


def _virtual_screen_origin() -> tuple[int, int]:
    """Top-left of the virtual screen (can be negative with a left monitor)."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetSystemMetrics.restype = ctypes.c_int
        return user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)  # VSM_X/Y
    except Exception:
        return 0, 0


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


class MouseDriver:
    """Relative-control wrapper over pynput with an armed/paused gate.

    dx/dy are normalized screen fractions (-1..1); they are converted to the
    platform's coordinate space (pixels on Windows, 0..1 on macOS)."""

    def __init__(self, sensitivity: float = 1.0, idle_seconds: int = 25) -> None:
        self.sensitivity = float(sensitivity)
        self.idle_seconds = int(idle_seconds)
        self._lock = threading.Lock()
        self._paused = False
        self._last_event = time.monotonic()
        self._ctrl = _pm.Controller() if _HAVE_PINPUT else None
        self._pending_x = 0.0
        self._pending_y = 0.0
        self._pump_stop = threading.Event()
        self._pump = threading.Thread(target=self._pump_loop, name="tether-mouse-pump", daemon=True)
        if _HAVE_PINPUT:
            self._pump.start()
        if _IS_WIN and _HAVE_PINPUT:
            vw, vh = _virtual_screen_size()
            self._vsize = (vw, vh)
            self._vorigin = _virtual_screen_origin()
        else:
            self._vsize = (0, 0)
            self._vorigin = (0, 0)

    # ------------------------------------------------------------------ pump
    # Upstream (UDP/WS/relay) delivers deltas in bursts at whatever rate the
    # network allows. The pump drains that backlog at 60 Hz in small substeps,
    # so a chunky 250 ms relayed arrival still reads as smooth motion instead
    # of a jump.
    DRAIN_PER_TICK = 0.04  # normalized screen fraction per 60 Hz tick

    def queue_move(self, dx: float, dy: float) -> None:
        with self._lock:
            if self._paused:
                return  # manual pause: drop, so resuming never fires a burst
            # cap backlog so a huge stall can't launch the cursor off-screen
            self._pending_x = max(-0.5, min(0.5, self._pending_x + dx))
            self._pending_y = max(-0.5, min(0.5, self._pending_y + dy))
            self._last_event = time.monotonic()

    def _pump_loop(self) -> None:
        while not self._pump_stop.is_set():
            time.sleep(1.0 / 60.0)
            with self._lock:
                if self._paused:
                    self._pending_x = self._pending_y = 0.0
                    continue
                px, py = self._pending_x, self._pending_y
                if px == 0.0 and py == 0.0:
                    continue
                denom = max(abs(px), abs(py), 1e-9)
                scale = min(1.0, self.DRAIN_PER_TICK / denom)
                sx, sy = px * scale, py * scale
                self._pending_x, self._pending_y = px - sx, py - sy
            self._apply_delta(sx, sy)

    def _apply_delta(self, dx: float, dy: float) -> None:
        """Apply one (small) normalized delta with clamping; no gate check —
        queue_move already validated it, and pump clears paused backlogs."""
        if not self._ctrl:
            return
        pos = self._ctrl.position
        if _IS_WIN:
            vw, vh = self._vsize or (1920, 1080)
            ox, oy = self._vorigin
            x = float(pos[0]) + dx * self.sensitivity * vw
            y = float(pos[1]) + dy * self.sensitivity * vh
            x = max(ox, min(ox + vw - 1, x))
            y = max(oy, min(oy + vh - 1, y))
        else:
            x = _clamp(float(pos[0]) + dx * self.sensitivity)
            y = _clamp(float(pos[1]) + dy * self.sensitivity)
        self._ctrl.position = (x, y)

    def stop_pump(self) -> None:
        self._pump_stop.set()

    # -- gate ---------------------------------------------------------------
    @property
    def armed(self) -> bool:
        with self._lock:
            return not self._paused and not (
                self.idle_seconds and time.monotonic() - self._last_event > self.idle_seconds
            )

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._last_event = time.monotonic()

    def note_event(self) -> None:
        with self._lock:
            self._last_event = time.monotonic()

    # -- actions (no-op unless armed) ----------------------------------------
    def _gate(self) -> bool:
        """True iff the cursor may act on this event.

        Two pause classes with different semantics:

        * manual pause (``pause`` command / fingerprint toggle) — STICKY:
          no incoming event re-arms it; only an explicit ``resume`` does.
        * idle auto-pause — WEDGED: cleared by the next event. An event can
          only arrive because the phone is being actively touched, so the
          sender is present by construction; waiting for an explicit resume
          would make the trackpad feel dead for 25s after any idle gap.
        """
        with self._lock:
            if self._paused:
                return False
            self._last_event = time.monotonic()
            return True

    def move(self, dx: float, dy: float) -> None:
        if not self._ctrl or not self._gate():
            return
        pos = self._ctrl.position
        if _IS_WIN:
            # pynput Windows: pixel coords in virtual-screen space; dx/dy are
            # normalized fractions of that space. Clamp so an out-of-bounds
            # absolute set can't wrap the cursor to a corner.
            vw, vh = self._vsize or (1920, 1080)
            ox, oy = self._vorigin
            x = float(pos[0]) + dx * self.sensitivity * vw
            y = float(pos[1]) + dy * self.sensitivity * vh
            x = max(ox, min(ox + vw - 1, x))
            y = max(oy, min(oy + vh - 1, y))
        else:
            # macOS reports normalized [0, 1]
            x = _clamp(float(pos[0]) + dx * self.sensitivity)
            y = _clamp(float(pos[1]) + dy * self.sensitivity)
        self._ctrl.position = (x, y)

    def click(self, button: str = "left") -> None:
        if not self._ctrl or not self._gate():
            return
        btn = {
            "left": _pm.Button.left if hasattr(_pm, "Button") else None,
            "right": _pm.Button.right if hasattr(_pm, "Button") else None,
            "middle": _pm.Button.middle if hasattr(_pm, "Button") else None,
        }.get(button)
        if btn is None:
            return
        self._ctrl.click(btn)

    def scroll(self, dy: int) -> None:
        if not self._ctrl or not self._gate():
            return
        self._ctrl.scroll(0, int(dy))

    def aim(self, x: float, y: float) -> None:
        """Absolute aim (laser-pointer mode): x/y in -1..1, (0,0) = screen center.
        Treated like any active event: clears the idle-pause wedge."""
        if not self._ctrl or not self._gate():
            return
        fx = (max(-1.0, min(1.0, x)) + 1.0) / 2.0
        fy = (max(-1.0, min(1.0, y)) + 1.0) / 2.0
        if _IS_WIN:
            vw, vh = self._vsize or (1920, 1080)
            ox, oy = self._vorigin
            self._ctrl.position = (ox + fx * (vw - 1), oy + fy * (vh - 1))
        else:
            self._ctrl.position = (fx, fy)
