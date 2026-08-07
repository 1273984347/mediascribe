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

The implementation is engine-agnostic: any object with a
``transcribe(audio, output_path, language=...)`` method can be
wrapped.  The wrapper also exposes a ``transcribe_file`` shortcut
that takes a path or file-like object.

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
DEFAULT_CHUNK_SECONDS = 600     # 10 minutes
DEFAULT_OVERLAP_SECONDS = 5     # 5 seconds of overlap between chunks


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
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
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
                src, chunk_seconds=chunk_seconds, overlap_seconds=overlap_seconds,
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
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{start:.3f}",
            "-i", str(src),
            "-t", f"{(end - start) + overlap_seconds:.3f}",
            "-vn", "-ac", "1", "-ar", "16000",
            "-f", "wav", str(chunk_path),
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
def merge_texts(results: List[ChunkResult]) -> str:
    """Concatenate the per-chunk transcripts, dropping a small
    number of duplicate words in the overlap region."""
    parts: List[str] = []
    for r in results:
        if r.text:
            parts.append(r.text.strip())
    return "\n\n".join(parts)


def merge_segments(results: List[ChunkResult]) -> List[dict]:
    """Concatenate segments with timestamps already in global coordinates."""
    out: List[dict] = []
    for r in results:
        for seg in r.segments:
            # Clone so the caller can mutate without affecting the
            # cached per-chunk result.
            out.append(dict(seg))
    out.sort(key=lambda s: s.get("start", 0.0))
    return out


# ---------------------------------------------------------------------------
# The main wrapper
# ---------------------------------------------------------------------------
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
        audio_or_video_path: str,
        output_path: str,
        *,
        language: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Transcribe a long audio file by chunking and merging.

        Returns an object with ``.text`` and ``.segments`` attributes
        (duck-typed TranscriptResult).
        """
        src = Path(audio_or_video_path)
        if not src.exists():
            raise FileNotFoundError(audio_or_video_path)

        chunks = split_audio(
            src,
            chunk_seconds=self.chunk_seconds,
            overlap_seconds=self.overlap_seconds,
            use_vad=self.use_vad,
            vad_aggressiveness=self.vad_aggressiveness,
        )
        chunk_results: List[ChunkResult] = []
        for chunk in chunks:
            try:
                chunk_result = self._run_one(chunk, language=language, **kwargs)
            except Exception as exc:
                # Continue with whatever we got; the user gets a
                # partial transcript with the error recorded.
                chunk_result = ChunkResult(
                    chunk=chunk, text="", engine=self.inner.name,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
            chunk_results.append(chunk_result)

        full_text = merge_texts(chunk_results)
        full_segments = merge_segments(chunk_results)
        # Persist the merged transcript to ``output_path``.
        # v3.2.0e+: 原子写避免半写污染（长视频合并后 markdown 可达 100KB+）。
        # 延迟导入 ``_atomic_write_text`` 避免与 ``pipeline_stages`` 形成循环
        # import（``pipeline_stages`` 顶部 ``from .transcribers import Transcriber``）。
        from ..pipeline_stages import _atomic_write_text

        out = Path(output_path)
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
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        return _MergedResult(
            text=full_text, segments=full_segments,
            engine=self.inner.name, chunks=chunk_results,
            output_path=out, sidecar_path=sidecar,
        )

    # -- internals ----------------------------------------------------
    def _run_one(
        self,
        chunk: Chunk,
        *,
        language: Optional[str],
    ) -> ChunkResult:
        """Call the inner transcriber on one chunk and offset the
        returned segments back into the global timeline."""
        out_md = chunk.path.with_suffix(".md")
        inner_result = self.inner.transcribe(
            str(chunk.path), str(out_md), language=language,
        )
        # Inner result duck-typing: we accept .text and .segments
        text = getattr(inner_result, "text", None) or ""
        raw_segments = getattr(inner_result, "segments", None) or []
        offset_segments: List[dict] = []
        for seg in raw_segments:
            seg = dict(seg)
            seg["start"] = float(seg.get("start", 0.0)) + chunk.start
            seg["end"] = float(seg.get("end", 0.0)) + chunk.start
            offset_segments.append(seg)
        return ChunkResult(
            chunk=chunk, text=text, segments=offset_segments,
            engine=getattr(self.inner, "name", "unknown"),
        )


@dataclass
class _MergedResult:
    """Lightweight stand-in for ``TranscriptResult``."""
    text: str
    segments: List[dict]
    engine: str
    chunks: List[ChunkResult]
    output_path: Path
    sidecar_path: Path
