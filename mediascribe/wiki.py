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

Phase 2 — 概念笔记: 配置了 ``MEDIASCRIBE_LLM_*`` 后,
:class:`ConceptExtractor` 从每篇转写稿提取概念实体及 ``**[mm:ss]**``
时间戳引用, 自动生成 ``wiki/概念 - X.md`` 并在 raw 笔记头部互链。
提及记录存在 vault 内部账本 ``.mediascribe/index.json``
(机器索引; 人读层仍然只有纯 Markdown)。未配置 LLM 时自动降级,
不产生概念笔记, 其余功能不受影响。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

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
        "0",
        "false",
        "no",
        "off",
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
        ("bilibili.com", "Bilibili"),
        ("douyin.com", "抖音"),
        ("youtube.com", "YouTube"),
        ("youtu.be", "YouTube"),
        ("xiaohongshu.com", "小红书"),
        ("mp.weixin.qq.com", "微信公众号"),
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


# ---------------------------------------------------------------------------
# Phase 2 — LLM 概念提取(可选; 未配置 LLM 时优雅降级为无概念笔记)
# ---------------------------------------------------------------------------
class ConceptExtractor:
    """调用 OpenAI 兼容 LLM, 从转写稿提取概念实体及其时间戳引用。

    不直接绑定具体 SDK — 构造时注入 ``call_fn(prompt) -> str``;
    :meth:`from_env` 复用 :class:`LLMPostProcessor` 的通道装配。
    任何解析失败都返回空列表, 绝不让归档失败。
    """

    PROMPT = (
        "你是个人知识库的编辑。下面是一段视频转写稿, 请提取 3-8 个"
        "值得建笔记的概念/主题/实体(具体物件、技术名词、品牌、人物、方法, "
        '避免过于宽泛的词如"视频""分享")。\n'
        "只输出 JSON 数组, 不要任何其他文字, 格式:\n"
        '[{{"name": "概念名", "mentions": [{{"ts": "mm:ss", "quote": "不超过40字的原文关键句"}}]}}]\n'
        "ts 必须取自文中出现的时间戳。\n\n"
        "标题: {title}\n\n转写稿:\n{text}"
    )

    MAX_CONCEPTS = 8

    def __init__(self, call_fn: Callable[[str], str]):
        self._call = call_fn

    @classmethod
    def from_env(cls) -> Optional["ConceptExtractor"]:
        """未配置 LLM(无 api_key 或未显式启用)时返回 ``None``。"""
        try:
            from mediascribe.llm_post_process import LLMPostProcessor
        except ImportError:  # pragma: no cover - 可选依赖
            return None
        proc = LLMPostProcessor.from_env()
        if not proc.enabled or not proc.api_key:
            return None
        return cls(call_fn=lambda prompt: proc._call_api(prompt))

    def extract(self, title: str, text: str, max_chars: int = 6000) -> List[Dict[str, Any]]:
        """返回 ``[{"name": str, "mentions": [{"ts": str, "quote": str}]}]``。"""
        prompt = self.PROMPT.format(title=title, text=text[:max_chars])
        try:
            raw = self._call(prompt).strip()
            raw = re.sub(r"^```(?:json)?[ \t]*\n?|\n?```$", "", raw, flags=re.MULTILINE)
            data = json.loads(raw)
        except Exception as exc:
            logger.warning("wiki 概念提取失败(忽略): %r", exc)
            return []
        if not isinstance(data, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in data[: self.MAX_CONCEPTS]:
            if not isinstance(item, dict):
                continue
            name = sanitize_note_name(item.get("name"), maxlen=40)
            if not name:
                continue
            mentions = []
            for m in item.get("mentions") or []:
                if not isinstance(m, dict):
                    continue
                ts = str(m.get("ts") or "").strip()
                quote = str(m.get("quote") or "").strip()
                if ts or quote:
                    mentions.append({"ts": ts, "quote": quote[:80]})
            out.append({"name": name, "mentions": mentions})
        return out


class WikiVault:
    """一个 vault 目录的写入器。所有公开方法线程安全。"""

    LEDGER_DIRNAME = ".mediascribe"
    LEDGER_NAME = "index.json"

    def __init__(self, root: Path | str, extractor: Optional[ConceptExtractor] = None):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.wiki_dir = self.root / "wiki"
        self.ledger_dir = self.root / self.LEDGER_DIRNAME
        self.extractor = extractor
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------
    def archive_transcript(
        self,
        md_path: Path | str,
        metadata: Optional[Dict[str, Any]],
        extractor: Optional[ConceptExtractor] = None,
    ) -> Dict[str, Any]:
        """把一篇转写稿归档进 vault, 并重建聚合笔记与 HOME。

        ``extractor`` 缺省用构造时注入的 :attr:`extractor`;
        两者皆无或 LLM 未配置时不做概念提取。

        Returns
        -------
        dict
            ``{"raw": "raw/<name>.md", "author": ..., "platform": ...,
            "concepts": ["概念 - X", ...], "title": str}``
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

        active_extractor = extractor or self.extractor
        concepts: List[Dict[str, Any]] = []
        if active_extractor is not None and content:
            try:
                concepts = active_extractor.extract(title, content)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("wiki 概念提取异常(忽略): %r", exc)

        with self._lock:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            self.wiki_dir.mkdir(parents=True, exist_ok=True)
            self.ledger_dir.mkdir(parents=True, exist_ok=True)

            raw_name = self._write_raw(
                title=title,
                content=content,
                url=url,
                author=author,
                platform=platform,
                created=created,
                created_str=created_str,
                meta=meta,
                concepts=concepts,
            )
            if concepts:
                self._update_ledger(raw_name=raw_name, title=title, concepts=concepts)
            records = self._scan_raw()
            author_note = (
                self._write_group_note(
                    "作者",
                    author,
                    records,
                    lambda r: r.get("author") or "",
                )
                if author
                else None
            )
            platform_note = self._write_group_note(
                "平台",
                platform,
                records,
                lambda r: r.get("platform") or "",
            )
            concept_notes = self._write_concept_notes()
            self._write_home(records, concepts=concept_notes)

        return {
            "raw": f"raw/{raw_name}",
            "author": f"wiki/{author_note}" if author_note else None,
            "platform": f"wiki/{platform_note}" if platform_note else None,
            "concepts": [f"wiki/{n}" for n in concept_notes],
            "title": title,
        }

    # ------------------------------------------------------------------
    # 内部: 各笔记写入(调用方须持锁)
    # ------------------------------------------------------------------
    def _write_raw(
        self,
        *,
        title: str,
        content: str,
        url: str,
        author: Optional[str],
        platform: str,
        created: datetime,
        created_str: str,
        meta: Dict[str, Any],
        concepts: Optional[List[Dict[str, Any]]] = None,
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
            f"url: {_yaml_str(url)}",
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
        if concepts:
            names = [_yaml_str(c["name"]) for c in concepts]
            fm_lines.append(f"concepts: [{', '.join(names)}]")
        fm_lines.append(f"created: {created_str}")
        fm_lines.append("---")

        attribution = " · ".join(
            part
            for part in (
                f"[[作者 - {author}]]" if author else None,
                f"[[平台 - {platform}]]",
                f"[原始链接]({url})" if url else None,
            )
            if part
        )
        body_lines = [*fm_lines, "", f"> {attribution}"]
        if concepts:
            concept_links = " · ".join(f"[[概念 - {c['name']}]]" for c in concepts)
            body_lines.append(f"> 概念:{concept_links}")
        body_lines.extend(["", content.rstrip(), ""])
        (self.raw_dir / candidate).write_text("\n".join(body_lines), encoding="utf-8")
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
        self,
        kind: str,
        value: str,
        records: List[Dict[str, str]],
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

    def _write_concept_notes(self) -> List[str]:
        """依据账本重建全部 ``wiki/概念 - X.md``, 返回文件名列表。

        调用方须持锁(:meth:`archive_transcript` / :meth:`index`)。
        """
        ledger_path = self.ledger_dir / self.LEDGER_NAME
        if not ledger_path.exists():
            return []
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("wiki 账本损坏, 跳过概念笔记重建")
            return []
        concepts = ledger.get("concepts") or {}
        note_names: List[str] = []
        for name, mentions in concepts.items():
            safe = sanitize_note_name(name, maxlen=40)
            note_name = f"概念 - {safe}.md"
            # 按来源笔记分组, 每条带时间戳引用
            by_raw: Dict[str, List[Dict[str, str]]] = {}
            for m in mentions:
                raw = m.get("raw", "")
                raw_stem = raw[: -len(".md")] if raw.endswith(".md") else raw
                by_raw.setdefault(raw_stem, []).append(m)
            lines = [
                "---",
                "type: 概念",
                f"name: {_yaml_str(name)}",
                f"count: {len(mentions)}",
                "---",
                "",
                f"# 概念:{name}",
                "",
                f"在 {len(by_raw)} 篇转写中出现 {len(mentions)} 次。",
                "",
            ]
            for raw_stem, ms in by_raw.items():
                lines.append(f"## [[{raw_stem}]]")
                lines.append("")
                for m in ms:
                    ts = m.get("ts") or ""
                    quote = m.get("quote") or ""
                    stamp = f" **[{ts}]**" if ts else ""
                    if quote:
                        lines.append(f"-{stamp} {quote}")
                lines.append("")
            (self.wiki_dir / note_name).write_text("\n".join(lines) + "\n", encoding="utf-8")
            note_names.append(note_name)
        return sorted(note_names)

    def _update_ledger(
        self,
        *,
        raw_name: str,
        title: str,
        concepts: List[Dict[str, Any]],
    ) -> None:
        """把本次提取的概念提及合并进 ``.mediascribe/index.json``。

        调用方须持锁。按 ``(raw, ts, quote)`` 去重。
        """
        ledger_path = self.ledger_dir / self.LEDGER_NAME
        ledger: Dict[str, Any] = {"concepts": {}}
        if ledger_path.exists():
            try:
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                ledger = {"concepts": {}}
        bucket = ledger.setdefault("concepts", {})
        for c in concepts:
            entries = bucket.setdefault(c["name"], [])
            seen = {(e.get("raw"), e.get("ts"), e.get("quote")) for e in entries}
            for m in c.get("mentions") or []:
                key = (raw_name, m.get("ts"), m.get("quote"))
                if key in seen:
                    continue
                seen.add(key)
                entries.append(
                    {
                        "raw": raw_name,
                        "title": title,
                        "ts": m.get("ts", ""),
                        "quote": m.get("quote", ""),
                    }
                )
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")

    def index(self) -> Dict[str, Any]:
        """知识库视图数据: 笔记清单 + HOME 内容(供 Web API 使用)。"""
        with self._lock:
            records = self._scan_raw()
            notes: List[Dict[str, str]] = []
            for sub, ntype in (("raw", "raw"), ("wiki", "wiki")):
                for p in sorted((self.root / sub).glob("*.md")):
                    notes.append({"name": p.stem, "path": f"{sub}/{p.name}", "type": ntype})
            home_path = self.root / _HOME_NOTE
            home = home_path.read_text(encoding="utf-8") if home_path.exists() else ""
            return {
                "notes": notes,
                "home": home,
                "raw_count": len(records),
                "concepts": sorted(p.stem for p in self.wiki_dir.glob("概念 - *.md")),
            }

    def _write_home(
        self, records: List[Dict[str, str]], concepts: Optional[List[str]] = None
    ) -> None:
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
                part
                for part in (
                    r.get("platform"),
                    (r.get("author") or None),
                    (r.get("created") or "")[:10],
                )
                if part
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
        if concepts:
            lines.append("")
            lines.append("## 概念")
            lines.append("")
            for note_name in concepts:
                display = note_name[: -len(".md")] if note_name.endswith(".md") else note_name
                lines.append(f"- [[{display}]]")
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


def vault_for_workspace(
    workspace: Path | str, extractor: Optional[ConceptExtractor] = None
) -> Optional["WikiVault"]:
    """按环境开关构造 vault; 停用时返回 ``None``。"""
    if not wiki_enabled():
        return None
    return WikiVault(Path(workspace) / VAULT_DIRNAME, extractor=extractor)
