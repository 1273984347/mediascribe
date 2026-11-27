"""User-level identity, process session, and retention settings."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_RETENTION_DAYS = 90

_SESSION_ID: Optional[str] = None


def _app_data_dir() -> Path:
    override = os.environ.get("MEDIASCRIBE_APP_DATA", "").strip()
    if override:
        return Path(override)
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if local:
            return Path(local)
    return Path.home() / ".local" / "share"


def event_db_path() -> Path:
    override = os.environ.get("MEDIASCRIBE_EVENT_DB", "").strip()
    if override:
        return Path(override)
    return _app_data_dir() / "MediaScribe" / "events.sqlite3"


def session_id() -> str:
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = uuid.uuid4().hex
    return _SESSION_ID


def _retention_setting() -> tuple[int, Optional[str]]:
    raw = os.environ.get("MEDIASCRIBE_EVENT_RETENTION_DAYS", "").strip()
    if not raw:
        return DEFAULT_RETENTION_DAYS, None
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_DAYS, "EVENT_RETENTION_INVALID"
    if value < 0:
        return DEFAULT_RETENTION_DAYS, "EVENT_RETENTION_INVALID"
    return value, None


def event_retention_status() -> Dict[str, Any]:
    days, warning = _retention_setting()
    return {"days": days, "enabled": days > 0, "warning": warning}
