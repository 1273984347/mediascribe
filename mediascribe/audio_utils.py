"""
音频工具 - 参考 bili2text 的 FFmpeg 实现
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, List, Optional, Tuple

_logger = logging.getLogger(__name__)


# FFmpeg subprocess 默认超时（秒）。
# 30 分钟视频通常 < 1 分钟即可提取完毕，10 分钟超时对绝大多数用例留足余量。
# 损坏视频/无限流可能导致 ffmpeg 死循环 → subprocess 阻塞 → pipeline 卡死。
# 显式 timeout 参数 > MEDIASCRIBE_FFMPEG_TIMEOUT 环境变量 > 此默认值。
_FFMPEG_DEFAULT_TIMEOUT_SECONDS = 600.0


def _source_tag(video_path: Path) -> str:
    """返回源文件的 8 字符短指纹,掺入输出文件名。

    P2-8: 输出文件名固定为 ``{stem}.wav`` 时,并发/连续处理同
    stem 的不同视频会互相覆盖(后写胜出,先完成的 pipeline 拿到
    被覆盖的文件)。指纹取 路径 + 大小 + mtime_ns 的 SHA-256 前
    8 位 — 不读文件内容(视频可达数 GB,全量哈希太贵),同一文件
    被重新下载/修改后指纹也会变化。
    """
    try:
        st = video_path.stat()
        payload = (
            f"{video_path.resolve()}|{st.st_size}|{st.st_mtime_ns}"
        )
    except OSError:
        payload = str(video_path.resolve())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def extract_audio(
    video_path: Path,
    output_dir: Path,
    stem: str,
    progress: Optional[Any] = None,
    timeout: Optional[float] = None,
) -> Optional[Path]:
    """从视频提取音频 - 参考 bili2text

    v3.2.0e+ 增加超时控制:
      * 优先级: 显式 ``timeout`` 参数 > ``MEDIASCRIBE_FFMPEG_TIMEOUT`` 环境变量
        > 默认 ``_FFMPEG_DEFAULT_TIMEOUT_SECONDS`` (600s)。
      * 超时抛 ``RuntimeError`` (包裹 ``subprocess.TimeoutExpired``)，
        避免损坏视频/无限流导致 pipeline 永久阻塞。

    v3.2.0x P2-8: 输出文件名掺入源文件短指纹(``{stem}_{tag}.wav``),
    同 stem 的不同源不再互相覆盖;失败/超时路径清理半写的输出文件。
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg，请先安装并添加到 PATH")

    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / f"{stem}_{_source_tag(video_path)}.wav"

    cmd = [
        ffmpeg,
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(audio_path),
    ]

    if progress:
        print("提取音频中...")

    # 超时解析: 参数 > env > 默认。env="0" 表示禁用超时（极端调试场景）。
    resolved_timeout = timeout
    if resolved_timeout is None:
        env_val = os.environ.get("MEDIASCRIBE_FFMPEG_TIMEOUT", "").strip()
        if env_val:
            try:
                resolved_timeout = float(env_val)
            except ValueError:
                # DRL R2 F-7: 静默回退会让用户误以为 env 生效。改 log warning 提示。
                _logger.warning(
                    "MEDIASCRIBE_FFMPEG_TIMEOUT=%r 不是合法浮点数, "
                    "回退到默认 %.0fs", env_val, _FFMPEG_DEFAULT_TIMEOUT_SECONDS
                )
                resolved_timeout = _FFMPEG_DEFAULT_TIMEOUT_SECONDS
        else:
            resolved_timeout = _FFMPEG_DEFAULT_TIMEOUT_SECONDS
    if resolved_timeout is not None and resolved_timeout <= 0:
        resolved_timeout = None  # 显式禁用

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            timeout=resolved_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # P2-8: 超时路径清理半写输出,避免留下被误认为完整的 wav
        audio_path.unlink(missing_ok=True)
        timeout_desc = f"{resolved_timeout:.0f}s" if resolved_timeout else "N/A"
        raise RuntimeError(
            f"FFmpeg 提取音频超时 (>{timeout_desc}): {video_path.name}"
        ) from exc

    if result.returncode != 0:
        # P2-8: 失败路径同样清理半写输出
        audio_path.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg 错误: {result.stderr}")

    if audio_path.exists():
        return audio_path
    return None


# ---------------------------------------------------------------------------
# VAD (Voice Activity Detection)
# ---------------------------------------------------------------------------
# Optional dependency: webrtcvad is a tiny C library wrapper (~1 MB).
# It expects 16-bit PCM mono audio at 8/16/32/48 kHz.  ffmpeg already
# produces this format, so no resampling is needed at runtime.
#
# VAD is *not* required: the chunked transcriber falls back to fixed
# windows when the package is missing or the audio is not in a
# supported format.  The split is also bounded to ``max_chunk_seconds``
# so a single continuous speech segment longer than the cap still
# gets cut into pieces.

# Allowed sample rates for webrtcvad
_VAD_SAMPLE_RATES = (8000, 16000, 32000, 48000)
_VAD_FRAME_MS = 30  # 10/20/30 are supported
_VAD_FRAME_BYTES_MULT = 2  # 16-bit = 2 bytes per sample


def _is_webrtcvad_available() -> bool:
    """Return True if webrtcvad is importable."""
    try:
        import webrtcvad  # noqa: F401

        return True
    except ImportError:
        return False


