"""
Pipeline performance profiling utilities.

Three layers:

1. ``@profile_step`` decorator — measure wall-time of any callable
   and record it in :data:`STEP_TIMES` for later inspection.
2. ``PerformanceReport`` — collect step timings into a structured
   report that can be JSON-serialised for a ``.perf.json``
   sidecar next to the transcript.
3. ``parallel_map`` — run a CPU-bound mapping function over a list
   using a thread pool.  Most downloaders do network I/O, so
   threads work fine and are cheaper than processes.

A separate ``download_cache`` provides a URL → on-disk-path cache
so the same URL is not re-downloaded within a single pipeline run.
"""

from __future__ import annotations

import contextvars
import functools
import json
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# 1. Per-step timing decorator
# ---------------------------------------------------------------------------
STEP_TIMES: Dict[str, List[float]] = {}

# P2-7: per-run 计时上下文。并发 pipeline run 各自在自己的
# context 里持有一份注册表,不再互相清空/混写进程级 ``STEP_TIMES``。
# ``None`` 表示无 run 上下文 — 所有 API 回退到全局 ``STEP_TIMES``
# (向后兼容:老调用方直接读写 ``STEP_TIMES`` 的行为不变)。
_RUN_TIMES: contextvars.ContextVar[Optional[Dict[str, List[float]]]] = contextvars.ContextVar(
    "mediascribe_step_times_run", default=None
)


def _current_registry() -> Dict[str, List[float]]:
    """返回当前生效的计时注册表(run 上下文优先,否则全局)。"""
    reg = _RUN_TIMES.get()
    return STEP_TIMES if reg is None else reg


def begin_run_registry() -> Dict[str, List[float]]:
    """开启一个新的 per-run 计时注册表(context-local)。

    :meth:`Pipeline._run_stage_chain` 在 ``profile=True`` 时调用,
    使并发 run 的计时互不干扰。返回新注册表。
    """
    reg: Dict[str, List[float]] = {}
    _RUN_TIMES.set(reg)
    return reg


def end_run_registry() -> None:
    """脱离 per-run 注册表,后续计时回落到全局 ``STEP_TIMES``。"""
    _RUN_TIMES.set(None)


def profile_step(name: Optional[str] = None, *, log_to: Optional[Path] = None) -> Callable:
    """Decorator that times the wrapped function and stores the wall-clock
    duration (seconds) in the active timing registry (:data:`STEP_TIMES`
    unless a per-run registry is active, see :func:`begin_run_registry`).

    Usage::

        @profile_step("download")
        def download(self, url, ...):
            ...

    With ``log_to`` set, every call also appends a JSONL line to the
    given file for offline analysis by ``python -m mediascribe.profile``.
    """

    def deco(fn: Callable) -> Callable:
        label = name or fn.__qualname__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                dur = time.perf_counter() - t0
                _current_registry().setdefault(label, []).append(dur)
                if log_to is not None:
                    _append_jsonl(log_to, label, dur)

        return wrapper

    return deco


def _append_jsonl(path: Path, label: str, duration: float) -> None:
    """Append a single timing record to ``path`` as a JSONL line."""
    import json
    from datetime import datetime, timezone

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "duration_sec": duration,
    }
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def clear_step_times() -> None:
    """Reset the timing registry (per-run one if active, plus the global).

    Useful between runs.  P2-7: 也把 run 上下文重置回全局模式,
    避免上一个 profile run 在本线程遗留的 per-run 注册表吞掉
    后续计时。
    """
    _RUN_TIMES.set(None)
    STEP_TIMES.clear()


def get_step_times() -> Dict[str, List[float]]:
    """Return a copy of the current timing registry (run-scoped if active)."""
    return {k: list(v) for k, v in _current_registry().items()}


