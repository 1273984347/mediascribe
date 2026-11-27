"""
whisper.cpp transcriber (C++/GGML engine) — the third leg for
resource-constrained machines.

Positioning
-----------
Local measurements, 2026-10-23 (RTX 4060 Laptop / i7-13650HX / 16 GB):

=================  ============  ==============  ==============
Engine             Tier          RTF             Peak RAM
=================  ============  ==============  ==============
faster-whisper     small (CUDA)   0.1336          1167 MiB
faster-whisper     large-v3(CPU)  1.334           3613 MiB
**whisper.cpp**     **tiny (CPU)** **0.0504**     **291 MiB**
=================  ============  ==============  ==============

⇒ whisper.cpp is the **most memory-frugal and fastest (CPU tier)** of the
three, and the only route that runs on an "old machine with no GPU + 4 GB
RAM". It does require an **external binary** and **GGML weights** to be
pre-provisioned (this product must run fully offline ⇒ weights must be
placed in advance, not downloaded on demand).

Differences vs the other engines (handled per 2026-10-23/24 measurements)
------------------------------------------------------------------------
* **Chinese defaults to traditional script** ⇒ handled via the simplified
  ``initial_prompt`` nudge + output-layer normalization (see
  :mod:`mediascribe.zh_normalize`).
* **No VAD by default** ⇒ severe hallucinations on noisy audio (measured
  84–86% error rate on the heavy-noise tier). Engine-layer VAD is
  therefore **opt-in** (``vad=True``): clean-audio accuracy measurably
  *degrades* under tiny-model VAD (see the class docstring), and the
  pipeline already runs its own VAD + chunking. When enabled but the VAD
  model is absent, it falls back to no-VAD with a warning.
* Requires 16 kHz mono WAV ⇒ every input (``.wav`` included) is transcoded
  via ffmpeg into a temp file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from ..checkpoint import CheckpointSession, run_windowed_transcription
from ..i18n import t
from ..model_weights import ModelNotReadyError, check_weights
from ..zh_normalize import simplified_initial_prompt
from .base import Transcriber

logger = logging.getLogger(__name__)

#: whisper.cpp 的 GGML 权重目录（可用 MEDIASCRIBE_WHISPER_CPP_MODELS 覆盖）
DEFAULT_MODELS_DIR = Path.home() / ".cache" / "mediascribe" / "whisper-cpp"

#: 常见���预编译二进制安装位置（按序探测）
_BIN_CANDIDATE_NAMES = ("whisper-cli.exe", "whisper-cli")
#: 旧版 whisper.cpp 的 CLI 叫 ``main`` —— 名字太模糊，**只在 whisper.cpp
#: 专属目录里找**，绝不走 PATH（否则会命中 Windows 的 main.CPL 之类）。
_BIN_LEGACY_NAMES = ("main.exe", "main")
_BIN_SEARCH_DIRS = (
    Path.home() / ".cache" / "mediascribe" / "whisper-cpp" / "bin" / "Release",
    Path.home() / ".cache" / "mediascribe" / "whisper-cpp" / "bin",
    Path.home() / ".cache" / "mediascribe" / "whisper-cpp",
    Path("C:/Program Files/whisper.cpp/bin"),
)

#: 二进制最小合理体积（whisper.cpp 的 CLI 约 0.5–2 MB；Windows 系统里的
#: main.CPL 只有几十 KB ⇒ 用体积把系统文件挡在外面）。
_MIN_BIN_BYTES = 100 * 1024

#: Windows 系统目录（里面的同名文件不是 whisper.cpp）
_SYSTEM_DIRS = ("C:/Windows/System32", "C:/Windows/SysWOW64")


def _is_plausible_binary(path: Path) -> bool:
    """Reject system files that merely look like the binary.

    Pitfall measured in practice: on Windows ``shutil.which("main.exe")``
    returns ``C:\\Windows\\system32\\main.CPL`` (Control Panel) ⇒ the
    system file was once mistaken for the whisper.cpp binary, making the
    engine look "available".
    """
    try:
        if not path.is_file() or path.stat().st_size < _MIN_BIN_BYTES:
            return False
    except OSError:
        return False
    normalized = str(path).replace("\\", "/").lower()
    return not any(normalized.startswith(d.lower()) for d in _SYSTEM_DIRS)


#: VAD 模型文件名（silero v6.2，864 KB）
_VAD_MODEL_NAMES = ("ggml-silero-v6.2.0.bin", "ggml-silero-v5.1.2.bin")


def models_dir() -> Path:
    """GGML weight directory."""
    env = os.environ.get("MEDIASCRIBE_WHISPER_CPP_MODELS", "").strip()
    return Path(env) if env else DEFAULT_MODELS_DIR


def find_binary() -> Optional[Path]:
    """Locate the whisper.cpp executable.

    Search order: ``MEDIASCRIBE_WHISPER_CPP_BIN`` env var → PATH → common
    install directories. Returns ``None`` when not found (no exception —
    callers treat it as "engine unavailable").
    """
    env = os.environ.get("MEDIASCRIBE_WHISPER_CPP_BIN", "").strip()
    if env:
        p = Path(env)
        if p.exists() and _is_plausible_binary(p):
            return p
    for name in _BIN_CANDIDATE_NAMES:
        found = shutil.which(name)
        if found and _is_plausible_binary(Path(found)):
            return Path(found)
    for d in _BIN_SEARCH_DIRS:
        for name in (*_BIN_CANDIDATE_NAMES, *_BIN_LEGACY_NAMES):
            p = d / name
            if p.exists() and _is_plausible_binary(p):
                return p
    return None


def missing_runtime_dlls(binary: Path) -> List[str]:
    """List runtime DLLs missing next to the binary (the official package
    is dynamically linked).

    Pitfall measured in practice: provisioning only ``whisper-cli.exe``
    without the sibling ``whisper.dll`` / ``ggml*.dll`` ⇒ the process
    exits silently with ``STATUS_DLL_NOT_FOUND`` (0xC0000135) and no
    readable error.
    """
    parent = binary.parent
    if not parent.exists():
        return []
    dlls = {p.name.lower() for p in parent.glob("*.dll")}
    if not dlls:
        return []  # 静态链接构建 ⇒ 不需要运行时 DLL
    required = ("whisper.dll", "ggml.dll")
    return [name for name in required if name not in dlls]


def find_model(model: str) -> Optional[Path]:
    """Locate the GGML weight file (``ggml-<model>.bin``)."""
    d = models_dir()
    for name in (f"ggml-{model}.bin", f"ggml-{model}.q5_0.bin", f"ggml-{model}-q5_0.bin"):
        p = d / name
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def find_vad_model() -> Optional[Path]:
    """Locate the VAD model (optional — without it only no-VAD decoding is possible)."""
    d = models_dir()
    for name in _VAD_MODEL_NAMES:
        p = d / name
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def is_available() -> bool:
    """Whether the engine is usable (binary present — no Python deps needed)."""
    return find_binary() is not None


def _to_wav_16k(src: Path, dst: Path) -> bool:
    """Convert to 16 kHz mono PCM16 WAV (whisper.cpp's input requirement)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(dst),
            ],
            check=True,
            timeout=600,
        )
        return True
    except Exception as exc:
        logger.warning("whisper.cpp: 音频转码失败: %s", exc)
        return False


