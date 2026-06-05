"""
音频工具 - 参考 bili2text 的 FFmpeg 实现
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional


def extract_audio(
    video_path: Path,
    output_dir: Path,
    stem: str,
    progress: Optional[Any] = None,
) -> Optional[Path]:
    """从视频提取音频 - 参考 bili2text"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg，请先安装并添加到 PATH")

    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / f"{stem}.wav"

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

    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
    )

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg 错误: {result.stderr}")

    if audio_path.exists():
        return audio_path
    return None
