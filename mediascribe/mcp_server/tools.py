"""MCP tool handlers — 工具注册表与各 _tool_* 实现 (自 mcp_server.py 拆出, 2026-11-03)。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..models import TranscriptResult  # noqa: F401  (re-exported for type hints)

if TYPE_CHECKING:  # pragma: no cover
    from mediascribe.wiki import WikiVault


SCHEMA_VERSION = "mediascribe.mcp/v1"

TOOL_LIST = [
    {
        "name": "transcribe_video",
        "description": (
            "Download a video from Bilibili / Douyin / YouTube / Xiaohongshu / "
            "local file and transcribe its audio to Markdown. Returns the path "
            "to the transcript file plus metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "Video URL (Bilibili / Douyin / YouTube / Xiaohongshu) or local file path"
                    ),
                },
                "language": {
                    "type": "string",
                    "enum": ["auto", "zh", "en", "ja"],
                    "default": "auto",
                },
                "whisper_model": {
                    "type": "string",
                    "enum": ["tiny", "base", "small", "medium", "large"],
                    "default": "small",
                },
                "output_dir": {
                    "type": "string",
                    "default": "output",
                },
            },
            "required": ["source"],
        },
    },
    {
        "name": "batch_transcribe_creator",
        "description": (
            "Discover all videos from a Douyin/Bilibili creator and transcribe "
            "them in batch. Supports resume / dedup via the local cache."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_url": {
                    "type": "string",
                    "description": "Creator profile URL",
                },
                "from_video": {
                    "type": "string",
                    "description": "Or: discover the creator from one of their videos",
                },
                "max_videos": {
                    "type": "integer",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 200,
                },
                "language": {
                    "type": "string",
                    "default": "zh",
                },
                "output_dir": {
                    "type": "string",
                    "default": "output",
                },
            },
            "anyOf": [{"required": ["user_url"]}, {"required": ["from_video"]}],
        },
    },
    {
        "name": "get_cache_stats",
        "description": "Return cache statistics: total / success / failed / skipped video counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "validate_url",
        "description": (
            "Check whether a URL is safe to fetch (HTTPS only, trusted domain "
            "allowlist, no embedded scripts). Use before passing user-supplied "
            "URLs into downloaders. Returns ``safe`` plus a ``reason`` when "
            "unsafe — never process URLs reported unsafe."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "sanitize_filename",
        "description": (
            "Convert an arbitrary string into a safe filename (Windows + Linux). "
            "Strips reserved names, control characters, and path separators."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "detect_platform",
        "description": (
            "Identify which platform a URL belongs to (bilibili / douyin / "
            "youtube / xiaohongshu / tiktok / wechat_mp / unknown)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "get_transcript",
        "description": (
            "Read an existing transcript file (.md) and return its content plus "
            "metadata header. If the file does not exist, returns an error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "wiki_list",
        "description": (
            "List the Karpathy-style knowledge vault (wiki) notes: raw "
            "transcript notes + aggregated wiki notes + the HOME index "
            "content. Vault location: MEDIASCRIBE_VAULT_DIR, else "
            "<workspace>/vault. Disabled (MEDIASCRIBE_WIKI=0) returns "
            "enabled=False."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "wiki_read_note",
        "description": (
            "Read one vault note's markdown content by its relative name "
            "(e.g. 'raw/foo' or 'wiki/概念 - X'). Root-sandboxed to the "
            "vault directory; 5 MiB cap."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Note path relative to the vault, with or without .md",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "transcribe_wechat_mp",
        "description": (
            "Extract a WeChat MP article (mp.weixin.qq.com/s?...) into a "
            "markdown transcript. For text articles the body is saved "
            "directly (no ASR). For video messages the embedded mp4 is "
            "downloaded and transcribed. For image articles, OCR is run "
            "with the requested engine. Cookies can be supplied to bypass "
            "login walls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "WeChat MP article URL",
                },
                "cookies": {
                    "type": "object",
                    "description": (
                        "Optional cookies dict (name → value) for login-walled "
                        "articles. Highest priority."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "cookies_file": {
                    "type": "string",
                    "description": (
                        "Optional path to a cookie file. Accepts Netscape or JSON formats."
                    ),
                },
                "whisper_model": {
                    "type": "string",
                    "enum": ["tiny", "base", "small", "medium", "large"],
                    "default": "small",
                },
                "output_dir": {
                    "type": "string",
                    "default": "output",
                },
                "ocr_engine": {
                    "type": "string",
                    "enum": ["auto", "paddleocr", "pytesseract", "easyocr", "none"],
                    "default": "auto",
                    "description": (
                        "OCR engine for embedded images. ``auto`` (default) "
                        "tries paddleocr → pytesseract → easyocr in order. "
                        "Any unavailable engine is silently skipped. "
                        "``none`` disables OCR entirely."
                    ),
                },
                "ocr_lang": {
                    "type": "string",
                    "default": "chi_sim+eng",
                    "description": (
                        "OCR language code (pytesseract / easyocr style). "
                        "Examples: ``chi_sim+eng``, ``eng``, ``chi_tra+eng``."
                    ),
                },
                "save_images": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "If true, download and persist every image URL in the "
                        "article next to the markdown transcript (in addition "
                        "to running OCR)."
                    ),
                },
            },
            "required": ["url"],
        },
    },
]


def _tool_validate_url(args: Dict[str, Any]) -> Dict[str, Any]:
    # 域名白名单版检查（check_url_safety）。历史上这里用 is_safe_url
    # （纯格式检查），``https://evil.com/x`` 会得到 safe:true，误导
    # 下游 Agent（2026-10-29 审查 SEC-004）。
    from mediascribe.security import check_url_safety

    url = args.get("url", "")
    safe, reason = check_url_safety(url)
    return {"url": url, "safe": safe, "reason": reason}


def _tool_sanitize_filename(args: Dict[str, Any]) -> Dict[str, Any]:
    from mediascribe.platform_compat import safe_filename

    return {"name": args.get("name", ""), "safe": safe_filename(args.get("name", ""))}


def _tool_detect_platform(args: Dict[str, Any]) -> Dict[str, Any]:
    """detect_platform 工具 — 用 url_utils 指纹表识别平台。

    与指纹表不同的两个单列值: 空 URL → ``empty``, 无指纹的
    http(s) URL → ``other``; 其余无指纹输入 → ``unknown``。
    """
    from mediascribe.url_utils import match_platform

    raw_url = args.get("url", "") or ""
    platform = match_platform(raw_url) or "unknown"
    if not raw_url:
        platform = "empty"
    elif platform == "unknown":
        platform = "other" if raw_url.lower().startswith(("http://", "https://")) else "unknown"
    return {"url": args.get("url", ""), "platform": platform}


def _tool_get_cache_stats(_: Dict[str, Any]) -> Dict[str, Any]:
    from mediascribe.batch_cache import ProcessCache

    cache = ProcessCache()
    return cache.get_stats()


# MCP 工具读文件的大小上限(5 MiB) — get_transcript 与 wiki_read_note 共用。
_MCP_READ_CAP_BYTES = 5 * 1024 * 1024


def _read_text_capped(target: Path, max_bytes: int = _MCP_READ_CAP_BYTES):
    """限长 + UTF-8 文本读取 — 两个读文件工具的共用尾部。

    返回 ``(text, size, error)``: 成功时 ``error`` 为 ``None``;
    超限 ``"too_large"``; 非 UTF-8 ``"bad_utf8"``。错误 payload 的
    键名(path/name)与文案由调用方组装 — 两个工具的历史输出略有
    差异, 逐字保留。
    """
    size = target.stat().st_size
    if size > max_bytes:
        return None, size, "too_large"
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, size, "bad_utf8"
    return text, size, None


def _tool_get_transcript(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return the contents of a transcript file, with safety constraints.

    The MCP server is meant to be hit by trusted local clients (a
    coding agent, a Web UI).  Even so, a naive ``Path(args['path'])``
    read is a remote-file-read primitive: any caller can ask for
    ``/etc/passwd`` or ``~/.ssh/id_rsa``.  This implementation pins the
    reachable root to a single directory and refuses anything else.

    Configuration:
        MEDIASCRIBE_TRANSCRIPT_ROOT — directory under which transcripts
        may be read (default: ``./output/transcripts``).  Relative paths
        are resolved against the current working directory.

    Limits:
        * File must exist and be a regular file (no directories, no
          device files, no sockets).
        * File must be a descendant of the configured root (after
          symlink resolution).
        * File must be at most 5 MiB; larger files return ``truncated``
          metadata without the full content.
    """
    max_bytes = _MCP_READ_CAP_BYTES
    root = (
        Path(os.environ.get("MEDIASCRIBE_TRANSCRIPT_ROOT", "output/transcripts"))
        .expanduser()
        .resolve()
    )

    raw = str(args.get("path", "")).strip()
    if not raw:
        return {"path": raw, "exists": False, "error": "path is required"}

    try:
        candidate = Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        return {"path": raw, "exists": False, "error": f"cannot resolve path: {exc}"}

    # Containment check: the resolved candidate must live under ``root``.
    try:
        candidate.relative_to(root)
    except ValueError:
        return {
            "path": raw,
            "exists": candidate.exists(),
            "error": (
                f"path escapes the transcript root ({root}); "
                "set MEDIASCRIBE_TRANSCRIPT_ROOT to allow a different directory"
            ),
        }

    if not candidate.exists():
        return {"path": str(candidate), "exists": False, "error": "file not found"}
    if not candidate.is_file():
        return {
            "path": str(candidate),
            "exists": True,
            "error": "not a regular file",
        }

    text, size, read_err = _read_text_capped(candidate, max_bytes)
    if read_err == "too_large":
        return {
            "path": str(candidate),
            "exists": True,
            "size_bytes": size,
            "truncated": True,
            "max_bytes": max_bytes,
            "error": f"file is larger than {max_bytes} bytes; refusing to inline",
        }
    if read_err == "bad_utf8":
        return {
            "path": str(candidate),
            "exists": True,
            "size_bytes": size,
            "error": "file is not valid utf-8; refusing to inline",
        }
    return {
        "path": str(candidate),
        "exists": True,
        "size_bytes": size,
        "content": text,
    }


