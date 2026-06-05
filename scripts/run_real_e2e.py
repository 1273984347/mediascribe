"""
Real end-to-end smoke test runner for Video2Text v3.

This script is intentionally **opt-in** and **network-dependent**: it
attempts to download a small public video from each of the 6 supported
platforms, transcribe it (or skip transcription gracefully when no
ASR engine is installed), and dump the resulting markdown to a
JSON-indexed output directory.

It is designed to be run on a developer machine with full network
access, real cookies (for WeChat MP / Xiaohongshu), and at least one
Whisper variant installed. It is NOT a unit test — there is no
assertion: every step is reported to stdout and to a per-platform
log file. Failures are non-fatal so a single broken platform does
not abort the rest of the matrix.

Usage:
    python scripts/run_real_e2e.py --output-dir ./e2e-results
    python scripts/run_real_e2e.py --url-only                 # no download
    python scripts/run_real_e2e.py --platform bilibili        # subset
    python scripts/run_real_e2e.py --platform wechat_mp --wechat-cookies "wxuin=abc"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# 1. Test matrix
# ---------------------------------------------------------------------------
@dataclass
class E2ECase:
    """One row in the smoke-test matrix."""
    platform: str           # bilibili | douyin | youtube | xiaohongshu | wechat_mp
    url: str                # the public URL to exercise
    expect_kind: str        # SourceRef.kind
    needs_cookies: bool = False
    needs_playwright: bool = False
    skip_asr: bool = False  # text-only content (wechat_mp article)
    description: str = ""


# A conservative starter set. URLs intentionally point to *small*
# public videos so the suite finishes in a few minutes even on
# consumer hardware. Replace any of these with private test URLs.
E2E_TEST_URLS: List[E2ECase] = [
    E2ECase(
        platform="youtube",
        url="https://www.youtube.com/watch?v=jNQXAC9IVRw",
        expect_kind="youtube",
        description="First YouTube video ever uploaded (Me at the zoo, 19s)",
    ),
    E2ECase(
        platform="bilibili",
        url="https://www.bilibili.com/video/BV1GJ411x7h7",
        expect_kind="bilibili",
        description="A short, popular Bilibili test video",
    ),
    E2ECase(
        platform="wechat_mp",
        # Real WeChat MP article URLs always carry a __biz query param
        # so the detector's "mp.weixin.qq.com + /s?" check matches.
        url="https://mp.weixin.qq.com/s/nGm_g_J9C2eIwSthWVoWhg?__biz=MzA&mid=123&idx=1",
        expect_kind="wechat_mp",
        skip_asr=True,
        description="Public WeChat MP article (text + image only)",
    ),
    # The following two require authentication / Playwright. They are
    # skipped automatically when their prerequisites are missing.
    E2ECase(
        platform="douyin",
        url="https://www.douyin.com/video/7234567890123456789",
        expect_kind="douyin",
        needs_cookies=True,
        description="Douyin video (requires douyin cookies for production)",
    ),
    E2ECase(
        platform="xiaohongshu",
        url="https://www.xiaohongshu.com/explore/abc123?xsec_token=test",
        expect_kind="xiaohongshu",
        needs_playwright=True,
        description="Xiaohongshu explore URL (requires Playwright + cookies)",
    ),
]


# ---------------------------------------------------------------------------
# 2. Result container
# ---------------------------------------------------------------------------
@dataclass
class E2EResult:
    case: E2ECase
    started_at: str
    finished_at: str = ""
    duration_sec: float = 0.0
    detected_kind: Optional[str] = None
    detected_ok: bool = False
    pipeline_ok: bool = False
    transcript_path: Optional[str] = None
    transcript_chars: int = 0
    transcript_engine: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["case"] = asdict(self.case)
        return d


# ---------------------------------------------------------------------------
# 3. Step helpers
# ---------------------------------------------------------------------------
def detect(url: str) -> Optional[str]:
    """Run the platform detector. Returns the kind string or None."""
    try:
        from video2text.inputs import parse_source
        ref = parse_source(url)
        return ref.kind
    except Exception as exc:  # pragma: no cover - defensive
        return f"<error: {exc.__class__.__name__}>"


def run_pipeline(
    case: E2ECase,
    workspace: Path,
    *,
    wechat_cookies: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run the full Pipeline for one case and return a small report dict."""
    from video2text.config import Settings
    from video2text.pipeline import Pipeline

    settings = Settings(
        workspace_root=workspace,
        model="tiny",         # smallest model for fast smoke tests
        engine="whisper",     # most portable engine
        wechat_cookies=wechat_cookies,
    )
    pipeline = Pipeline(settings)
    out_path = workspace / case.platform / (f"{case.platform}_e2e.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = pipeline.transcribe(
            case.url,
            output=out_path,
            ocr_engine="auto",
            save_images=False,
        )
        if not out_path.exists():
            return {
                "ok": False,
                "error": f"Pipeline returned but no file at {out_path}",
            }
        text = out_path.read_text(encoding="utf-8", errors="replace")
        return {
            "ok": True,
            "path": str(out_path),
            "chars": text.__len__(),
            "engine": getattr(result, "engine", "unknown"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{exc.__class__.__name__}: {exc}",
            "trace": traceback.format_exc(limit=3),
        }


# ---------------------------------------------------------------------------
# 4. Case runner
# ---------------------------------------------------------------------------
def run_case(
    case: E2ECase,
    out_root: Path,
    *,
    url_only: bool,
    wechat_cookies: Optional[Dict[str, str]],
) -> E2EResult:
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    res = E2EResult(case=case, started_at=started)

    # 4.1 detection (always)
    res.detected_kind = detect(case.url)
    res.detected_ok = res.detected_kind == case.expect_kind
    if not res.detected_ok:
        res.errors.append(
            f"detector returned {res.detected_kind!r}, expected {case.expect_kind!r}"
        )

    if url_only:
        res.skipped_reason = "url-only mode (no download attempted)"
        res.finished_at = datetime.now(timezone.utc).isoformat()
        res.duration_sec = time.monotonic() - t0
        return res

    # 4.2 pipeline
    pl = run_pipeline(case, out_root, wechat_cookies=wechat_cookies)
    res.pipeline_ok = bool(pl.get("ok"))
    res.transcript_path = pl.get("path")
    res.transcript_chars = int(pl.get("chars", 0))
    res.transcript_engine = pl.get("engine")
    if not res.pipeline_ok:
        res.errors.append(f"pipeline failed: {pl.get('error')}")

    res.finished_at = datetime.now(timezone.utc).isoformat()
    res.duration_sec = time.monotonic() - t0
    return res


# ---------------------------------------------------------------------------
# 5. Reporting
# ---------------------------------------------------------------------------
def render_summary(results: List[E2EResult]) -> str:
    lines = ["", "=" * 78, "E2E SUMMARY", "=" * 78]
    for r in results:
        if r.skipped_reason:
            flag = "SKIP"
        elif r.pipeline_ok:
            flag = "OK"
        else:
            flag = "FAIL"
        lines.append(
            f"[{flag:4}] {r.case.platform:12} {r.duration_sec:6.2f}s "
            f"{r.case.url}"
        )
        if r.skipped_reason:
            lines.append(f"        ~ {r.skipped_reason}")
        if r.errors:
            for err in r.errors:
                lines.append(f"        - {err}")
    passed = sum(1 for r in results if r.pipeline_ok)
    skipped = sum(1 for r in results if r.skipped_reason)
    lines.append("-" * 78)
    lines.append(
        f"Passed: {passed}/{len(results)}  (skipped: {skipped})"
    )
    return "\n".join(lines)


def write_reports(results: List[E2EResult], out_root: Path) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    aggregate = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(results),
        "passed": sum(1 for r in results if r.pipeline_ok),
        "results": [r.to_dict() for r in results],
    }
    (out_root / "summary.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_root / "summary.txt").write_text(
        render_summary(results), encoding="utf-8"
    )
    lines = ["| Platform | URL | Detect | Pipeline | Engine | Chars | Time |",
             "|----------|-----|--------|----------|--------|-------|------|"]
    for r in results:
        det = "OK" if r.detected_ok else f"NO({r.detected_kind})"
        # Map the run result to a short display flag (SKIP/OK/FAIL)
        if r.skipped_reason:
            pl = "SKIP"
        elif r.pipeline_ok:
            pl = "OK"
        else:
            pl = "FAIL"
        lines.append(
            f"| {r.case.platform} | {r.case.url} | {det} | {pl} | "
            f"{r.transcript_engine or '-'} | {r.transcript_chars} | "
            f"{r.duration_sec:.1f}s |"
        )
    (out_root / "summary.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------
def _parse_cookie_arg(s: Optional[str]) -> Optional[Dict[str, str]]:
    if not s:
        return None
    if s.startswith("{"):
        return json.loads(s)
    cookies: Dict[str, str] = {}
    for part in s.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies or None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--output-dir", default="./e2e-results",
        help="Directory to write per-platform logs and the summary",
    )
    parser.add_argument(
        "--platform", action="append",
        help="Restrict to one or more platforms (repeatable)",
    )
    parser.add_argument(
        "--url-only", action="store_true",
        help="Only exercise the platform detector (skip pipeline)",
    )
    parser.add_argument(
        "--wechat-cookies", default=None,
        help='WeChat cookies string, e.g. \'wxuin=abc123; pass_ticket=xyz\'',
    )
    args = parser.parse_args(argv)

    cases = E2E_TEST_URLS
    if args.platform:
        wanted = {p.lower() for p in args.platform}
        cases = [c for c in E2E_TEST_URLS if c.platform in wanted]

    out_root = Path(args.output_dir).resolve()
    wechat_cookies = _parse_cookie_arg(args.wechat_cookies)

    results: List[E2EResult] = []
    for case in cases:
        print(f"\n>>> {case.platform:12} {case.url}")
        try:
            r = run_case(
                case, out_root,
                url_only=args.url_only,
                wechat_cookies=wechat_cookies,
            )
        except Exception as exc:
            r = E2EResult(
                case=case,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            r.finished_at = datetime.now(timezone.utc).isoformat()
            r.errors.append(f"runner exception: {exc}\n{traceback.format_exc()}")
        results.append(r)
        if r.skipped_reason:
            flag = "SKIP"
        elif r.pipeline_ok:
            flag = "OK"
        else:
            flag = "FAIL"
        print(f"<<< {flag}  {r.duration_sec:.1f}s")

    write_reports(results, out_root)
    print(render_summary(results))
    print(f"\nReports written to: {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
