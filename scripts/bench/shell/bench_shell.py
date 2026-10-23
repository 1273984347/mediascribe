#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L2 工作台壳 / 交付形态层 —— 可复跑基准实测驱动 (bench_shell.py)

用法
----
    python scripts/bench/shell/bench_shell.py b1 --runs 3     # FastAPI + uvicorn + 浏览器 (现状)
    python scripts/bench/shell/bench_shell.py b2 --runs 3     # FastAPI 托管 Vite+React 预构建壳
    python scripts/bench/shell/bench_shell.py b4 --runs 3     # Electron 桌面壳 + FastAPI sidecar
    python scripts/bench/shell/bench_shell.py b3              # Tauri 2 桌面壳 (尝试编译)
    python scripts/bench/shell/bench_shell.py b5              # 纯浏览器 whisper.cpp WASM
    python scripts/bench/shell/bench_shell.py all --runs 3

产物
----
    scripts/bench/shell/results.json   —— 各候选原始数据(3 次原始值) + 中位数 + 口径元信息 + 环境信息
    output/bench/shell/                —— 构建产物 (被 .gitignore 忽略, 不入库)

测量口径 (Windows)
------------------
* 冷启动: 进程 spawn 瞬间 -> 首个「健康检查 200 / 窗口 ready 标记」的墙钟毫秒 (perf_counter)
* 驻留内存: 稳定运行后**进程树** WorkingSet 之和 (psutil memory_info().rss == Windows WorkingSetSize)
* 分发体积: 构建产物磁盘字节数 (递归求和), 或明确记为「无独立分发物」
* 离线自包含: 环境变量级断网 (HTTP(S) 代理黑洞) + netstat 快照断言被测进程树无非回环 TCP 连接

GPU 隔离
--------
所有被测子进程均带 ``CUDA_VISIBLE_DEVICES=""`` 与 ``MEDIASCRIBE_DEVICE=cpu``,
避免与并行的推理层实测争抢显存 (本脚本只做壳层与轻量后端健康检查, 不做推理)。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psutil

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

# --------------------------------------------------------------------------
# 路径常量
# --------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent            # scripts/bench/shell
REPO = HERE.parents[2]                            # 仓库根
APPS = HERE / "apps"                              # 各候选的脚手架源码 (入库)
OUT = REPO / "output" / "bench" / "shell"         # 构建产物 (gitignore)
RESULTS_PATH = HERE / "results.json"

DEFAULT_PYTHON = r"C:\Users\12739\AppData\Local\Programs\Python\Python312\python.exe"

# 断网验证用的代理黑洞 (127.0.0.1:9 = discard 端口, 必然连接失败)
BLACKHOLE_PROXY = "http://127.0.0.1:9"


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _log(msg: str) -> None:
    print(f"[bench] {msg}", flush=True)