def _tool_transcribe_video(args: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronous transcription (small videos only — for large batch use the CLI).

    The actual ``Pipeline`` constructor signature is
    ``Pipeline(settings, downloader=None, transcriber=None)`` and the entry
    point is ``Pipeline.transcribe(source_input, **opts)`` returning a
    ``TranscriptResult``.  This wrapper maps the flat MCP argument shape to
    that API.
    """
    from mediascribe.config import Settings
    from mediascribe.pipeline import Pipeline

    source_input = args.get("source") or args.get("url") or args.get("path")
    if not source_input:
        raise ValueError("transcribe_video requires 'source' (URL or local path)")

    # SEC-003 (2026-11-02 审计): URL 源过域名白名单, 与 Web 层同标准 —
    # 防止 drive-by 调用把任意 URL 塞给 yt-dlp 抓取
    if isinstance(source_input, str) and source_input.lower().startswith(("http://", "https://")):
        from mediascribe.security import check_url_safety

        is_safe, reason = check_url_safety(source_input)
        if not is_safe:
            raise ValueError(f"URL rejected by domain allowlist: {reason}")

    # The MCP tool is opinionated about defaults that may be different from
    # what the CLI uses.  Build a minimal Settings that honours the args
    # the caller passed through, falling back to safe defaults.
    model = args.get("whisper_model") or args.get("model") or "small"
    engine = args.get("engine") or "whisper"
    language = args.get("language")
    settings = Settings(
        workspace_root=Path(args.get("output_dir", "output")).resolve(),
        model=model,
        engine=engine,
        language=None if language in (None, "auto", "") else language,
    )
    pipeline = Pipeline(settings=settings)
    result: TranscriptResult = pipeline.transcribe(
        source_input=source_input,
        output=Path(args.get("output_dir", "output")).resolve(),
        language=None if language in (None, "auto", "") else language,
    )
    return {
        "source": source_input,
        "transcript": str(result.transcript_path),
        "audio": str(result.audio_path),
        "video": str(result.video_path) if result.video_path else None,
        "engine": result.engine,
        "model": result.model,
        "language": language or "auto",
    }


_BATCH_DEFAULT_TIMEOUT_SECONDS = 1800  # 30 min; 批量转录可能涉及多视频下载+ASR


def _tool_batch_transcribe_creator(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run the batch CLI as a subprocess and return the parsed JSON output.

    v3.2.0e+ 安全加固 (F-10):
      * 为 ``subprocess.run`` 添加 ``timeout`` 参数（默认 1800s = 30min,
        可通过环境变量 ``MEDIASCRIBE_BATCH_TIMEOUT`` 覆盖），
        防止批量 CLI 长时间阻塞 MCP 服务进程。
      * 捕获 ``subprocess.TimeoutExpired`` 异常，返回结构化错误而非崩溃
        MCP 工具调用链。
    """
    import subprocess

    env_val = os.environ.get("MEDIASCRIBE_BATCH_TIMEOUT", "").strip()
    if env_val:
        try:
            timeout = float(env_val)
        except ValueError:
            timeout = _BATCH_DEFAULT_TIMEOUT_SECONDS
    else:
        timeout = _BATCH_DEFAULT_TIMEOUT_SECONDS

    cmd = [
        sys.executable,
        "-m",
        "mediascribe",
        "archive",
        "--json",
        "--lang",
        "en",
    ]
    if args.get("user_url"):
        cmd.extend(["--user", args["user_url"]])
    elif args.get("from_video"):
        cmd.extend(["--from-video", args["from_video"]])
    # P1-4⑥: handler 层强制 clamp 到 schema 声明的 1-200 — 恶意/异常
    # 客户端可能绕过 schema 校验直接传 0 / 负数 / 超大值。
    try:
        max_videos = int(args.get("max_videos", 10))
    except (TypeError, ValueError):
        max_videos = 10
    max_videos = max(1, min(200, max_videos))
    cmd.extend(["-n", str(max_videos)])
    # NOTE: the batch CLI has no ``--language`` flag.  Its ``--lang`` is the *UI*
    # language (i18n, en/zh), while the transcription language is configured via
    # the ``DOUYIN_BATCH_LANGUAGE`` environment variable (see
    # ``mediascribe.batch_config._ENV_FIELDS``).  Passing ``--language`` made
    # argparse abort with "unrecognized arguments" (exit 2) and the tool
    # returned a non-JSON error instead of a result.
    child_env = dict(os.environ)
    if args.get("language"):
        child_env["DOUYIN_BATCH_LANGUAGE"] = str(args["language"])
    if args.get("output_dir"):
        cmd.extend(["--output-dir", args["output_dir"]])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
            timeout=timeout,
            env=child_env,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "error": f"batch transcribe timed out after {timeout:.0f}s",
            "cmd": cmd,
            "stdout": e.stdout or "",
            "stderr": e.stderr or "",
        }
    try:
        result: Dict[str, Any] = json.loads(proc.stdout)
        return result
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "non-JSON output",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }


