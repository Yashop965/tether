"""Pick which session voice notes land in."""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def resolve_session_key(settings: Mapping[str, Any], ctx: Any) -> Optional[str]:
    """Settings key wins; 'active' resolves to the most recently active local
    session in the plugin's profile state.db; otherwise None (keyless: the
    live REPL / TUI host picks its own session)."""
    key = str(settings.get("session_key") or "active").strip()
    if key.lower() != "active":
        return key
    try:
        from hermes_constants import get_hermes_home
        db_path = get_hermes_home() / "state.db"
    except Exception:
        return None
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "select session_key, id from sessions "
                "where coalesce(archived, 0) = 0 "
                "order by last_activity_at desc limit 1"
            ).fetchone()
        finally:
            con.close()
        if not row:
            return None
        return row[0] or row[1]
    except Exception:
        logger.debug("tether: active-session resolution failed", exc_info=True)
        return None
