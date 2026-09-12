"""Karpathy 式 LLM-wiki 知识库层(Phase 1 — 纯本地, 零 LLM 依赖)。

把每次转写产物归档为一个 Obsidian 兼容的 vault 目录::

    <workspace>/vault/
      HOME.md              # 索引 / MOC — 每次归档后全量重建(自愈)
      raw/                 # 转写稿原样 + YAML front-matter(文献笔记)
      wiki/                # 作者 / 平台聚合笔记(Phase 2 将引入概念笔记)

设计要点:

* **raw 不动原文** — 转写稿内容原样拷贝, 仅前置 front-matter 与一行
  归属链接(``> [[作者 - X]] · [[平台 - Y]] · [原始链接](…)``)。
* **幂等** — 同一 URL 重复归档不会产生重复文件; HOME / 聚合笔记
  每次全量重建, 天然自愈。
* **纯 Markdown + wikilink** — vault 目录用 Obsidian 直接打开即得
  双链 / 反链 / 图谱, 无需任何私有格式。
* **线程安全** — Web 端多个后台任务线程并发归档, 写路径全程持锁。
* 可用环境变量 ``MEDIASCRIBE_WIKI=0`` 整体停用(见 :func:`wiki_enabled`)。

Phase 2 规划(未实现): LLM 概念实体提取 → ``wiki/概念 - X.md`` 自动
成文与互链; ``doctor`` 扩展 wiki 健康检查(孤儿笔记 / 断链)。
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # 复用 markdown_builder 的平台中文名映射(kind → "Bilibili" 等)
    from mediascribe.markdown_builder import _PLATFORM_LABELS as _PLATFORM_LABELS_SRC
except Exception:  # pragma: no cover - 包未安装时兜底
    _PLATFORM_LABELS_SRC = {}

PLATFORM_LABELS: Dict[str, str] = dict(_PLATFORM_LABELS_SRC)

VAULT_DIRNAME = "vault"
_HOME_NOTE = "HOME.md"

# Obsidian 文件名非法字符(\ / : * ? " < > |)与 wikilink 元字符(# ^ [ ]|)
_ILLEGAL = re.compile(r'[\\/:*?"<>|#^\[\]\r\n\t]+')
_WS = re.compile(r"\s+")
_FM_URL = re.compile(r'^url:\s*"(.*)"\s*$', re.MULTILINE)
_FM_FIELD_TMPL = "^{key}:[ \\t]*(.*)$"


def wiki_enabled() -> bool:
    """环境开关 — ``MEDIASCRIBE_WIKI=0/false/no/off`` 时停用归档。"""
    return os.environ.get("MEDIASCRIBE_WIKI", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def sanitize_note_name(name: Any, maxlen: int = 80) -> str:
    """把任意标题清洗成跨平台合法、Obsidian 友好的笔记名。"""
    cleaned = _ILLEGAL.sub(" ", str(name or ""))
    cleaned = _WS.sub(" ", cleaned).strip(" .-")
    cleaned = cleaned[:maxlen].rstrip(" .-")
    return cleaned or "未命名"


def _yaml_str(value: Any) -> str:
    text = str(value if value is not None else "")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _format_duration(seconds: Any) -> Optional[str]:
    try:
        sec = float(seconds)
    except (TypeError, ValueError):
        return None
    if sec <= 0:
        return None
    return f"{int(sec // 60)}分{int(sec % 60)}秒"


def _parse_created(value: Any) -> datetime:
    if value:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            pass
    return datetime.now()


def _platform_label(kind: Any, url: str = "") -> str:
    kind = str(kind or "").strip()
    if kind in PLATFORM_LABELS:
        return PLATFORM_LABELS[kind]
    lowered = (url or "").lower()
    for host, label in (
        ("bilibili.com", "Bilibili"), ("douyin.com", "抖音"),
        ("youtube.com", "YouTube"), ("youtu.be", "YouTube"),
        ("xiaohongshu.com", "小红书"), ("mp.weixin.qq.com", "微信公众号"),
        ("tiktok.com", "TikTok"),
    ):
        if host in lowered:
            return label
    return kind or "未知平台"


def _pick(dl: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = dl.get(key)
        if value:
            return str(value)
    return None


class WikiVault:
    """一个 vault 目录的写入器。所有公开方法线程安全。"""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.wiki_dir = self.root / "wiki"
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------
    def archive_transcript(self, md_path: Path | str, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """把一篇转写稿归档进 vault, 并重建聚合笔记与 HOME。

        Returns
        -------
        dict
            ``{"raw": "raw/<name>.md", "author": "wiki/作者 - X.md"|None,
            "platform": "wiki/平台 - Y.md"|None, "title": str}``
            路径相对 vault 根目录。
        """
        md_path = Path(md_path)
        meta = metadata or {}
        dl = meta.get("download_metadata") or {}
        src = meta.get("source") or {}

        content = ""
        if md_path.exists():
            content = md_path.read_text(encoding="utf-8", errors="replace")
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = sanitize_note_name(
            _pick(dl, "title", "desc")
            or (title_match.group(1) if title_match else "")
            or md_path.stem
        )
        url = _pick(dl, "webpage_url", "url") or str(src.get("url") or "")
        author = _pick(dl, "uploader", "nickname", "author")
        platform = _platform_label(src.get("kind"), url)
        created = _parse_created(meta.get("generated_at"))
        created_str = created.strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            self.wiki_dir.mkdir(parents=True, exist_ok=True)

            raw_name = self._write_raw(
                title=title, content=content, url=url, author=author,
                platform=platform, created=created, created_str=created_str,
                meta=meta,
            )
            records = self._scan_raw()
            author_note = self._write_group_note(
                "作者", author, records,
                lambda r: r.get("author") or "",
            ) if author else None
            platform_note = self._write_group_note(
                "平台", platform, records,
                lambda r: r.get("platform") or "",
            )
            self._write_home(records)

        return {
            "raw": f"raw/{raw_name}",
            "author": f"wiki/{author_note}" if author_note else None,
            "platform": f"wiki/{platform_note}" if platform_note else None,
            "title": title,
        }

    # ------------------------------------------------------------------
    # 内部: 各笔记写入(调用方须持锁)
    # ------------------------------------------------------------------
    def _write_raw(
        self, *, title: str, content: str, url: str, author: Optional[str],
        platform: str, created: datetime, created_str: str,
        meta: Dict[str, Any],
    ) -> str:
        """写入 raw 文献笔记, 返回文件名。同 URL 幂等。"""
        base = f"{created.strftime('%Y-%m-%d')} {title}"
        candidate = f"{base}.md"
        counter = 2
        while True:
            existing = self.raw_dir / candidate
            if not existing.exists():
                break
            fm_url = self._read_field(existing, "url")
            if fm_url == url:
                return candidate  # 同 URL 已归档 — 幂等跳过
            candidate = f"{base} -{counter}.md"
            counter += 1

        duration = _format_duration((meta.get("download_metadata") or {}).get("duration"))
        fm_lines = [
            "---",
            f"title: {_yaml_str(title)}",
            f'url: {_yaml_str(url)}',
            f"platform: {_yaml_str(platform)}",
        ]
        if author:
            fm_lines.append(f"author: {_yaml_str(author)}")
        if duration:
            fm_lines.append(f"duration: {_yaml_str(duration)}")
        engine = meta.get("engine")
        if engine:
            fm_lines.append(f"engine: {_yaml_str(engine)}")
        model = meta.get("model")
        if model:
            fm_lines.append(f"model: {_yaml_str(model)}")
        language = meta.get("language")
        if language:
            fm_lines.append(f"language: {_yaml_str(language)}")
        fm_lines.append(f"created: {created_str}")
        fm_lines.append("---")

        attribution = " · ".join(
            part for part in (
                f"[[作者 - {author}]]" if author else None,
                f"[[平台 - {platform}]]",
                f"[原始链接]({url})" if url else None,
            ) if part
        )
        body = "\n".join([*fm_lines, "", f"> {attribution}", "", content.rstrip(), ""])
        (self.raw_dir / candidate).write_text(body, encoding="utf-8")
        return candidate

    def _scan_raw(self) -> List[Dict[str, str]]:
        """扫描 raw/ 全部笔记的 front-matter, 按 created 倒序。"""
        records: List[Dict[str, str]] = []
        for path in self.raw_dir.glob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if not text.startswith("---"):
                continue
            end = text.find("\n---", 3)
            if end < 0:
                continue
            block = text[4:end]
            record = {
                "name": path.stem,
                "title": self._fm_get(block, "title") or path.stem,
                "url": self._fm_get(block, "url"),
                "platform": self._fm_get(block, "platform") or "未知平台",
                "author": self._fm_get(block, "author"),
                "duration": self._fm_get(block, "duration"),
                "created": self._fm_get(block, "created"),
            }
            records.append(record)
        records.sort(key=lambda r: r.get("created") or "", reverse=True)
        return records

    @staticmethod
    def _fm_get(block: str, key: str) -> Optional[str]:
        match = re.search(_FM_FIELD_TMPL.format(key=re.escape(key)), block, re.MULTILINE)
        if not match:
            return None
        value = match.group(1).strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return value or None

    def _write_group_note(
        self, kind: str, value: str, records: List[Dict[str, str]],
        key_of,  # callable: record -> str
    ) -> str:
        """重建 ``wiki/{kind} - {value}.md`` 聚合笔记, 返回文件名。"""
        mine = [r for r in records if key_of(r) == value]
        note_name = f"{kind} - {sanitize_note_name(value)}.md"
        lines = [
            "---",
            f"type: {kind}",
            f"name: {_yaml_str(value)}",
            f"count: {len(mine)}",
            "---",
            "",
            f"# {kind}:{value}",
            "",
            f"共 {len(mine)} 篇转写。",
            "",
        ]
        for r in mine:
            meta_bits = " · ".join(
                part for part in (r.get("platform"), r.get("created", "")[:10]) if part
            )
            lines.append(f"- [[{r['name']}]] — {meta_bits}")
        (self.wiki_dir / note_name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return note_name

    def _write_home(self, records: List[Dict[str, str]]) -> None:
        """全量重建 HOME.md 索引(MOC)。"""
        authors: Dict[str, int] = {}
        platforms: Dict[str, int] = {}
        for r in records:
            if r.get("author"):
                authors[r["author"]] = authors.get(r["author"], 0) + 1
            platforms[r["platform"]] = platforms.get(r["platform"], 0) + 1

        lines = [
            "---",
            "type: home",
            f"count: {len(records)}",
            "---",
            "",
            "# MediaScribe 知识库",
            "",
            f"共 {len(records)} 篇转写 · {len(authors)} 位作者 · {len(platforms)} 个平台",
            "",
            "## 最近转写",
            "",
        ]
        for r in records[:20]:
            bits = " · ".join(
                part for part in (
                    r.get("platform"), (r.get("author") or None),
                    (r.get("created") or "")[:10],
                ) if part
            )
            lines.append(f"- [[{r['name']}]] — {bits}")
        lines.append("")
        lines.append("## 作者")
        lines.append("")
        for name, count in sorted(authors.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- [[作者 - {sanitize_note_name(name)}]] ({count})")
        lines.append("")
        lines.append("## 平台")
        lines.append("")
        for name, count in sorted(platforms.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- [[平台 - {sanitize_note_name(name)}]] ({count})")
        (self.root / _HOME_NOTE).write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 兼容旧引用
    def _read_field(self, path: Path, key: str) -> Optional[str]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - defensive
            return None
        if not text.startswith("---"):
            return None
        end = text.find("\n---", 3)
        if end < 0:
            return None
        return self._fm_get(text[4:end], key)


def vault_for_workspace(workspace: Path | str) -> Optional["WikiVault"]:
    """按环境开关构造 vault; 停用时返回 None。"""
    if not wiki_enabled():
        return None
    return WikiVault(Path(workspace) / VAULT_DIRNAME)