# ---------------------------------------------------------------------------
# 2. PerformanceReport
# ---------------------------------------------------------------------------
@dataclass
class PerformanceReport:
    """A serialisable snapshot of per-step timings."""

    generated_at: str = ""
    steps: Dict[str, Dict[str, float]] = field(default_factory=dict)
    total_sec: float = 0.0

    @classmethod
    def from_registry(cls) -> "PerformanceReport":
        from datetime import datetime, timezone

        steps: Dict[str, Dict[str, float]] = {}
        for label, durations in _current_registry().items():
            if not durations:
                continue
            steps[label] = {
                "count": float(len(durations)),
                "total_sec": sum(durations),
                "mean_sec": sum(durations) / len(durations),
                "min_sec": min(durations),
                "max_sec": max(durations),
            }
        return cls(
            generated_at=datetime.now(timezone.utc).isoformat(),
            steps=steps,
            total_sec=sum(s["total_sec"] for s in steps.values()),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        if not self.steps:
            return "_No steps recorded._\n"
        lines = [
            "| Step | Count | Total (s) | Mean (s) | Min (s) | Max (s) |",
            "|------|-------|-----------|----------|---------|---------|",
        ]
        for label, s in sorted(
            self.steps.items(),
            key=lambda x: -x[1]["total_sec"],
        ):
            lines.append(
                f"| {label} | {int(s['count'])} | {s['total_sec']:.3f} | "
                f"{s['mean_sec']:.3f} | {s['min_sec']:.3f} | {s['max_sec']:.3f} |"
            )
        lines.append(f"\n**Total: {self.total_sec:.2f} s**")
        return "\n".join(lines)

    def write(self, out_path: Path) -> None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# 3. parallel_map: thread-pool mapper
# ---------------------------------------------------------------------------
def parallel_map(
    fn: Callable[..., Any],
    items: Iterable[Any],
    *,
    max_workers: Optional[int] = None,
) -> List[Any]:
    """Apply ``fn`` to every item in ``items`` concurrently.

    Returns a list of results in the same order as the input.
    Threads (not processes) are used because the workload is
    network-bound (HTTP downloads) and threads avoid the pickle
    overhead of multiprocessing.

    On Python 3.8 the stdlib ``ThreadPoolExecutor`` works fine;
    we just cap the worker count so a 1000-URL run doesn't
    saturate the file-descriptor table.
    """
    items = list(items)
    if not items:
        return []
    workers = max_workers or min(8, len(items), (os.cpu_count() or 4) * 2)
    results: List[Optional[Any]] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_to_idx = {ex.submit(fn, x): i for i, x in enumerate(items)}
        for fut in as_completed(future_to_idx):
            i = future_to_idx[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:  # surface as a structured failure
                results[i] = exc
    return list(results)


# ---------------------------------------------------------------------------
# 4. download_cache
# ---------------------------------------------------------------------------
class DownloadCache:
    """A simple on-disk URL→path cache for the lifetime of a pipeline run.

    Entries live in a temporary directory; calling :meth:`clear` deletes
    them.  This is a single-run cache, not a persistent one (that
    would be a v3.2 feature).
    """

    def __init__(self, base: Optional[Path] = None) -> None:
        self._root = Path(base or tempfile.mkdtemp(prefix="v2t_dl_cache_"))
        self._seen: Dict[str, Path] = {}
        self._hits = 0
        self._misses = 0

    @property
    def root(self) -> Path:
        return self._root

    def get(self, url: str) -> Optional[Path]:
        """Return the cached path for ``url`` or None if not present."""
        p = self._seen.get(url)
        if p is not None and p.exists():
            self._hits += 1
            return p
        self._misses += 1
        return None

    def put(self, url: str, src: Path) -> Path:
        """Copy ``src`` into the cache under a stable name and remember it."""
        safe = (
            "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in url)[:200]
            or "unnamed"
        )
        dst = self._root / safe
        if not dst.exists():
            shutil.copy2(src, dst)
        self._seen[url] = dst
        return dst

    def stats(self) -> Dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "entries": len(self._seen)}

    def clear(self) -> None:
        if self._root.exists():
            shutil.rmtree(self._root, ignore_errors=True)
        self._seen.clear()