def _parse_offsets(value: Any) -> float:
    """Convert a finite, nonnegative whisper.cpp offset from milliseconds to seconds."""
    if isinstance(value, bool):
        raise ValueError("invalid whisper.cpp offset")
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid whisper.cpp offset") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("invalid whisper.cpp offset")
    return seconds


class WhisperCppTranscriber(Transcriber):
    """whisper.cpp transcriber.

    Parameters
    ----------
    model : str
        Whisper tier name (``"tiny"`` / ``"base"`` / ``"small"`` / ...),
        mapping to ``ggml-<model>.bin``.
    device : str, optional
        Only ``"cpu"`` is meaningful (this implementation calls a
        prebuilt CPU binary; a CUDA build needs a separately provisioned
        CUDA binary package, to be wired through the same interface).
    binary : Path, optional
        whisper.cpp executable path. Auto-detected when ``None``.
    vad : bool
        Whether to enable engine-layer VAD. Default **False**.

        Measured basis (2026-10-24, tiny model, Chinese corpus, same machine):

        ==========================  ==========  ==========
        Audio                        VAD off     VAD on
        ==========================  ==========  ==========
        Clean Chinese                **7.38%**   19.67%
        Heavy environmental noise    94.67%      **81.97%**
        ==========================  ==========  ==========

        ⇒ VAD **only clearly helps in noise** (12.7 points) and actively
        **hurts on clean audio** (12.3 points — the tiny model's VAD
        threshold is aggressive and clips valid speech). The MediaScribe
        pipeline **already has** its own VAD and chunking
        (``webrtcvad`` + chunked transcriber) ⇒ enabling it again at the
        engine layer is duplicate processing. Hence default off,
        opt-in.
    threads : int
        Inference thread count. ``None`` → half the physical cores.
    """

    name = "whisper-cpp"

    def __init__(
        self,
        model: str = "tiny",
        device: Optional[str] = None,
        *,
        binary: Optional[Path] = None,
        vad: bool = False,
        threads: Optional[int] = None,
    ):
        self.model_name = model
        self.device = device or "cpu"
        self._binary = binary
        self.vad = vad
        self.threads = threads
        self._binary_hash_cache: dict[tuple[Path, int, int], str] = {}

    # -- 资源定位 ----------------------------------------------------------
    def _resolve_binary(self) -> Path:
        if self._binary is not None:
            return Path(self._binary)
        found = find_binary()
        if found is None:
            raise RuntimeError(t("CPP_BINARY_NOT_FOUND"))
        missing = missing_runtime_dlls(found)
        if missing:
            raise RuntimeError(
                t("CPP_DLL_INCOMPLETE", missing=", ".join(missing), parent=found.parent)
            )
        self._binary = found
        return found

    def _resolve_model(self) -> Path:
        found = find_model(self.model_name)
        if found is None:
            raise ModelNotReadyError(check_weights("whisper-cpp", self.model_name))
        return found

    def _resolve_threads(self) -> int:
        if self.threads:
            return self.threads
        try:
            import psutil  # type: ignore

            cores = psutil.cpu_count(logical=False) or (os.cpu_count() or 2)
        except Exception:
            cores = (os.cpu_count() or 2) // 2
        return max(1, cores // 2)

    def prepare(self, **kwargs) -> None:
        self._resolve_binary()
        self._resolve_model()

    def _binary_sha256(self, binary: Path) -> str:
        """Hash the resolved binary, reusing the result while its stat identity is stable."""
        resolved = binary.resolve()
        stat = resolved.stat()
        key = (resolved, stat.st_size, stat.st_mtime_ns)
        cached = self._binary_hash_cache.get(key)
        if cached is not None:
            return cached
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        fingerprint = digest.hexdigest()
        self._binary_hash_cache[key] = fingerprint
        return fingerprint

    def effective_decode_params(
        self, *, language: str | None, initial_prompt: str | None
    ) -> dict[str, Any]:
        """Return the runtime and decode inputs that identify a resumable run."""
        capability = self.checkpoint_capability()
        if capability is None or capability.mode != "window":
            raise ValueError("whisper.cpp requires a window checkpoint capability")
        return {
            "engine": self.name,
            "model": self.model_name,
            "device": self.device,
            "language": language,
            "initial_prompt": simplified_initial_prompt(initial_prompt, language),
            "vad": self.vad,
            "threads": self._resolve_threads(),
            "binary_sha256": self._binary_sha256(self._resolve_binary()),
            "window_seconds": capability.window_seconds,
            "overlap_seconds": capability.overlap_seconds,
        }

    # -- 转录 --------------------------------------------------------------
    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: Optional[str] = None,
        progress: Optional[Any] = None,
        checkpoint_session: Optional[CheckpointSession] = None,
        **kwargs,
    ) -> dict[str, Any]:
        self._resolve_binary()
        self._resolve_model()
        audio_path = Path(audio_path)
        language = kwargs.get("language")
        effective_prompt = simplified_initial_prompt(prompt, language)

        if checkpoint_session is not None:
            if checkpoint_session.capability.mode != "window":
                raise ValueError("whisper.cpp requires a window checkpoint session")

            detected_language = (
                checkpoint_session.cursor.detected_language if checkpoint_session.cursor else None
            )

            def transcribe_window(window_path: Path, window_duration: float) -> dict[str, Any]:
                nonlocal detected_language
                with tempfile.TemporaryDirectory(prefix="mediascribe-cpp-window-") as directory:
                    out_prefix = Path(directory) / "out"
                    segments, window_language = self._run_binary_on_wav(
                        window_path,
                        out_prefix,
                        detected_language or language,
                        effective_prompt,
                        window_duration=window_duration,
                    )
                detected_language = detected_language or window_language
                return {
                    "text": " ".join(s["text"] for s in segments),
                    "segments": segments,
                    "language": detected_language,
                }

            window_result = run_windowed_transcription(
                audio_path,
                capability=checkpoint_session.capability,
                session=checkpoint_session,
                transcribe_window=transcribe_window,
                cancel_event=kwargs.get("cancel_event"),
            )
            return {**window_result, "model": self.model_name}

        tmp_dir = Path(tempfile.mkdtemp(prefix="mediascribe-cpp-"))
        wav_path = tmp_dir / "input.wav"
        out_prefix = tmp_dir / "out"
        try:
            # 16 kHz mono 是 whisper.cpp 的硬输入要求 —— 即便是 .wav 输入
            # 也必须重采样/缩声道(曾经 .wav 直接拷贝,44.1k 立体声直录会
            # 报错或劣化,违背模块契约,2026-10-29 审查 P2 修正)。
            if not _to_wav_16k(audio_path, wav_path):
                raise RuntimeError(t("CPP_TRANSCODE_FAILED", path=audio_path))

            if progress:
                logger.info("whisper.cpp 转录中: %s", audio_path.name)
            segments, detected_language = self._run_binary_on_wav(
                wav_path,
                out_prefix,
                language,
                effective_prompt,
            )
            return {
                "text": " ".join(s["text"] for s in segments).strip(),
                "segments": segments,
                "language": detected_language or language,
                "model": self.model_name,
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _run_binary_on_wav(
        self,
        wav_path: Path,
        out_prefix: Path,
        language: Optional[str],
        effective_prompt: Optional[str],
        *,
        window_duration: Optional[float] = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Run whisper.cpp on a canonical WAV and parse its complete JSON output."""
        binary = self._resolve_binary()
        model_file = self._resolve_model()
        cmd: List[str] = [
            str(binary),
            "-m",
            str(model_file),
            "-f",
            str(wav_path),
            "-t",
            str(self._resolve_threads()),
            "-oj",
            "-of",
            str(out_prefix),
            "-nt",
        ]
        if language:
            cmd += ["-l", language]
        if effective_prompt:
            cmd += ["--prompt", effective_prompt]
        vad_model = find_vad_model() if self.vad else None
        if self.vad and vad_model is None:
            logger.warning(
                "whisper.cpp: 未找到 VAD 模型（%s），退回无 VAD 转写 —— 噪声音频上可能出现明显幻觉",
                models_dir() / _VAD_MODEL_NAMES[0],
            )
        if vad_model is not None:
            cmd += ["--vad", "--vad-model", str(vad_model)]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=False)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-800:]
            raise RuntimeError(t("CPP_RUN_FAILED", code=proc.returncode, tail=tail))
        json_path = Path(str(out_prefix) + ".json")
        if not json_path.is_file():
            raise RuntimeError(
                t("CPP_RUN_FAILED", code=proc.returncode, tail="missing JSON output")
            )
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("whisper.cpp JSON output must be an object")
        detected_language: str | None = None
        if "result" in data:
            result = data["result"]
            if not isinstance(result, dict):
                raise ValueError("whisper.cpp JSON result must be an object")
            if "language" in result:
                detected_language = result["language"]
                if not isinstance(detected_language, str):
                    raise ValueError("whisper.cpp detected language must be a string")
        items = data.get("transcription")
        if not isinstance(items, list):
            raise ValueError("whisper.cpp JSON transcription must be a list")
        if window_duration is not None and (
            not math.isfinite(window_duration) or window_duration < 0
        ):
            raise ValueError("invalid whisper.cpp window duration")

        segments: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("whisper.cpp JSON transcription item must be an object")
            offsets = item.get("offsets")
            if not isinstance(offsets, dict):
                raise ValueError("invalid whisper.cpp offset object")
            start = _parse_offsets(offsets.get("from"))
            end = _parse_offsets(offsets.get("to"))
            if end < start:
                raise ValueError("invalid whisper.cpp offset order")
            if window_duration is not None and end > window_duration + 0.05:
                raise ValueError("whisper.cpp offset exceeds window duration")
            text = item.get("text")
            if not isinstance(text, str):
                raise ValueError("whisper.cpp JSON transcription text must be a string")
            segments.append({"text": text.strip(), "start": start, "end": end})
        return segments, detected_language
