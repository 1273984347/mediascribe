#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B5 候选 —— 抓取 whisper.cpp 纯浏览器 (WASM) 路线的全部本地产物。

与任务书预期相反的实测发现: huggingface.co 主站不可达, 但 **hf-mirror.com 可达**,
且 whisper.cpp 官方在 ggml.ai 发布了**预构建**的 WASM 包 (libmain.js / main.js)。
因此 B5 的「不可实测」前提不成立 —— 只要拿到权重就能真跑一次浏览器内推理。

抓取清单:
    index.html            ggml.ai/whisper.cpp/            (官方部署的 demo 页)
    main.js               ggml.ai/whisper.cpp/            (Emscripten 产物, 内嵌 WASM)
    helpers.js            ggml.ai/whisper.cpp/
    coi-serviceworker.js  ggml.ai/whisper.cpp/            (跨源隔离, 为 SharedArrayBuffer)
    ggml-tiny.bin         hf-mirror.com/ggerganov/whisper.cpp  (模型权重, 74 MiB)
    jfk.wav               github raw (whisper.cpp 官方样例音频, 11 s)

全部落盘到 output/bench/shell/b5-app/ (被 gitignore 忽略, 不入库)。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.request
from pathlib import Path

FILES = [
    ("index.html", "https://ggml.ai/whisper.cpp/index.html", "official deployed demo page"),
    ("main.js", "https://ggml.ai/whisper.cpp/main.js", "official prebuilt Emscripten bundle"),
    ("libmain.js", "https://ggml.ai/whisper.cpp/libmain.js", "official prebuilt bundle (alias)"),
    ("helpers.js", "https://ggml.ai/whisper.cpp/helpers.js", "official demo helpers"),
    (
        "coi-serviceworker.js",
        "https://ggml.ai/whisper.cpp/coi-serviceworker.js",
        "cross-origin isolation",
    ),
    (
        "ggml-tiny.bin",
        "https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
        "ggml model (hf-mirror)",
    ),
    (
        "jfk.wav",
        "https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/samples/jfk.wav",
        "official sample audio",
    ),
]

UA = {"User-Agent": "mediascribe-bench/1.0"}


def fetch(url: str, dest: Path, timeout: int = 600) -> int:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    dest.write_bytes(data)
    return len(data)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="输出目录 (默认 output/bench/shell/b5-app)")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[4]
    out = Path(args.out) if args.out else repo / "output" / "bench" / "shell" / "b5-app"
    out.mkdir(parents=True, exist_ok=True)

    report = []
    for name, url, note in FILES:
        dest = out / name
        t0 = time.perf_counter()
        if dest.is_file() and dest.stat().st_size > 0:
            # 已下载过 —— 跳过, 避免重复拉 74 MiB 权重
            report.append(
                {
                    "file": name,
                    "url": url,
                    "ok": True,
                    "bytes": dest.stat().st_size,
                    "sha256": sha256(dest),
                    "elapsed_ms": 0,
                    "note": note + " (已存在, 跳过下载)",
                }
            )
            print(f"[b5-assets] SKIP {name:<22} {dest.stat().st_size:>12,} B  (已存在)")
            continue
        try:
            size = fetch(url, dest)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            report.append(
                {
                    "file": name,
                    "url": url,
                    "ok": True,
                    "bytes": size,
                    "sha256": sha256(dest),
                    "elapsed_ms": ms,
                    "note": note,
                }
            )
            print(f"[b5-assets] OK   {name:<22} {size:>12,} B  {ms:>8} ms  <- {url}")
        except Exception as exc:
            ms = round((time.perf_counter() - t0) * 1000, 1)
            report.append(
                {
                    "file": name,
                    "url": url,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_ms": ms,
                    "note": note,
                }
            )
            print(f"[b5-assets] FAIL {name:<22} {type(exc).__name__}: {exc}  <- {url}")
    return 0 if all(r["ok"] for r in report) else 1


if __name__ == "__main__":
    sys.exit(main())
