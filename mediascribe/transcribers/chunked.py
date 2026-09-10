"""
Long-video chunked transcriber.

For audio longer than ~30 minutes, feeding the whole file into a
Whisper model at once:

* blows the GPU memory budget on consumer cards (8-12 GB),
* produces inferior alignment (Whisper's attention is local),
* makes failures unrecoverable — one bad chunk kills the run.

This module solves all three with a **chunk + overlap + merge**
strategy:

1. Probe the audio length (ffprobe, falls back to wave module).
2. Split into N overlapping windows of ``--chunk-seconds`` length
   (default 600s = 10 min) with a small overlap (default 5s) so
   that words straddling a chunk boundary are not lost.
3. For each chunk, run the underlying transcriber independently.
4. Stitch the per-chunk segments back together, offsetting their
   timestamps by the chunk start time.

The implementation is engine-agnostic: any object following the
``Transcriber`` contract — ``transcribe(audio_path, *, prompt=...,
language=...)`` with a single positional argument — can be wrapped.
Chunk WAVs are written to a temporary directory that is removed
when ``transcribe`` returns (pass ``output_dir`` to keep the merged
transcript on disk instead).

VAD is intentionally **not** used here because the optional
``silero-vad`` package is heavyweight.  Energy-based silence
detection via ``pydub`` / wave is used as a cheap proxy when
available, but chunking falls back to fixed windows otherwise.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

from .base import Transcriber

# Default chunking parameters; can be overridden per-instance.
DEFAULT_CHUNK_SECONDS = 600  # 10 minutes
DEFAULT_OVERLAP_SECONDS = 5  # 5 seconds of overlap between chunks


@dataclass
class Chunk:
    """One slice of the long audio.  ``start`` is in seconds."""

    index: int
    start: float
    end: float
    path: Path

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class ChunkResult:
    """A single chunk's transcript, with segments already offset to the
    timeline of the original (un-chunked) audio."""

    chunk: Chunk
    text: str
    segments: List[dict] = field(default_factory=list)
    engine: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Audio probe + split helpers
# ---------------------------------------------------------------------------
def probe_duration(path: Path) -> float:
    """Return the audio duration in seconds.

    Prefers ``ffprobe`` (always present alongside ffmpeg).  Falls
    back to the stdlib ``wave`` module for .wav files.  Raises
    RuntimeError if both fail.
    """
    if shutil.which("ffprobe"):
        try:
            out = subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                stderr=subprocess.STDOUT,
                timeout=30,
            )
            return float(out.decode("utf-8", errors="replace").strip())
        except Exception:
            pass
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                frames = w.getnframes()
                rate = w.getframerate() or 1
                return frames / float(rate)
        except Exception:
            pass
    raise RuntimeError(
        f"Cannot determine duration of {path!s}. "
        "Install ffmpeg (provides ffprobe) or pre-convert to .wav."
    )


def split_audio(
    src: Path,
    *,
    chunk_seconds: int = DEFAULT_CHUNK_SECONDS,
    overlap_seconds: int = DEFAULT_OVERLAP_SECONDS,
    out_dir: Optional[Path] = None,
    use_vad: bool = False,
    vad_aggressiveness: int = 2,
) -> List[Chunk]:
    """Split ``src`` into overlapping WAV chunks.  Requires ffmpeg.

    If ffmpeg is missing, returns a single chunk that spans the
    whole file (i.e. effectively disables chunking).

    When ``use_vad=True`` *and* the ``webrtcvad`` package is installed
    and the audio is in a compatible format, chunks are aligned to
    detected speech regions instead of fixed windows.  Each speech
    segment longer than ``chunk_seconds`` is itself sub-split at
    ``chunk_seconds`` boundaries so the transcriber never sees a
    single chunk larger than the cap.
    """
    duration = probe_duration(src)
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="v2t_chunks_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if not shutil.which("ffmpeg"):
        # Single-chunk fallback; ChunkedTranscriber will treat this
        # as one big chunk and call the inner transcriber once.
        single = out_dir / "chunk_000.wav"
        shutil.copy(src, single)
        return [Chunk(index=0, start=0.0, end=duration, path=single)]

    # Build the list of (start, end) windows to extract.
    windows: List[Tuple[float, float]] = []
    if use_vad and _vad_segmentation_available(src):
        try:
            windows = _windows_from_vad(
                src,
                chunk_seconds=chunk_seconds,
                overlap_seconds=overlap_seconds,
                aggressiveness=vad_aggressiveness,
            )
        except Exception as exc:  # noqa: BLE001
            # Fall back to fixed windows but keep going.
            print(f"[chunked] VAD split failed ({exc!r}); falling back to fixed windows.")
            windows = []
    if not windows:
        # Fixed-window fallback (original behaviour).
        stride = max(chunk_seconds - overlap_seconds, 1)
        cursor = 0.0
        while cursor < duration:
            end = min(cursor + chunk_seconds, duration)
            windows.append((cursor, end))
            cursor += stride

    chunks: List[Chunk] = []
    for idx, (start, end) in enumerate(windows):
        chunk_path = out_dir / f"chunk_{idx:03d}.wav"
        # Each chunk is re-encoded to 16 kHz mono PCM so the
        # transcriber always sees a clean WAV.  ``-ss`` before ``-i``
        # is a fast keyframe seek; for short windows this is exact.
        # 注意：窗口本身已含 ``overlap_seconds`` 重叠（stride =
        # chunk_seconds - overlap_seconds），``-t`` 只取窗口长度，
        # 不再额外加 overlap，否则相邻块实际重叠约 2 倍、音频被
        # 转写两遍浪费 GPU。
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(src),
            "-t",
            f"{end - start:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(chunk_path),
        ]
        subprocess.run(cmd, check=True, timeout=300)
        chunks.append(Chunk(index=idx, start=start, end=end, path=chunk_path))
    return chunks


def _vad_segmentation_available(src: Path) -> bool:
    """True iff webrtcvad is installed AND the audio is readable as PCM16 mono."""
    try:
        from ..audio_utils import _read_wave_pcm16, vad_available
    except ImportError:
        return False
    if not vad_available():
        return False
    if src.suffix.lower() != ".wav":
        return False
    try:
        _read_wave_pcm16(src)
    except Exception:
        return False
    return True


def _windows_from_vad(
    src: Path,
    *,
    chunk_seconds: int,
    overlap_seconds: int,
    aggressiveness: int = 2,
) -> List[Tuple[float, float]]:
    """Run VAD on ``src`` and return a flat list of (start, end) windows.

    Each detected speech region is sub-split at ``chunk_seconds`` so
    the downstream transcriber never sees a chunk larger than the cap.
    Extracted into its own function so the test suite can mock it
    without touching the actual webrtcvad integration.
    """
    from ..audio_utils import detect_speech_segments

    out: List[Tuple[float, float]] = []
    for s, e in detect_speech_segments(src, aggressiveness=aggressiveness):
        out.extend(_slice_long(s, e, chunk_seconds, overlap_seconds))
    return out


def _slice_long(
    start: float,
    end: float,
    chunk_seconds: int,
    overlap_seconds: int,
) -> List[Tuple[float, float]]:
    """Sub-split a long speech region into ≤ chunk_seconds windows.

    Returns a list of (start, end) tuples in *global* time, preserving
    the same semantics as the fixed-window splitter: each window is
    ``chunk_seconds`` long and consecutive windows overlap by
    ``overlap_seconds``.  The last window is clamped to ``end``.
    """
    duration = end - start
    if duration <= chunk_seconds:
        return [(start, end)]
    stride = max(chunk_seconds - overlap_seconds, 1)
    out: List[Tuple[float, float]] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + chunk_seconds, end)
        out.append((cursor, window_end))
        if window_end >= end:
            break
        cursor += stride
    return out


# ---------------------------------------------------------------------------
# Result merger
# ---------------------------------------------------------------------------
# 相邻段首尾相接（end == next start）不算重叠；重叠区的重复转写
# 至少重叠数十毫秒，这里取 10ms 作为判定阈值。
_SEGMENT_OVERLAP_EPS = 0.01


def merge_segments(results: List[ChunkResult]) -> List[dict]:
    """Concatenate segments with timestamps already in global coordinates,
    dropping duplicates introduced by the overlap between adjacent chunks.

    相邻 chunk 的窗口互相重叠，重叠区的同一段话会被转写两次。
    这里按全局时间线排序后，丢弃与已保留 segment 时间区间重叠的
    segment（首尾相接、不重叠的 segment 不受影响），保证输出时间线
    无重复段。
    """
    out: List[dict] = []
    for r in results:
        for seg in r.segments:
            # Clone so the caller can mutate without affecting the
            # cached per-chunk result.
            out.append(dict(seg))
    out.sort(key=lambda s: s.get("start", 0.0))
    kept: List[dict] = []
    kept_ranges: List[Tuple[float, float]] = []
    for seg in out:
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", seg_start))
        is_duplicate = any(
            seg_start < k_end - _SEGMENT_OVERLAP_EPS and seg_end > k_start + _SEGMENT_OVERLAP_EPS
            for k_start, k_end in kept_ranges
        )
        if is_duplicate:
            continue
        kept.append(seg)
        kept_ranges.append((seg_start, seg_end))
    return kept


def merge_texts(results: List[ChunkResult]) -> str:
    """Concatenate the per-chunk transcripts, dropping the duplicate
    sentences produced by the overlap region.

    只要任一 chunk 带 segments，就基于 :func:`merge_segments` 去重后
    的时间线拼接（与 segments 输出保持一致，无重复句）；所有 chunk
    都没有 segments 时退化为按 chunk 文本顺序拼接。
    """
    segments = merge_segments(results)
    if segments:
        parts = [str(seg.get("text", "")).strip() for seg in segments]
        return "\n".join(p for p in parts if p)
    return "\n\n".join(r.text.strip() for r in results if r.text)


# ---------------------------------------------------------------------------
# The main wrapper
# ---------------------------------------------------------------------------
def _cleanup_chunk_files(chunk_dir: Path, keep: Tuple[Optional[Path], ...] = ()) -> None:
    """Delete the chunk WAVs (and any per-chunk leftovers, including
    those from failed chunks) inside ``chunk_dir``.

    Paths in ``keep`` — e.g. the merged transcript written into a
    caller-supplied ``output_dir`` — are preserved.
    """
    keep_paths = {p.resolve() for p in keep if p is not None}
    try:
        candidates = list(chunk_dir.glob("chunk_*"))
    except OSError:
        return
    for f in candidates:
        try:
            if f.is_file() and f.resolve() not in keep_paths:
                f.unlink()
        except OSError:
            pass


class ChunkedTranscriber(Transcriber):
    """A ``Transcriber`` that delegates to an inner one after chunking.

    Parameters
    ----------
    inner : Transcriber
        The actual ASR engine to call per chunk.
    chunk_seconds : int
        Target chunk size in seconds.  Defaults to 600 (10 min).
    overlap_seconds : int
        Overlap between consecutive chunks in seconds.  Defaults to 5.
    """

    name = "chunked"

    def __init__(
        self,
        inner: Transcriber,
        *,
        chunk_seconds: int = DEFAULT_CHUNK_SECONDS,
        overlap_seconds: int = DEFAULT_OVERLAP_SECONDS,
        use_vad: bool = False,
        vad_aggressiveness: int = 2,
    ):
        self.inner = inner
        self.chunk_seconds = chunk_seconds
        self.overlap_seconds = overlap_seconds
        self.use_vad = use_vad
        self.vad_aggressiveness = vad_aggressiveness

    # -- The Transcriber interface ------------------------------------
    def transcribe(
        self,
        audio_path: Any,
        *,
        prompt: Optional[str] = None,
        progress: Optional[Any] = None,
        language: Optional[str] = None,
        output_dir: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Transcribe a long audio file by chunking and merging.

        Signature follows the base-class contract (single positional
        argument plus keyword options).  Chunk WAVs go into a temp
        directory that is always removed in ``finally`` — including
        chunks that failed to transcribe.  Pass ``output_dir`` to
        additionally persist the merged transcript (``<stem>.md`` and
        a ``.chunks.json`` sidecar) into that directory; only the
        chunk files are cleaned up there, the final output is kept.

        Returns a dict compatible with the built-in transcribers
        (``result["text"]`` / ``result.get("text")``), which also
        exposes ``.segments`` / ``.chunks`` / ``.output_path`` as
        attributes.
        """
        src = Path(audio_path)
        if not src.exists():
            raise FileNotFoundError(audio_path)

        owns_chunk_dir = output_dir is None
        chunk_dir = (
            Path(output_dir)
            if output_dir is not None
            else Path(tempfile.mkdtemp(prefix="v2t_chunks_"))
        )
        out: Optional[Path] = None
        sidecar: Optional[Path] = None
        try:
            chunks = split_audio(
                src,
                chunk_seconds=self.chunk_seconds,
                overlap_seconds=self.overlap_seconds,
                out_dir=chunk_dir,
                use_vad=self.use_vad,
                vad_aggressiveness=self.vad_aggressiveness,
            )
            chunk_results: List[ChunkResult] = []
            for chunk in chunks:
                try:
                    chunk_result = self._run_one(chunk, language=language, prompt=prompt, **kwargs)
                except Exception as exc:
                    # Continue with whatever we got; the user gets a
                    # partial transcript with the error recorded.
                    chunk_result = ChunkResult(
                        chunk=chunk,
                        text="",
                        engine=self.inner.name,
                        error=f"{exc.__class__.__name__}: {exc}",
                    )
                chunk_results.append(chunk_result)

            full_text = merge_texts(chunk_results)
            full_segments = merge_segments(chunk_results)

            if output_dir is not None:
                # Persist the merged transcript to ``output_dir``.
                # v3.2.0e+: 原子写避免半写污染（长视频合并后 markdown 可达 100KB+）。
                # 延迟导入 ``_atomic_write_text`` 避免与 ``pipeline_stages`` 形成循环
                # import（``pipeline_stages`` 顶部 ``from .transcribers import Transcriber``）。
                from ..pipeline_stages import _atomic_write_text

                out = chunk_dir / f"{src.stem}.md"
                _atomic_write_text(out, full_text, encoding="utf-8")
                # Sidecar JSON for downstream tools.
                sidecar = out.with_suffix(out.suffix + ".chunks.json")
                _atomic_write_text(
                    sidecar,
                    json.dumps(
                        {
                            "engine": self.inner.name,
                            "chunk_seconds": self.chunk_seconds,
                            "overlap_seconds": self.overlap_seconds,
                            "chunk_count": len(chunks),
                            "chunks": [
                                {
                                    "index": r.chunk.index,
                                    "start": r.chunk.start,
                                    "end": r.chunk.end,
                                    "engine": r.engine,
                                    "error": r.error,
                                    "segment_count": len(r.segments),
                                    "chars": len(r.text),
                                }
                                for r in chunk_results
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            return _MergedResult(
                text=full_text,
                segments=full_segments,
                # 与内置转录器的 dict 返回值字段对齐（pipeline 会读
                # "model" / "language"）；chunked 层拿不到各 chunk 的
                # 检测语言，置 None 交由调用方处理。
                language=None,
                model=getattr(self.inner, "model_name", None),
                engine=self.inner.name,
                chunks=chunk_results,
                output_path=out,
                sidecar_path=sidecar,
            )
        finally:
            # P1-5: 无论成功失败都清理本次的 chunk 音频
            # (10min 16kHz 单声道 wav ≈ 19MB/块，不清理会持续累积)。
            if owns_chunk_dir:
                shutil.rmtree(chunk_dir, ignore_errors=True)
            else:
                _cleanup_chunk_files(chunk_dir, keep=(out, sidecar))

    # -- internals ----------------------------------------------------
    def _run_one(
        self,
        chunk: Chunk,
        *,
        language: Optional[str],
        prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> ChunkResult:
        """Call the inner transcriber on one chunk and offset the
        returned segments back into the global timeline."""
        # 基类契约：单个位置参数 + 关键字参数（内置转录器均如此）。
        inner_result = self.inner.transcribe(chunk.path, language=language, prompt=prompt, **kwargs)
        # Inner result duck-typing: 内置转录器返回 dict，
        # 也兼容带 .text / .segments 属性的对象。
        if isinstance(inner_result, dict):
            text = inner_result.get("text") or ""
            raw_segments = inner_result.get("segments") or []
        else:
            text = getattr(inner_result, "text", None) or ""
            raw_segments = getattr(inner_result, "segments", None) or []
        offset_segments: List[dict] = []
        for seg in raw_segments:
            seg = dict(seg)
            seg["start"] = float(seg.get("start", 0.0)) + chunk.start
            seg["end"] = float(seg.get("end", 0.0)) + chunk.start
            offset_segments.append(seg)
        return ChunkResult(
            chunk=chunk,
            text=text,
            segments=offset_segments,
            engine=getattr(self.inner, "name", "unknown"),
        )


class _MergedResult(dict):
    """Merged transcript result.

    Subclasses ``dict`` so it is drop-in compatible with the built-in
    transcribers' return value (``result["text"]`` /
    ``result.get("text")`` in ``pipeline_stages``), while keeping
    attribute access (``result.segments`` / ``result.chunks``) for
    callers that inspect per-chunk metadata.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
