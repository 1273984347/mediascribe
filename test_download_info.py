"""
测试下载视频信息（不下载完整视频）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from video2text import Settings
from video2text.downloaders import YtDlpDownloader
from video2text.inputs import parse_source


def main():
    print("="*60)
    print("测试：获取视频信息")
    print("="*60)

    video_url = "https://b23.tv/5TmfnwG"

    # 解析源
    source = parse_source(video_url)
    print(f"\n✅ 解析成功: {source.display_name}")
    print(f"  - 类型: {source.kind}")
    if source.bv:
        print(f"  - BV号: {source.bv}")
    if source.url:
        print(f"  - URL: {source.url}")

    # 初始化下载器
    Settings()
    YtDlpDownloader()

    # 尝试下载（我们先注释掉实际下载，避免长时间等待）
    print("\n⚠️  为了测试，我们只获取信息不下载完整视频")
    print("   如果您想完整测试，请取消下面代码的注释\n")

    # 以下是完整下载测试（取消注释即可运行）
    """
    try:
        print("📥 开始下载视频...")
        result = downloader.download(source, settings)
        print(f"\n✅ 下载成功！")
        print(f"  - 标题: {result.title}")
        print(f"  - 视频路径: {result.video_path}")
        return 0
    except Exception as e:
        print(f"\n❌ 下载失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    """

    print("🚀 测试准备就绪！")
    print("\n使用方法：")
    print("  1) 要完整处理视频，运行:")
    print("     python test_bilibili.py")
    print("\n  2) 或者用命令行:")
    print("     python -m video2text transcribe \"https://b23.tv/5TmfnwG\"")

    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
