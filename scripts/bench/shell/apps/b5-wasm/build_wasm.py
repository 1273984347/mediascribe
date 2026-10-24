#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B5 —— 本机自建 whisper.cpp WASM (官方文档路径: emcmake cmake .. && make)。

为什么需要自建:
    whisper.cpp 官方在 ggml.ai 发布的**预构建** WASM 包 (main.js / libmain.js, 内嵌
    wasm 二进制) 在本机 Chrome 155 / Node 24 的 V8 上无法实例化:
        CompileError: WebAssembly.instantiate(): unknown type form: 106 @+12
    (Node: "section was shorter than expected size (151363 bytes expected, 1246 decoded)")
    GitHub Releases 里也没有 wasm 资产 —— 只有原生二进制。
    因此走官方文档给出的自建路径, 用较新的 Emscripten 重新构建, 看是否可跑。

流程: 装 cmake -> 装 emsdk -> 拉 whisper.cpp 源码 -> emcmake cmake -> make
产物: output/bench/shell/b5-app/ (index.html + main.js/libmain.js + libmain.wasm)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

TOOLS = Path(r"D:\MediaScribe\output\bench\shell\tools")
WORK = Path(r"D:\MediaScribe\output\bench\shell\b5-build")
APPDIR = Path(r"D:\MediaScribe\output\bench\shell\b5-app")

CMAKE_VER = "3.31.6"
EMSCRIPTEN_VER = os.environ.get("B5_EMSDK_VERSION", "4.0.16")
WHISPER_REF = os.environ.get("B5_WHISPER_REF", "master")

UA = {"User-Agent": "mediascribe-bench/1.0"}


def log(msg: str) -> None:
    print(f"[b5-build] {msg}", flush=True)


