"""
L1 前端框架层基准 —— 统一口径测量驱动。

设计原则
--------
**口径由本脚本单点定义**，候选实现由 ``scaffold_candidates.py`` 模板化生成，
两者配合保证 5 个候选（vanilla / react / vue / svelte / solid）的数字**逐项可比**：

* 同一份数据（固定种子 10000 条，5 个候选读同一个 JSON 文件）
* 同一个静态服务器（Python http.server，root 统一，所有页面同层级 ⇒ 相对路径一致）
* 同一个浏览器（Chromium headless）、同一视口（1440×900）
* 同一套埋点协议（``window.__bench`` / ``window.__benchScroll``）
* 每个指标跑 3 次取中位数，且**每次测量重启浏览器进程**（避免缓存与 GC 污染）

测量的指标
----------
=========================  ====================================================
指标                       口径
=========================  ====================================================
build_s                    ``npm run build`` 墙钟；先 warmup 1 次弃掉，再计 3 次取中位数
dist_bytes                 dist 产物体积（字节）
dist_gzip_bytes            dist 内每个文件 gzip 后大小之和（≈ 真实传输开销）
node_modules_bytes         候选独立 node_modules 总体积
prod_dep_count             生产依赖树里的包数量
cold_start_ms              导航开始 → 10000 条渲染完成（页面内 performance.now()）
render_ms                  纯 DOM 构建耗时（不含取数）
filter_ms                  输入 1 个字符 → 筛选结果落到 DOM
scroll_avg_frame_ms        滚动 1000px 期间的平均帧间隔
scroll_dropped_frames      掉帧数（帧间隔 > 32ms）
=========================  ====================================================

跑法
----
::

    python scripts/bench/frontend/bench_frontend.py            # 全量
    python scripts/bench/frontend/bench_frontend.py --only react,vue
    python scripts/bench/frontend/bench_frontend.py --skip-install

产物：``scripts/bench/frontend/results.json``（入库）+ ``output/bench/frontend/``（构建产物）
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_DIR = Path(__file__).resolve().parent
OUT_ROOT = REPO_ROOT / "output" / "bench" / "frontend"
CANDIDATE_ROOT = OUT_ROOT / "candidates"
SERVE_ROOT = OUT_ROOT / "serve"
DATA_DIR = OUT_ROOT / "data"

RESULTS_PATH = BENCH_DIR / "results.json"
SCHEMA = "mediascribe-benchmark/v1-frontend"

VIEWPORT = {"width": 1440, "height": 900}
SCROLL_PX = 1000
SCROLL_FRAMES = 32
FILTER_CHAR = "e"
RUNS = 3
BUILD_RUNS = 3

CANDIDATE_NAMES = ["vanilla", "react", "vue", "svelte", "solid"]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _npm_bin() -> str:
    """定位可执行的 npm。

    Windows 上 npm 实际是 ``npm.cmd`` —— ``subprocess`` 不经过 shell 时
    不会解析 PATHEXT，直接用 ``npm`` 会 ``FileNotFoundError``。
    """
    if os.name == "nt":
        p = shutil.which("npm.cmd")
        if p:
            return p
    return shutil.which("npm") or "npm"


def _run(
    cmd: List[str], cwd: Optional[Path] = None, timeout: int = 900
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc


def dir_size(path: Path) -> int:
    """目录总字节数。"""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def gzip_total(path: Path) -> int:
    """目录下所有文件 gzip 后大小之和。"""
    if not path.exists():
        return 0
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    total = 0
    for f in files:
        try:
            total += len(gzip.compress(f.read_bytes(), compresslevel=9))
        except OSError:
            pass
    return total


def prod_dep_count(candidate_dir: Path) -> int:
    """生产依赖树中的包数量（``npm ls --omit=dev --all --json`` 递归计数）。"""
    proc = _run([_npm_bin(), "ls", "--omit=dev", "--all", "--json"], cwd=candidate_dir, timeout=180)
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        return 0
    try:
        tree = json.loads(proc.stdout)
    except Exception:
        return 0

    seen = set()

    def walk(node: Dict[str, Any]) -> None:
        for name, info in (node.get("dependencies") or {}).items():
            if name in seen:
                continue
            seen.add(name)
            if isinstance(info, dict):
                walk(info)

    walk(tree)
    return len(seen)


# ---------------------------------------------------------------------------
# 静态服务器
# ---------------------------------------------------------------------------


class _Handler(SimpleHTTPRequestHandler):
    """显式指定 .js / .mjs 的 MIME —— ES module 的 MIME 错了浏览器会拒绝执行。"""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".json": "application/json",
        ".css": "text/css",
        ".html": "text/html",
    }

    def log_message(self, *args: Any) -> None:  # 静音
        pass


def start_server(root: Path) -> Tuple[ThreadingHTTPServer, int]:
    handler = partial(_Handler, directory=str(root))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------


def build_candidate(name: str, candidate_dir: Path, install: bool) -> Dict[str, Any]:
    """装依赖 + 构建，返回构建相关指标。"""
    result: Dict[str, Any] = {"name": name}

    # vanilla 是零依赖的静态页面，没有 package.json —— 对它跑 npm install
    # 必然失败（ENOENT package.json），会让人误以为候选不可用。
    if install and name != "vanilla":
        proc = _run(
            [_npm_bin(), "install", "--no-audit", "--no-fund"], cwd=candidate_dir, timeout=1800
        )
        if proc.returncode != 0:
            result["install_ok"] = False
            result["install_error"] = (proc.stderr or proc.stdout)[-2000:]
            return result
        result["install_ok"] = True

    nm = candidate_dir / "node_modules"
    result["node_modules_bytes"] = dir_size(nm)
    result["prod_dep_count"] = prod_dep_count(candidate_dir) if nm.exists() else 0

    if name == "vanilla":
        # 无构建步骤 —— 产物体积按源文件（html + js + css）计
        result["build_s"] = 0.0
        result["build_runs"] = []
        result["build_note"] = "无构建步骤（维持现状的原生页面）"
        files = [
            candidate_dir / "index.html",
            candidate_dir / "bench-shared.js",
            candidate_dir / "src" / "main.js",
            candidate_dir / "styles.css",
        ]
        result["dist_bytes"] = sum(f.stat().st_size for f in files if f.exists())
        result["dist_gzip_bytes"] = gzip_total(candidate_dir)
        result["dist_gzip_note"] = "按候选目录内全部源文件 gzip 求和"
        return result

    # warmup 一次（弃掉），再计时 3 次
    warm = _run([_npm_bin(), "run", "build"], cwd=candidate_dir, timeout=900)
    if warm.returncode != 0:
        result["build_ok"] = False
        result["build_error"] = (warm.stderr or warm.stdout)[-2000:]
        return result
    result["build_ok"] = True

    times: List[float] = []
    for _ in range(BUILD_RUNS):
        t0 = time.perf_counter()
        proc = _run([_npm_bin(), "run", "build"], cwd=candidate_dir, timeout=900)
        times.append(round(time.perf_counter() - t0, 3))
        if proc.returncode != 0:
            result["build_ok"] = False
            result["build_error"] = (proc.stderr or proc.stdout)[-2000:]
            break
    result["build_runs"] = times
    result["build_s"] = sorted(times)[len(times) // 2] if times else None

    dist = candidate_dir / "dist"
    result["dist_bytes"] = dir_size(dist)
    result["dist_gzip_bytes"] = gzip_total(dist)
    return result


# ---------------------------------------------------------------------------
# 浏览器测量
# ---------------------------------------------------------------------------


def measure_once(page: Any, url: str) -> Dict[str, Any]:
    """跑一次浏览器测量（冷启动 → 滚动 → 筛选）。"""
    out: Dict[str, Any] = {}
    page.goto(url, wait_until="load", timeout=60_000)
    page.wait_for_function("window.__bench && window.__bench.ready === true", timeout=30_000)
    out["cold_start_ms"] = page.evaluate("window.__bench.coldStartMs")
    out["render_ms"] = page.evaluate("window.__bench.renderMs")
    out["rows"] = page.evaluate("window.__bench.rows")

    scroll = page.evaluate(f"window.__benchScroll({SCROLL_PX}, {SCROLL_FRAMES})")
    out["scroll_avg_frame_ms"] = scroll.get("avgFrameMs")
    out["scroll_max_frame_ms"] = scroll.get("maxFrameMs")
    out["scroll_dropped_frames"] = scroll.get("dropped")
    out["scroll_frames"] = scroll.get("frames")

    page.fill("#filter", FILTER_CHAR)
    page.wait_for_function("window.__bench && window.__bench.filterDone === true", timeout=60_000)
    out["filter_ms"] = page.evaluate("window.__bench.filterMs")
    return out


def measure_candidate(name: str, port: int) -> Dict[str, Any]:
    """对单个候选跑 RUNS 轮浏览器测量。"""
    from playwright.sync_api import sync_playwright  # type: ignore

    url = f"http://127.0.0.1:{port}/{name}/index.html"
    runs: List[Dict[str, Any]] = []
    errors: List[str] = []

    with sync_playwright() as p:
        for i in range(RUNS):
            # 每次测量**重启浏览器进程** —— 避免缓存 / JIT / GC 污染
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport=VIEWPORT)
                page.set_default_timeout(120_000)
                runs.append(measure_once(page, url))
            except Exception as exc:
                errors.append(f"run{i + 1}: {type(exc).__name__}: {exc}")
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    def median(key: str) -> Optional[float]:
        vals = [r[key] for r in runs if r.get(key) is not None]
        if not vals:
            return None
        return round(sorted(vals)[len(vals) // 2], 3)

    return {
        "name": name,
        "runs": runs,
        "errors": errors,
        "cold_start_ms": median("cold_start_ms"),
        "render_ms": median("render_ms"),
        "filter_ms": median("filter_ms"),
        "scroll_avg_frame_ms": median("scroll_avg_frame_ms"),
        "scroll_max_frame_ms": median("scroll_max_frame_ms"),
        "scroll_dropped_frames": median("scroll_dropped_frames"),
        "rows": runs[0].get("rows") if runs else None,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def publish_to_serve(name: str, candidate_dir: Path) -> None:
    """把候选产物发布到统一静态服务器的 ``serve/<name>/`` 下。"""
    target = SERVE_ROOT / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    if name == "vanilla":
        for rel in ("index.html", "bench-shared.js", "styles.css"):
            src = candidate_dir / rel
            if src.exists():
                shutil.copy2(src, target / rel)
        src_dir = candidate_dir / "src"
        if src_dir.exists():
            shutil.copytree(src_dir, target / "src")
    else:
        dist = candidate_dir / "dist"
        if dist.exists():
            shutil.copytree(dist, target, dirs_exist_ok=True)
        # 共享层不在 Vite 的产物里（它在项目根，不在 public/）——
        # 必须一并发布，否则页面 404 → 全局工具缺失 → 静默不渲染。
        shared = candidate_dir / "bench-shared.js"
        if shared.exists():
            shutil.copy2(shared, target / "bench-shared.js")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="L1 frontend framework benchmark")
    p.add_argument("--only", default="", help="逗号分隔的候选名；空=全部")
    p.add_argument("--skip-install", action="store_true", help="跳过 npm install")
    p.add_argument("--skip-build", action="store_true", help="跳过构建（用已有 dist）")
    p.add_argument("--scaffold", action="store_true", help="先跑脚手架重新生成候选")
    args = p.parse_args(argv)

    names = [n.strip() for n in args.only.split(",") if n.strip()] or list(CANDIDATE_NAMES)

    if args.scaffold:
        print("[scaffold] 生成候选工程…")
        proc = _run([sys.executable, str(BENCH_DIR / "scaffold_candidates.py")], cwd=REPO_ROOT)
        print(proc.stdout[-3000:])
        if proc.returncode != 0:
            print(proc.stderr[-3000:], file=sys.stderr)
            return 1

    SERVE_ROOT.mkdir(parents=True, exist_ok=True)
    if DATA_DIR.exists():
        (SERVE_ROOT / "data").mkdir(parents=True, exist_ok=True)
        for f in DATA_DIR.iterdir():
            if f.is_file():
                shutil.copy2(f, SERVE_ROOT / "data" / f.name)

    results: List[Dict[str, Any]] = []
    for name in names:
        candidate_dir = CANDIDATE_ROOT / name
        if not candidate_dir.exists():
            print(f"[skip] {name}: 候选目录不存在，先跑 scaffold")
            continue

        print(f"\n=== {name} ===", flush=True)
        if args.skip_build:
            build_info: Dict[str, Any] = {"name": name, "build_s": None, "build_note": "跳过构建"}
            dist = candidate_dir / "dist"
            build_info["dist_bytes"] = dir_size(dist)
            build_info["dist_gzip_bytes"] = gzip_total(dist)
            build_info["node_modules_bytes"] = dir_size(candidate_dir / "node_modules")
            build_info["prod_dep_count"] = prod_dep_count(candidate_dir)
        else:
            build_info = build_candidate(name, candidate_dir, install=not args.skip_install)
        print(
            f"  build_s={build_info.get('build_s')}  dist_gzip={build_info.get('dist_gzip_bytes')}"
        )

        publish_to_serve(name, candidate_dir)

        entry: Dict[str, Any] = {"name": name, "build": build_info}
        results.append(entry)

    # 统一服务器起一次，所有候选共用 ⇒ 消除服务器差异
    httpd, port = start_server(SERVE_ROOT)
    try:
        for entry in results:
            name = entry["name"]
            print(f"\n=== 浏览器测量 {name} ===", flush=True)
            try:
                entry["measure"] = measure_candidate(name, port)
                m = entry["measure"]
                print(
                    f"  cold_start={m.get('cold_start_ms')}ms  render={m.get('render_ms')}ms"
                    f"  filter={m.get('filter_ms')}ms  frame={m.get('scroll_avg_frame_ms')}ms"
                    f"  dropped={m.get('scroll_dropped_frames')}"
                )
                if m.get("errors"):
                    print(f"  [warn] {m['errors'][:2]}")
            except Exception as exc:
                entry["measure"] = {"name": name, "errors": [f"{type(exc).__name__}: {exc}"]}
                print(f"  [error] {exc}")
    finally:
        httpd.shutdown()

    payload = {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "method": {
            "viewport": VIEWPORT,
            "runs": RUNS,
            "build_runs": BUILD_RUNS,
            "rows": 10000,
            "scroll_px": SCROLL_PX,
            "scroll_frames": SCROLL_FRAMES,
            "filter_char": FILTER_CHAR,
            "browser": "chromium (headless)",
            "server": "python http.server (统一 root，所有候选同层级)",
            "note": "每次测量重启浏览器进程；数据为固定种子生成的同一份 JSON",
        },
        "candidates": results,
    }
    # 增量合并：单独重跑某个候选（如 --only vue）时，**不能**把其它候选
    # 的既有结果冲掉 —— 那等于让一次局部复跑毁掉整份数据集。
    if RESULTS_PATH.exists():
        try:
            old = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
            prev = {c.get("name"): c for c in old.get("candidates", [])}
        except Exception:
            prev = {}
    else:
        prev = {}
    merged: List[Dict[str, Any]] = []
    for name in CANDIDATE_NAMES:
        fresh = next((r for r in results if r.get("name") == name), None)
        if fresh is not None:
            merged.append(fresh)
        elif name in prev:
            merged.append(prev[name])
    payload["candidates"] = merged
    payload["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    RESULTS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ok] 结果（{len(merged)} 个候选）-> {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
