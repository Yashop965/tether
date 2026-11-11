"""Tether — your phone as a remote trackpad, tilt-mouse, and voice terminal.

register(ctx):
  * starts the LAN bridge (WebSocket) in a daemon thread,
  * exposes the ``tether_status`` tool (agent sees phone state / transcripts),
  * exposes ``/tether-pause`` and ``/tether-status`` in-session commands,
  * exposes the CLI:
      hermes tether show     — pairing targets + token + QR (+ live cloud URLs)
      hermes tether hotspot  — start the Windows hotspot for phone auto-join
      hermes tether tunnel   — Cloudflare quick tunnels (remote/any-network mode)
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from .server import TetherServer

logger = logging.getLogger(__name__)


def _settings(ctx) -> Mapping[str, Any]:
    """Read this plugin's config keys through the documented ``ctx.get_config``
    (``plugins.entries.<plugin_id>.settings`` subtree, per key)."""
    out: dict[str, Any] = {}
    for key in ("disabled", "allow_gateway_injection", "token", "host",
                "port", "stt_model", "idle_seconds", "session_key"):
        try:
            value = ctx.get_config(key, None)
        except Exception:
            value = None
        if value is not None:
            out[key] = value
    return out


def _gen_token(settings: Mapping[str, Any], ctx) -> str:
    """Settings token wins; otherwise mint one into the plugin data dir."""
    token = str(settings.get("token") or "").strip()
    if token:
        return token
    import secrets
    token = secrets.token_urlsafe(16)
    try:
        data_dir = ctx.plugin_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "token").write_text(token)
    except Exception:
        logger.debug("tether: could not persist generated token", exc_info=True)
    return token


def register(ctx) -> None:
    settings = _settings(ctx)
    if settings.get("disabled"):
        logger.info("tether: disabled by settings")
        return
    if settings.get("allow_gateway_injection") is not True:
        # voice-to-session needs this; warn, don't block (mouse still works)
        logger.warning(
            "tether: set plugins.entries.%s.allow_gateway_injection: true "
            "to enable voice notes landing in sessions", ctx.plugin_id,
        )

    server = TetherServer(ctx, settings, _gen_token(settings, ctx))
    server.register_with_ctx(ctx)
    server.start()
