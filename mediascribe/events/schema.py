"""Event schemas, privacy validation, and low-cost context probes."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

_CORE_EVENT_NAMES = (
    "first_run_started",
    "readiness_checked",
    "input_submitted",
    "transcription_finished",
    "artifact_opened",
    "recovery_action_selected",
)
CORE_EVENTS = frozenset(_CORE_EVENT_NAMES)
ARTIFACT_ACTIONS = frozenset({"open", "copy", "download"})
RECOVERY_ACTIONS = frozenset({"retry"})
FINISHED_RESULTS = frozenset({"success", "failed", "cancelled", "no_speech"})
READINESS_STATUSES = frozenset({"READY", "READY_WITH_WARNINGS", "BLOCKED"})
INPUT_KINDS = frozenset({"local_audio", "local_video", "platform_url", "generic_url", "unknown"})
DURATION_BUCKETS = frozenset({"<1m", "1-3m", "3-10m", "10-30m", ">30m", "unknown"})
MODEL_CACHE_STATES = frozenset({"warm", "cold", "unavailable", "unknown"})
HARDWARE_TIERS = frozenset({"reference_16", "standard_8", "constrained_4", "unknown"})
OS_FAMILIES = frozenset({"windows", "macos", "linux", "unknown"})
_OS_FAMILIES = OS_FAMILIES

ALLOWED_PROPERTIES = {
    "action",
    "artifact_exists",
    "blocker_codes",
    "bytes_if_known",
    "cache_state",
    "device",
    "duration_bucket",
    "engine",
    "error_code",
    "exit_code",
    "input_kind",
    "metadata_consistent",
    "model",
    "network_required",
    "reason_code",
    "result",
    "retry_result",
    "retry_task_id",
    "selection_mode",
    "stage",
    "status",
    "task_id",
    "text_nonempty",
    "warning_codes",
}

_common_cache: Dict[str, str] = {}
_model_cache_states: Dict[tuple[str, str], str] = {}


class EventPrivacyError(ValueError):
    """Raised when an event property is outside the local privacy whitelist."""


class EventRetentionError(RuntimeError):
    """Raised when automatic local event recording is disabled."""


def os_family() -> str:
    from ..platform_compat import get_os

    value = get_os()
    return value if value in _OS_FAMILIES else "unknown"


def _total_ram_mb() -> int:
    try:
        import psutil  # type: ignore[import-not-found]

        return max(0, int(psutil.virtual_memory().total / 1024 / 1024))
    except Exception:
        pass

    try:
        if hasattr(os, "sysconf"):
            page_size = os.sysconf("SC_PAGE_SIZE")
            page_count = os.sysconf("SC_PHYS_PAGES")
            if page_size > 0 and page_count > 0:
                return int(page_size * page_count / 1024 / 1024)
    except (ValueError, OSError, AttributeError):
        pass

    system = platform.system()
    if system == "Windows":
        try:
            import ctypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            windll = getattr(ctypes, "windll", None)
            if windll is not None and windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys / 1024 / 1024)
        except Exception:
            return 0
    elif system == "Darwin":
        try:
            proc = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            return int(proc.stdout.strip()) // 1024 // 1024
        except Exception:
            return 0

    return 0


def _hardware_tier_from_mb(total_mb: int) -> str:
    if total_mb >= 16 * 1024:
        return "reference_16"
    if total_mb >= 8 * 1024:
        return "standard_8"
    if total_mb >= 4 * 1024:
        return "constrained_4"
    return "unknown"


def hardware_tier() -> str:
    cached = _common_cache.get("hardware_tier")
    if cached is not None:
        return cached
    try:
        tier = _hardware_tier_from_mb(_total_ram_mb())
    except Exception:
        tier = "unknown"
    _common_cache["hardware_tier"] = tier
    return tier


def probe_duration_seconds(path: Path) -> Optional[float]:
    from ..audio_utils import probe_duration_seconds as probe

    return probe(path)


def classify_input_kind(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "unknown"

    lowered = raw.lower()
    if lowered.startswith(("http://", "https://")):
        from ..url_utils import match_platform

        try:
            return "platform_url" if match_platform(raw) else "generic_url"
        except Exception:
            return "generic_url"

    try:
        path = Path(raw)
        exists = path.exists()
    except (OSError, ValueError):
        return "unknown"
    if not exists:
        return "unknown"

    from ..inputs import is_audio_file, is_video_file

    try:
        if is_audio_file(path):
            return "local_audio"
        if is_video_file(path):
            return "local_video"
    except OSError:
        return "unknown"
    return "unknown"


def duration_bucket(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if value < 0:
        return "unknown"
    if value < 60:
        return "<1m"
    if value < 180:
        return "1-3m"
    if value < 600:
        return "3-10m"
    if value < 1800:
        return "10-30m"
    return ">30m"


def model_cache_state(engine: Optional[str] = None, model: Optional[str] = None) -> str:
    requested_model = (model or "small").strip() or "small"
    if requested_model == "auto":
        requested_model = "small"
    cache_key = (engine or "", requested_model)
    cached = _model_cache_states.get(cache_key)
    if cached is not None:
        return cached

    try:
        from ..config import EngineUnavailableError, resolve_default_engine
        from ..model_weights import check_weights

        resolved_engine = engine
        if resolved_engine is None:
            try:
                resolved_engine, _ = resolve_default_engine()
            except EngineUnavailableError:
                return "unavailable"
        status = check_weights(str(resolved_engine), requested_model)
        state = "warm" if status.ok else "cold"
    except Exception:
        state = "unknown"

    _model_cache_states[cache_key] = state
    return state


def is_internal_test() -> bool:
    raw = os.environ.get("MEDIASCRIBE_INTERNAL_TEST", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def common_context(
    *,
    event: Optional[str] = None,
    engine: Optional[str] = None,
    model: Optional[str] = None,
    is_first_run: bool = False,
    cache_state: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_cache_state = cache_state
    if resolved_cache_state is None:
        resolved_cache_state = model_cache_state(engine=engine, model=model)
    return {
        "os_family": os_family(),
        "hardware_tier": hardware_tier(),
        "is_first_run": bool(is_first_run or event == "first_run_started"),
        "model_cache_state": resolved_cache_state,
        "is_internal_test": is_internal_test(),
    }


def validate_event(event: str) -> str:
    if event not in CORE_EVENTS:
        raise ValueError(f"unsupported first-use event: {event}")
    return event


def validate_properties(properties: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if properties is None:
        return {}
    if not isinstance(properties, dict):
        raise ValueError("event properties must be a mapping")
    unknown = [str(key) for key in properties if key not in ALLOWED_PROPERTIES]
    if unknown:
        raise EventPrivacyError(f"event properties are not whitelisted: {', '.join(unknown)}")
    return dict(properties)


def validate_enum(value: str, allowed: Iterable[str], label: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in allowed:
        raise ValueError(f"unsupported {label}: {value!r}")
    return normalized
