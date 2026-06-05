#!/usr/bin/env python3
"""
测试抖音下载器 - 使用真实的 douyinvod URL
"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

from video2text.config import Settings
from video2text.pipeline import Pipeline


def main():
    print("=" * 60)
    print("测试抖音下载器（无需 cookies！）")
    print("=" * 60)

    # 使用我们刚才测试时获取的真实 douyinvod URL
    # 注意：这个 URL 可能会过期，实际使用时请获取新的
    test_media_url = "https://v26-web.douyinvod.com/38ad1e2b1cf8fe7558eb094492e4aa21/6a20a8b6/video/tos/cn/tos-cn-ve-15c000-ce/oIXASHAA5FgAABnAKq9g9CwAnERTfEhDAeAhjC/media-audio-und-mp4a/?a=6383&br=189&bt=189&btag=c0000e00038000&cd=0%7C0%7C0%7C11&ch=0&cquery=100o_100w&cr=11&cs=4&cv=1&dr=0&dy_q=1780437990&er=1&l=20260603060630F50B9098F9A371BCED22&lr=default&mime_type=video_mp4&qs=0&rc=NDllOzM2ZTg6ZDNkZDplNUBpM2pvZHg5cnJxOzMzbGkzNUBjYi1gNV9fLmEyMzZjL2MvYSNkLTVyMmQ0Z2JhLS1kLTRzcw%3D%3D&temp=1"

    print("\n🎯 测试媒体 URL:")
    print(f"   {test_media_url[:80]}...")

    print("\n🚀 初始化 Pipeline...")
    settings = Settings()
    pipeline = Pipeline(settings)

    print("\n📝 开始完整转录流程...")
    try:
        # 直接传入 douyinvod URL，我们的下载器会自动识别
        result = pipeline.transcribe(test_media_url, language="zh")

        print("\n" + "=" * 60)
        print("✅ 测试成功！")
        print("=" * 60)
        print(f"📝 转录文件: {result.transcript_path}")
        print(f"🔊 音频文件: {result.audio_path}")
        print(f"📊 元数据: {result.metadata_path}")

        print("\n📄 转录内容预览:")
        with open(result.transcript_path, "r", encoding="utf-8") as f:
            content = f.read()
            # 只显示前 500 字符
            print(content[:500] + "..." if len(content) > 500 else content)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
