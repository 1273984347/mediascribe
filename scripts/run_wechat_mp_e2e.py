"""
Real WeChat MP article end-to-end smoke test.

This script targets **only** the WeChat MP downloader and exercises
the 3 cookie-injection paths (CLI / file / env), the OCR fallback
chain, and the bilingual output toggle.

Why a separate script?  WeChat MP is the only platform where
content extraction depends on optional side effects (OCR) and
where login-protected articles are the norm.  Isolating it makes
failures easier to triage and keeps the URL fixture list focused.

Usage:
    # 1. Public article, no cookies (cold pull)
    python scripts/run_wechat_mp_e2e.py --url "https://mp.weixin.qq.com/s/xxx?__biz=..."

    # 2. Login-wall article, inline cookies
    python scripts/run_wechat_mp_e2e.py --url "https://mp.weixin.qq.com/s/yyy?__biz=..." \
        --wechat-cookies "wxuin=abc123; pass_ticket=xyz"

    # 3. Login-wall article, cookie file
    python scripts/run_wechat_mp_e2e.py --url "..." --wechat-cookies-file cookies.json

    # 4. From a fixture file (./scripts/wechat_mp_urls.txt)
    python scripts/run_wechat_mp_e2e.py --from-fixture --output-dir ./e2e-wechat

The script is opt-in: by default it expects a real host with
network access.  Pass --dry-run to validate the local pipeline
without making HTTP requests.
"""
from __future__ import annotations

import argparse
import json
import os
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
# 1. URL fixture loader
# ---------------------------------------------------------------------------
DEFAULT_FIXTURE_PATH = Path(__file__).parent / "wechat_mp_urls.txt"


def load_fixture(path: Path) -> List[Dict[str, str]]:
    """Load URLs from a text file.  Format: one JSON object per line:

        {"url": "...", "label": "public_text_1", "expect_kind": "wechat_mp"}

    Lines starting with ``#`` and blank lines are ignored.
    """
    if not path.exists():
        return []
    out: List[Dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
            if "url" in obj:
                out.append(obj)
        except json.JSONDecodeError as exc:
            print(f"[fixture] skipping malformed line: {exc}")
    return out


# ---------------------------------------------------------------------------
# 2. Result container
# ---------------------------------------------------------------------------
@dataclass
class WechatMpResult:
    url: str
    label: str
    started_at: str
    finished_at: str = ""
    duration_sec: float = 0.0
    detected_kind: Optional[str] = None
    pipeline_ok: bool = False
    md_path: Optional[str] = None
    md_chars: int = 0
    title: Optional[str] = None
    author: Optional[str] = None
    has_video: bool = False
    has_images: bool = False
    image_count: int = 0
    ocr_success: int = 0
    ocr_total: int = 0
    wechat_mp_status: Optional[str] = None
    cookie_source: str = "none"
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 3. Core step
# ---------------------------------------------------------------------------
def parse_cookies(s: Optional[str]) -> Optional[Dict[str, str]]:
    if not s:
        return None
    s = s.strip()
    if s.startswith("{"):
        return {k: str(v) for k, v in json.loads(s).items()}
    cookies: Dict[str, str] = {}
    for part in s.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies or None


def run_article(
    url: str,
    workspace: Path,
    *,
    cookies: Optional[Dict[str, str]] = None,
    cookie_file: Optional[Path] = None,
    ocr_engine: str = "auto",
    save_images: bool = False,
    bilingual: bool = False,
    dry_run: bool = False,
) -> WechatMpResult:
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    res = WechatMpResult(url=url, label=url, started_at=started)

    # 3.1 detection
    try:
        from video2text.inputs import parse_source
        ref = parse_source(url)
        res.detected_kind = ref.kind
        if ref.kind != "wechat_mp":
            res.errors.append(
                f"detector returned {ref.kind!r}, expected 'wechat_mp'"
            )
    except Exception as exc:
        res.errors.append(f"parse_source failed: {exc}")
        res.finished_at = datetime.now(timezone.utc).isoformat()
        res.duration_sec = time.monotonic() - t0
        return res

    if dry_run:
        # Validate cookie parsing only, no network
        from video2text.config import _parse_cookie_string
        if cookies:
            parsed = _parse_cookie_string("; ".join(f"{k}={v}" for k, v in cookies.items()))
            res.cookie_source = "dict"
            if not parsed:
                res.errors.append("cookie dict did not parse to non-empty dict")
        elif cookie_file and cookie_file.exists():
            parsed = _parse_cookie_string(cookie_file.read_text(encoding="utf-8"))
            res.cookie_source = f"file:{cookie_file}"
            if not parsed:
                res.errors.append("cookie file did not parse to non-empty dict")
        else:
            res.cookie_source = "none"
        res.pipeline_ok = True
        res.finished_at = datetime.now(timezone.utc).isoformat()
        res.duration_sec = time.monotonic() - t0
        return res

    # 3.2 full pipeline
    try:
        from video2text.config import Settings
        from video2text.pipeline import Pipeline

        settings = Settings(
            workspace_root=workspace,
            model="tiny",
            engine="whisper",
            wechat_cookies=cookies,
            wechat_cookies_file=cookie_file,
        )
        pipeline = Pipeline(settings)
        out_path = workspace / "wechat_mp" / f"{int(time.time())}_{url[-12:]}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        result = pipeline.transcribe(
            url,
            output=out_path,
            ocr_engine=ocr_engine,
            save_images=save_images,
            bilingual=bilingual,
        )
        if out_path.exists():
            text = out_path.read_text(encoding="utf-8", errors="replace")
            res.pipeline_ok = True
            res.md_path = str(out_path)
            res.md_chars = text.__len__()
            res.title = result.metadata.get("title") if result.metadata else None
            res.author = result.metadata.get("author") if result.metadata else None
            meta = result.metadata or {}
            res.has_video = bool(meta.get("video_url"))
            res.has_images = bool(meta.get("image_urls"))
            res.image_count = len(meta.get("image_urls") or [])
            res.ocr_success = int(meta.get("ocr_success", 0))
            res.ocr_total = int(meta.get("ocr_total", 0))
            res.wechat_mp_status = meta.get("wechat_mp_status")
        else:
            res.errors.append(f"Pipeline returned but no file at {out_path}")
    except Exception as exc:
        res.errors.append(f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc(limit=2)}")

    res.cookie_source = (
        "dict" if cookies
        else f"file:{cookie_file}" if cookie_file
        else "env" if os.environ.get("VIDEO2TEXT_WECHAT_COOKIE")
        else "none"
    )
    res.finished_at = datetime.now(timezone.utc).isoformat()
    res.duration_sec = time.monotonic() - t0
    return res


