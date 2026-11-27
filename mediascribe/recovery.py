"""Stable error categories with localized, actionable recovery guidance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .downloaders.download_errors import (
    CATEGORY_BOT_WALL,
    CATEGORY_FATAL,
    CATEGORY_GONE,
    CATEGORY_TRANSIENT,
    DownloadError,
)
from .i18n import get_language, t


@dataclass(frozen=True)
class _Action:
    id: str
    label_key: str
    command: str | None = None


@dataclass(frozen=True)
class _Rule:
    reason_key: str
    scope: str
    retryable: bool
    actions: tuple[_Action, ...]


_RETRY = _Action("retry", "RECOVERY_ACTION_RETRY")
_EDIT_INPUT = _Action("edit_input", "RECOVERY_ACTION_EDIT_INPUT")
_CHANGE_INPUT = _Action("change_input", "RECOVERY_ACTION_EDIT_INPUT")
_INSTALL_ENGINE = _Action(
    "install_engine",
    "RECOVERY_ACTION_INSTALL_ENGINE",
    'python -m pip install "mediascribe[faster-whisper]"',
)
_RUN_DOCTOR = _Action("run_doctor", "RECOVERY_ACTION_RUN_DOCTOR", "python -m mediascribe doctor")
_PREPARE_MODEL = _Action("prepare_model", "RECOVERY_ACTION_PREPARE_MODEL")
_CHOOSE_DEVICE = _Action("choose_device", "RECOVERY_ACTION_CHOOSE_DEVICE")
_CHANGE_OUTPUT = _Action("change_output", "RECOVERY_ACTION_CHANGE_OUTPUT")
_SUPPORT = _Action("support", "RECOVERY_ACTION_SUPPORT")

_RULES = {
    "INPUT_INVALID": _Rule("RECOVERY_INPUT_INVALID", "input", False, (_EDIT_INPUT,)),
    "FFMPEG_MISSING": _Rule(
        "RECOVERY_FFMPEG_MISSING",
        "environment",
        False,
        (_Action("install_ffmpeg", "RECOVERY_ACTION_INSTALL_FFMPEG"), _RUN_DOCTOR),
    ),
    "ENGINE_UNAVAILABLE": _Rule(
        "RECOVERY_ENGINE_UNAVAILABLE", "environment", False, (_INSTALL_ENGINE, _RUN_DOCTOR)
    ),
    "MODEL_NOT_READY": _Rule("RECOVERY_MODEL_NOT_READY", "model", False, (_PREPARE_MODEL,)),
    "MODEL_DOWNLOAD_FAILED": _Rule(
        "RECOVERY_MODEL_DOWNLOAD_FAILED", "model", True, (_RETRY, _PREPARE_MODEL)
    ),
    "DEVICE_UNAVAILABLE": _Rule("RECOVERY_DEVICE_UNAVAILABLE", "device", False, (_CHOOSE_DEVICE,)),
    "DOWNLOAD_FAILED": _Rule("RECOVERY_DOWNLOAD_TRANSIENT", "download", True, (_RETRY,)),
    "TRANSCRIPTION_EMPTY": _Rule(
        "RECOVERY_TRANSCRIPTION_EMPTY", "transcription", True, (_RETRY, _EDIT_INPUT)
    ),
    "OUTPUT_WRITE_FAILED": _Rule(
        "RECOVERY_OUTPUT_WRITE_FAILED", "output", False, (_CHANGE_OUTPUT,)
    ),
    "CANCELLED": _Rule(
        "RECOVERY_CANCELLED",
        "task",
        False,
        (_Action("resubmit", "RECOVERY_ACTION_RESUBMIT"),),
    ),
    "UNKNOWN": _Rule("RECOVERY_UNKNOWN", "unknown", False, (_SUPPORT,)),
    "NO_SPEECH": _Rule("RECOVERY_NO_SPEECH", "transcription", False, (_CHANGE_INPUT,)),
    "MODEL_LOAD_FAILED": _Rule("RECOVERY_MODEL_LOAD_FAILED", "model", False, (_PREPARE_MODEL,)),
    "RESULT_WRITE_FAILED": _Rule(
        "RECOVERY_OUTPUT_WRITE_FAILED", "output", False, (_CHANGE_OUTPUT,)
    ),
    "RESULT_INVALID": _Rule("RECOVERY_RESULT_INVALID", "transcription", False, (_SUPPORT,)),
    "RESOURCE_PRESSURE": _Rule(
        "RECOVERY_RESOURCE_PRESSURE",
        "device",
        True,
        (
            _RETRY,
            _Action("lower_model", "RECOVERY_ACTION_LOWER_MODEL"),
        ),
    ),
}

_DOWNLOAD_RULES = {
    CATEGORY_TRANSIENT: _Rule("RECOVERY_DOWNLOAD_TRANSIENT", "download", True, (_RETRY,)),
    CATEGORY_BOT_WALL: _Rule(
        "RECOVERY_DOWNLOAD_BOT_WALL",
        "download",
        False,
        (_Action("slow_down", "RECOVERY_ACTION_SLOW_DOWN"),),
    ),
    CATEGORY_GONE: _Rule("RECOVERY_DOWNLOAD_GONE", "download", False, (_CHANGE_INPUT,)),
    CATEGORY_FATAL: _Rule("RECOVERY_DOWNLOAD_FATAL", "download", False, (_CHANGE_INPUT,)),
}

_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and _CODE_PATTERN.fullmatch(code):
        return code
    return "UNKNOWN"


def recovery_for_exception(exc: BaseException) -> dict[str, Any]:
    """Return safe recovery metadata without embedding raw exception details."""
    code = _error_code(exc)
    category = "OUTPUT_WRITE_FAILED" if code == "RESULT_WRITE_FAILED" else code
    if isinstance(exc, DownloadError):
        category = "DOWNLOAD_FAILED"
        rule = _DOWNLOAD_RULES.get(exc.category, _DOWNLOAD_RULES[CATEGORY_FATAL])
    else:
        rule = _RULES.get(category, _RULES["UNKNOWN"])
        if category not in _RULES:
            category = "UNKNOWN"
    actions = []
    for action in rule.actions:
        item: dict[str, Any] = {"id": action.id, "label": t(action.label_key)}
        if action.command:
            item["command"] = action.command
        actions.append(item)
    reason = (
        t(rule.reason_key, device=getattr(exc, "device", "requested device"))
        if code == "DEVICE_UNAVAILABLE"
        else t(rule.reason_key)
    )
    return {
        "code": code,
        "category": category,
        "lang": get_language(),
        "reason": reason,
        "scope": rule.scope,
        "retryable": rule.retryable,
        "actions": actions,
    }


def recovery_for_code(code: str) -> dict[str, Any]:
    """Return safe recovery metadata for a stable machine-readable error code."""
    normalized = code if isinstance(code, str) and _CODE_PATTERN.fullmatch(code) else "UNKNOWN"
    rule = _RULES.get(normalized, _RULES["UNKNOWN"])
    if normalized not in _RULES:
        normalized = "UNKNOWN"
    actions = []
    for action in rule.actions:
        item: dict[str, Any] = {"id": action.id, "label": t(action.label_key)}
        if action.command:
            item["command"] = action.command
        actions.append(item)
    return {
        "code": normalized,
        "category": normalized,
        "lang": get_language(),
        "reason": t(rule.reason_key),
        "scope": rule.scope,
        "retryable": rule.retryable,
        "actions": actions,
    }


def format_recovery(exc: BaseException) -> str:
    """Format recovery metadata for CLI and local logs."""
    info = recovery_for_exception(exc)
    lines = [
        t("RECOVERY_HEADER", code=info["code"], reason=info["reason"]),
        t(
            "RECOVERY_SCOPE_LABEL",
            scope=t(f"RECOVERY_SCOPE_{info['scope'].upper()}"),
        ),
        t(
            "RECOVERY_RETRY_LABEL",
            value=t("RECOVERY_YES" if info["retryable"] else "RECOVERY_NO"),
        ),
        t("RECOVERY_ACTIONS_LABEL"),
    ]
    for action in info["actions"]:
        suffix = f" (`{action['command']}`)" if action.get("command") else ""
        lines.append(f"  - {action['label']}{suffix}")
    return "\n".join(lines)
