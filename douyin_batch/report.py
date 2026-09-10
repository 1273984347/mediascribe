"""
报告生成模块 - 生成汇总报告

v3.4.0 升级：
- 新增「目录」区（锚点直达每个视频小节）
- 新增「失败与重试」区（逐条列出失败阶段 + 重试指引）
- 时间戳归档之外，额外维护固定名 ``summary.md`` / ``results.json``
  （最新一份，方便脚本与 AI Agent 直接读取，不用 glob 时间戳）
- ``results.json`` 增加 ``schema_version`` / ``generated_at`` 字段
- 落盘 ``LATEST.txt`` 指针，内容为最新汇总报告路径
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List


def _anchor(i: int, video_id: str) -> str:
    """与 ``### {i}. 视频 {video_id}`` 标题对应的 GitHub 风格锚点。"""
    return f"#{i}-视频-{video_id}"


def generate_summary_report(
    user_url: str,
    results: List[Dict],
    output_dir: Path,
) -> Path:
    """
    生成汇总报告（Markdown格式 + 全部转录内容合并）

    Returns:
        汇总报告文件路径
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 读取每个成功转录的内容
    success_results = [r for r in results if r.get("status") == "success"]
    failed_results = [r for r in results if r.get("status") != "success"]

    # 生成汇总Markdown
    # v3.2.0g (P2-15): 文件名改 ASCII 固定格式，避免中文文件名在
    # 跨平台 / --json 输出中的编码与转义问题。
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_file = output_dir / f"summary_{timestamp}.md"

    content_parts = [
        "# 抖音作者往期内容汇总",
        "",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**作者主页**: {user_url}",
        f"**总视频数**: {len(results)}",
        f"**成功转录**: {len(success_results)}",
        f"**失败**: {len(failed_results)}",
        "",
        "## 📊 视频列表",
        "",
    ]

    for i, r in enumerate(results, 1):
        status_icon = "✅" if r.get("status") == "success" else "❌"
        video_url = r.get("url") or f"https://www.douyin.com/video/{r['video_id']}"
        content_parts.append(f"{i}. {status_icon} `{r['video_id']}` - [{video_url}]({video_url})")
        if r.get("status") != "success":
            content_parts.append(f"   - 失败阶段: {r.get('stage', 'unknown')}")
        else:
            if r.get("transcript"):
                content_parts.append(f"   - 转录文件: `{Path(r['transcript']).name}`")

    # v3.4.0: 目录区 — 锚点直达下方每个视频小节
    if success_results:
        content_parts.extend(["", "## 📑 目录", ""])
        for i, r in enumerate(success_results, 1):
            video_id = r.get("video_id", "")
            video_url = r.get("url") or f"https://www.douyin.com/video/{video_id}"
            content_parts.append(
                f"{i}. [视频 {video_id}]({_anchor(i, video_id)}) — [原文]({video_url})"
            )

    # 失败与重试区
    if failed_results:
        content_parts.extend(
            [
                "",
                "## ❌ 失败与重试",
                "",
            ]
        )
        for r in failed_results:
            video_url = r.get("url") or f"https://www.douyin.com/video/{r['video_id']}"
            stage = r.get("stage", "unknown")
            error = r.get("error")
            line = f"- `{r.get('video_id', '?')}` — 失败阶段: {stage} — [链接]({video_url})"
            content_parts.append(line)
            if error:
                content_parts.append(f"  - 错误: {error}")
        content_parts.extend(
            [
                "",
                "**重试方法**: 原样重跑同一条批量命令即可 — 断点续传缓存会"
                "自动跳过已成功的视频，只重试失败项。例如：",
                "",
                "```bash",
                "python douyin_batch_v3.py --user <作者主页URL> -n <数量>",
                "```",
            ]
        )

    # 添加每个视频的转录内容
    content_parts.extend(
        [
            "",
            "## 📝 完整转录内容",
            "",
        ]
    )

    for i, r in enumerate(success_results, 1):
        transcript = r.get("transcript")
        if not transcript:
            continue
        transcript_path = Path(transcript)
        if not transcript_path.exists():
            continue

        video_url = r.get("url") or f"https://www.douyin.com/video/{r['video_id']}"
        content_parts.extend(
            [
                f"### {i}. 视频 {r['video_id']}",
                "",
                f"**链接**: {video_url}",
                "",
            ]
        )
        if transcript_path.exists():
            content_parts.append(f"**转录文件**: `{transcript_path.name}`")
            content_parts.append("")

        # 读取转录内容
        with open(transcript_path, "r", encoding="utf-8") as f:
            md_content = f.read()

        # 提取转录正文部分（跳过元数据）
        lines = md_content.split("\n")
        in_content = False
        transcript_lines = []
        for line in lines:
            if "## 转录内容" in line:
                in_content = True
                continue
            if in_content:
                transcript_lines.append(line)

        transcript_text = "\n".join(transcript_lines).strip()
        if transcript_text:
            content_parts.extend(
                [
                    "**转录内容**:",
                    "",
                    transcript_text,
                    "",
                ]
            )

        content_parts.append("---")
        content_parts.append("")

    # 写入文件
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("\n".join(content_parts))

    # 同时保存 JSON 格式结果（ASCII 固定格式文件名，P2-15）
    json_file = output_dir / f"results_{timestamp}.json"
    payload = {
        "schema_version": 2,
        "generated_at": datetime.now().isoformat(),
        "user_url": user_url,
        "total": len(results),
        "success": len(success_results),
        "failed": len(failed_results),
        "results": results,
    }
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # v3.4.0: 固定名「最新一份」副本 — 脚本/Agent 不必 glob 时间戳文件
    _copy_latest(summary_file, output_dir / "summary.md")
    _copy_latest(json_file, output_dir / "results.json")

    # LATEST 指针：内容为最新汇总报告绝对路径
    try:
        latest_pointer = output_dir / "LATEST.txt"
        latest_pointer.write_text(str(summary_file.resolve()), encoding="utf-8")
    except Exception:
        pass

    return summary_file


def _copy_latest(src: Path, dst: Path) -> None:
    """把本次报告复制为固定名「最新」副本；失败不阻塞主流程。"""
    try:
        dst.write_bytes(src.read_bytes())
    except Exception:
        pass