# ---------------------------------------------------------------------------
# 4. Reporting
# ---------------------------------------------------------------------------
def render_summary(results: List[WechatMpResult]) -> str:
    lines = ["", "=" * 78, "WECHAT MP E2E SUMMARY", "=" * 78]
    for r in results:
        flag = "OK" if r.pipeline_ok else "FAIL"
        meta = f"images={r.image_count} ocr={r.ocr_success}/{r.ocr_total}"
        lines.append(
            f"[{flag:4}] {r.duration_sec:6.2f}s {r.cookie_source:8} "
            f"status={r.wechat_mp_status or '-':8} {meta:24} {r.url}"
        )
        if r.errors:
            for err in r.errors:
                lines.append(f"        - {err}")
    passed = sum(1 for r in results if r.pipeline_ok)
    lines.append("-" * 78)
    lines.append(f"Passed: {passed}/{len(results)}")
    return "\n".join(lines)


def write_reports(results: List[WechatMpResult], out_root: Path) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "summary.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(timezone.utc).isoformat(),
             "count": len(results),
             "passed": sum(1 for r in results if r.pipeline_ok),
             "results": [r.to_dict() for r in results]},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (out_root / "summary.txt").write_text(
        render_summary(results), encoding="utf-8"
    )
    lines = ["| URL | Detect | Status | Cookie | OCR | MD chars | Time |",
             "|-----|--------|--------|--------|-----|----------|------|"]
    for r in results:
        det = r.detected_kind or "-"
        st = r.wechat_mp_status or ("OK" if r.pipeline_ok else "FAIL")
        ocr = f"{r.ocr_success}/{r.ocr_total}"
        lines.append(
            f"| {r.url} | {det} | {st} | {r.cookie_source} | {ocr} | "
            f"{r.md_chars} | {r.duration_sec:.1f}s |"
        )
    (out_root / "summary.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--url", help="A single WeChat MP article URL to test")
    parser.add_argument(
        "--wechat-cookies", default=None,
        help='Inline cookies, e.g. \'wxuin=abc123; pass_ticket=xyz\'',
    )
    parser.add_argument(
        "--wechat-cookies-file", default=None, type=Path,
        help="Path to a cookie file (JSON or Netscape format)",
    )
    parser.add_argument(
        "--from-fixture", action="store_true",
        help="Load URLs from scripts/wechat_mp_urls.txt",
    )
    parser.add_argument(
        "--output-dir", default="./e2e-wechat",
        help="Workspace for downloaded files + summary reports",
    )
    parser.add_argument(
        "--ocr-engine", default="auto",
        choices=["auto", "paddleocr", "pytesseract", "easyocr", "none"],
    )
    parser.add_argument("--save-images", action="store_true")
    parser.add_argument("--bilingual", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate cookie parsing and URL detection without HTTP",
    )
    args = parser.parse_args(argv)

    cookies = parse_cookies(args.wechat_cookies)
    workspace = Path(args.output_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    fixtures: List[Dict[str, str]] = []
    if args.from_fixture:
        fixtures = load_fixture(DEFAULT_FIXTURE_PATH)
        if not fixtures:
            print(f"[fixture] no entries found in {DEFAULT_FIXTURE_PATH}")
    if args.url:
        fixtures.append({"url": args.url, "label": "cli"})

    if not fixtures:
        print("Nothing to do. Pass --url or --from-fixture.")
        return 1

    results: List[WechatMpResult] = []
    for fx in fixtures:
        url = fx["url"]
        label = fx.get("label", url)
        print(f"\n>>> [{label}] {url}")
        try:
            r = run_article(
                url, workspace,
                cookies=cookies,
                cookie_file=args.wechat_cookies_file,
                ocr_engine=args.ocr_engine,
                save_images=args.save_images,
                bilingual=args.bilingual,
                dry_run=args.dry_run,
            )
        except Exception as exc:
            r = WechatMpResult(
                url=url, label=label,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            r.errors.append(f"runner exception: {exc}")
        r.label = label
        results.append(r)
        flag = "OK" if r.pipeline_ok else "FAIL"
        print(f"<<< {flag}  {r.duration_sec:.1f}s")

    write_reports(results, workspace)
    print(render_summary(results))
    print(f"\nReports written to: {workspace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