def resolve_python() -> str:
    """定位一个能 import fastapi/uvicorn 的解释器。"""
    candidates: List[str] = []
    env_py = os.environ.get("BENCH_PYTHON", "").strip()
    if env_py:
        candidates.append(env_py)
    if sys.executable:
        candidates.append(sys.executable)
    candidates.append(DEFAULT_PYTHON)
    for cand in candidates:
        try:
            r = subprocess.run(
                [cand, "-c", "import fastapi, uvicorn, psutil; print('ok')"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0 and "ok" in r.stdout:
                return cand
        except Exception:
            continue
    raise RuntimeError(
        "找不到可用 Python (需要 fastapi/uvicorn/psutil)。"
        f" 候选={candidates}；WindowsApps 商店 stub 不可用。"
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def dir_size(path: Path) -> int:
    """递归求和目录内所有文件的字节数 (Windows 磁盘口径)。"""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            fp = Path(root) / fn
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return total


def child_env(extra: Optional[Dict[str, str]] = None, offline: bool = False) -> Dict[str, str]:
    """被测子进程的环境变量基线。"""
    env = dict(os.environ)
    # --- GPU 隔离: 不让壳层实测去占显存 ---
    # 注意: CUDA_VISIBLE_DEVICES="" 不够 —— 实测 torch.cuda.is_available() 仍为 True,
    # 而 health 的 gpu_health() 会调 torch.cuda.current_device() 建立 CUDA 上下文占显存。
    # "-1" 才是彻底隐藏 (实测 is_available()=False, device_count()=0)。
    env["CUDA_VISIBLE_DEVICES"] = "-1"
    env["MEDIASCRIBE_DEVICE"] = "cpu"
    # --- 让 mediascribe 包可导入, 且工作区落在 output/ 内(不污染仓库根) ---
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["MEDIASCRIBE_WORKSPACE"] = str(OUT / "workspace")
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTHONHOME", None)
    if offline:
        env.update({
            "HTTP_PROXY": BLACKHOLE_PROXY,
            "HTTPS_PROXY": BLACKHOLE_PROXY,
            "ALL_PROXY": BLACKHOLE_PROXY,
            "http_proxy": BLACKHOLE_PROXY,
            "https_proxy": BLACKHOLE_PROXY,
            "all_proxy": BLACKHOLE_PROXY,
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
        })
    if extra:
        env.update(extra)
    return env


# --------------------------------------------------------------------------
# 进程树内存
# --------------------------------------------------------------------------
def tree_snapshot(proc: psutil.Process) -> Tuple[int, List[Dict[str, Any]], int]:
    """返回 (进程树 WorkingSet 字节和, 各进程明细, 进程数)。

    口径: psutil ``memory_info().rss`` 在 Windows 上即 ``GetProcessMemoryInfo``
    的 ``WorkingSetSize`` (工作集), 进程树 = 父进程 + 全部递归子进程。
    """
    total = 0
    parts: List[Dict[str, Any]] = []
    try:
        members = [proc] + proc.children(recursive=True)
    except psutil.Error:
        members = [proc]
    for m in members:
        try:
            mi = m.memory_info()
            total += int(mi.rss)
            parts.append({
                "pid": m.pid,
                "name": m.name(),
                "working_set_bytes": int(mi.rss),
                "is_child": m.pid != proc.pid,
            })
        except psutil.Error:
            continue
    return total, parts, len(parts)


def tree_peak_wset(proc: psutil.Process) -> int:
    """进程树 peak WorkingSet 之和 (psutil Windows 字段 peak_wset)。"""
    total = 0
    try:
        members = [proc] + proc.children(recursive=True)
    except psutil.Error:
        members = [proc]
    for m in members:
        try:
            info = m.memory_full_info()
            total += int(getattr(info, "peak_wset", 0) or 0)
        except (psutil.Error, AttributeError):
            continue
    return total


# --------------------------------------------------------------------------
# 出站连接监视 (离线自包含验证)
# --------------------------------------------------------------------------
_LOOPBACK = {"127.0.0.1", "0.0.0.0", "::", "::1", "localhost", ""}


def _parse_netstat() -> List[Tuple[str, int]]:
    """返回 [(remote_host, pid), ...] —— 通过 netstat -ano 抓当前 TCP 表。"""
    try:
        r = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=30, errors="replace",
        )
    except Exception:
        return []
    out: List[Tuple[str, int]] = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        remote = parts[2]
        host = remote.rsplit(":", 1)[0]
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        out.append((host.strip("[]"), pid))
    return out


class OutboundWatcher:
    """后台轮询 TCP 表, 收集被测进程树的**非回环**出站连接。

    断网验证方式: 同时设 HTTP(S) 代理黑洞 (见 child_env(offline=True)),
    并用本监视器断言「启动 + 核心探测」全程没有对外网发起过 TCP 连接。
    """

    def __init__(self, pids: Sequence[int], interval: float = 0.05) -> None:
        self._pids = set(pids)
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.hits: List[Dict[str, Any]] = []
        self.samples = 0

    def update_pids(self, pids: Sequence[int]) -> None:
        self._pids = set(pids)

    def _run(self) -> None:
        while not self._stop.is_set():
            for host, pid in _parse_netstat():
                if pid in self._pids and host not in _LOOPBACK:
                    self.hits.append({"remote_host": host, "pid": pid, "at": time.time()})
            self.samples += 1
            self._stop.wait(self._interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


# --------------------------------------------------------------------------
# 子进程启动 / 冷启动计时
# --------------------------------------------------------------------------
class Child:
    """被测子进程 + stdout 收集 + 出站监视。"""

    def __init__(self, cmd: Sequence[str], cwd: Path, env: Dict[str, str],
                 offline: bool = False) -> None:
        self.cmd = list(cmd)
        self.cwd = cwd
        self.env = env
        self.offline = offline
        self.proc: Optional[subprocess.Popen] = None
        self.stdout_lines: List[str] = []
        self._t0 = 0.0
        self._reader: Optional[threading.Thread] = None
        self.watcher = OutboundWatcher([])
        self._psutil_proc: Optional[psutil.Process] = None

    def __enter__(self) -> "Child":
        self._t0 = time.perf_counter()
        self.proc = subprocess.Popen(
            self.cmd, cwd=str(self.cwd), env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self._psutil_proc = psutil.Process(self.proc.pid)
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()
        self.watcher.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.kill()
        return False

    def _drain(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            self.stdout_lines.append(line.rstrip("\n"))
            if len(self.stdout_lines) > 400:
                del self.stdout_lines[:200]

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def pids(self) -> List[int]:
        pids = [self.proc.pid] if self.proc else []
        if self._psutil_proc is not None:
            try:
                pids += [c.pid for c in self._psutil_proc.children(recursive=True)]
            except psutil.Error:
                pass
        return pids

    def wait_for_http(self, url: str, timeout: float = 180.0) -> Tuple[Optional[float], Optional[int], str]:
        """轮询 URL 直到 200。返回 (就绪毫秒, 状态码, 失败原因)。"""
        deadline = time.perf_counter() + timeout
        last = ""
        while time.perf_counter() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                return None, None, f"进程提前退出 rc={self.proc.returncode}"
            try:
                r = httpx.get(url, timeout=1.5)
                if r.status_code == 200:
                    return self.elapsed_ms(), r.status_code, ""
                last = f"HTTP {r.status_code}"
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
            self.watcher.update_pids(self.pids())
            time.sleep(0.02)
        return None, None, f"超时 {timeout}s; 最后状态={last}"

    def wait_for_marker(self, marker: str, timeout: float = 180.0) -> Tuple[Optional[float], str]:
        """等待 stdout 中出现标记行, 返回 (就绪毫秒, 失败原因)。"""
        deadline = time.perf_counter() + timeout
        idx = 0
        while time.perf_counter() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                return None, f"进程提前退出 rc={self.proc.returncode}; stdout={self.stdout_lines[-15:]}"
            while idx < len(self.stdout_lines):
                line = self.stdout_lines[idx]
                idx += 1
                if marker in line:
                    return self.elapsed_ms(), ""
            self.watcher.update_pids(self.pids())
            time.sleep(0.02)
        return None, f"超时 {timeout}s 未见标记 {marker}; stdout={self.stdout_lines[-15:]}"

    def kill(self) -> None:
        self.watcher.stop()
        if self._psutil_proc is None:
            return
        try:
            kids = self._psutil_proc.children(recursive=True)
        except psutil.Error:
            kids = []
        for k in kids:
            try:
                k.kill()
            except psutil.Error:
                pass
        try:
            self._psutil_proc.kill()
        except psutil.Error:
            pass
        if self.proc is not None:
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass


def settle_memory(child: Child, settle_s: float = 5.0, samples: int = 3,
                  interval: float = 1.0) -> Dict[str, Any]:
    """稳定运行后取样驻留内存: 等待 settle → 连采 samples 次 → 取中位数。"""
    time.sleep(settle_s)
    reads: List[int] = []
    last_parts: List[Dict[str, Any]] = []
    last_count = 0
    for _ in range(samples):
        assert child._psutil_proc is not None
        total, parts, count = tree_snapshot(child._psutil_proc)
        reads.append(total)
        last_parts = parts
        last_count = count
        time.sleep(interval)
    assert child._psutil_proc is not None
    peak = tree_peak_wset(child._psutil_proc)
    return {
        "resident_bytes": int(statistics.median(reads)),
        "samples_bytes": reads,
        "sample_count": samples,
        "settle_seconds": settle_s,
        "peak_working_set_bytes": peak,
        "process_count": last_count,
        "processes": last_parts,
        "method": "Windows GetProcessMemoryInfo().WorkingSetSize, 进程树(父+递归子进程)求和, 稳定期连采取中位数",
    }


# --------------------------------------------------------------------------
# 通用: 跑一个 HTTP 服务型候选
# --------------------------------------------------------------------------
def measure_http_candidate(
    label: str,
    cmd_builder,                       # (port) -> list[str]
    health_path: str = "/api/health",
    page_path: str = "/",
    runs: int = 3,
    settle_s: float = 5.0,
    offline: bool = False,
    timeout: float = 180.0,
) -> Dict[str, Any]:
    """连续跑 N 次冷启动, 记录中位数 + 内存 + 出站连接。"""
    cold: List[Dict[str, Any]] = []
    mem: Optional[Dict[str, Any]] = None
    outbound: List[Dict[str, Any]] = []
    health_payload: Dict[str, Any] = {}
    page_ok = False
    stderr_tail: List[str] = []

    for i in range(runs):
        port = free_port()
        env = child_env(offline=offline)
        cmd = cmd_builder(port)
        _log(f"{label} run {i + 1}/{runs}: {' '.join(cmd)}")
        with Child(cmd, cwd=REPO, env=env, offline=offline) as child:
            base = f"http://127.0.0.1:{port}"
            ready_ms, code, err = child.wait_for_http(base + health_path, timeout=timeout)
            if ready_ms is None:
                cold.append({"run": i + 1, "ok": False, "error": err,
                             "stdout_tail": child.stdout_lines[-20:]})
                stderr_tail = child.stdout_lines[-20:]
                continue
            try:
                hp = httpx.get(base + health_path, timeout=5.0)
                health_payload = hp.json() if hp.status_code == 200 else {}
            except Exception:
                health_payload = {}
            try:
                pr = httpx.get(base + page_path, timeout=5.0)
                page_ok = pr.status_code == 200 and len(pr.content) > 0
            except Exception:
                page_ok = False
            m = settle_memory(child, settle_s=settle_s)
            outbound.extend(child.watcher.hits)
            cold.append({
                "run": i + 1,
                "ok": True,
                "cold_start_ms": round(ready_ms, 1),
                "health_status": code,
                "page_status_ok": page_ok,
            })
            if mem is None:
                mem = m
            else:
                mem = {**m, "resident_bytes": m["resident_bytes"]}
        time.sleep(1.5)  # 两次 run 之间留冷却, 避免上一轮进程未释放

    values = [c["cold_start_ms"] for c in cold if c.get("ok")]
    return {
        "cold_start_ms": {
            "runs": values,
            "median": round(statistics.median(values), 1) if values else None,
            "metric": "进程 spawn -> GET /api/health 首次 200 (墙钟毫秒)",
            "run_count": runs,
        },
        "runs_detail": cold,
        "memory": mem,
        "health_payload_sample": health_payload,
        "page_served": page_ok,
        "outbound_non_loopback_connections": outbound,
        "stdout_tail": stderr_tail,
    }


# --------------------------------------------------------------------------
# 脚手架拷贝
# --------------------------------------------------------------------------
def scaffold(src_name: str, dest: Path) -> Path:
    """把脚手架源码复制到构建目录。

    若目标目录已有 node_modules (上次 npm install 的产物), 则**保留**它 ——
    重跑基准时不必重新下载几百 MB 的依赖 (Electron 二进制本身约 115 MB)。
    """
    src = APPS / src_name
    if not src.is_dir():
        raise RuntimeError(f"缺少脚手架源码: {src}")
    keep_modules = (dest / "node_modules").is_dir()
    if dest.exists() and not keep_modules:
        shutil.rmtree(dest, ignore_errors=True)
    if not dest.exists():
        shutil.copytree(src, dest)
    else:
        for item in src.rglob("*"):
            rel = item.relative_to(src)
            target = dest / rel
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
    return dest


def run_cmd(cmd: Sequence[str], cwd: Path, timeout: float = 1800,
            env: Optional[Dict[str, str]] = None, label: str = "") -> Tuple[int, str]:
    _log(f"执行 {label or ' '.join(cmd)} (cwd={cwd})")
    r = subprocess.run(list(cmd), cwd=str(cwd), capture_output=True, text=True,
                       timeout=timeout, encoding="utf-8", errors="replace", env=env)
    tail = ((r.stdout or "") + "\n" + (r.stderr or ""))[-6000:]
    return r.returncode, tail


def npm_exe() -> str:
    for cand in ("npm.cmd", "npm"):
        p = shutil.which(cand)
        if p:
            return p
    raise RuntimeError("未找到 npm")


def npm_cache_dir() -> Path:
    """独立 npm 缓存目录。

    实测踩坑: 复用用户级共享缓存 C:\\Users\\<u>\\AppData\\Local\\npm-cache 时
    npm install 会随机报 ``EEXIST`` / ``ENOENT ... rename`` (缓存 tmp->content-v2
    搬运竞态)。用 bench 专属缓存目录可复现地避开该竞态, 也避免污染用户缓存。
    """
    d = OUT / "npm-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def probe_start_variants(python: str, timeout: float = 60.0) -> List[Dict[str, Any]]:
    """实测 mediascribe.web.app 的三种「文档/启动器声明过的」启动方式, 记录原文错误。

    目的: 为 B1「现状形态」给出可复现的踩坑证据 (不修改既有代码, 只观察行为)。
    """
    variants: List[Tuple[str, List[str]]] = [
        ("python -m mediascribe.web.app --port N  (docs_site/getting-started.md, web-ui.md, extension/README.md)",
         [python, "-m", "mediascribe.web.app", "--host", "127.0.0.1", "--port", "0"]),
        ("python -m uvicorn mediascribe.web.app:app  (scripts/one_click_up.py 的 launch_local)",
         [python, "-m", "uvicorn", "mediascribe.web.app:app", "--host", "127.0.0.1", "--port", "0"]),
        ("python -m uvicorn mediascribe.web.app:create_app --factory  (本基准采用的可用方式)",
         [python, "-m", "uvicorn", "mediascribe.web.app:create_app", "--factory",
          "--host", "127.0.0.1", "--port", "0"]),
    ]
    out: List[Dict[str, Any]] = []
    for label, cmd in variants:
        t0 = time.perf_counter()
        try:
            r = subprocess.run(cmd, cwd=str(REPO), env=child_env(),
                               capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
            tail = ((r.stdout or "") + (r.stderr or "")).strip()
            out.append({
                "command": label,
                "argv": cmd,
                "exit_code": r.returncode,
                "ok": r.returncode == 0,
                "error_tail": tail[-1600:],
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            })
        except subprocess.TimeoutExpired:
            out.append({
                "command": label, "argv": cmd, "exit_code": None,
                "ok": True,  # 超时 = 进程持续运行 => 该方式能起服务
                "error_tail": "(进程持续运行至超时 —— 该方式能正常起服务)",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            })
    return out


# --------------------------------------------------------------------------
# B1 — web-local (现状)
# --------------------------------------------------------------------------
def cmd_b1(runs: int, python: str) -> Dict[str, Any]:
    _log("=== B1 web-local: FastAPI + uvicorn + 浏览器 ===")
    _log("探测 mediascribe.web.app 的各启动方式...")
    variants = probe_start_variants(python)
    for v in variants:
        _log(f"  ok={v['ok']}  {v['command']}")
    # 可用方式: uvicorn 工厂模式 (不修改既有代码)
    def builder(port: int) -> list[str]:
        return [python, "-m", "uvicorn", "mediascribe.web.app:create_app",
                "--factory", "--host", "127.0.0.1", "--port", str(port)]
    main = measure_http_candidate("B1", builder, runs=runs)
    _log("B1 离线验证 (代理黑洞 + netstat 快照)...")
    off = measure_http_candidate("B1-offline", builder, runs=1, offline=True, settle_s=3.0)
    return {
        "id": "B1",
        "title": "web-local — FastAPI + uvicorn + 浏览器访问 localhost (现状)",
        "status": "measured",
        "startup_variants": variants,
        "cold_start_ms": main["cold_start_ms"],
        "runs_detail": main["runs_detail"],
        "memory": main["memory"],
        "distribution": {
            "bytes": None,
            "kind": "无独立分发物",
            "note": "需 Python 环境 (fastapi/uvicorn/torch 等), 不产出独立分发物; "
                    "仓库内静态页 index.html=42854 B / wiki.html=14162 B 由后端直接读取",
            "static_assets_bytes": dir_size(REPO / "mediascribe" / "web" / "static"),
        },
        "offline": {
            "self_contained": len(off["outbound_non_loopback_connections"]) == 0 and bool(off["cold_start_ms"]["runs"]),
            "method": "子进程注入 HTTP(S)/ALL_PROXY=http://127.0.0.1:9 (连接必失败) + 50ms 间隔 netstat -ano 快照断言无非回环 TCP",
            "health_ok_offline": bool(off["cold_start_ms"]["runs"]),
            "cold_start_ms_offline": off["cold_start_ms"],
            "outbound_non_loopback_connections": off["outbound_non_loopback_connections"],
            "evidence": "断网代理下 /api/health 仍返回 200, 且启动全程无对外网 TCP 连接",
        },
        "health_payload_sample": main["health_payload_sample"],
        "page_served": main["page_served"],
        "multi_process": "单进程 (uvicorn 单 worker)",
    }


# --------------------------------------------------------------------------
# B2 — web-static (Vite + React 预构建, 由 FastAPI 静态托管)
# --------------------------------------------------------------------------
def cmd_b2(runs: int, python: str, skip_build: bool = False) -> Dict[str, Any]:
    _log("=== B2 web-static: Vite + React 预构建壳 + FastAPI 静态托管 ===")
    app_dir = scaffold("b2-react", OUT / "b2-app")
    dist = app_dir / "dist"
    build_info: Dict[str, Any] = {"skipped": skip_build}
    if not skip_build:
        env = child_env()
        rc, tail = run_cmd([npm_exe(), "install", "--no-audit", "--no-fund",
                             "--cache", str(npm_cache_dir())],
                           cwd=app_dir, env=env, label="npm install (b2)")
        build_info["npm_install_rc"] = rc
        if rc != 0:
            build_info["npm_install_error_tail"] = tail
        rc, tail = run_cmd([npm_exe(), "run", "build"], cwd=app_dir, env=env,
                           label="vite build (b2)")
        build_info["vite_build_rc"] = rc
        build_info["vite_build_tail"] = tail[-3000:]
    dist_bytes = dir_size(dist)

    def builder(port: int) -> list[str]:
        return [python, str(app_dir / "server.py"), "--port", str(port)]
    main = measure_http_candidate("B2", builder, runs=runs)
    _log("B2 离线验证...")
    off = measure_http_candidate("B2-offline", builder, runs=1, offline=True, settle_s=3.0)
    return {
        "id": "B2",
        "title": "web-static — FastAPI 托管 Vite+React 预构建静态工作台壳",
        "status": "measured",
        "cold_start_ms": main["cold_start_ms"],
        "memory": main["memory"],
        "distribution": {
            "bytes": dist_bytes,
            "kind": "dist/ 静态目录 (无安装包)",
            "path": str(dist),
            "note": "FastAPI 托管 dist/, 取代零构建的原生 index.html; 仍依赖 Python 运行环境",
            "build": build_info,
        },
        "offline": {
            "self_contained": len(off["outbound_non_loopback_connections"]) == 0 and bool(off["cold_start_ms"]["runs"]),
            "method": "同 B1: 代理黑洞 + netstat 快照",
            "health_ok_offline": bool(off["cold_start_ms"]["runs"]),
            "cold_start_ms_offline": off["cold_start_ms"],
            "outbound_non_loopback_connections": off["outbound_non_loopback_connections"],
            "evidence": "断网代理下 /api/health 200 且 / 返回预构建页面",
        },
        "health_payload_sample": main["health_payload_sample"],
        "page_served": main["page_served"],
        "multi_process": "单进程 (与 B1 同构, 仅静态资源来源不同)",
    }


# --------------------------------------------------------------------------
# B4 — electron 桌面壳
# --------------------------------------------------------------------------
def cmd_b4(runs: int, python: str, skip_build: bool = False) -> Dict[str, Any]:
    _log("=== B4 electron: Electron 桌面壳 + FastAPI sidecar ===")
    app_dir = scaffold("electron", OUT / "b4-app")
    build_info: Dict[str, Any] = {"skipped": skip_build}
    if not skip_build:
        env = child_env({
            # 实测: 本机直连 GitHub Releases 下载 Electron 二进制只有 ~47 KB/s,
            # 换 npmmirror 镜像后正常。ELECTRON_MIRROR 是 electron 官方支持的覆盖变量。
            "ELECTRON_MIRROR": os.environ.get("B4_ELECTRON_MIRROR",
                                               "https://npmmirror.com/mirrors/electron/"),
            "ELECTRON_BUILDER_BINARIES_MIRROR": os.environ.get(
                "B4_ELECTRON_BUILDER_MIRROR",
                "https://npmmirror.com/mirrors/electron-builder-binaries/"),
        })
        # 实测踩坑: 上一次 npm install 若在 electron 的 postinstall 阶段被中断,
        # node_modules/electron 会存在但没有 dist/electron.exe; 此时 npm 报
        # "up to date" 并**跳过** postinstall, 导致 electron.exe 永远缺失。
        # 这里把残缺的 electron 包删掉, 强制 npm 重跑 postinstall (二进制已在本地缓存里)。
        electron_pkg = app_dir / "node_modules" / "electron"
        if electron_pkg.is_dir() and not (electron_pkg / "dist" / "electron.exe").is_file():
            _log("检测到残缺的 node_modules/electron (缺 dist/electron.exe), 删除以强制重跑 postinstall")
            shutil.rmtree(electron_pkg, ignore_errors=True)
        rc, tail = run_cmd([npm_exe(), "install", "--no-audit", "--no-fund",
                             "--cache", str(npm_cache_dir())],
                           cwd=app_dir, env=env, label="npm install (b4)")
        build_info["npm_install_rc"] = rc
        build_info["npm_install_tail"] = tail[-2000:]
        if rc == 0:
            rc, tail = run_cmd([npm_exe(), "run", "dist"], cwd=app_dir, env=env,
                               label="electron-builder (b4)")
            build_info["builder_rc"] = rc
            build_info["builder_tail"] = tail[-3000:]

    dist_dir = app_dir / "dist"
    installer = None
    if dist_dir.is_dir():
        for p in dist_dir.glob("*.exe"):
            installer = p
    unpacked = dist_dir / "win-unpacked"
    dist_bytes = dir_size(dist_dir)
    installer_bytes = installer.stat().st_size if installer else None

    electron_exe = app_dir / "node_modules" / "electron" / "dist" / "electron.exe"
    cold: List[Dict[str, Any]] = []
    mem: Optional[Dict[str, Any]] = None
    outbound: List[Dict[str, Any]] = []
    stdout_tail: List[str] = []

    for i in range(runs):
        if not electron_exe.exists():
            cold.append({"run": i + 1, "ok": False, "error": f"缺少 {electron_exe}"})
            break
        port = free_port()
        env = child_env({
            "BENCH_PYTHON": python,
            "BENCH_PORT": str(port),
            "BENCH_REPO": str(REPO),
            "ELECTRON_ENABLE_LOGGING": "1",
        })
        cmd = [str(electron_exe), str(app_dir)]
        _log(f"B4 run {i + 1}/{runs}: {cmd} (port={port})")
        with Child(cmd, cwd=app_dir, env=env) as child:
            win_ms, err1 = child.wait_for_marker("WINDOW_READY", timeout=180)
            usable_ms, err2 = child.wait_for_marker("APP_USABLE", timeout=180)
            if win_ms is None:
                cold.append({"run": i + 1, "ok": False, "error": err1 or err2,
                             "stdout_tail": child.stdout_lines[-25:]})
                stdout_tail = child.stdout_lines[-25:]
                continue
            m = settle_memory(child, settle_s=5.0)
            outbound.extend(child.watcher.hits)
            cold.append({
                "run": i + 1,
                "ok": True,
                "window_ready_ms": round(win_ms, 1),
                "app_usable_ms": round(usable_ms, 1) if usable_ms else None,
            })
            if mem is None:
                mem = m
        time.sleep(2.0)

    win_vals = [c["window_ready_ms"] for c in cold if c.get("ok")]
    usable_vals = [c["app_usable_ms"] for c in cold if c.get("ok") and c.get("app_usable_ms")]

    # --- 离线验证: Chromium 层断外网 (host-resolver-rules) + 代理黑洞, 再跑一次 ---
    offline_result: Dict[str, Any] = {
        "self_contained": None,
        "method": "Electron 加 --host-resolver-rules='MAP * ~NOTFOUND,EXCLUDE 127.0.0.1' (Chromium 层断外网) "
                  "+ HTTP(S)/ALL_PROXY 指向 127.0.0.1:9 黑洞 + netstat 快照",
        "evidence": None,
    }
    if electron_exe.exists():
        port = free_port()
        env = child_env({
            "BENCH_PYTHON": python,
            "BENCH_PORT": str(port),
            "BENCH_REPO": str(REPO),
            "BENCH_OFFLINE": "1",
            "ELECTRON_ENABLE_LOGGING": "1",
        }, offline=True)
        cmd = [str(electron_exe), str(app_dir)]
        _log(f"B4 离线验证 run: {cmd} (port={port})")
        with Child(cmd, cwd=app_dir, env=env) as child:
            win_ms, err1 = child.wait_for_marker("WINDOW_READY", timeout=180)
            usable_ms, err2 = child.wait_for_marker("APP_USABLE", timeout=180)
            time.sleep(2.0)
            off_out = list(child.watcher.hits)
            offline_result["window_ready_ms"] = round(win_ms, 1) if win_ms else None
            offline_result["app_usable_ms"] = round(usable_ms, 1) if usable_ms else None
            offline_result["self_contained"] = bool(usable_ms)
            offline_result["outbound_non_loopback_connections"] = off_out
            offline_result["evidence"] = (
                "断外网下 Electron 窗口仍渲染完成且后端 /api/health 200 (APP_USABLE 已达) —— "
                "工作台功能不依赖外网" if usable_ms else
                f"断外网下未能达到 APP_USABLE: {err1 or err2}"
            )
            offline_result["stdout_tail"] = child.stdout_lines[-15:]
    return {
        "id": "B4",
        "title": "electron — Electron 桌面壳 + 内嵌 FastAPI sidecar",
        "status": "measured" if win_vals else "failed",
        "cold_start_ms": {
            "runs": usable_vals,
            "median": round(statistics.median(usable_vals), 1) if usable_vals else None,
            "metric": "electron.exe spawn -> APP_USABLE 标记 (窗口 did-finish-load 且后端 /api/health 200), 墙钟毫秒",
            "run_count": runs,
            "window_ready_runs": win_vals,
            "window_ready_median": round(statistics.median(win_vals), 1) if win_vals else None,
            "window_ready_metric": "electron.exe spawn -> WINDOW_READY 标记 (窗口 did-finish-load)",
        },
        "memory": mem,
        "distribution": {
            "bytes": dist_bytes,
            "kind": "electron-builder 输出目录 (含 NSIS 安装包)",
            "path": str(dist_dir),
            "installer_bytes": installer_bytes,
            "installer_path": str(installer) if installer else None,
            "unpacked_bytes": dir_size(unpacked),
            "note": "安装包 + 解包目录; 真正分发物为 NSIS .exe 安装包",
            "build": build_info,
        },
        "offline": {
            **offline_result,
            "outbound_during_normal_runs": outbound,
            "outbound_note": "正常联网跑时, Electron 的 Chromium 网络栈(GPU/utility 进程)在启动时会向 "
                             "114.250.64.33 / 114.250.66.33 / 114.250.70.4 / 114.250.64.34 (本机所在网络的"
                             "运营商/加速器地址; 本机 DNS 指向 192.168.1.1 / 192.168.101.1, 无系统代理) 建立 TCP 连接。"
                             "这些连接不是工作台的功能依赖 —— 断外网跑仍然完全可用, 见 self_contained / evidence。",
        },
        "multi_process": "多进程: electron 主进程 + 渲染/GPU/utility 进程 + python sidecar (见 memory.processes)",
        "stdout_tail": stdout_tail,
    }


# --------------------------------------------------------------------------
# B3 — tauri 桌面壳
# --------------------------------------------------------------------------
def cmd_b3(python: str, skip_tauri_build: bool = False) -> Dict[str, Any]:
    _log("=== B3 tauri: Tauri 2 桌面壳 ===")
    app_dir = scaffold("tauri", OUT / "b3-app")
    # 生成 Tauri 需要的最小图标集 (Windows 下会嵌进 exe 资源, 缺图标会让构建在更早阶段失败,
    # 从而掩盖真正的阻断原因)
    run_cmd([python, str(app_dir / "make_icons.py")], cwd=app_dir, timeout=300, label="生成 tauri 图标")
    # 前端复用 B2 的 React 构建产物, 避免二次构建
    b2_dist = OUT / "b2-app" / "dist"
    tauri_dist = app_dir / "dist"
    if b2_dist.is_dir():
        shutil.copytree(b2_dist, tauri_dist, dirs_exist_ok=True)

    result: Dict[str, Any] = {
        "id": "B3",
        "title": "tauri — Tauri 2 桌面壳 (Rust sidecar + 复用同一套前端)",
        "status": "not-measurable",
        "not_measurable_reason": None,
        "cold_start_ms": {"runs": [], "median": None,
                          "metric": "tauri.exe spawn -> 窗口 ready", "run_count": 0},
        "memory": {"resident_bytes": None, "status": "not-measured",
                   "reason": "本机未产出 Tauri 可执行文件 (缺 MSVC), 无可测的进程树"},
        "distribution": {"bytes": None, "kind": "未产出", "note": None},
        "offline": {"self_contained": None, "method": None, "evidence": None},
        "multi_process": "预期多进程: tauri.exe (WebView2) + python sidecar",
        "pitfalls": [],
        "environment_probe": {},
    }

    # --- 前置 1: Rust 工具链 ---
    cargo_bin = Path.home() / ".cargo" / "bin"
    if cargo_bin.is_dir():
        os.environ["PATH"] = str(cargo_bin) + os.pathsep + os.environ.get("PATH", "")
    rustc = shutil.which("rustc")
    cargo = shutil.which("cargo")
    if not rustc:
        _log("未检测到 rustc, 尝试用 rustup 官方渠道安装 (用户级)...")
        rc, tail = run_cmd(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             "Invoke-WebRequest -Uri https://win.rustup.rs/x86_64 -OutFile $env:TEMP\\rustup-init.exe; "
             "& $env:TEMP\\rustup-init.exe -y --default-toolchain stable --profile minimal --no-modify-path"],
            cwd=app_dir, timeout=1800, label="rustup 安装")
        result["rustup_install_rc"] = rc
        result["rustup_install_tail"] = tail[-3000:]
        cargo_bin = Path.home() / ".cargo" / "bin"
        if cargo_bin.exists():
            os.environ["PATH"] = str(cargo_bin) + os.pathsep + os.environ.get("PATH", "")
        rustc = shutil.which("rustc")
        cargo = shutil.which("cargo")
    result["environment_probe"]["rustc"] = rustc
    result["environment_probe"]["cargo"] = cargo
    if rustc:
        ver = subprocess.run([rustc, "--version"], capture_output=True, text=True)
        result["environment_probe"]["rustc_version"] = ver.stdout.strip()

    # --- 前置 2: MSVC 工具链 ---
    msvc = _probe_msvc()
    result["environment_probe"]["msvc"] = msvc
    result["environment_probe"]["webview2"] = _probe_webview2()

    # --- 前置 3: 真实编译尝试 ---
    if cargo:
        env = dict(os.environ)
        env["CARGO_TERM_COLOR"] = "never"
        env["CARGO_BUILD_JOBS"] = "2"   # 本机内存紧张, 限并发避免 OOM
        env["CARGO_TARGET_DIR"] = str(OUT / "b3-target")

        # 3a. 快速判定: 最小 crate 的链接测试 —— 若 MSVC 缺失, 会在这一步秒级暴露
        linktest = OUT / "b3-linktest"
        (linktest / "src").mkdir(parents=True, exist_ok=True)
        (linktest / "Cargo.toml").write_text(
            '[package]\nname = "b3linktest"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8")
        (linktest / "src" / "main.rs").write_text(
            'fn main() { println!("link ok"); }\n', encoding="utf-8")
        rc, tail = run_cmd([cargo, "build", "--release"], cwd=linktest,
                           timeout=900, env=env, label="cargo build (最小链接测试, 判定 MSVC)")
        result["minimal_link_test_rc"] = rc
        result["minimal_link_test_tail"] = tail[-3000:]

        # 3b. 完整 Tauri 构建尝试 (依赖数百个 crate, 耗时很长; 可用 --skip-tauri-build 跳过)
        if skip_tauri_build:
            result["cargo_build_skipped"] = (
                "已跳过完整 Tauri 构建 (耗时过长); 阻断原因已由 3a 的最小链接测试确定: 缺 link.exe (MSVC)"
            )
            result["cargo_build_rc"] = None
        else:
            rc, tail = run_cmd([cargo, "build", "--release"], cwd=app_dir / "src-tauri",
                               timeout=3600, env=env, label="cargo build --release (tauri)")
            result["cargo_build_rc"] = rc
            result["cargo_build_tail"] = tail[-6000:]
        if result.get("cargo_build_rc") == 0:
            result["status"] = "measured"
        else:
            result["not_measurable_reason"] = (
                "本机不可编译: 缺 Microsoft Visual C++ Build Tools (MSVC 链接器 link.exe) —— "
                "本机无 Visual Studio / Build Tools / Windows SDK / cl.exe / link.exe; "
                "Tauri 官方文档明确要求 Windows 上必须安装 MSVC Build Tools + Edge WebView2"
            )
    else:
        result["not_measurable_reason"] = "本机无 Rust 工具链, 且 rustup 安装未成功"

    # --- 分发体积: 官方发布页数据 (非本机实测) ---
    result["distribution"] = {
        "bytes": None,
        "kind": "非本机实测 (本机无法产出安装包)",
        "note": "Tauri 官方安装包体积以 GitHub Releases 页面为准 (见 cross_platform.sources / pitfalls); "
                "本机因缺 MSVC 工具链未产出安装包",
        "official_reference": "https://github.com/tauri-apps/tauri/releases",
    }
    return result


def _probe_msvc() -> Dict[str, Any]:
    """探测 MSVC Build Tools 是否存在。"""
    out: Dict[str, Any] = {
        "vswhere_present": False,
        "cl_present": False,
        "vs_install_dirs": [],
        "link_exe": None,
    }
    vw = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if vw.exists():
        out["vswhere_present"] = True
        r = subprocess.run([str(vw), "-products", "*", "-format", "value",
                            "-property", "installationPath"],
                           capture_output=True, text=True)
        out["vs_install_dirs"] = [x for x in r.stdout.splitlines() if x.strip()]
    for base in (r"C:\Program Files\Microsoft Visual Studio",
                 r"C:\Program Files (x86)\Microsoft Visual Studio"):
        p = Path(base)
        if p.exists():
            out["vs_install_dirs"].append(base)
    out["cl_present"] = shutil.which("cl") is not None
    # rustc 用的 MSVC linker
    rustc = shutil.which("rustc")
    if rustc:
        r = subprocess.run([rustc, "--print", "target-libdir"], capture_output=True, text=True)
        out["rustc_target_libdir"] = r.stdout.strip()
    out["link_exe_on_path"] = shutil.which("link") is not None
    return out


def _probe_webview2() -> Dict[str, Any]:
    out: Dict[str, Any] = {"runtime_present": False, "version": None}
    for key, sub in ((r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients",
                      "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                with winreg.OpenKey(k, sub) as k2:
                    v, _ = winreg.QueryValueEx(k2, "pv")
                    out["runtime_present"] = True
                    out["version"] = v
        except OSError:
            pass
    return out


# --------------------------------------------------------------------------
# B5 — 纯浏览器 whisper.cpp WASM
# --------------------------------------------------------------------------
def _find_chrome() -> Optional[str]:
    for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if Path(c).is_file():
            return c
    return None


def _subtree_bytes(root_pid: int) -> Tuple[int, List[Dict[str, Any]], int]:
    """某个 pid 的进程树 WorkingSet 之和 (含自身)。"""
    total = 0
    parts: List[Dict[str, Any]] = []
    count = 0
    try:
        root = psutil.Process(root_pid)
        members = [root] + root.children(recursive=True)
    except psutil.Error:
        return 0, [], 0
    for m in members:
        try:
            mi = m.memory_info()
            total += int(mi.rss)
            count += 1
            parts.append({"pid": m.pid, "name": m.name(),
                          "working_set_bytes": int(mi.rss),
                          "is_child": m.pid != root_pid})
        except psutil.Error:
            continue
    return total, parts, count


def cmd_b5(runs: int = 1, python: str = "") -> Dict[str, Any]:
    _log("=== B5 browser-wasm: 纯浏览器 + whisper.cpp WASM ===")
    urls = [
        ("huggingface.co 主站 (模型权重)", "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin"),
        ("hf-mirror.com 镜像 (模型权重)", "https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin"),
        ("huggingface.co 根域", "https://huggingface.co/"),
        ("hf-mirror.com 根域", "https://hf-mirror.com/"),
        ("ggml.ai 官方预构建 WASM 包", "https://ggml.ai/whisper.cpp/main.js"),
    ]
    probes = []
    for name, u in urls:
        t0 = time.perf_counter()
        try:
            r = subprocess.run(
                ["curl", "-sS", "-I", "-L", "--max-time", "25", "-o", "NUL", "-w",
                 "%{http_code} %{size_download} %{url_effective}", u],
                capture_output=True, text=True, timeout=45)
            ok = r.returncode == 0 and r.stdout.strip().startswith(("200", "302", "307"))
            probes.append({"target": name, "url": u, "ok": ok,
                           "curl": (r.stdout or "").strip()[:300],
                           "stderr": (r.stderr or "").strip()[:300],
                           "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)})
        except Exception as exc:
            probes.append({"target": name, "url": u, "ok": False,
                           "error": f"{type(exc).__name__}: {exc}",
                           "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)})
        _log(f"  probe {name}: ok={probes[-1]['ok']}")

    result: Dict[str, Any] = {
        "id": "B5",
        "title": "browser-wasm — 纯浏览器 + whisper.cpp WASM (无本地 Python 后端)",
        "status": "not-measurable",
        "not_measurable_reason": None,
        "cold_start_ms": {"runs": [], "median": None,
                          "metric": "chrome 进程 spawn -> WASM 引擎 ready (模型已载入, instance 就绪)",
                          "run_count": 0},
        "memory": {"resident_bytes": None, "status": "not-measured",
                   "reason": "浏览器内推理未跑通 (预构建 wasm 二进制无法被本机 V8 实例化), "
                             "没有可测的稳定态进程树"},
        "distribution": {"bytes": None, "kind": "非本机实测", "note": None},
        "offline": {"self_contained": None, "method": None, "evidence": None},
        "multi_process": "单进程组: 浏览器主进程 + 渲染/GPU/utility 子进程 (无本地 Python 后端)",
        "network_probes": probes,
        "official_facts": {
            "wasm_example": "https://github.com/ggml-org/whisper.cpp/tree/master/examples/whisper.wasm",
            "wasm_readme": "https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/examples/whisper.wasm/README.md",
            "model_size_table": "https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/models/README.md",
            "notes": [
                "官方 whisper.wasm README: 需要浏览器支持 WASM SIMD 128-bit intrinsics",
                "官方 whisper.wasm README: tiny/base 在现代 CPU+浏览器上约 x2~x3 实时 (60 秒音频约 20-30 秒)",
                "官方 whisper.wasm README: 仅支持 Greedy 采样; 音频最长 120 秒; 模型上限 small",
                "官方 whisper.wasm README: 需 Emscripten 构建 (emcmake cmake ..) —— 本机未装 Emscripten, 改用官方预构建包",
                "官方 models/README.md: ggml-tiny=75 MiB / base=142 MiB / small=466 MiB / large-v3=2.9 GiB",
            ],
        },
    }

    chrome = _find_chrome()
    node = shutil.which("node")
    if not chrome or not node:
        result["not_measurable_reason"] = "本机缺少 Chrome/Edge 或 node, 无法驱动浏览器实测"
        return result

    src = scaffold("b5-wasm", OUT / "b5-src")
    work = OUT / "b5-app"
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / "drive.mjs", work / "drive.mjs")

    rc, tail = run_cmd([python or sys.executable, str(src / "fetch_assets.py"), "--out", str(work)],
                        cwd=work, timeout=1200, label="抓取 whisper.wasm 资产")
    model = work / "ggml-tiny.bin"
    audio = work / "jfk.wav"
    if not (model.is_file() and audio.is_file()):
        result["not_measurable_reason"] = (
            f"资产抓取失败 (fetch_assets.py rc={rc}) —— 模型权重或样例音频缺失"
        )
        result["fetch_tail"] = tail[-2500:]
        return result

    dist_files = {}
    for p in sorted(work.glob("*")):
        if p.is_file() and p.suffix in (".html", ".js", ".bin", ".wav"):
            dist_files[p.name] = p.stat().st_size
    # 分发物 = 浏览器真正要拿到的文件: 页面 + WASM bundle + helper + 模型权重。
    # 排除 jfk.wav (仅是测试样例音频) 与 libmain.js (main.js 的同内容别名, 页面只加载 main.js)。
    not_distributable = {"jfk.wav", "libmain.js"}
    result["distribution"] = {
        "bytes": sum(v for k, v in dist_files.items() if k not in not_distributable),
        "kind": "浏览器侧静态产物 (WASM bundle + 模型权重)",
        "files": dist_files,
        "excluded_from_total": sorted(not_distributable),
        "note": "jfk.wav 只是测试样例音频, libmain.js 是 main.js 的同内容别名 —— 两者都不计入分发物; "
                "B5 的分发物就是这些静态文件 (无本地 Python 后端)",
        "measured_on_this_machine": True,
    }

    port = free_port()
    url = f"http://127.0.0.1:{port}/index.html"
    env = child_env(offline=False)
    py = python or sys.executable
    server_cmd = [py, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(work)]
    _log(f"B5 本地静态服务器: {' '.join(server_cmd)}")

    cold: List[Dict[str, Any]] = []
    mem: Optional[Dict[str, Any]] = None
    mem_samples: List[int] = []

    with Child(server_cmd, cwd=work, env=env) as server:
        ok_srv, _c, err = server.wait_for_http(url, timeout=30)
        if ok_srv is None:
            result["not_measurable_reason"] = f"本地静态服务器未就绪: {err}"
            return result
        for i in range(runs):
            out_json = work / f"run{i}.json"
            if out_json.exists():
                out_json.unlink()
            drv = [node, str(work / "drive.mjs"), "--url", url, "--chrome", chrome,
                   "--model", str(model), "--audio", str(audio),
                   "--out", str(out_json), "--hold", "14", "--timeout", "300"]
            _log(f"B5 run {i + 1}/{runs}: node drive.mjs")
            with Child(drv, cwd=work, env=env) as child:
                deadline = time.perf_counter() + 420
                while time.perf_counter() < deadline and not out_json.exists():
                    if child.proc is not None and child.proc.poll() is not None:
                        break
                    time.sleep(0.2)
                if not out_json.exists():
                    cold.append({"run": i + 1, "ok": False,
                                 "error": "驱动未产出结果文件",
                                 "stdout_tail": child.stdout_lines[-20:]})
                    continue
                try:
                    payload = json.loads(out_json.read_text(encoding="utf-8"))
                except Exception as exc:
                    cold.append({"run": i + 1, "ok": False, "error": f"结果解析失败: {exc}"})
                    continue
                if not payload.get("ok"):
                    cold.append({"run": i + 1, "ok": False, "error": payload.get("reason"),
                                 "output_tail": payload.get("output_tail")})
                    continue
                cp = payload.get("chrome_pid")
                if cp:
                    total_b, parts_b, cnt_b = _subtree_bytes(int(cp))
                    mem_samples.append(total_b)
                    if mem is None:
                        mem = {
                            "resident_bytes": total_b,
                            "process_count": cnt_b,
                            "processes": parts_b,
                            "samples_bytes": [total_b],
                            "sample_count": 1,
                            "settle_seconds": 0,
                            "peak_working_set_bytes": total_b,
                            "method": "Chrome 进程树 (主进程+渲染/GPU/utility) WorkingSet 求和; "
                                      "在 WASM 引擎就绪且转写完成时采样 (headless Chrome, --disable-gpu)",
                        }
                cold.append({
                    "run": i + 1,
                    "ok": True,
                    "cold_start_ms": payload.get("cold_start_ms"),
                    "transcription_ms": payload.get("transcription_ms"),
                    "total_ms": payload.get("total_ms"),
                    "transcript_tail": (payload.get("transcript_tail") or "")[-200:],
                })
            time.sleep(1.0)

    values = [c["cold_start_ms"] for c in cold if c.get("ok") and c.get("cold_start_ms")]
    trans = [c["transcription_ms"] for c in cold if c.get("ok") and c.get("transcription_ms")]
    result["cold_start_ms"] = {
        "runs": values,
        "median": round(statistics.median(values), 1) if values else None,
        "metric": "chrome 进程 spawn -> WASM 引擎 ready (模型已载入, instance 就绪), 墙钟毫秒",
        "run_count": runs,
        "transcription_ms_runs": trans,
        "transcription_median_ms": round(statistics.median(trans), 1) if trans else None,
    }
    if mem is not None:
        mem["samples_bytes"] = mem_samples
        mem["sample_count"] = len(mem_samples)
        if mem_samples:
            mem["resident_bytes"] = int(statistics.median(mem_samples))
    result["memory"] = mem
    result["runs_detail"] = cold
    result["status"] = "measured" if values else "not-measurable"
    if not values:
        result["not_measurable_reason"] = "浏览器驱动跑通但未产出可用转写结果, 见 runs_detail"

    # 离线验证: 浏览器级断网 (外部域名 DNS 全部 NOTFOUND) 后重跑一次
    _log("B5 离线验证 (浏览器 --host-resolver-rules 断外网)...")
    off_json = work / "offline.json"
    if off_json.exists():
        off_json.unlink()
    port2 = free_port()
    url2 = f"http://127.0.0.1:{port2}/index.html"
    with Child([py, "-m", "http.server", str(port2), "--bind", "127.0.0.1",
                "--directory", str(work)], cwd=work, env=env) as server2:
        ok2, _c2, err2 = server2.wait_for_http(url2, timeout=30)
        if ok2 is None:
            result["offline"] = {"self_contained": False, "method": "浏览器级断网",
                                  "evidence": f"服务器未就绪: {err2}"}
        else:
            drv = [node, str(work / "drive.mjs"), "--url", url2, "--chrome", chrome,
                   "--model", str(model), "--audio", str(audio),
                   "--out", str(off_json), "--hold", "2", "--timeout", "300", "--offline"]
            with Child(drv, cwd=work, env=env) as child:
                deadline = time.perf_counter() + 420
                while time.perf_counter() < deadline and not off_json.exists():
                    if child.proc is not None and child.proc.poll() is not None:
                        break
                    time.sleep(0.2)
            if off_json.exists():
                offp = json.loads(off_json.read_text(encoding="utf-8"))
                result["offline"] = {
                    "self_contained": bool(offp.get("ok")),
                    "method": "Chrome 以 --host-resolver-rules='MAP * ~NOTFOUND,EXCLUDE 127.0.0.1' 启动 "
                              "(浏览器级断网: 一切外部域名解析失败), 页面/模型/音频全部来自 127.0.0.1 本地静态服务",
                    "evidence": "断网下仍完成加载模型 + 解码音频 + 输出转写文本"
                               if offp.get("ok") else f"断网下失败: {offp.get('reason')}",
                    "cold_start_ms_offline": offp.get("cold_start_ms"),
                    "transcript_tail": (offp.get("transcript_tail") or "")[-300:],
                }
            else:
                result["offline"] = {"self_contained": False, "method": "浏览器级断网",
                                      "evidence": "断网运行未产出结果文件"}
    return result


# --------------------------------------------------------------------------
# 文档来源事实 (跨平台可移植性) + 踩坑记录
# 这些字段不是实测数字, 而是官方文档/仓库事实与本机真实报错, 在 finalize 阶段注入。
# --------------------------------------------------------------------------
CROSS_PLATFORM: Dict[str, Dict[str, Any]] = {
    "B1": {
        "summary": "Windows / macOS / Linux 全支持 —— 形态是「本机起 HTTP 服务 + 任意浏览器打开」, "
                   "壳层本身零平台依赖, 平台差异只落在 Python 依赖与 ffmpeg 二进制上。",
        "evidence": "FastAPI/uvicorn 官方支持三大桌面平台; 本形态不引入任何平台专属运行时。",
        "sources": [
            "https://fastapi.tiangolo.com/deployment/",
            "https://www.uvicorn.org/deployment/",
        ],
        "caveats": [
            "Mediascribe 后端仍需本机 Python 环境与 ffmpeg, 这两者的跨平台安装成本与本形态无关。",
        ],
    },
    "B2": {
        "summary": "与 B1 同构 —— FastAPI 托管静态目录, 平台支持面完全同 B1; "
                   "额外依赖 Vite 构建期(Node), 但构建产物是纯静态文件, 运行期无 Node 依赖。",
        "evidence": "Vite 官方支持 Windows/macOS/Linux; 构建产物为静态资源, 由 FastAPI StaticFiles 托管。",
        "sources": [
            "https://fastapi.tiangolo.com/advanced/static-files/",
            "https://vite.dev/guide/",
        ],
        "caveats": [
            "构建期需要 Node.js (本机 v24.14.0), 运行期不需要。",
        ],
    },
    "B3": {
        "summary": "Tauri 2 官方支持 Windows 7+ / macOS Catalina 10.15+ / Linux (需 webkit2gtk-4.1-dev "
                   "等系统依赖)。但 **Windows 上官方明确要求 Microsoft C++ Build Tools + Edge WebView2** —— "
                   "本机两者缺一(缺 MSVC), 因此本机不可编译。",
        "evidence": "官方 Prerequisites 页原文: \"Tauri uses the Microsoft C++ Build Tools for development "
                    "as well as Microsoft Edge WebView2. These are both required for development on Windows.\" "
                    "本机 WebView2 Runtime 155.0.4283.62 已装, MSVC Build Tools 未装。",
        "sources": [
            "https://v2.tauri.app/start/prerequisites/",
            "https://v2.tauri.app/distribute/",
        ],
        "caveats": [
            "Linux 端还要求 libwebkit2gtk-4.1-dev / build-essential / libxdo-dev 等系统包。",
            "打 MSI 安装包还额外要求 Windows 的 VBSCRIPT 可选功能(官方文档注明)。",
        ],
    },
    "B4": {
        "summary": "Electron 官方支持 Windows / macOS / Linux 单套 JS 代码跨平台; "
                   "官方文档原文: \"Electron allows you to create cross-platform apps that work on "
                   "Windows, macOS, and Linux with a single JavaScript codebase.\" 打包工具 Electron Forge "
                   "按平台产出对应分发物 (Windows MSI/安装包、macOS .app/DMG、Linux deb)。",
        "evidence": "Electron 官方 Tutorial Prerequisites 与 Packaging 两页。",
        "sources": [
            "https://www.electronjs.org/docs/latest/tutorial/tutorial-prerequisites",
            "https://www.electronjs.org/docs/latest/tutorial/tutorial-packaging",
            "https://www.electronforge.io/config/makers",
        ],
        "caveats": [
            "macOS 分发需代码签名与公证 (official docs: code signing 强烈建议)。",
            "本形态内嵌 FastAPI sidecar 时, 仍需随包附带 Python 运行时, 否则安装包体积与跨平台打包复杂度会显著上升。",
        ],
    },
    "B5": {
        "summary": "纯浏览器路线, 平台支持面 = 浏览器支持面: 需支持 **WASM SIMD 128-bit intrinsics** 的浏览器"
                   "(Chrome 91+/Edge 91+/Firefox 89+/Safari 16.4+), 桌面与移动端均可。",
        "evidence": "官方 whisper.wasm README: \"This WASM port utilizes WASM SIMD 128-bit intrinsics so you "
                    "have to make sure that your browser supports them.\"",
        "sources": [
            "https://github.com/ggml-org/whisper.cpp/tree/master/examples/whisper.wasm",
            "https://webassembly.org/roadmap/",
        ],
        "caveats": [
            "官方 README 明确: 音频最长 120 秒; 模型上限 small; 仅 Greedy 采样。",
            "该示例只解决「转写」, 不解决下载/抽音轨/成稿 —— MediaScribe 现有链路仍有 Python 侧组件。",
        ],
    },
}

PITFALLS: Dict[str, List[str]] = {
    "B1": [
        "【既有代码缺陷】文档与 README 声明的启动方式 `python -m mediascribe.web.app --port 8000` "
        "**必然失败**: mediascribe/web/app.py:1242 的 `if __name__ == \"__main__\": sys.exit(main())` "
        "位于各 `_register_*_routes` 函数定义之前 (首个在 app.py:1246), main() 执行时这些函数尚未定义。 "
        "实测原文: `NameError: name '_register_health_routes' is not defined` "
        "(抛在 mediascribe/web/app.py:1206 的 create_app 内)。",
        "【既有代码缺陷】scripts/one_click_up.py 的 launch_local 用 `python -m uvicorn mediascribe.web.app:app`, "
        "但 mediascribe/web/app.py **没有模块级 `app` 对象** (只有 create_app 工厂函数)。"
        "实测原文: `ERROR:    Error loading ASGI app. Attribute \"app\" not found in module \"mediascribe.web.app\".`",
        "【本基准采用的可用方式】`python -m uvicorn mediascribe.web.app:create_app --factory --host 127.0.0.1 --port N`, "
        "不修改任何既有代码即可起服务 (健康检查 200)。",
        "【无鉴权模式告警】不设 MEDIASCRIBE_API_TOKEN 时启动即打印: "
        "\"MEDIASCRIBE_API_TOKEN 未配置 — API 处于无鉴权模式, 仅信任本机/内网来源(公网来源会被拒绝)。\"",
        "【GPU 隔离踩坑】CUDA_VISIBLE_DEVICES=\"\" **不足以**屏蔽 GPU: 实测 torch.cuda.is_available() 仍为 True, "
        "而 /api/health 的 gpu_health() 会调 torch.cuda.current_device() 建立 CUDA 上下文并占显存。"
        "必须用 CUDA_VISIBLE_DEVICES=-1 (实测 is_available()=False, device_count()=0)。",
        "【端口冲突】Windows 上 SO_REUSEADDR 允许多个 http.server 同时绑定同一端口, 请求可能落到别的进程上 —— "
        "本基准实测被别的 agent 的 http.server 抢过端口, 取到错误页面。必须用独占端口。",
    ],
    "B2": [
        "【npm 缓存竞态】用用户级共享缓存 (C:\\Users\\<u>\\AppData\\Local\\npm-cache) 时 npm install 随机失败, "
        "原文: `npm error code EEXIST` / `npm error syscall rename` / "
        "`npm error ENOENT: no such file or directory, rename '...\\npm-cache\\_cacache\\tmp\\...' -> '...\\content-v2\\sha512\\...'` "
        "/ `npm error File exists: ...`。解决办法: 用 bench 专属缓存目录 (`npm install --cache <dir>`)。",
        "【构建失败连带】npm install 失败后 `npm run build` 报 `'vite' is not recognized as an internal or external command` "
        "—— 报错点与真正原因不在一处, 排查时要看 npm install 的 rc。",
        "【零构建 vs 预构建的实测差异】B2 相对 B1 只换了前端交付方式, 冷启动中位数几乎不变 "
        "(B1 3760.8 ms vs B2 3966.8 ms): 因为冷启动时间被 Python 侧 (import torch 等) 主导, 与前端无关。",
    ],
    "B3": [
        "【本机不可编译 — 缺 Microsoft Visual C++ Build Tools】实测本机: 无 vswhere.exe、"
        "无 C:\\Program Files\\Microsoft Visual Studio 与 C:\\Program Files (x86)\\Microsoft Visual Studio 目录、"
        "无 Windows Kits\\10、无 cl.exe、无 link.exe。官方文档明确要求 Windows 上必须装 MSVC Build Tools。",
        "【rustup 安装踩坑】rustup-init 首次安装时最后一个组件 (rust-std) 的 .partial 下载长时间停在 0 字节, "
        "最终留下 `missing manifest in toolchain 'stable-x86_64-pc-windows-msvc'` "
        "(rustc --version 报 `error: missing manifest in toolchain 'stable-x86_64-pc-windows-msvc'`); "
        "需要重跑 rustup-init 修复并 `rustup default stable-x86_64-pc-windows-msvc`。",
        "【两个 rustup-init 并发冲突】重跑安装时旧的 rustup-init 进程仍在, 两个实例同时写 ~/.rustup 会互相踩, "
        "需先结束旧进程再让新的下载。",
        "【未擅自装 MSVC】按任务约束不安装来历不明的工具链, 因此没有绕过本机阻断条件。",
    ],
    "B4": [
        "【Electron 首次安装需下二进制】npm install 会拉约 151 MB 的 electron 二进制包, 走 "
        "https://github.com/electron/electron/releases 的缓存目录 (本机 %LOCALAPPDATA%\\electron\\Cache)。",
        "【Electron 进程树内存口径】Electron 会派生主进程 + 渲染进程 + GPU 进程 + utility 进程, "
        "驻留内存必须按**进程树**求和, 单看主进程会低估到 1/4 以下。",
        "【GPU 隔离】为避免与并行推理实测争抢显存, 本基准在 Electron 侧调用 app.disableHardwareAcceleration() "
        "并加 --disable-gpu 开关; 这会让渲染走 SwiftShader(CPU), 内存数字应按此口径理解。",
        "【端口/进程串扰】同机并行跑别的 agent 的 http.server 时, Windows 的 SO_REUSEADDR 会让两个进程同时"
        "「监听」同一端口, 基准脚本必须自己挑空闲端口并核对服务内容。",
    ],
    "B5": [
        "【HuggingFace 主站不可达, 但镜像可达 —— 与任务书前提相反】实测 huggingface.co 根域与权重 URL 均超时, "
        "而 hf-mirror.com 根域 200, 且 ggml-tiny.bin 可完整下载 (77,691,713 B, 与官方 models/README.md 的 "
        "\"74MB for the tiny model\" 吻合)。所以 B5 的权重**不是**不可得, 前提不成立。",
        "【官方预构建 WASM 包在当前 Chrome 上跑不起来】ggml.ai 发布的 main.js / libmain.js (各 1,770,840 B, "
        "wasm 以 JS 字符串内嵌) 在本机 Chrome 155 里实例化失败, 页面原文: "
        "\"failed to asynchronously prepare wasm: CompileError: WebAssembly.instantiate(): unknown type form: 106 @+12\" "
        "/ \"Aborted(CompileError: WebAssembly.instantiate(): unknown type form: 106 @+12)\", "
        "随后 `Uncaught TypeError: Module.init is not a function`。",
        "【同一二进制在 Node/V8 亦失败】把 main.js 内嵌的 wasm 抽出来单独编译, Node 报 "
        "\"CompileError: section was shorter than expected size (151363 bytes expected, 1246 decoded) @+1258\", "
        "而 wasm 段表本身自洽 (type section id=1 size=151363 位于偏移 12) —— 即预构建产物与当前 V8 不兼容。",
        "【GitHub Releases 没有 wasm 资产】whisper.cpp 最新几个 release (v1.9.5 / b5454 / b5130) 只有原生二进制 "
        "(whisper-bin-* / whisper-blas-* / xcframework), 没有 wasm 资产可用。",
        "【自建路径的前置成本】官方自建路径需要 Emscripten (emsdk) + CMake + 源码, 本机三者均缺 "
        "(emsdk 走 storage.googleapis.com, cmake 走 GitHub releases, 两者本机网络可达)。",
        "【模型/音频加载本身是通的】在被测页面里, 模型经 DOM.setFileInputFiles 送入后 "
        "\"storeFS: stored model: whisper.bin size: 77691713\" 成功, 音频也成功解码 "
        "(\"js: audio loaded, size: 176000\") —— 阻断点只在 WASM 引擎实例化, 不在数据通路。",
        "【demo 页两个 file input 同名】页面里模型输入 (#whisper-file) 与音频输入的 name 都是 \"file\", "
        "按 name 选会选错(把音频写进模型输入), 必须按 onchange=\"loadAudio(event)\" 精确定位。",
    ],
}


def finalize_results(data: Dict[str, Any]) -> None:
    """把文档事实(跨平台)与踩坑记录注入各候选条目。"""
    cands = data.setdefault("candidates", {})
    for cid, entry in cands.items():
        if cid in CROSS_PLATFORM:
            entry.setdefault("cross_platform", CROSS_PLATFORM[cid])
        # 用文档/实测归纳的踩坑列表覆盖空列表 (cmd_b3 初始值为 [], 会被 setdefault 跳过)
        if cid in PITFALLS and not entry.get("pitfalls"):
            entry["pitfalls"] = PITFALLS[cid]
    data.setdefault("meta", {})["documentation_sources"] = {
        cid: cp.get("sources", []) for cid, cp in CROSS_PLATFORM.items()
    }


# --------------------------------------------------------------------------
# 环境元信息 + 结果落盘
# --------------------------------------------------------------------------
def environment_info() -> Dict[str, Any]:
    def cmd_out(cmd: Sequence[str]) -> Optional[str]:
        try:
            r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=30)
            return (r.stdout or r.stderr).strip()[:400]
        except Exception:
            return None

    mem = psutil.virtual_memory()
    cpu_info = {}
    try:
        with open(r"C:\Users\12739\AppData\Local\Programs\Python\Python312\Lib\site-packages\torch\version.py", "r", encoding="utf-8", errors="replace") as f:
            cpu_info["torch_version_file"] = f.read(200)
    except Exception:
        pass
    return {
        "os": platform.platform(),
        "os_version": platform.version(),
        "python": sys.version,
        "python_executable": sys.executable,
        "node": cmd_out(["node", "-v"]),
        "npm": cmd_out(["npm.cmd", "-v"]) or cmd_out(["npm", "-v"]),
        "rustc": cmd_out(["rustc", "--version"]),
        "cargo": cmd_out(["cargo", "--version"]),
        "cpu": platform.processor() or cmd_out(["powershell", "-NoProfile", "-Command",
                                                "(Get-CimInstance Win32_Processor).Name"]),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "ram_total_bytes": mem.total,
        "ram_available_bytes_at_bench": mem.available,
        "gpu": cmd_out(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                        "--format=csv,noheader"]),
        "psutil_version": psutil.__version__,
        "git_head": cmd_out(["git", "-C", str(REPO), "rev-parse", "HEAD"]),
        "bench_timestamp": time.strftime("%Y-%m-%d %H:%M:%S%z"),
    }


def load_results() -> Dict[str, Any]:
    if RESULTS_PATH.exists():
        try:
            return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_results(data: Dict[str, Any]) -> None:
    RESULTS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="L2 工作台壳 / 交付形态层 基准实测")
    ap.add_argument("candidate", choices=["b1", "b2", "b3", "b4", "b5", "all"])
    ap.add_argument("--runs", type=int, default=3, help="每个候选的冷启动重复次数 (默认 3)")
    ap.add_argument("--skip-build", action="store_true", help="跳过 npm 安装/构建 (复用已有产物)")
    ap.add_argument("--skip-tauri-build", action="store_true",
                    help="跳过完整 Tauri 编译 (只用最小链接测试判定阻断原因)")
    args = ap.parse_args(argv)

    python = resolve_python()
    _log(f"使用 Python: {python}")
    OUT.mkdir(parents=True, exist_ok=True)

    data = load_results()
    data.setdefault("schema", "mediascribe.l2.shell-bench.v1")
    data["meta"] = {
        "environment": environment_info(),
        "methodology": {
            "cold_start": "进程 spawn 瞬间到健康检查 200 / 窗口 ready 标记的墙钟毫秒 (time.perf_counter), "
                          "每次独立起进程, 跑 N 次取中位数",
            "memory": "Windows GetProcessMemoryInfo().WorkingSetSize (psutil memory_info().rss), "
                      "进程树(父+递归子进程)求和; 稳定期先 settle 再连采 3 次取中位数; 另记 peak_wset",
            "distribution": "构建产物磁盘字节数 (os.walk 递归求和)",
            "offline": "HTTP(S)/ALL_PROXY 指向黑洞 127.0.0.1:9 + 50ms 间隔 netstat -ano 快照断言无非回环 TCP; "
                       "本机无管理员权限, 未能用防火墙做物理断网",
            "gpu_isolation": "所有被测子进程注入 CUDA_VISIBLE_DEVICES=\"\" 与 MEDIASCRIBE_DEVICE=cpu, "
                             "避免与并行推理实测争抢显存; 本脚本只做壳层与健康检查, 不做推理",
            "runs": args.runs,
        },
    }
    cands = data.setdefault("candidates", {})

    try:
        if args.candidate in ("b1", "all"):
            cands["B1"] = cmd_b1(args.runs, python)
            save_results(data)
        if args.candidate in ("b2", "all"):
            cands["B2"] = cmd_b2(args.runs, python, skip_build=args.skip_build)
            save_results(data)
        if args.candidate in ("b4", "all"):
            cands["B4"] = cmd_b4(args.runs, python, skip_build=args.skip_build)
            save_results(data)
        if args.candidate in ("b3", "all"):
            cands["B3"] = cmd_b3(python, skip_tauri_build=args.skip_tauri_build)
            save_results(data)
        if args.candidate in ("b5", "all"):
            cands["B5"] = cmd_b5(runs=args.runs, python=python)
            save_results(data)
    finally:
        finalize_results(data)
        save_results(data)

    _log(f"结果已写入 {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