def _read_wave_pcm16(path: Path) -> Tuple[bytes, int]:
    """Read a 16-bit mono WAV file.  Returns (raw_pcm_bytes, sample_rate).

    Raises ValueError if the file is not in a format compatible with
    webrtcvad (16-bit, mono, supported rate).

    Note: 整段 PCM 驻留内存 — 仅供 ``transcribers.chunked`` 做格式
    校验等小文件场景;``detect_speech_segments`` 已改为流式读取
    (P2-9),不再经过本函数。
    """
    if path.suffix.lower() != ".wav":
        raise ValueError(f"webrtcvad needs a WAV file, got {path.suffix}")
    with wave.open(str(path), "rb") as w:
        rate = _validate_wave_params(w)
        frames = w.readframes(w.getnframes())
    return frames, rate


def _validate_wave_params(w: wave.Wave_read) -> int:
    """Validate an open WAV against webrtcvad requirements; return rate."""
    if w.getsampwidth() != 2:
        raise ValueError(
            f"webrtcvad needs 16-bit audio (got {w.getsampwidth() * 8}-bit)"
        )
    if w.getnchannels() != 1:
        raise ValueError(
            f"webrtcvad needs mono audio (got {w.getnchannels()}-channel)"
        )
    rate = w.getframerate()
    if rate not in _VAD_SAMPLE_RATES:
        raise ValueError(
            f"webrtcvad needs sample rate in {_VAD_SAMPLE_RATES}, got {rate}"
        )
    return rate


def detect_speech_segments(
    audio_path: Path,
    *,
    aggressiveness: int = 2,
    min_speech_seconds: float = 0.5,
    min_silence_seconds: float = 0.3,
) -> List[Tuple[float, float]]:
    """Detect speech regions in a 16 kHz mono WAV file.

    Parameters
    ----------
    audio_path
        Path to a 16-bit mono WAV file at 8/16/32/48 kHz.
    aggressiveness
        VAD aggressiveness 0-3 (higher = more aggressive silence).
    min_speech_seconds
        Drop speech segments shorter than this after merging.
    min_silence_seconds
        Pad each side of every speech region by this much so that the
        Whisper transcriber has enough context to lock on.

    Returns
    -------
    list of (start_sec, end_sec) tuples covering all speech regions.
    Raises ``ImportError`` if ``webrtcvad`` is not installed.
    Raises ``ValueError`` if the audio is not in a compatible format.

    P2-9: 音频按 ``wave.readframes`` 分块流式读取 — 内存中只保留
    每帧 1 bit 的语音布尔列表与最终区间列表,30 分钟 16 kHz 单声道
    (~57 MB PCM) 不再整段驻留。签名与输出语义不变。
    """
    if not _is_webrtcvad_available():
        raise ImportError(
            "webrtcvad is not installed. "
            "Install with `pip install mediascribe[vad]`."
        )
    import webrtcvad  # local import so the module loads even if missing

    if audio_path.suffix.lower() != ".wav":
        raise ValueError(f"webrtcvad needs a WAV file, got {audio_path.suffix}")

    frame_is_speech: List[bool] = []
    with wave.open(str(audio_path), "rb") as w:
        rate = _validate_wave_params(w)
        vad = webrtcvad.Vad(int(aggressiveness))

        frame_bytes = int(rate * _VAD_FRAME_MS / 1000) * _VAD_FRAME_BYTES_MULT
        # 每次 readframes 读 0.5 s 的帧数(32000 字节 @16 kHz),
        # 比逐帧读减少 ~2000x 系统调用,又不会整段驻留内存。
        frames_per_read = max(1, rate // 2)

        tail = b""
        while True:
            buf = w.readframes(frames_per_read)
            if not buf:
                break
            buf = tail + buf
            n_complete = len(buf) // frame_bytes
            for i in range(n_complete):
                chunk = buf[i * frame_bytes : (i + 1) * frame_bytes]
                try:
                    flag = vad.is_speech(chunk, rate)
                except Exception:
                    flag = False
                frame_is_speech.append(flag)
            # 不完整的尾部帧留到下一个块(readframes 可能恰好截断)
            tail = buf[n_complete * frame_bytes :]

    n_frames = len(frame_is_speech)
    if n_frames == 0:
        return []

    # Convert per-frame flags to (start, end) segments in seconds.
    segments: List[Tuple[float, float]] = []
    in_speech = False
    seg_start = 0
    sec_per_frame = _VAD_FRAME_MS / 1000.0
    for i, is_speech in enumerate(frame_is_speech):
        if is_speech and not in_speech:
            seg_start = i
            in_speech = True
        elif not is_speech and in_speech:
            segments.append((seg_start * sec_per_frame, i * sec_per_frame))
            in_speech = False
    if in_speech:
        segments.append((seg_start * sec_per_frame, n_frames * sec_per_frame))

    # Merge nearby speech regions (gaps shorter than min_silence are
    # treated as the same utterance — e.g. short breaths).
    merged: List[List[float]] = []
    for s, e in segments:
        if merged and s - merged[-1][1] < min_silence_seconds:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    # Apply context padding and drop too-short segments.
    total_seconds = n_frames * sec_per_frame
    out: List[Tuple[float, float]] = []
    for s, e in merged:
        s = max(0.0, s - min_silence_seconds)
        e = min(total_seconds, e + min_silence_seconds)
        if e - s >= min_speech_seconds:
            out.append((s, e))
    return out


def vad_available() -> bool:
    """Public predicate: True iff ``detect_speech_segments`` can run."""
    return _is_webrtcvad_available()