# ---------------------------------------------------------------------------
# wiki Phase 4 — Karpathy 式知识库的 MCP 查询入口 (2026-10-03 拍板)
# 落点解析与 web 层一致: MEDIASCRIBE_VAULT_DIR > <workspace>/vault,
# MEDIASCRIBE_WIKI=0 时与 web 同步停用。读取沿用 get_transcript 的
# root 沙箱 + 5MiB 上限模式。
# ---------------------------------------------------------------------------
def _mcp_vault() -> Optional["WikiVault"]:
    """构造 MCP 侧 vault 实例; 停用/不可导入时返回 None。"""
    try:
        from mediascribe.wiki import vault_for_workspace
    except ImportError:  # pragma: no cover - 可选依赖
        return None
    workspace = os.environ.get("MEDIASCRIBE_WORKSPACE", "").strip() or "output"
    return vault_for_workspace(Path(workspace))


def _tool_wiki_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """列出 vault 笔记清单 + HOME 索引(供 agent 决定要读哪篇)。"""
    vault = _mcp_vault()
    if vault is None:
        return {"enabled": False, "notes": [], "home": ""}
    data = vault.index()
    data["enabled"] = True
    data["path"] = str(vault.root)
    return data


def _tool_wiki_read_note(args: Dict[str, Any]) -> Dict[str, Any]:
    """返回单篇笔记原文。与 web 层相同的路径校验(相对 .md、防穿越)。"""
    max_bytes = _MCP_READ_CAP_BYTES  # 与 get_transcript 一致
    vault = _mcp_vault()
    if vault is None:
        return {"name": args.get("name", ""), "enabled": False, "error": "wiki disabled"}

    raw = str(args.get("name", "")).strip()
    if not raw:
        return {"name": raw, "error": "name is required"}

    rel = Path(raw)
    if rel.suffix != ".md":
        rel = rel.with_suffix(".md")
    if rel.is_absolute() or ".." in rel.parts:
        return {"name": raw, "error": "invalid note path"}
    target = (vault.root / rel).resolve()
    try:
        contained = target.is_relative_to(vault.root.resolve())
    except OSError:  # pragma: no cover - defensive
        contained = False
    if not contained:
        return {"name": raw, "error": "invalid note path"}
    if not target.is_file():
        return {"name": raw, "exists": False, "error": "note not found"}

    text, size, read_err = _read_text_capped(target, max_bytes)
    if read_err == "too_large":
        return {
            "name": raw,
            "exists": True,
            "size_bytes": size,
            "truncated": True,
            "max_bytes": max_bytes,
            "error": f"file is larger than {max_bytes} bytes; refusing to inline",
        }
    if read_err == "bad_utf8":
        return {"name": raw, "exists": True, "error": "file is not valid utf-8"}
    return {"name": raw, "exists": True, "size_bytes": size, "content": text}


