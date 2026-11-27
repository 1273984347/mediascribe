"""First-use event recorder with local-only identity and safe classifications."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import schema
from .identity import event_retention_status
from .store import EventStore


class EventRecorder:
    """Record the six US-013 core events without leaving the local machine."""

    def __init__(self, *, store: Optional[EventStore] = None, surface: str = "cli"):
        self._store = store
        self.surface = surface
        self._recorded_readiness_surfaces: set[str] = set()

    @property
    def store(self) -> EventStore:
        if self._store is None:
            self._store = EventStore()
        return self._store

    def _disabled(self) -> bool:
        return schema.is_internal_test() or not event_retention_status()["enabled"]

    def _ensure_first_run(self, surface: Optional[str] = None) -> bool:
        if self._disabled():
            return False
        return self.store.record_first_run(surface=surface or self.surface)

    def record_first_run(self, *, surface: Optional[str] = None) -> bool:
        if self._disabled():
            return False
        target_surface = surface or self.surface
        return self.store.record_first_run(surface=target_surface)

    def _record(
        self,
        event: str,
        *,
        surface: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        engine: Optional[str] = None,
        model: Optional[str] = None,
        cache_state: Optional[str] = None,
        is_first_run: bool = False,
        now: Optional[float] = None,
    ) -> bool:
        if self._disabled():
            return False
        if event not in schema.CORE_EVENTS:
            raise ValueError(f"unsupported first-use event: {event}")
        context = schema.common_context(
            engine=engine,
            model=model,
            cache_state=cache_state,
            is_first_run=is_first_run,
        )
        context["schema.os_family"] = schema.os_family()
        context["schema.hardware_tier"] = schema.hardware_tier()
        return self.store.record_event(
            event,
            surface=surface or self.surface,
            properties=properties,
            context=context,
            now=now,
        )

    def record_readiness(
        self,
        *,
        status: str,
        surface: Optional[str] = None,
        exit_code: Optional[int] = None,
        blockers: Optional[List[str]] = None,
        warnings: Optional[List[str]] = None,
        engine: Optional[str] = None,
        cache_state: Optional[str] = None,
        force: bool = False,
        dedupe: bool = True,
        now: Optional[float] = None,
    ) -> bool:
        schema.validate_enum(status, schema.READINESS_STATUSES, "readiness status")
        target_surface = surface or self.surface
        if dedupe and not force and target_surface in self._recorded_readiness_surfaces:
            return False
        self._ensure_first_run(target_surface)
        properties: Dict[str, Any] = {
            "status": status,
            "blocker_codes": list(blockers or []),
            "warning_codes": list(warnings or []),
        }
        if exit_code is not None:
            properties["exit_code"] = int(exit_code)
        if engine is not None:
            properties["engine"] = engine
        if cache_state is not None:
            properties["cache_state"] = cache_state
        recorded = self._record(
            "readiness_checked",
            surface=target_surface,
            properties=properties,
            engine=engine,
            cache_state=cache_state,
            now=now,
        )
        if recorded:
            self._recorded_readiness_surfaces.add(target_surface)
        return recorded

    def record_input(
        self,
        *,
        input_value: Any,
        selection_mode: str = "auto",
        engine: Optional[str] = None,
        model: Optional[str] = None,
        device: Optional[str] = None,
        task_id: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> bool:
        if selection_mode not in {"auto", "explicit"}:
            raise ValueError(f"unsupported selection mode: {selection_mode!r}")
        self._ensure_first_run()
        input_kind = schema.classify_input_kind(input_value)
        seconds = duration_seconds
        if seconds is None and input_kind in {"local_audio", "local_video"}:
            try:
                seconds = schema.probe_duration_seconds(Path(str(input_value)))
            except (OSError, ValueError):
                seconds = None
        properties: Dict[str, Any] = {
            "input_kind": input_kind,
            "duration_bucket": schema.duration_bucket(seconds),
            "selection_mode": selection_mode,
            "network_required": input_kind in {"platform_url", "generic_url"},
        }
        if engine is not None:
            properties["engine"] = engine
        if model is not None:
            properties["model"] = model
        if device is not None:
            properties["device"] = device
        if task_id is not None:
            properties["task_id"] = task_id
        return self._record(
            "input_submitted",
            properties=properties,
            engine=engine,
            model=model,
            now=now,
        )

    def record_finished(
        self,
        *,
        result: str,
        task_id: Optional[str] = None,
        error_code: Optional[str] = None,
        stage: Optional[str] = None,
        engine: Optional[str] = None,
        model: Optional[str] = None,
        device: Optional[str] = None,
        artifact_exists: Optional[bool] = None,
        text_nonempty: Optional[bool] = None,
        metadata_consistent: Optional[bool] = None,
        surface: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        normalized = schema.validate_enum(result, schema.FINISHED_RESULTS, "transcription result")
        self._ensure_first_run(surface)
        properties: Dict[str, Any] = {"result": normalized}
        if task_id is not None:
            properties["task_id"] = task_id
        if error_code is not None:
            properties["error_code"] = error_code
        if stage is not None:
            properties["stage"] = stage
        if engine is not None:
            properties["engine"] = engine
        if model is not None:
            properties["model"] = model
        if device is not None:
            properties["device"] = device
        if normalized == "success":
            flags = {
                "artifact_exists": True if artifact_exists is None else bool(artifact_exists),
                "text_nonempty": True if text_nonempty is None else bool(text_nonempty),
                "metadata_consistent": (
                    True if metadata_consistent is None else bool(metadata_consistent)
                ),
            }
            if not all(flags.values()):
                raise ValueError("success requires artifact, text, and metadata validation")
            properties.update(flags)
        return self._record(
            "transcription_finished",
            surface=surface,
            properties=properties,
            engine=engine,
            model=model,
            now=now,
        )

    def record_artifact(
        self,
        *,
        action: str,
        task_id: Optional[str] = None,
        surface: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        normalized_action = schema.validate_enum(action, schema.ARTIFACT_ACTIONS, "artifact action")
        self._ensure_first_run(surface)
        properties: Dict[str, Any] = {"action": normalized_action}
        if task_id is not None:
            properties["task_id"] = task_id
        return self._record(
            "artifact_opened",
            surface=surface,
            properties=properties,
            now=now,
        )

    def record_recovery(
        self,
        *,
        action: str,
        task_id: Optional[str] = None,
        retry_task_id: Optional[str] = None,
        error_code: Optional[str] = None,
        surface: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        normalized_action = schema.validate_enum(action, schema.RECOVERY_ACTIONS, "recovery action")
        self._ensure_first_run(surface)
        properties: Dict[str, Any] = {"action": normalized_action}
        if error_code is not None:
            properties["error_code"] = error_code
        if task_id is not None:
            properties["task_id"] = task_id
        if retry_task_id is not None:
            properties["retry_task_id"] = retry_task_id
        return self._record(
            "recovery_action_selected",
            surface=surface,
            properties=properties,
            now=now,
        )
