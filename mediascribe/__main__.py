"""
MediaScribe 命令行工具
真正深度整合了：
- yt-dlp（视频下载）
- bili2text（工作流设计）
- WhisperX（说话人分离、Word-level 对齐）
- faster-whisper（高性能）

子命令分发：
- ``transcribe`` / ``batch``：原 CLI（v3.1.0）
- ``profile`` (v3.2.0a)：渲染 profile JSONL 报告
- ``learn`` (v3.2.0b)：ASR 术语自动学习
- ``doctor`` (v3.4.0)：环境自检（ffmpeg / GPU / 引擎 / 模型缓存）
- ``archive`` (v3.4.0)：创作者主页批量转录（收编 douyin_batch_v3.py）
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
#
# v3.4.0: 所有公共选项 default=argparse.SUPPRESS — Python 3.7+ 的
# 子解析器会把参数解析进全新 namespace 再整包回写主 namespace，
# 若这里给正常默认值，子命令之前解析到的全局值会被子命令的默认值
# 覆盖（``--model large transcribe x`` 静默变回 small）。SUPPRESS 让
# 「未显式传入」的选项不产生属性，前置值得以幸存；读取侧统一用
# ``getattr(args, ..., None)``。实际生效值按
# 「CLI 显式参数 > --config JSON > 内置默认」解析（见 _run_legacy）。
_common_transcribe_opts = argparse.ArgumentParser(add_help=False)
_common_transcribe_opts.add_argument(
    "--model",
    "-m",
    default=argparse.SUPPRESS,
    choices=[
        "auto",
        "tiny",
        "base",
        "small",
        "medium",
        "large",
        # v3.2.0d: faster-whisper 支持的扩展模型
        "large-v1",
        "large-v2",
        "large-v3",
        "distil-large-v2",
        "distil-large-v3",
    ],
    help=(
        "Whisper 模型（默认: small）。\n"
        "  auto: 按音频时长自动推荐（短→medium, 中→small, 长→tiny）\n"
        "  快速预览: tiny / base / small\n"
        "  准确率优先: large-v3（推荐, 中文最佳）\n"
        "  速度+准确率平衡: distil-large-v3"
    ),
)
_common_transcribe_opts.add_argument(
    "--device",
    "-d",
    default=argparse.SUPPRESS,
    choices=["auto", "cpu", "cuda"],
    help="运行设备（默认: auto 自动检测 CUDA > CPU）",
)
_common_transcribe_opts.add_argument(
    "--engine",
    "-e",
    default=argparse.SUPPRESS,
    choices=["whisper", "whisperx", "faster-whisper"],
    help="转录引擎（默认: whisper；faster-whisper 同质量更快）",
)
_common_transcribe_opts.add_argument(
    "--language",
    "-l",
    default=argparse.SUPPRESS,
    help="语言代码（如: zh, en, ja）",
)
_common_transcribe_opts.add_argument(
    "--config",
    metavar="FILE",
    default=argparse.SUPPRESS,
    help=(
        "JSON 配置文件（可含 model/engine/language/device/workspace/"
        "hf_token/diarization/timestamps）。"
        "CLI 显式参数优先于配置文件。"
    ),
)
_common_transcribe_opts.add_argument(
    "--timestamps",
    action="store_true",
    default=argparse.SUPPRESS,
    help="Markdown 正文按 ASR 分段并给每段加 [mm:ss] 时间戳前缀",
)


def _write_latest_pointer(settings, result) -> None:
    """转录成功后写 ``<workspace>/LATEST.txt`` — 内容为最新转录稿路径。

    让"刚才是输出到哪个带时间戳的文件来着"不再靠滚屏找。任何失败
    都吞掉：指针只是便利功能，绝不影响转录主流程（测试 mock 场景
    ``result``/``settings`` 属性不可用也应静默跳过）。
    """
    try:
        ws = Path(settings.workspace_root)
        transcript = Path(result.transcript_path)
        (ws / "LATEST.txt").write_text(str(transcript), encoding="utf-8")
    except Exception:
        pass


def _print_next_steps(result) -> None:
    """转录完成后打印输出位置与可选的下一步操作提示。"""
    try:
        if not Path(result.transcript_path).exists():
            return
        print("\n💡 下一步（可选）：")
        print(
            "   · 校对后回填术语库:  python -m mediascribe learn edit "
            "--original <原稿> --corrected <校对稿>"
        )
        print(
            "   · 开启 LLM 润色（标点/分段/专名）:  "
            "MEDIASCRIBE_LLM_ENABLED=1 + MEDIASCRIBE_LLM_API_BASE <endpoint>"
        )
        print("   · 环境自检:  python -m mediascribe doctor")
    except Exception:
        pass


def _run_legacy(argv: Optional[List[str]]) -> int:
    """Original v3.1.0 CLI — ``transcribe`` / ``batch`` subcommands."""
    parser = argparse.ArgumentParser(
        description="🎬 MediaScribe - 视频转文字工具（深度整合版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[_common_transcribe_opts],
        epilog="""
