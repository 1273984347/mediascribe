"""
将已有的 .txt 转录文件转换为 .md 格式（完整内容）
"""
import re
import sys
from datetime import datetime
from pathlib import Path

# 设置路径
sys.path.insert(0, str(Path(__file__).parent))

transcripts_dir = Path(__file__).parent / "output" / "transcripts"

# 查找所有 .txt 文件
txt_files = list(transcripts_dir.glob("*.txt"))
print(f"📁 找到 {len(txt_files)} 个 .txt 文件")
print()

def build_markdown(title, text, metadata_path=None):
    """构建完整的 Markdown 内容"""
    lines = []

    # 标题
    lines.append(f"# {title}")
    lines.append("")

    # 元数据信息
    lines.append("## 基本信息")
    lines.append("")

    # 尝试从元数据文件读取
    meta = None
    if metadata_path and metadata_path.exists():
        import json
        try:
            meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass

    if meta:
        if meta.get("download_metadata", {}).get("uploader"):
            lines.append(f"- **作者**: {meta['download_metadata']['uploader']}")
        if meta.get("download_metadata", {}).get("duration"):
            duration = meta['download_metadata']['duration']
            duration_min = int(duration // 60)
            duration_sec = int(duration % 60)
            lines.append(f"- **时长**: {duration_min}分{duration_sec}秒")
        lines.append(f"- **生成时间**: {meta.get('generated_at', 'unknown')}")
        lines.append(f"- **转录引擎**: {meta.get('engine', 'unknown')}")
        lines.append(f"- **识别语言**: {meta.get('language', 'unknown')}")
    else:
        lines.append(f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    lines.append("")

    # 转录内容
    lines.append("## 转录内容")
    lines.append("")

    # 将文本分割成段落
    paragraphs = split_into_paragraphs(text)
    for paragraph in paragraphs:
        if paragraph.strip():
            lines.append(paragraph)
            lines.append("")

    return "\n".join(lines)


def split_into_paragraphs(text):
    """将文本分割成段落"""
    # 按中文和英文标点分割
    text = re.sub(r'([。！？!?])', r'\1\n', text)
    sentences = [s.strip() for s in text.split('\n') if s.strip()]

    # 组合成段落（每段 2-4 句）
    paragraphs = []
    current = []
    for s in sentences:
        current.append(s)
        if len(current) >= 3:
            paragraphs.append(''.join(current))
            current = []

    if current:
        paragraphs.append(''.join(current))

    return paragraphs


# 处理每个 .txt 文件
for txt_file in txt_files:
    print(f"🔄 处理: {txt_file.name}")

    # 读取完整内容
    text = txt_file.read_text(encoding="utf-8").strip()

    # 提取标题（去掉时间戳后缀）
    title = txt_file.stem
    # 去掉 -YYYYMMDD-HHMMSS 后缀
    title = re.sub(r'-\d{8}-\d{6}$', '', title)

    # 查找对应的元数据文件
    metadata_dir = transcripts_dir.parent / "metadata"
    metadata_path = metadata_dir / f"{txt_file.stem}.json"

    # 构建 Markdown
    md_content = build_markdown(title, text, metadata_path)

    # 写入 .md 文件
    md_file = txt_file.with_suffix(".md")
    md_file.write_text(md_content, encoding="utf-8")

    # 统计
    chars = len(text)
    print(f"   ✅ 已生成: {md_file.name}")
    print(f"   📊 字符数: {chars}")
    print()

print("🎊 所有 .txt 文件已转换为 .md 格式！")
