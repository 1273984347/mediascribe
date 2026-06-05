#!/usr/bin/env python3
import sys
from pathlib import Path

# 确保我们在正确的目录
sys.path.insert(0, str(Path(__file__).parent))

from video2text.config import Settings
from video2text.pipeline import Pipeline


def main():
    print("正在初始化 Pipeline...")
    settings = Settings()
    pipeline = Pipeline(settings)

    audio_path = Path("output") / "downloads" / "douyin_audio_test.mp4"

    if not audio_path.exists():
        print(f"找不到文件: {audio_path}")
        return

    print(f"正在转录: {audio_path}")
    result = pipeline.transcribe(str(audio_path), language="zh")

    print("\n" + "="*60)
    print("转录完成！")
    print(f"输出文件: {result.transcript_path}")
    print("="*60)

    # 显示转录内容的前几行
    if result.transcript_path.exists():
        print("\n转录内容预览：")
        with open(result.transcript_path, 'r', encoding='utf-8') as f:
            print(f.read())

if __name__ == "__main__":
    main()