def download(url: str, dest: Path, timeout: int = 900) -> None:
    if dest.is_file() and dest.stat().st_size > 0:
        log(f"已存在, 跳过: {dest.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as f:
        shutil.copyfileobj(resp, f, length=1 << 20)
    dt = time.perf_counter() - t0
    log(
        f"下载 {dest.name}: {dest.stat().st_size:,} B  ({dt:.1f}s, {dest.stat().st_size / 1e6 / max(dt, 1e-6):.2f} MB/s)"
    )


def run(cmd, cwd=None, env=None, timeout=7200, label=""):
    log(f"执行 {label or ' '.join(map(str, cmd))}")
    t0 = time.perf_counter()
    r = subprocess.run(
        list(map(str, cmd)),
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    tail = (r.stdout or "") + "\n" + (r.stderr or "")
    log(f"  -> rc={r.returncode}  {time.perf_counter() - t0:.1f}s")
    return r.returncode, tail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=3, help="make 并行度 (内存紧张, 别调高)")
    args = ap.parse_args()

    TOOLS.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    # 1) cmake (官方 zip, 免安装)
    cmake_dir = TOOLS / f"cmake-{CMAKE_VER}-windows-x86_64"
    if not (cmake_dir / "bin" / "cmake.exe").is_file():
        zip_path = TOOLS / f"cmake-{CMAKE_VER}-windows-x86_64.zip"
        download(
            f"https://github.com/Kitware/CMake/releases/download/v{CMAKE_VER}/cmake-{CMAKE_VER}-windows-x86_64.zip",
            zip_path,
        )
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(TOOLS)
    cmake_exe = cmake_dir / "bin" / "cmake.exe"
    log(f"cmake: {cmake_exe} exists={cmake_exe.is_file()}")

    # 2) emsdk
    emsdk_dir = TOOLS / "emsdk"
    if not (emsdk_dir / "emsdk.bat").is_file():
        download(
            "https://github.com/emscripten-core/emsdk/archive/refs/heads/main.zip",
            TOOLS / "emsdk-main.zip",
        )
        with zipfile.ZipFile(TOOLS / "emsdk-main.zip") as z:
            z.extractall(TOOLS)
        extracted = TOOLS / "emsdk-main"
        if extracted.is_dir():
            if emsdk_dir.exists():
                shutil.rmtree(emsdk_dir, ignore_errors=True)
            extracted.rename(emsdk_dir)
    emsdk_bat = emsdk_dir / "emsdk.bat"
    log(f"emsdk: {emsdk_bat} exists={emsdk_bat.is_file()}")

    rc, tail = run(
        [emsdk_bat, "install", EMSCRIPTEN_VER], cwd=emsdk_dir, timeout=3600, label="emsdk install"
    )
    if rc != 0:
        log("emsdk install 失败:\n" + tail[-2500:])
        return 2
    rc, tail = run(
        [emsdk_bat, "activate", EMSCRIPTEN_VER], cwd=emsdk_dir, timeout=900, label="emsdk activate"
    )
    log("emsdk activate rc=%d" % rc)

    # 3) 抓 emsdk 环境 (emsdk_env.bat 会设 PATH / EMSDK / EMSDK_NODE ...)
    rc, envtxt = run(
        ["cmd", "/c", f'call "{emsdk_dir}\\emsdk_env.bat" >nul 2>&1 && set'],
        timeout=300,
        label="抓取 emsdk 环境变量",
    )
    env = dict(os.environ)
    for line in envtxt.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    log(f"EMSDK={env.get('EMSDK')}")
    log(f"EMSDK_NODE={env.get('EMSDK_NODE')}")
    env["PATH"] = env.get("PATH", "")
    # 真正驱动 cmake 的是 emscripten 的 CMake 前端 ``emcmake.bat``；
    # ``emsdk`` 只是环境管理器本身，不能直接用来跑 cmake。
    emcmake = Path(env.get("EMSDK", "")) / "upstream" / "emscripten" / "emcmake.bat"

    # 4) whisper.cpp 源码
    src_zip = TOOLS / "whisper.cpp-src.zip"
    download(
        f"https://codeload.github.com/ggml-org/whisper.cpp/zip/refs/heads/{WHISPER_REF}", src_zip
    )
    src_dir = WORK / "whisper.cpp"
    if not src_dir.is_dir():
        with zipfile.ZipFile(src_zip) as z:
            z.extractall(WORK)
        # 解出来的目录名是 whisper.cpp-<ref>
        for p in WORK.iterdir():
            if p.is_dir() and p.name.startswith("whisper.cpp-"):
                p.rename(src_dir)
                break
    log(f"whisper.cpp 源码: {src_dir} exists={src_dir.is_dir()}")

    # 5) emcmake cmake
    build_dir = WORK / "build-em"
    rc, tail = run(
        [
            str(emcmake),
            "cmake",
            str(src_dir),
            "-B",
            str(build_dir),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DWHISPER_BUILD_EXAMPLES=ON",
            "-DWHISPER_WASM_SINGLE_FILE=OFF",
            "-DGGML_NATIVE=OFF",
        ],
        cwd=WORK,
        env=env,
        timeout=1800,
        label="emcmake cmake",
    )
    if rc != 0:
        log("emcmake cmake 失败:\n" + tail[-3000:])
        return 3

    # 6) make
    rc, tail = run(
        [str(cmake_exe), "--build", str(build_dir), "-j", str(args.jobs)],
        cwd=WORK,
        env=env,
        timeout=7200,
        label="cmake --build",
    )
    if rc != 0:
        log("构建失败:\n" + tail[-3000:])
        return 4

    # 7) 收集产物
    wasmdir = build_dir / "bin" / "whisper.wasm"
    log(f"产物目录: {wasmdir} exists={wasmdir.is_dir()}")
    if not wasmdir.is_dir():
        log("未生成 bin/whisper.wasm")
        return 5
    APPDIR.mkdir(parents=True, exist_ok=True)
    copied = []
    for p in wasmdir.iterdir():
        if p.is_file():
            shutil.copy2(p, APPDIR / p.name)
            copied.append((p.name, p.stat().st_size))
    tmpl = wasmdir / "index-tmpl.html"
    if tmpl.is_file():
        shutil.copy2(tmpl, APPDIR / "index.html")
        copied.append(("index.html", tmpl.stat().st_size))
    for name, size in copied:
        log(f"  复制 {name}: {size:,} B")
    log("构建完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
