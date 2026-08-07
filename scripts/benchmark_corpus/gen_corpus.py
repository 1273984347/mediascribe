"""
v3.2.0b benchmark corpus 生成器 (本地工具,不进 git)。

用途
----
真正的 1-hour WAV 文件太大(>50 MiB),不进 git。这个脚本
按 :file:`corpus.json` 描述的结构生成 4 个占位 WAV(短样本,5-10 秒)
和 reference JSON,只供 CI 跑 schema 验证;真实长音频由 maintainer
在本地用 ``ffmpeg`` / 麦克风 / 公共数据集生成,然后发布到
Hugging Face Datasets 仓库,benchmark 脚本优先从网络拉、本地兜底。

调用
----
::

    python scripts/benchmark_corpus/gen_corpus.py \\
        --out scripts/benchmark_corpus/

不依赖 ffmpeg / GPU,纯 stdlib + wave 模块,保证 CI 可跑。
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import wave
from pathlib import Path
from typing import List

# 文件 / 段落 / 时长与 corpus.json 保持一致
PLACEHOLDER_CLIPS = [
    {
        "name": "01_zh_5min.wav",
        "lang": "zh",
        "target_duration_s": 5.0,  # 占位只 5 秒,真实 benchmark 跑 5 min
        "tone_hz": 440.0,
        "reference": "01_zh_5min.json",
        "placeholder_text": "[ZH placeholder] 真实 corpus 由 maintainer 生成",
    },
    {
        "name": "02_en_15min.wav",
        "lang": "en",
        "target_duration_s": 5.0,
        "tone_hz": 523.25,
        "reference": "02_en_15min.json",
        "placeholder_text": "[EN placeholder] real corpus by maintainer",
    },
    {
        "name": "03_mixed_30min.wav",
        "lang": "mixed",
        "target_duration_s": 5.0,
        "tone_hz": 659.25,
        "reference": "03_mixed_30min.json",
        "placeholder_text": "[MIXED placeholder] 30-min real corpus",
    },
    {
        "name": "04_zh_60min.wav",
        "lang": "zh",
        "target_duration_s": 5.0,
        "tone_hz": 783.99,
        "reference": "04_zh_60min.json",
        "placeholder_text": "[ZH placeholder] 60-min real corpus",
    },
]

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM


def synthesize_sine_wav(
    path: Path,
    duration_s: float,
    tone_hz: float,
    sample_rate: int = SAMPLE_RATE,
) -> None:
    """写一个单频正弦 WAV,纯占位,无任何语义。"""
    n = int(duration_s * sample_rate)
    amplitude = 0.1 * 32767  # 防止削顶
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n):
            t = i / sample_rate
            sample = int(amplitude * math.sin(2 * math.pi * tone_hz * t))
            frames += struct.pack("<h", sample)
        w.writeframes(bytes(frames))


def write_placeholder_reference(
    out_dir: Path,
    clip: dict,
) -> None:
    """写 reference JSON,标记是 placeholder 真实 transcript 不存在。"""
    ref = {
        "schema_version": "video2text-benchmark-corpus/v1",
        "file": clip["name"],
        "language": clip["lang"],
        "duration_s": clip["target_duration_s"],
        "wer_notes": (
            "PLACEHOLDER — real transcript is hand-corrected by a "
            "maintainer. CI does not use this file for WER; the "
            "real benchmark downloads the production corpus from "
            "Hugging Face Datasets."
        ),
        "transcript": [
            {
                "start": 0.0,
                "end": clip["target_duration_s"],
                "speaker": "S1",
                "text": clip["placeholder_text"],
            }
        ],
        "full_text": clip["placeholder_text"],
    }
    out = out_dir / clip["reference"]
    out.write_text(json.dumps(ref, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate placeholder benchmark corpus")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("scripts/benchmark_corpus"),
        help="Output directory (default: scripts/benchmark_corpus)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing placeholder WAVs",
    )
    args = p.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    for clip in PLACEHOLDER_CLIPS:
        wav_path = args.out / clip["name"]
        if wav_path.exists() and not args.force:
            print(f"[skip] {wav_path} (exists, --force to overwrite)")
            continue
        synthesize_sine_wav(wav_path, clip["target_duration_s"], clip["tone_hz"])
        print(f"[ok]   {wav_path} ({clip['target_duration_s']:.0f}s, {clip['tone_hz']} Hz)")
        write_placeholder_reference(args.out, clip)
    print(f"\nDone. Wrote {len(PLACEHOLDER_CLIPS)} placeholder clips to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
