#!/usr/bin/env python3
"""
快速验证汇总报告功能（用已有的转录结果）
"""
import sys
from pathlib import Path

sys.path.insert(0, '.')

from douyin_batch.report import generate_summary_report

# 模拟一批处理结果
results = [
    {
        "video_id": "7635519151444320369",
        "url": "https://www.douyin.com/video/7635519151444320369",
        "status": "success",
        "transcript": "output/transcripts/7635519151444320369_mp4-20260603-162117.md",
        "audio": "output/downloads/user_videos/7635519151444320369.mp4",
    },
    {
        "video_id": "7629929627313846757",
        "url": "https://www.douyin.com/video/7629929627313846757",
        "status": "success",
        "transcript": "output/transcripts/7629929627313846757_mp4-20260603-160517.md",
        "audio": "output/downloads/user_videos/7629929627313846757.mp4",
    },
]

summary = generate_summary_report(
    user_url="https://www.douyin.com/user/MS4wLjABAAAAfZf5xo0_HNS3M5GMZY183vk7KCHa4nk_HqVq27pipMU",
    results=results,
    output_dir=Path("output"),
)

print(f"✅ 汇总报告生成成功: {summary}")
print(f"   文件大小: {summary.stat().st_size / 1024:.1f} KB")
