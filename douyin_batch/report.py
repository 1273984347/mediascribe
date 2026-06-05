"""
报告生成模块 - 生成汇总报告
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List


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
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_file = output_dir / f"作者往期内容汇总_{timestamp}.md"

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

    # 添加每个视频的转录内容
    content_parts.extend([
        "",
        "## 📝 完整转录内容",
        "",
    ])

    for i, r in enumerate(success_results, 1):
        transcript = r.get("transcript")
        if not transcript:
            continue
        transcript_path = Path(transcript)
        if not transcript_path.exists():
            continue

        video_url = r.get("url") or f"https://www.douyin.com/video/{r['video_id']}"
        content_parts.extend([
            f"### {i}. 视频 {r['video_id']}",
            "",
            f"**链接**: {video_url}",
            "",
        ])

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
            content_parts.extend([
                "**转录内容**:",
                "",
                transcript_text,
                "",
            ])

        content_parts.append("---")
        content_parts.append("")

    # 写入文件
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("\n".join(content_parts))

    # 同时保存 JSON 格式结果
    json_file = output_dir / f"处理结果_{timestamp}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({
            "user_url": user_url,
            "total": len(results),
            "success": len(success_results),
            "failed": len(failed_results),
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    return summary_file
