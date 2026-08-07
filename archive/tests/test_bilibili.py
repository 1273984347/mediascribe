"""
测试 Bilibili 视频转写
视频链接: https://b23.tv/5TmfnwG
"""
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from video2text import Pipeline, Settings


def main():
    print("="*60)
    print("Video2Text - Bilibili 视频测试")
    print("="*60)

    # 视频链接
    video_url = "https://b23.tv/5TmfnwG"
    print(f"\n处理视频: {video_url}")

    # 初始化配置（使用 small 模型，速度和质量平衡）
    settings = Settings(
        model="small",  # tiny, base, small, medium, large
        engine="whisper",  # whisper, whisperx, faster-whisper
    )

    print("\n配置:")
    print(f"  - 工作目录: {settings.workspace_root}")
    print(f"  - 模型: {settings.model}")
    print(f"  - 引擎: {settings.engine}")

    # 创建 Pipeline
    print("\n创建 Pipeline...")
    pipeline = Pipeline(settings)

    # 开始处理
    print("\n开始处理视频...")
    print("-"*60)

    try:
        result = pipeline.transcribe(
            video_url,
            language="zh"  # 指定中文，提升识别准确率
        )

        print("\n" + "="*60)
        print("✅ 处理完成！")
        print("="*60)
        print(f"\n📝 转写文件: {result.transcript_path}")
        print(f"📋 元数据: {result.metadata_path}")
        print(f"🎵 音频文件: {result.audio_path}")
        print(f"🎬 视频文件: {result.video_path}")
        print(f"🗣️  识别语言: {result.language}")
        print(f"⚙️  使用引擎: {result.engine}")

        # 显示前 500 字
        print("\n📖 前 500 字预览:")
        print("-"*60)
        print(result.text[:500])
        print("...")

        return 0

    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