📦 更多命令：
  python -m mediascribe doctor    环境自检（ffmpeg/GPU/引擎/模型缓存）
  python -m mediascribe archive   批量转录创作者主页（原 douyin_batch_v3.py）
  python -m mediascribe learn     ASR 术语自动学习（越用越准）
  python -m mediascribe profile   渲染性能 profile 报告

🚀 使用示例：
  # 基本使用（模型按音频时长自动推荐）
  python -m mediascribe transcribe video.mp4 --model auto

  # 中文视频：faster-whisper + large-v3（推荐组合）
  python -m mediascribe transcribe video.mp4 -e faster-whisper -m large-v3 -l zh

  # 转录 + 分段时间戳
  python -m mediascribe transcribe <URL> --timestamps

  # 使用 WhisperX + 说话人分离
  python -m mediascribe transcribe video.mp4 --engine whisperx --diarization --hf-token YOUR_TOKEN

  # 批量处理
  python -m mediascribe batch video1.mp4 video2.mp4 https://...

  # 配置文件持久化常用参数
  python -m mediascribe --config mediascribe.json transcribe <URL>
        """,
    )

    # 全局选项
    parser.add_argument(
        "--workspace",
        "-w",
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
        "--output",
        "-o",
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
        help=("微信公众号 cookies（key=value 形式，可用逗号分隔多个）。 用于绕过登录墙文章。"),
    )
    transcribe_parser.add_argument(
        "--wechat-cookie-file",
        type=Path,
        help=("微信公众号 cookies 文件路径。Netscape / JSON / key=value 格式均支持。"),
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
        "--file",
        "-f",
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

    # v3.4.0: JSON 配置文件 — 优先级「CLI 显式参数 > 配置文件 > 内置默认」。
    # 公共选项 default=argparse.SUPPRESS，因此 getattr 取不到即
    # 「CLI 未显式指定」，可以安全地用配置文件值回填。
    file_cfg: dict = {}
    cli_config = getattr(args, "config", None)
    if cli_config:
        import json as _json

        cfg_path = Path(cli_config).expanduser()
        try:
            file_cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"❌ 配置文件不存在: {cfg_path}")
            sys.exit(1)
        except ValueError as exc:
            print(f"❌ 配置文件不是合法 JSON: {cfg_path} — {exc}")
            sys.exit(1)
        if not isinstance(file_cfg, dict):
            print(f"❌ 配置文件顶层必须是 JSON 对象: {cfg_path}")
            sys.exit(1)

    def _from_cli_or_cfg(cli_value, cfg_key: str, fallback):
        if cli_value is not None:
            return cli_value
        v = file_cfg.get(cfg_key)
        return fallback if v is None else v

    # 创建配置
    settings = Settings(
        workspace_root=_from_cli_or_cfg(args.workspace, "workspace", None) or None,
        model=_from_cli_or_cfg(getattr(args, "model", None), "model", "small"),
        device=_from_cli_or_cfg(getattr(args, "device", None), "device", None),
        engine=_from_cli_or_cfg(getattr(args, "engine", None), "engine", "whisper"),
        language=_from_cli_or_cfg(getattr(args, "language", None), "language", None),
        hf_token=_from_cli_or_cfg(getattr(args, "hf_token", None), "hf_token", None),
        diarization=getattr(args, "diarization", False) or bool(file_cfg.get("diarization", False)),
        timestamps=bool(getattr(args, "timestamps", False))
        or bool(file_cfg.get("timestamps", False)),
        wechat_cookies=wechat_cookies_dict or None,
        wechat_cookies_file=getattr(args, "wechat_cookie_file", None),
    )

    # 创建 Pipeline
    pipeline = Pipeline(settings)

    # 执行命令
    try:
        if args.command in ["transcribe", "t"]:
            result = pipeline.transcribe(
                args.input,
                output=args.output,
                language=getattr(args, "language", None),
                timestamps=bool(getattr(args, "timestamps", False)),
            )
            _write_latest_pointer(settings, result)
            _print_next_steps(result)
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
                raw = asyncio.run(
                    ap.run_batch(
                        inputs,
                        language=getattr(args, "language", None),
                        timestamps=bool(getattr(args, "timestamps", False)),
                    )
                )
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
            print(f"\n{'=' * 50}")
            print("📊 批量处理完成")
            success = sum(1 for _, ok, _ in results if ok)
            print(f"✅ 成功: {success}/{len(results)}")
            for source, ok, result in results:
                status = "✅" if ok else "❌"
                print(f"{status} {source}")
            if success < len(results):
                # 有输入失败：以非零退出码暴露，供脚本/CI 判断（此前误返回 0）
                return 1
    except KeyboardInterrupt:
        print("\n\n⏹️ 用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    return 0


def _print_quickstart() -> None:
    """无参数调用时打印的快速开始引导（exit 0，不吓退新用户）。"""
    print(
        "🎬 MediaScribe — 离线视频转文字\n"
        "\n"
        "快速开始：\n"
        "  python -m mediascribe transcribe <视频链接或本地文件>\n"
        "  python -m mediascribe transcribe <URL> -e faster-whisper -m large-v3 -l zh\n"
        "  python -m mediascribe doctor        # 环境自检（首次推荐先跑）\n"
        "\n"
        "常用命令：\n"
        "  transcribe   转录单个视频/音频（URL 或本地文件）\n"
        "  batch        批量处理多个输入\n"
        "  archive      批量转录某创作者的主页视频\n"
        "  learn        ASR 术语自动学习（越用越准）\n"
        "  profile      渲染性能 profile 报告\n"
        "  doctor       环境自检（ffmpeg / GPU / 引擎 / 模型缓存）\n"
        "\n"
        "详细帮助：python -m mediascribe --help\n"
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Dispatch to the right sub-CLI.

    v3.2.0a adds the ``profile`` subcommand while preserving the v3.1.0
    ``transcribe`` / ``batch`` interface.

    v3.2.0b adds the ``learn`` subcommand for ASR auto-learning.

    v3.4.0 adds ``doctor`` (环境自检) and ``archive`` (创作者主页批量,
    收编 douyin_batch_v3.py); 无参数调用改为打印快速开始引导并返回 0.

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
    if argv and argv[0] == "doctor":
        return _run_doctor_cli(argv[1:])
    if argv and argv[0] == "archive":
        return _run_archive(argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        if not argv:
            _print_quickstart()
            return 0
        # ``-h`` / ``--help`` — let argparse print + exit (POSIX).
        return _run_legacy(["--help"])
    if argv[0] == "help":
        print(__doc__)
        return 0
    if argv[0] not in ("transcribe", "t", "batch") and not argv[0].startswith("-"):
        print(f"mediascribe: unknown command {argv[0]!r}", file=sys.stderr)
        print(
            "可用命令: transcribe / batch / archive / learn / profile / doctor\n"
            "Try 'python -m mediascribe --help'",
            file=sys.stderr,
        )
        return 1
    return _run_legacy(argv)


def _run_doctor_cli(argv: List[str]) -> int:
    """``python -m mediascribe doctor`` — 环境自检。"""
    parser = argparse.ArgumentParser(
        prog="python -m mediascribe doctor",
        description="环境自检 — ffmpeg / GPU / 转录引擎 / 模型缓存 / cookies",
    )
    parser.add_argument(
        "--workspace",
        "-w",
        type=Path,
        help="工作目录（默认: ./output 或 MEDIASCRIBE_WORKSPACE）",
    )
    args = parser.parse_args(argv)
    from .doctor import run_doctor

    return run_doctor(workspace=args.workspace)


def _run_archive(argv: List[str]) -> int:
    """``python -m mediascribe archive`` — 创作者主页批量转录。

    直接复用 douyin_batch_v3 的完整流程（浏览器抓取 / 断点续传 /
    汇总报告），统一入口。旧入口 ``python douyin_batch_v3.py ...``
    保留为兼容壳。
    """
    parser = argparse.ArgumentParser(
        prog="python -m mediascribe archive",
        description="批量转录创作者主页视频（抖音 / B站等）",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true")
    pre_args, _ = parser.parse_known_args(argv)
    if pre_args.help:
        print(__doc__)
        print(
            "\narchive 用法（与 douyin_batch_v3.py 参数一致）:\n"
            "  python -m mediascribe archive --user <作者主页URL> -n 20\n"
            "  python -m mediascribe archive --from-video <单条视频URL> -n 10\n"
            "\n"
            "完整参数: python douyin_batch_v3.py --help\n"
        )
        return 0
    try:
        # 脚本在仓库根目录，包安装场景下可能不在 sys.path
        script = Path(__file__).resolve().parent.parent / "douyin_batch_v3.py"
        import importlib.util

        spec = importlib.util.spec_from_file_location("douyin_batch_v3", script)
        module = importlib.util.module_from_spec(spec)
        if spec.loader is None:  # pragma: no cover
            raise ImportError(f"cannot load {script}")
        spec.loader.exec_module(module)
    except Exception as exc:
        print(
            f"❌ 无法加载 douyin_batch_v3.py ({exc})。\n"
            "请直接运行: python douyin_batch_v3.py --user <URL>",
            file=sys.stderr,
        )
        return 1
    return module.main(list(argv))


def _run_learn(argv: List[str]) -> int:
    """``python -m mediascribe learn`` — ASR 术语自动学习。"""
    parser = argparse.ArgumentParser(
        prog="python -m mediascribe learn",
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
        _load_db,  # type: ignore[attr-defined]
        clear_learned_terms,
        compare,
        confirm_term,
        export_terms,
        import_terms,
        learn,
        learn_from_edit,
        remove_term,
    )

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
        print(
            f"已学习 {stats['total']} 条术语 ({stats['active']} 条生效, {stats['pending']} 条待确认)"
        )
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
        print(
            f"术语库: {stats['total']} 条 ({stats['confirmed']} 已确认, {stats['pending']} 待确认, {stats['active']} 生效)"
        )
        print()
        for c in db.terms.values():
            status = "✓" if c.should_apply() else "…"
            print(f'  {status} "{c.wrong}" → "{c.right}" (×{c.count}, {c.confidence:.0%})')
            if c.contexts:
                print(f"     上下文: {c.contexts[0]}")
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
