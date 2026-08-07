"""
Video2Text 命令行工具
真正深度整合了：
- yt-dlp（视频下载）
- bili2text（工作流设计）
- WhisperX（说话人分离、Word-level 对齐）
- faster-whisper（高性能）

子命令分发：
- ``transcribe`` / ``batch``：原 CLI（v3.1.0）
- ``profile`` (v3.2.0a)：渲染 profile JSONL 报告
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Optional

from .config import Settings
from .pipeline import Pipeline
from .pipeline_async import AsyncPipeline, _FailedResult

# 通用转录选项：供 transcribe / batch 子命令继承，支持放在子命令前或后。
# 例如 `transcribe x.mp4 --model large` 与 `--model large transcribe x.mp4` 等价。
_common_transcribe_opts = argparse.ArgumentParser(add_help=False)
_common_transcribe_opts.add_argument(
    "--model", "-m",
    default="small",
    choices=[
        "tiny", "base", "small", "medium", "large",
        # v3.2.0d: faster-whisper 支持的扩展模型
        "large-v1", "large-v2", "large-v3",
        "distil-large-v2", "distil-large-v3",
    ],
    help=(
        "Whisper 模型（默认: small）。\n"
        "  快速预览: tiny / base / small\n"
        "  准确率优先: large-v3（推荐, 中文最佳）\n"
        "  速度+准确率平衡: distil-large-v3"
    ),
)
_common_transcribe_opts.add_argument(
    "--device", "-d",
    choices=["cpu", "cuda"],
    help="运行设备（默认: 自动检测）",
)
_common_transcribe_opts.add_argument(
    "--engine", "-e",
    default="whisper",
    choices=["whisper", "whisperx", "faster-whisper"],
    help="转录引擎（默认: whisper）",
)
_common_transcribe_opts.add_argument(
    "--language", "-l",
    help="语言代码（如: zh, en, ja）",
)


def _run_legacy(argv: Optional[List[str]]) -> int:
    """Original v3.1.0 CLI — ``transcribe`` / ``batch`` subcommands."""
    parser = argparse.ArgumentParser(
        description="🎬 Video2Text - 视频转文字工具（深度整合版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[_common_transcribe_opts],
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
    # --model/--device/--engine/--language 见模块级 _common_transcribe_opts，
    # 已通过 parents=[...] 注入顶层解析器与 transcribe/batch 子命令，
    # 支持放在子命令前或后（如 `transcribe x.mp4 --model large`）。

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
        parents=[_common_transcribe_opts],
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
        parents=[_common_transcribe_opts],
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

    args = parser.parse_args(argv)

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
            # v3.2.0e: 批量走 AsyncPipeline.run_batch — GPU 感知并发 +
            # 单视频失败隔离(某个失败不影响其他)。失败位置返回 _FailedResult。
            ap = AsyncPipeline(pipeline)
            try:
                raw = asyncio.run(ap.run_batch(inputs, language=args.language))
            except KeyboardInterrupt:
                print("\n\n⏹️ 用户中断")
                sys.exit(130)
            results = []
            for i, (src, r) in enumerate(zip(inputs, raw), 1):
                print(f"\n--- [{i}/{len(inputs)}] ---")
                if isinstance(r, _FailedResult):
                    print(f"❌ 处理失败: {r.exc}")
                    results.append((src, False, str(r.exc)))
                else:
                    results.append((src, True, r))
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
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Dispatch to the right sub-CLI.

    v3.2.0a adds the ``profile`` subcommand while preserving the v3.1.0
    ``transcribe`` / ``batch`` interface.

    v3.2.0b adds the ``learn`` subcommand for ASR auto-learning.

    If ``argv`` is ``None`` we read ``sys.argv[1:]`` (original v3.1.0
    behaviour).  Callers that want to invoke the CLI in-process can
    pass an explicit list (including an empty list) without
    side-effects from the surrounding shell argv.
    """
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "profile":
        from .profile_cli import main as profile_main
        return profile_main(argv[1:])
    if argv and argv[0] == "learn":
        return _run_learn(argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        if not argv:
            parser = argparse.ArgumentParser(
                prog="python -m video2text",
                description="🎬 Video2Text — video/audio to text pipeline",
            )
            sub = parser.add_subparsers(dest="command")
            sub.add_parser("transcribe", aliases=["t"], help="转录单个视频/音频")
            sub.add_parser("batch", help="批量处理多个输入")
            sub.add_parser("profile", help="渲染 profile JSONL 报告")
            sub.add_parser("learn", help="ASR 术语自动学习")
            parser.print_help(sys.stderr)
            return 1
        # ``-h`` / ``--help`` — let argparse print + exit (POSIX).
        return _run_legacy(["--help"] if argv[0] in ("-h", "--help") else "help")
    if argv[0] == "help":
        print(__doc__)
        return 0
    if argv[0] not in ("transcribe", "t", "batch") and not argv[0].startswith("-"):
        print(f"video2text: unknown command {argv[0]!r}", file=sys.stderr)
        print("Try 'python -m video2text help'", file=sys.stderr)
        return 1
    return _run_legacy(argv)


def _run_learn(argv: List[str]) -> int:
    """``python -m video2text learn`` — ASR 术语自动学习。"""
    parser = argparse.ArgumentParser(
        prog="python -m video2text learn",
        description="ASR 术语自动学习 — 对比正确文本与转录,累积术语映射",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    # learn compare
    cmp = sub.add_parser("compare", help="对比正确文本与转录,提取错误映射")
    cmp.add_argument("--reference", "-r", required=True, help="正确文本 (人工校对)")
    cmp.add_argument("--transcript", "-t", required=True, help="ASR 转录文本")

    # learn edit (推荐)
    edit = sub.add_parser("edit", help="从用户编辑前后文本中学习 (推荐)")
    edit.add_argument("--original", "-o", required=True, help="ASR 原始转录文本")
    edit.add_argument("--corrected", "-c", required=True, help="用户校对后的文本")

    # learn save
    save = sub.add_parser("save", help="手动保存术语映射")
    save.add_argument("--wrong", "-w", required=True, help="错误词")
    save.add_argument("--right", "-r", required=True, help="正确词")

    # learn list
    sub.add_parser("list", help="列出所有术语 (含待确认)")

    # learn confirm
    confirm = sub.add_parser("confirm", help="确认一条待定术语")
    confirm.add_argument("--wrong", "-w", required=True, help="错误词")
    confirm.add_argument("--right", "-r", required=True, help="正确词")

    # learn remove
    remove = sub.add_parser("remove", help="删除一条术语")
    remove.add_argument("--wrong", "-w", required=True, help="错误词")
    remove.add_argument("--right", "-r", required=True, help="正确词")

    # learn export
    exp = sub.add_parser("export", help="导出术语库到 JSON 文件")
    exp.add_argument("--output", "-o", required=True, type=Path, help="输出文件路径")

    # learn import
    imp = sub.add_parser("import", help="从 JSON 文件导入术语库")
    imp.add_argument("--input", "-i", required=True, type=Path, help="输入文件路径")
    imp.add_argument("--no-merge", action="store_true", help="覆盖而非合并")

    # learn clear
    sub.add_parser("clear", help="清空学习记录")

    args = parser.parse_args(argv)

    from .learn import (
        clear_learned_terms,
        compare,
        confirm_term,
        export_terms,
        get_learned_terms,
        import_terms,
        learn,
        learn_from_edit,
        remove_term,
    )
    from .learn import _load_db  # type: ignore[attr-defined]

    if args.action == "compare":
        corrections = compare(args.reference, args.transcript)
        if not corrections:
            print("未发现错误映射")
            return 0
        print(f"发现 {len(corrections)} 条语音相似的错误映射:")
        for wrong, right, ctx in corrections:
            print(f'  "{wrong}" → "{right}"  (上下文: {ctx})')
        db = learn(corrections)
        stats = db.stats()
        print(f"\n已保存到术语库 (共 {stats['total']} 条, {stats['active']} 条生效)")
        return 0

    if args.action == "edit":
        db = learn_from_edit(args.original, args.corrected)
        stats = db.stats()
        if stats["total"] == 0:
            print("未发现语音相似的错误映射")
            return 0
        print(f"已学习 {stats['total']} 条术语 ({stats['active']} 条生效, {stats['pending']} 条待确认)")
        return 0

    if args.action == "save":
        db = learn([(args.wrong, args.right)])
        stats = db.stats()
        print(f'已保存: "{args.wrong}" → "{args.right}"')
        print(f"术语库共 {stats['total']} 条 ({stats['active']} 条生效)")
        return 0

    if args.action == "list":
        db = _load_db()
        stats = db.stats()
        if stats["total"] == 0:
            print("术语库为空")
            return 0
        print(f"术语库: {stats['total']} 条 ({stats['confirmed']} 已确认, {stats['pending']} 待确认, {stats['active']} 生效)")
        print()
        for c in db.terms.values():
            status = "✓" if c.should_apply() else "…"
            print(f'  {status} "{c.wrong}" → "{c.right}" (×{c.count}, {c.confidence:.0%})')
            if c.contexts:
                print(f'     上下文: {c.contexts[0]}')
        return 0

    if args.action == "confirm":
        if confirm_term(args.wrong, args.right):
            print(f'已确认: "{args.wrong}" → "{args.right}"')
        else:
            print(f'未找到: "{args.wrong}" → "{args.right}"')
            return 1
        return 0

    if args.action == "remove":
        if remove_term(args.wrong, args.right):
            print(f'已删除: "{args.wrong}" → "{args.right}"')
        else:
            print(f'未找到: "{args.wrong}" → "{args.right}"')
            return 1
        return 0

    if args.action == "export":
        export_terms(args.output)
        print(f"已导出到 {args.output}")
        return 0

    if args.action == "import":
        db = import_terms(args.input, merge=not args.no_merge)
        stats = db.stats()
        print(f"已导入 ({stats['total']} 条, {stats['active']} 条生效)")
        return 0

    if args.action == "clear":
        clear_learned_terms()
        print("已清空学习记录")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
