#!/usr/bin/env python3
from pathlib import Path

import requests

# 从浏览器中提取的音频URL
audio_url = "https://v26-web.douyinvod.com/38ad1e2b1cf8fe7558eb094492e4aa21/6a20a8b6/video/tos/cn/tos-cn-ve-15c000-ce/oIXASHAA5FgAABnAKq9g9CwAnERTfEhDAeAhjC/media-audio-und-mp4a/?a=6383&br=189&bt=189&btag=c0000e00038000&cd=0%7C0%7C0%7C11&ch=0&cquery=100o_100w&cr=11&cs=4&cv=1&dr=0&dy_q=1780437990&er=1&l=20260603060630F50B9098F9A371BCED22&lr=default&mime_type=video_mp4&qs=0&rc=NDllOzM2ZTg6ZDNkZDplNUBpM2pvZHg5cnJxOzMzbGkzNUBjYi1gNV9fLmEyMzZjL2MvYSNkLTVyMmQ0Z2JhLS1kLTRzcw%3D%3D&temp=1"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.douyin.com/",
}

# 测试下载
print("正在测试直接下载抖音视频/音频...")
output_dir = Path("output") / "downloads"
output_dir.mkdir(parents=True, exist_ok=True)
output_file = output_dir / "douyin_audio_test.mp4"

try:
    response = requests.get(audio_url, headers=headers, stream=True)
    response.raise_for_status()

    with open(output_file, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    print(f"✓ 成功下载到: {output_file}")
    print(f"文件大小: {output_file.stat().st_size / 1024 / 1024:.2f} MB")
except Exception as e:
    print(f"✗ 下载失败: {e}")
