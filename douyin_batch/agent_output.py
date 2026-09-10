"""
Agent-friendly JSON output for AI agents / scripting.

When the CLI is invoked with `--json`, all human-readable log output is
suppressed on stdout and a single structured JSON document is emitted at
the end. This lets AI agents (Claude Code, Cursor, Cline, etc.) consume
results programmatically without parsing free-form text.

Output schema (v1, stable):

    {
      "schema": "mediascribe.agent-output/v1",
      "ok": true | false,
      "command": "douyin_batch_v3",
      "version": "<douyin_batch.__version__>",
      "started_at": "2026-06-04T00:00:00Z",
      "finished_at": "2026-06-04T00:00:42Z",
      "elapsed_seconds": 42.3,
      "config": {
        "max_videos": 10,
        "workers": 1,
        "headless": true,
        "language": "zh",
        "whisper_model": "small",
        "output_dir": "output"
      },
      "user_url": "https://www.douyin.com/user/...",
      "videos": [
        {
          "video_id": "v1",
          "url": "https://...",
          "platform": "bilibili" | "douyin" | "youtube" | "xiaohongshu"
                      | "wechat_mp" | "tiktok" | "local" | "unknown",
          "status": "success" | "failed" | "skipped" | "partial",
          "stage": "media_url" | "download" | "transcribe" | "text_extract" | null,
          "transcript": "output/transcripts/v1.md" | null,
          "audio": "output/downloads/v1.mp4" | null,
          "error": null | "human readable error"
        }
      ],
      "stats": {
        "total": 10,
        "success": 8,
        "failed": 2,
        "skipped": 0,
        "partial": 0
      },
      "summary_report": "output/reports/...md" | null,
      "errors": [
        {"at": "...", "kind": "...", "message": "..."}
      ]
    }

This schema is the contract with AI agents. Add fields, never remove or
rename, and bump the schema version on breaking changes.

Status values
- ``success`` — the transcript (or text article) is saved successfully
- ``failed`` — unrecoverable error (see ``error`` field)
- ``skipped`` — already in cache, intentionally bypassed
- ``partial`` — non-fatal issue (e.g. WeChat MP text article without
  embedded video saved, but image extraction failed)

Stage values
- ``media_url`` — extracting the real CDN URL (Douyin / Xiaohongshu)
- ``download`` — downloading the media file
- ``transcribe`` — running ASR
- ``text_extract`` — pulling text from a non-video source
  (WeChat MP text article, OCR-friendly Xiaohongshu image note)
- ``ocr`` — running OCR on embedded images
  (WeChat MP image articles via paddleocr / pytesseract / easyocr)
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "mediascribe.agent-output/v1"

try:  # P2-9: 版本号统一来自包元数据，避免多处漂移
    from douyin_batch import __version__ as APP_VERSION
except ImportError:  # pragma: no cover - 以单文件方式导入时的兜底
    APP_VERSION = "3.1.0"

# Status string constants — never localised, must remain stable for agents.
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_PARTIAL = "partial"

# Stage string constants
STAGE_MEDIA_URL = "media_url"
STAGE_DOWNLOAD = "download"
STAGE_TRANSCRIBE = "transcribe"
STAGE_TEXT_EXTRACT = "text_extract"
STAGE_OCR = "ocr"

# Platform string constants (from SourceRef.kind)
PLATFORM_BILIBILI = "bilibili"
PLATFORM_DOUYIN = "douyin"
PLATFORM_YOUTUBE = "youtube"
PLATFORM_XIAOHONGSHU = "xiaohongshu"
PLATFORM_WECHAT_MP = "wechat_mp"
PLATFORM_TIKTOK = "tiktok"
PLATFORM_LOCAL = "local"
PLATFORM_UNKNOWN = "unknown"

# Mapping from SourceRef.kind → i18n Messages key for a human label.
_PLATFORM_I18N_KEYS = {
    "bilibili": "PLATFORM_BILIBILI",
    "douyin": "PLATFORM_DOUYIN",
    "youtube": "PLATFORM_YOUTUBE",
    "xiaohongshu": "PLATFORM_XIAOHONGSHU",
    "wechat_mp": "PLATFORM_WECHAT_MP",
    "tiktok": "PLATFORM_TIKTOK",
    "video": "PLATFORM_LOCAL",
    "audio": "PLATFORM_LOCAL",
}


def platform_label(kind: Optional[str]) -> str:
    """
    Return a localised human-readable platform label for ``kind``.
    Falls back to PLATFORM_UNKNOWN if the kind is not recognised.
    """
    from .i18n import t

    key = _PLATFORM_I18N_KEYS.get(kind or "", "PLATFORM_UNKNOWN")
    return t(key)


def status_label(status: str) -> str:
    """Return a localised human-readable label for a status value."""
    from .i18n import t

    return {
        STATUS_SUCCESS: t("STATUS_SUCCESS"),
        STATUS_FAILED: t("STATUS_FAILED"),
        STATUS_SKIPPED: t("STATUS_SKIPPED"),
        STATUS_PARTIAL: t("STATUS_PARTIAL"),
    }.get(status, status)


def stage_label(stage: Optional[str]) -> Optional[str]:
    """Return a localised human-readable label for a stage value."""
    if stage is None:
        return None
    from .i18n import t

    return {
        STAGE_MEDIA_URL: t("STAGE_MEDIA_URL"),
        STAGE_DOWNLOAD: t("STAGE_DOWNLOAD"),
        STAGE_TRANSCRIBE: t("STAGE_TRANSCRIBE"),
        STAGE_TEXT_EXTRACT: t("STAGE_TEXT_EXTRACT"),
        STAGE_OCR: t("STAGE_OCR"),
    }.get(stage, stage)


def _apply_labels(record: Dict[str, Any], lang: Optional[str] = None) -> Dict[str, Any]:
    """
    Return a copy of ``record`` with parallel ``*_label`` fields for
    ``platform``, ``status``, and ``stage``. Used by bilingual mode.

    The locale used to render those labels is recorded in
    ``*_label_i18n_lang`` so downstream agents / editors can know which
    language the human-readable text is in.

    When ``lang`` is given, the i18n language is temporarily switched so
    the rendered labels actually match that locale (then restored).
    """
    from .i18n import get_language, set_language

    out = dict(record)
    prev_lang = get_language()
    target_lang = lang or prev_lang
    if target_lang != prev_lang:
        set_language(target_lang)
    try:
        if "platform" in record:
            out["platform_label"] = platform_label(record.get("platform"))
            out["platform_label_i18n_lang"] = target_lang
        if "status" in record:
            out["status_label"] = status_label(record.get("status") or "")
            out["status_label_i18n_lang"] = target_lang
        if "stage" in record:
            out["stage_label"] = stage_label(record.get("stage"))
            out["stage_label_i18n_lang"] = target_lang
    finally:
        if target_lang != prev_lang:
            set_language(prev_lang)
    return out


class AgentOutput:
    """
    Build and emit a structured JSON document for AI agents.

    Usage:
        out = AgentOutput(command="douyin_batch_v3")
        out.set_config({...})
        out.set_user_url("...")
        out.add_video({"video_id": "v1", "status": "success", ...})
        ...
        out.finish(ok=True, summary_report="...")
        out.emit()   # writes JSON to stdout
    """

    def __init__(self, command: str) -> None:
        self._command = command
        self._ok: bool = True
        self._started = time.time()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._finished_at: Optional[str] = None
        self._config: Dict[str, Any] = {}
        self._user_url: Optional[str] = None
        self._videos: List[Dict[str, Any]] = []
        self._errors: List[Dict[str, Any]] = []
        self._summary_report: Optional[str] = None
        # Bilingual mode: when True, every video/platform/status/stage
        # gets a parallel `*_label` field with the localised human label.
        # Stable English constants are preserved — agents that grep
        # for `"status": "success"` still work.
        self._bilingual: bool = False
        # Optional override of the language used to render labels.
        # If None, the current i18n language at emit() time is used.
        self._label_lang: Optional[str] = None

    # ------------------------------------------------------------------
    # Mode controls
    # ------------------------------------------------------------------
    def set_bilingual(self, enabled: bool, lang: Optional[str] = None) -> None:
        """
        Enable or disable bilingual mode. When enabled, ``to_dict()``
        returns a parallel ``videos[*].{platform,status,stage}_label``
        field with the localised human label.

        Parameters
        ----------
        enabled : bool
            Whether to add labels.
        lang : str, optional
            "en" or "zh". If None, uses the current i18n language at
            emit time.
        """
        self._bilingual = bool(enabled)
        self._label_lang = lang

    # ------------------------------------------------------------------
    # Configuration & lifecycle
    # ------------------------------------------------------------------
    def set_config(self, cfg: Dict[str, Any]) -> None:
        """Set the effective config (will be serialised in the output)."""
        self._config = dict(cfg)

    def set_user_url(self, url: str) -> None:
        self._user_url = url

    def add_video(self, record: Dict[str, Any]) -> None:
        """Append a per-video result."""
        self._videos.append(record)

    def add_video_for_platform(
        self,
        *,
        video_id: str,
        url: str,
        platform: str,
        status: str,
        stage: Optional[str] = None,
        transcript: Optional[str] = None,
        audio: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Convenience wrapper that fills in all schema fields from
        ``platform``/``status``/``stage`` constants. Use this when you
        want the JSON output to be schema-complete without copy-paste.
        """
        self._videos.append(
            {
                "video_id": video_id,
                "url": url,
                "platform": platform,
                "status": status,
                "stage": stage,
                "transcript": transcript,
                "audio": audio,
                "error": error,
            }
        )

    def add_error(self, kind: str, message: str) -> None:
        """Append a top-level error (e.g. browser launch failure)."""
        self._errors.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "kind": kind,
                "message": message,
            }
        )

    def set_summary_report(self, path: Optional[str]) -> None:
        self._summary_report = str(path) if path else None

    def finish(self, ok: bool) -> None:
        self._ok = ok
        self._finished_at = datetime.now(timezone.utc).isoformat()

    def _stats(self) -> Dict[str, int]:
        total = len(self._videos)
        success = sum(1 for v in self._videos if v.get("status") == STATUS_SUCCESS)
        failed = sum(1 for v in self._videos if v.get("status") == STATUS_FAILED)
        skipped = sum(1 for v in self._videos if v.get("status") == STATUS_SKIPPED)
        partial = sum(1 for v in self._videos if v.get("status") == STATUS_PARTIAL)
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "partial": partial,
        }

    def to_dict(self) -> Dict[str, Any]:
        from .i18n import get_language, set_language

        # Temporarily switch to the requested label language
        prev_lang = get_language()
        if self._label_lang and self._label_lang != prev_lang:
            set_language(self._label_lang)
        try:
            active_lang = self._label_lang or get_language()
            videos = list(self._videos)
            if self._bilingual:
                videos = [_apply_labels(v, lang=active_lang) for v in videos]
            return {
                "schema": SCHEMA_VERSION,
                "ok": self._ok,
                "command": self._command,
                "version": APP_VERSION,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "elapsed_seconds": round(time.time() - self._started, 3),
                "config": self._config,
                "user_url": self._user_url,
                "videos": videos,
                "stats": self._stats(),
                "summary_report": self._summary_report,
                "errors": self._errors,
                # Top-level locale marker for the whole document; mirrors
                # the per-record ``*_label_i18n_lang`` field but makes
                # it easy for an agent to detect language once at the
                # top of the payload.
                "i18n_lang": active_lang,
            }
        finally:
            if self._label_lang and self._label_lang != prev_lang:
                set_language(prev_lang)

    def emit(self, stream=None) -> None:
        """Write the JSON document to ``stream`` (default: stdout)."""
        stream = stream or sys.stdout
        json.dump(self.to_dict(), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()

    def save(self, path: Path) -> None:
        """Write the JSON document to a file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            f.write("\n")
