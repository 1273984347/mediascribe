"""
Video2Text 演示脚本 - 展示如何使用深度整合的功能
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from video2text import Pipeline, Settings

print("="*70)
print("🎬 Video2Text 深度整合演示")
print("="*70)
print()
print("这个项目整合了:")
print("  1. yt-dlp - 支持数千个网站的视频下载")
print("  2. bili2text - 优雅的架构和工作流")
print("  3. WhisperX - 说话人分离和词级对齐")
print("  4. faster-whisper - whisper.cpp 的高性能 Python 版本")
print()
print("="*70)
print()

# 1. 初始化设置
print("📋 步骤 1: 初始化配置...")
settings = Settings(
    model="small",      # 可用: tiny, base, small, medium, large
    engine="whisper",   # 可用: whisper, whisperx, faster-whisper
    language="zh"       # 可选: zh, en, ja, etc.
)
print(f"   ✓ 工作目录: {settings.workspace_root}")
print(f"   ✓ 模型: {settings.model}")
print(f"   ✓ 引擎: {settings.engine}")
print(f"   ✓ 语言: {settings.language}")
print()

# 2. 创建 Pipeline
print("🔧 步骤 2: 创建 Pipeline...")
pipeline = Pipeline(settings)
print("   ✓ 已准备好处理")
print()

print("="*70)
print("🚀 使用指南")
print("="*70)
print()
print("命令行使用:")
print("  # 基本使用")
print("  python -m video2text --language zh transcribe <视频路径或URL>")
print()
print("  # 使用 faster-whisper (更快)")
print("  python -m video2text --engine faster-whisper --language zh transcribe <视频>")
print()
print("  # 使用 WhisperX (需要安装)")
print("  pip install whisperx")
print("  python -m video2text --engine whisperx --language zh transcribe <视频>")
print()
print("="*70)
print("📝 支持的功能")
print("="*70)
print()
print("✓ 本地视频文件转文字")
print("✓ Bilibili 等在线视频转文字 (需有效链接)")
print("✓ 多模型选择")
print("✓ 多转录引擎")
print("✓ 自动生成字幕和元数据")
print()
print("💡 提示: 要处理您的 Bilibili 链接, 请确保使用完整的 URL")
print("   (如 https://www.bilibili.com/video/BVxxxxxx)")
print()
print("="*70)