def _tool_transcribe_wechat_mp(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract a WeChat MP article. Cookies may be supplied as either a dict
    (highest priority) or a file path. Returns a summary suitable for
    agents: {url, mode, transcript_path, audio_path, error}.
    """
    from mediascribe.config import Settings
    from mediascribe.inputs import parse_source
    from mediascribe.pipeline import Pipeline

    url = args.get("url", "").strip()
    if not url:
        return {"ok": False, "error": "url is required"}

    # Validate the URL is a WeChat MP link before invoking the pipeline
    src = parse_source(url)
    if src.kind != "wechat_mp":
        return {
            "ok": False,
            "error": f"url is not a WeChat MP article (detected kind={src.kind!r})",
            "url": url,
        }

    cookies = args.get("cookies") or None
    cookies_file = args.get("cookies_file") or None
    whisper_model = args.get("whisper_model", "small")
    output_dir = args.get("output_dir", "output")
    ocr_engine = args.get("ocr_engine", "auto")
    ocr_lang = args.get("ocr_lang", "chi_sim+eng")
    save_images = bool(args.get("save_images", False))

    settings = Settings(
        workspace_root=Path(output_dir).expanduser(),
        model=whisper_model,
        wechat_cookies=cookies,
        wechat_cookies_file=Path(cookies_file).expanduser() if cookies_file else None,
    )

    try:
        result = Pipeline(settings=settings).transcribe(
            url,
            ocr_engine=ocr_engine,
            ocr_lang=ocr_lang,
            save_images=save_images,
        )
        return {
            "ok": True,
            "url": url,
            "mode": ("video" if result.engine != "wechat_mp_text" else "text"),
            "transcript_path": str(result.transcript_path),
            "audio_path": str(result.audio_path) if result.audio_path else None,
            "engine": result.engine,
            "language": result.language,
            "ocr_engine": ocr_engine if result.engine == "wechat_mp_text" else None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
        }


TOOL_HANDLERS = {
    "transcribe_video": _tool_transcribe_video,
    "batch_transcribe_creator": _tool_batch_transcribe_creator,
    "transcribe_wechat_mp": _tool_transcribe_wechat_mp,
    "get_cache_stats": _tool_get_cache_stats,
    "validate_url": _tool_validate_url,
    "sanitize_filename": _tool_sanitize_filename,
    "detect_platform": _tool_detect_platform,
    "get_transcript": _tool_get_transcript,
    "wiki_list": _tool_wiki_list,
    "wiki_read_note": _tool_wiki_read_note,
}


# ---------------------------------------------------------------------------
# Minimal stdio JSON-RPC server (works without the official `mcp` SDK)
# ---------------------------------------------------------------------------
