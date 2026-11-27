"""Immutable durable-job events and checkpoint conflicts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JobEvent:
    event_id: str
    job_id: str
    attempt_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: float

    def encoded_payload(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CheckpointConflict(RuntimeError):
    """The same event or segment key was reused with different content."""
