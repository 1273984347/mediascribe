"""
Video2Text 命令行工具
真正深度整合了：
- yt-dlp（视频下载）
- bili2text（工作流设计）
- WhisperX（说话人分离、Word-level 对齐）
- faster-whisper（高性能）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Settings
from .pipeline import Pipeline


def main():
    parser = argparse.ArgumentParser(
        description="🎬 Video2Text - 视频转文字工具（深度整合版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
📦 整合项目：
  - yt-dlp: 多平台视频下载
  - bili2text: 优雅的工作流设计
  - WhisperX: 说话人分离 + Word-level 对齐
  - faster-whisper: whisper.cpp 的高性能 Python 版本

🚀 使用示例：
  # 基本使用（Whisper）
  python -m video2text transcribe video.mp4
  python -m video2text transcribe https://www.bilibili.com/video/BV...

  # 使用 WhisperX + 说话人分离
  python -m video2text transcribe video.mp4 --engine whisperx --diarization --hf-token YOUR_TOKEN

  # 使用 faster-whisper（更快）
  python -m video2text transcribe video.mp4 --engine faster-whisper

  # 批量处理
  python -m video2text batch video1.mp4 video2.mp4 https://...
        """,
    )

    # 全局选项
    parser.add_argument(
        "--workspace", "-w",
        type=Path,
        help="工作目录（默认: ./output）",
    )
    parser.add_argument(
        "--model", "-m",
        default="small",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper 模型（默认: small）",
    )
    parser.add_argument(
        "--device", "-d",
        choices=["cpu", "cuda"],
        help="运行设备（默认: 自动检测）",
    )
    parser.add_argument(
        "--engine", "-e",
        default="whisper",
        choices=["whisper", "whisperx", "faster-whisper"],
        help="转录引擎（默认: whisper）",
    )
    parser.add_argument(
        "--language", "-l",
        help="语言代码（如: zh, en, ja）",
    )

    # 子命令
    subparsers = parser.add_subparsers(
        title="命令",
        dest="command",
        required=True,
    )

    # 转录命令
    transcribe_parser = subparsers.add_parser(
        "transcribe",
        aliases=["t"],
        help="转录单个视频/音频",
    )
    transcribe_parser.add_argument(
        "input",
        help="输入源（本地文件或 URL）",
    )
    transcribe_parser.add_argument(
        "--output", "-o",
        type=Path,
        help="输出文件路径",
    )
    transcribe_parser.add_argument(
        "--hf-token",
        help="Hugging Face Token（说话人分离需要）",
    )
    transcribe_parser.add_argument(
        "--diarization",
        action="store_true",
        help="启用说话人分离（需要 WhisperX + HF Token）",
    )
    transcribe_parser.add_argument(
        "--wechat-cookies",
        help=(
            "微信公众号 cookies（key=value 形式，可用逗号分隔多个）。"
            " 用于绕过登录墙文章。"
        ),
    )
    transcribe_parser.add_argument(
        "--wechat-cookie-file",
        type=Path,
        help=(
            "微信公众号 cookies 文件路径。Netscape / JSON / key=value 格式均支持。"
        ),
    )

    # 批量命令
    batch_parser = subparsers.add_parser(
        "batch",
        help="批量处理多个输入",
    )
    batch_parser.add_argument(
        "inputs",
        nargs="*",
        help="输入源列表",
    )
    batch_parser.add_argument(
        "--file", "-f",
        type=Path,
        help="从文件读取输入列表（每行一个）",
    )
    batch_parser.add_argument(
        "--hf-token",
        help="Hugging Face Token",
    )
    batch_parser.add_argument(
        "--diarization",
        action="store_true",
        help="启用说话人分离",
    )
    batch_parser.add_argument(
        "--wechat-cookies",
        help="微信公众号 cookies（key=value 形式，可用逗号分隔多个）",
    )
    batch_parser.add_argument(
        "--wechat-cookie-file",
        type=Path,
        help="微信公众号 cookies 文件路径（Netscape / JSON / key=value）",
    )

    args = parser.parse_args()

    # 解析 --wechat-cookies（CLI 形式：skey=abc,uin=12345）
    wechat_cookies_dict: dict = {}
    raw_cookies = getattr(args, "wechat_cookies", None)
    if raw_cookies:
        for kv in raw_cookies.split(","):
            kv = kv.strip()
            if "=" in kv:
                k, v = kv.split("=", 1)
                wechat_cookies_dict[k.strip()] = v.strip()

    # 创建配置
    settings = Settings(
        workspace_root=args.workspace,
        model=args.model,
        device=args.device,
        engine=args.engine,
        language=args.language,
        hf_token=getattr(args, "hf_token", None),
        diarization=getattr(args, "diarization", False),
        wechat_cookies=wechat_cookies_dict or None,
        wechat_cookies_file=getattr(args, "wechat_cookie_file", None),
    )

    # 创建 Pipeline
    pipeline = Pipeline(settings)

    # 执行命令
    try:
        if args.command in ["transcribe", "t"]:
            pipeline.transcribe(
                args.input,
                output=args.output,
                language=args.language,
            )

        elif args.command == "batch":
            inputs = list(args.inputs)
            if args.file:
                with open(args.file, "r", encoding="utf-8") as f:
                    inputs.extend([line.strip() for line in f if line.strip()])

            if not inputs:
                print("❌ 没有提供输入")
                sys.exit(1)

            print(f"🚀 批量处理 {len(inputs)} 个输入...")
            results = []
            for i, input_source in enumerate(inputs, 1):
                print(f"\n--- [{i}/{len(inputs)}] ---")
                try:
                    result = pipeline.transcribe(
                        input_source,
                        language=args.language,
                    )
                    results.append((input_source, True, result))
                except Exception as e:
                    print(f"❌ 处理失败: {e}")
                    results.append((input_source, False, str(e)))

            # 总结
            print(f"\n{'='*50}")
            print("📊 批量处理完成")
            success = sum(1 for _, ok, _ in results if ok)
            print(f"✅ 成功: {success}/{len(results)}")
            for source, ok, result in results:
                status = "✅" if ok else "❌"
                print(f"{status} {source}")

    except KeyboardInterrupt:
        print("\n\n⏹️ 用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
