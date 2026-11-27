"""Thread-safe local-only SQLite event storage."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .._version import __version__
from . import schema
from .identity import (
    _retention_setting,
    event_db_path,
    session_id,
)

EXPORT_SCHEMA = "mediascribe.local-events/v2"
_COMMON_COLUMNS = (
    "os_family",
    "hardware_tier",
    "is_first_run",
    "model_cache_state",
    "is_internal_test",
)


EventPrivacyError = schema.EventPrivacyError


class EventStore:
    """Thread-safe, local-only SQLite event store."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = Path(db_path) if db_path is not None else event_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.retention_days, self.retention_warning = _retention_setting()
        with self._conn(create=True) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            self._ensure_schema(conn)
            conn.commit()
        if self.retention_days == 0:
            self.clear()
        else:
            self.purge()

    def close(self) -> None:
        """Compatibility hook; this store uses short-lived SQLite connections."""

    @contextmanager
    def _conn(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        if create:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        else:
            conn = sqlite3.connect(
                f"{self._db_path.as_uri()}?mode=rw", uri=True, check_same_thread=False
            )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_identity (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS local_events (
                event_id TEXT PRIMARY KEY,
                event TEXT NOT NULL,
                ts REAL NOT NULL,
                install_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                surface TEXT NOT NULL,
                version TEXT NOT NULL,
                os_family TEXT NOT NULL DEFAULT 'unknown',
                hardware_tier TEXT NOT NULL DEFAULT 'unknown',
                is_first_run INTEGER NOT NULL DEFAULT 0,
                model_cache_state TEXT NOT NULL DEFAULT 'unknown',
                is_internal_test INTEGER NOT NULL DEFAULT 0,
                properties TEXT NOT NULL
            )
            """
        )
        existing = {str(row["name"]) for row in conn.execute("PRAGMA table_info(local_events)")}
        column_sql = {
            "os_family": "ALTER TABLE local_events ADD COLUMN os_family TEXT NOT NULL DEFAULT 'unknown'",
            "hardware_tier": (
                "ALTER TABLE local_events ADD COLUMN hardware_tier TEXT NOT NULL DEFAULT 'unknown'"
            ),
            "is_first_run": (
                "ALTER TABLE local_events ADD COLUMN is_first_run INTEGER NOT NULL DEFAULT 0"
            ),
            "model_cache_state": (
                "ALTER TABLE local_events ADD COLUMN model_cache_state TEXT NOT NULL DEFAULT 'unknown'"
            ),
            "is_internal_test": (
                "ALTER TABLE local_events ADD COLUMN is_internal_test INTEGER NOT NULL DEFAULT 0"
            ),
        }
        for column in _COMMON_COLUMNS:
            if column not in existing:
                conn.execute(column_sql[column])
        conn.execute("CREATE INDEX IF NOT EXISTS idx_local_events_ts ON local_events(ts)")

    def _install_id(self, conn: sqlite3.Connection) -> str:
        row = conn.execute("SELECT value FROM event_identity WHERE key = 'install_id'").fetchone()
        if row is not None:
            return str(row["value"])
        value = uuid.uuid4().hex
        conn.execute("INSERT INTO event_identity(key, value) VALUES ('install_id', ?)", (value,))
        conn.commit()
        return value

    def install_id(self) -> str:
        with self._lock, self._conn(create=True) as conn:
            return self._install_id(conn)

    def first_run_completed(self) -> bool:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM event_identity WHERE key = 'first_run_completed'"
            ).fetchone()
            return row is not None

    def _mark_first_run_completed(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO event_identity(key, value) VALUES ('first_run_completed', '1')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )

    @staticmethod
    def _context_for(
        context: Optional[Dict[str, Any]],
        *,
        event: str,
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        if context is not None:
            resolved = dict(context)
        else:
            resolved = schema.common_context(
                event=event,
                engine=properties.get("engine"),
                model=properties.get("model"),
            )
        if event == "first_run_started":
            resolved["is_first_run"] = True
        missing = [column for column in _COMMON_COLUMNS if column not in resolved]
        if missing:
            raise ValueError(f"event context is missing columns: {', '.join(missing)}")
        resolved_context = {
            "os_family": str(resolved["os_family"]),
            "hardware_tier": str(resolved["hardware_tier"]),
            "is_first_run": bool(resolved["is_first_run"]),
            "model_cache_state": str(resolved["model_cache_state"]),
            "is_internal_test": bool(resolved["is_internal_test"]),
        }
        allowed_values = {
            "os_family": schema.OS_FAMILIES,
            "hardware_tier": schema.HARDWARE_TIERS,
            "model_cache_state": schema.MODEL_CACHE_STATES,
        }
        for column, allowed in allowed_values.items():
            if resolved_context[column] not in allowed:
                raise ValueError(f"invalid event context {column}: {resolved_context[column]!r}")
        return resolved_context

    def _insert(
        self,
        conn: sqlite3.Connection,
        *,
        event: str,
        surface: str,
        properties: Dict[str, Any],
        context: Dict[str, Any],
        timestamp: float,
    ) -> None:
        validated_properties = schema.validate_properties(properties)
        if set(validated_properties) - schema.ALLOWED_PROPERTIES:
            raise EventPrivacyError("event property is outside the local privacy whitelist")
        install = self._install_id(conn)
        conn.execute(
            """
            INSERT INTO local_events(
                event_id, event, ts, install_id, session_id, surface, version,
                os_family, hardware_tier, is_first_run, model_cache_state,
                is_internal_test, properties
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                event,
                timestamp,
                install,
                session_id(),
                surface,
                __version__,
                context["os_family"],
                context["hardware_tier"],
                int(context["is_first_run"]),
                context["model_cache_state"],
                int(context["is_internal_test"]),
                json.dumps(validated_properties, ensure_ascii=False, sort_keys=True),
            ),
        )

    def record(
        self,
        event: str,
        *,
        surface: str = "cli",
        properties: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> bool:
        return self.record_event(event, surface=surface, properties=properties, now=now)

    def record_event(
        self,
        event: str,
        *,
        surface: str = "cli",
        properties: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> bool:
        if self.retention_days == 0:
            return False
        event_name = schema.validate_event(event)
        safe_properties = dict(properties or {})
        timestamp = time.time() if now is None else float(now)
        resolved_context = self._context_for(
            context,
            event=event_name,
            properties=safe_properties,
        )
        with self._lock, self._conn(create=True) as conn:
            self._insert(
                conn,
                event=event_name,
                surface=surface,
                properties=safe_properties,
                context=resolved_context,
                timestamp=timestamp,
            )
            if event_name == "first_run_started":
                self._mark_first_run_completed(conn)
            conn.commit()
        return True

    def record_first_run(self, *, surface: str = "cli", now: Optional[float] = None) -> bool:
        if self.retention_days == 0:
            return False
        timestamp = time.time() if now is None else float(now)
        context = schema.common_context(event="first_run_started")
        with self._lock, self._conn(create=True) as conn:
            marker = conn.execute(
                "SELECT value FROM event_identity WHERE key = 'first_run_completed'"
            ).fetchone()
            if marker is not None:
                return False
            self._insert(
                conn,
                event="first_run_started",
                surface=surface,
                properties={},
                context=context,
                timestamp=timestamp,
            )
            self._mark_first_run_completed(conn)
            conn.commit()
        return True

    def list_events(self, *, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    event_id, event, ts, install_id, session_id, surface, version,
                    os_family, hardware_tier, is_first_run, model_cache_state,
                    is_internal_test, properties
                FROM local_events
                ORDER BY ts DESC, event_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["is_first_run"] = bool(item["is_first_run"])
            item["is_internal_test"] = bool(item["is_internal_test"])
            item["properties"] = json.loads(item["properties"])
            result.append(item)
        return result

    def summary(self) -> Dict[str, Any]:
        rows = self.list_events(limit=100_000)
        counts: Dict[str, int] = {}
        surfaces: Dict[str, int] = {}
        for row in rows:
            counts[row["event"]] = counts.get(row["event"], 0) + 1
            surfaces[row["surface"]] = surfaces.get(row["surface"], 0) + 1
        return {
            "schema": EXPORT_SCHEMA,
            "install_id": self.install_id(),
            "session_id": session_id(),
            "retention_days": self.retention_days,
            "retention_warning": self.retention_warning,
            "event_count": len(rows),
            "counts_by_event": counts,
            "counts_by_surface": surfaces,
        }

    def export_payload(self) -> Dict[str, Any]:
        summary = self.summary()
        return {
            "schema": EXPORT_SCHEMA,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "install_id": summary["install_id"],
            "retention_days": summary["retention_days"],
            "events": self.list_events(limit=100_000),
        }

    def export(self, output_path: Path) -> Path:
        from ..atomic_io import atomic_write_text

        target = Path(output_path)
        atomic_write_text(target, json.dumps(self.export_payload(), ensure_ascii=False, indent=2))
        return target

    def purge(self, *, now: Optional[float] = None) -> int:
        if self.retention_days <= 0:
            return 0
        cutoff = (time.time() if now is None else float(now)) - self.retention_days * 86400
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM local_events WHERE ts <= ?", (cutoff,))
            conn.commit()
            return max(0, cur.rowcount)

    def clear(self) -> int:
        with self._lock, self._conn(create=True) as conn:
            cur = conn.execute("DELETE FROM local_events")
            conn.commit()
            return max(0, cur.rowcount)
