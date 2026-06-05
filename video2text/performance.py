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


def profile_step(name: Optional[str] = None) -> Callable:
    """Decorator that times the wrapped function and stores the wall-clock
    duration (seconds) in :data:`STEP_TIMES`.

    Usage::

        @profile_step("download")
        def download(self, url, ...):
            ...
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
                STEP_TIMES.setdefault(label, []).append(dur)
        return wrapper
    return deco


def clear_step_times() -> None:
    """Reset the global timing registry.  Useful between runs."""
    STEP_TIMES.clear()


def get_step_times() -> Dict[str, List[float]]:
    """Return a copy of the current timing registry."""
    return {k: list(v) for k, v in STEP_TIMES.items()}


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
        for label, durations in STEP_TIMES.items():
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
        lines = ["| Step | Count | Total (s) | Mean (s) | Min (s) | Max (s) |",
                 "|------|-------|-----------|----------|---------|---------|"]
        for label, s in sorted(
            self.steps.items(), key=lambda x: -x[1]["total_sec"],
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
        safe = "".join(
            c if c.isalnum() or c in ("-", "_", ".") else "_"
            for c in url
        )[:200] or "unnamed"
        dst = self._root / safe
        if not dst.exists():
            shutil.copy2(src, dst)
        self._seen[url] = dst
        return dst

    def stats(self) -> Dict[str, int]:
        return {"hits": self._hits, "misses": self._misses,
                "entries": len(self._seen)}

    def clear(self) -> None:
        if self._root.exists():
            shutil.rmtree(self._root, ignore_errors=True)
        self._seen.clear()
