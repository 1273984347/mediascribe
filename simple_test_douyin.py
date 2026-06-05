#!/usr/bin/env python3
"""
简单测试抖音下载器 - 直接使用已有文件演示流程
"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

from video2text import Pipeline, Settings


def main():
    print("=" * 60)
    print("抖音视频转录（无需 cookies！）")
    print("=" * 60)

    # 使用我们之前已经下载好的文件来演示
    existing_file = Path("output/downloads/douyin_audio_test.mp4")

    if not existing_file.exists():
        print(f"\n❌ 找不到测试文件: {existing_file}")
        print("请先运行我们之前的测试来获取文件")
        return 1

    print(f"\n🎯 使用已有文件: {existing_file}")

    print("\n🚀 初始化 Pipeline...")
    settings = Settings()
    pipeline = Pipeline(settings)

    print("\n📝 开始转录...")
    try:
        # 直接使用本地文件
        result = pipeline.transcribe(str(existing_file), language="zh")

        print("\n" + "=" * 60)
        print("✅ 成功！")
        print("=" * 60)
        print(f"📝 转录文件: {result.transcript_path}")
        print(f"🔊 音频文件: {result.audio_path}")
        print(f"📊 元数据: {result.metadata_path}")

        print("\n📄 转录内容预览:")
        with open(result.transcript_path, "r", encoding="utf-8") as f:
            content = f.read()
            # 显示前 800 字符
            print(content[:800] + "..." if len(content) > 800 else content)

    except Exception as e:
        print(f"\n❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print("\n" + "=" * 60)
    print("💡 使用说明:")
    print("=" * 60)
    print("1. 在浏览器中打开抖音视频")
    print("2. 按 F12 打开开发者工具 -> Network 标签")
    print("3. 播放视频，找到 douyinvod.com 开头的请求")
    print("4. 复制该 URL")
    print("5. 运行: python -m video2text transcribe \"<douyinvod_url>\"")
    print("   或者使用我们的测试脚本:")
    print("   python simple_test_douyin.py")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
