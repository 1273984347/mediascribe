"""
ASR 自动学习模块 v2 (v3.2.0b)

从错误中累积术语映射,越用越准。

核心改进 (v1 → v2)
------------------
1. **置信度计数器** — 同一错误出现 N 次自动确认,减少误替换
2. **拼音相似度过滤** — 只保留语音相似的替换,过滤语义差异
3. **learn_from_edit** — diff 用户编辑前后文本,比 compare 更实用
4. **上下文记录** — 存 5 条上下文示例,方便审查
5. **Prompt 注入** — 已确认术语以自然语言注入 Whisper prompt
6. **导出/导入** — 跨机器迁移术语库

术语库路径
----------
存储在持久缓存目录下 (遵循 ``$XDG_CACHE_HOME`` / ``%LOCALAPPDATA%``),
而非源码目录,避免 pip install 后丢失。

``post_process_transcript()`` 会自动加载**已确认**的学习术语,
无需手动调用。

用法
----
.. code-block:: python

    # 方式 1: 对比参考文本与转录
    from mediascribe.learn import compare, learn
    corrections = compare("霍去病是名将", "获取病是名将")
    learn(corrections)

    # 方式 2: 从用户编辑中学习 (推荐)
    from mediascribe.learn import learn_from_edit
    learn_from_edit(original_transcript, user_corrected_transcript)

    # 下次转录自动应用已确认术语
    from mediascribe.post_process import post_process_transcript
    fixed = post_process_transcript(raw_text)
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .cache import persistent_cache_dir

__all__ = [
    "compare",
    "learn",
    "learn_from_edit",
    "get_learned_terms",
    "get_prompt_terms",
    "confirm_term",
    "remove_term",
    "export_terms",
    "import_terms",
    "clear_learned_terms",
    "learned_terms_path",
    "Correction",
    "LearnedTermsDB",
]

# 置信度阈值: 同一错误出现 N 次后自动确认
AUTO_CONFIRM_THRESHOLD = 2


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Correction:
    """单条校正记录。"""

    wrong: str  # ASR 错误文本
    right: str  # 正确文本
    count: int = 1  # 出现次数
    confirmed: bool = False  # 是否已确认
    contexts: list[str] = field(default_factory=list)  # 上下文示例 (最多 5 条)
    source: str = ""  # 来源 (视频文件名等)

    @property
    def confidence(self) -> float:
        """置信度 0-1。"""
        if self.confirmed:
            return 1.0
        return min(self.count / AUTO_CONFIRM_THRESHOLD, 0.99)

    def should_apply(self) -> bool:
        """是否应该应用此校正 (已确认或达到阈值)。"""
        return self.confirmed or self.count >= AUTO_CONFIRM_THRESHOLD


@dataclass
class LearnedTermsDB:
    """术语库。"""

    terms: dict[str, Correction] = field(default_factory=dict)
    version: int = 2

    def _key(self, wrong: str, right: str) -> str:
        """内部 key: 用 NUL 分隔避免冲突。"""
        return f"{wrong}\x00{right}"

    def add(
        self,
        wrong: str,
        right: str,
        context: str = "",
        source: str = "",
    ) -> Correction:
        """添加或更新一条校正记录。"""
        key = self._key(wrong, right)
        if key in self.terms:
            c = self.terms[key]
            c.count += 1
            if context and len(c.contexts) < 5:
                c.contexts.append(context)
            if c.count >= AUTO_CONFIRM_THRESHOLD:
                c.confirmed = True
        else:
            c = Correction(
                wrong=wrong,
                right=right,
                count=1,
                confirmed=False,
                contexts=[context] if context else [],
                source=source,
            )
            self.terms[key] = c
        return self.terms[key]

    def confirm(self, wrong: str, right: str) -> bool:
        """手动确认一条校正。"""
        key = self._key(wrong, right)
        if key in self.terms:
            self.terms[key].confirmed = True
            return True
        return False

    def remove(self, wrong: str, right: str) -> bool:
        """删除一条校正。"""
        key = self._key(wrong, right)
        if key in self.terms:
            del self.terms[key]
            return True
        return False

    def get_active_terms(self) -> dict[str, str]:
        """获取应生效的术语映射 (已确认 + 达到阈值)。"""
        return {c.wrong: c.right for c in self.terms.values() if c.should_apply()}

    def get_unconfirmed(self) -> list[Correction]:
        """获取待确认的校正列表。"""
        return [c for c in self.terms.values() if not c.confirmed]

    def stats(self) -> dict[str, int]:
        """统计信息。"""
        return {
            "total": len(self.terms),
            "confirmed": sum(1 for c in self.terms.values() if c.confirmed),
            "pending": sum(1 for c in self.terms.values() if not c.confirmed),
            "active": len(self.get_active_terms()),
        }


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------
def learned_terms_path() -> Path:
    """返回学习术语库的文件路径。

    遵循缓存目录约定 (XDG / LOCALAPPDATA / MEDIASCRIBE_CACHE_DIR)。
    """
    return persistent_cache_dir() / "learned_terms.json"


def _load_db(path: Optional[Path] = None) -> LearnedTermsDB:
    """从文件加载术语库。"""
    store = path or learned_terms_path()
    if not store.exists():
        return LearnedTermsDB()
    try:
        raw = json.loads(store.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return LearnedTermsDB()

    db = LearnedTermsDB()
    for val in raw.get("terms", {}).values():
        try:
            c = Correction(**val)
            db.terms[db._key(c.wrong, c.right)] = c
        except (TypeError, KeyError):
            continue
    return db


def _save_db(db: LearnedTermsDB, path: Optional[Path] = None) -> None:
    """保存术语库到文件 (原子写入)。

    v3.2.0e+: tmp 名含 PID + UUID8 避免并发碰撞，写失败时清理 tmp
    （原 ``.tmp`` 名固定，多进程同时写会互相覆盖）。"""
    import os
    import uuid

    store = path or learned_terms_path()
    data = {
        "version": db.version,
        "terms": {key: asdict(c) for key, c in db.terms.items()},
    }
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(store)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# 拼音相似度过滤
# ---------------------------------------------------------------------------
def _is_phonetically_similar(wrong: str, right: str) -> bool:
    """两个词的拼音是否相似 (ASR 典型错误模式)。

    只保留语音相似的替换 (如 "获取病" vs "霍去病"),
    过滤掉语义差异 (如 "鲁迅" vs "周树人")。
    """
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        # pypinyin 不可用时不过滤,全部保留
        return True

    pinyin_w = "".join(lazy_pinyin(wrong))
    pinyin_r = "".join(lazy_pinyin(right))
    if not pinyin_w or not pinyin_r:
        return False

    dist = _edit_distance(pinyin_w, pinyin_r)
    max_len = max(len(pinyin_w), len(pinyin_r))
    # 编辑距离 / 最大长度 < 0.6 → 语音相似
    return dist / max_len < 0.6


def _edit_distance(s1: str, s2: str) -> int:
    """Levenshtein 编辑距离。"""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    if not s2:
        return len(s1)

    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(prev[j] if c1 == c2 else 1 + min(prev[j], prev[j + 1], curr[j]))
        prev = curr
    return prev[-1]


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------
def compare(
    reference: str,
    transcript: str,
    source: str = "",
) -> list[tuple[str, str, str]]:
    """对比正确文本与 ASR 转录,提取错误映射。

    使用 difflib 序列匹配 + 拼音相似度过滤,
    只保留语音相似的替换 (ASR 典型错误模式)。

    Parameters
    ----------
    reference
        正确文本 (人工校对后的版本)。
    transcript
        ASR 转录文本 (原始机器输出)。
    source
        来源标识 (视频文件名等),存入 Correction.source。

    Returns
    -------
    ``[(wrong, right, context), ...]`` 列表。
    """
    ref_tokens = _tokenize(reference)
    trans_tokens = _tokenize(transcript)

    matcher = difflib.SequenceMatcher(None, ref_tokens, trans_tokens)

    corrections: list[tuple[str, str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            wrong = "".join(trans_tokens[j1:j2])
            right = "".join(ref_tokens[i1:i2])
            if wrong and right and wrong != right:
                # 只保留 2-6 字的替换,过滤 n-gram 拼接产生的长串
                if 2 <= len(wrong) <= 6 and 2 <= len(right) <= 6:
                    # 拼音相似度过滤
                    if _is_phonetically_similar(wrong, right):
                        context = right[:30]
                        corrections.append((wrong, right, context))
    return corrections


def learn_from_edit(
    original: str,
    corrected: str,
    source: str = "",
    path: Optional[Path] = None,
) -> LearnedTermsDB:
    """从用户编辑前后文本中学习 (推荐入口)。

    用户在转录结果上做了修改,系统 diff 前后差异,
    自动提取 ASR 错误映射。这比 :func:`compare` 更实用,
    因为用户只会改真正错的地方,噪音更低。

    Parameters
    ----------
    original
        ASR 原始转录文本。
    corrected
        用户校对后的文本。
    source
        来源标识。
    path
        自定义术语库路径 (测试用)。

    Returns
    -------
    更新后的术语库。
    """
    # diff 编辑前后文本
    ref_tokens = _tokenize(corrected)
    trans_tokens = _tokenize(original)

    matcher = difflib.SequenceMatcher(None, ref_tokens, trans_tokens)

    db = _load_db(path)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            wrong = "".join(trans_tokens[j1:j2])
            right = "".join(ref_tokens[i1:i2])
            if wrong and right and wrong != right:
                # 只保留 2-6 字的替换,过滤 n-gram 拼接产生的长串
                if 2 <= len(wrong) <= 6 and 2 <= len(right) <= 6:
                    if _is_phonetically_similar(wrong, right):
                        context = right[:30]
                        db.add(wrong, right, context=context, source=source)

    _save_db(db, path)
    return db


def learn(
    correction_pairs: list[tuple[str, str] | tuple[str, str, str]],
    *,
    source: str = "",
    path: Optional[Path] = None,
) -> LearnedTermsDB:
    """将错误-正确映射累积到术语库。

    Parameters
    ----------
    correction_pairs
        ``(wrong, right)`` 或 ``(wrong, right, context)`` 列表。
    source
        来源标识。
    path
        自定义术语库路径 (测试用)。

    Returns
    -------
    更新后的术语库。
    """
    db = _load_db(path)
    for pair in correction_pairs:
        if len(pair) >= 3:
            wrong, right, context = pair[0], pair[1], pair[2]
        else:
            wrong, right = pair[0], pair[1]
            context = ""
        if wrong and right and wrong != right:
            db.add(wrong, right, context=context, source=source)
    _save_db(db, path)
    return db


def get_learned_terms(path: Optional[Path] = None) -> dict[str, str]:
    """获取应生效的术语映射 (已确认 + 达到阈值)。

    Returns
    -------
    ``{wrong: right}`` 字典,可直接用于 ``str.replace``。
    """
    return _load_db(path).get_active_terms()


def get_prompt_terms(path: Optional[Path] = None) -> str:
    """获取用于 Whisper prompt 的自然语言术语字符串。

    把已确认的正确词融入自然语言句子,而非术语堆砌,
    因为 Whisper 对自然语言 prompt 效果更好。
    """
    active = _load_db(path).get_active_terms()
    if not active:
        return ""
    terms = list(set(active.values()))[:30]
    return "视频中可能涉及以下专有名词：" + "、".join(terms)


def confirm_term(wrong: str, right: str, path: Optional[Path] = None) -> bool:
    """手动确认一条校正。"""
    db = _load_db(path)
    result = db.confirm(wrong, right)
    _save_db(db, path)
    return result


def remove_term(wrong: str, right: str, path: Optional[Path] = None) -> bool:
    """删除一条校正。"""
    db = _load_db(path)
    result = db.remove(wrong, right)
    _save_db(db, path)
    return result


def export_terms(output_path: Path, db_path: Optional[Path] = None) -> None:
    """导出术语库到指定路径。"""
    db = _load_db(db_path)
    data = {
        "version": db.version,
        "terms": {key: asdict(c) for key, c in db.terms.items()},
    }
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def import_terms(
    input_path: Path,
    merge: bool = True,
    db_path: Optional[Path] = None,
) -> LearnedTermsDB:
    """从文件导入术语库。"""
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    db = _load_db(db_path) if merge else LearnedTermsDB()
    for val in raw.get("terms", {}).values():
        try:
            c = Correction(**val)
            db.terms[db._key(c.wrong, c.right)] = c
        except (TypeError, KeyError):
            continue
    _save_db(db, db_path)
    return db


def clear_learned_terms(path: Optional[Path] = None) -> None:
    """清空学习记录。"""
    store = path or learned_terms_path()
    if store.exists():
        store.unlink()


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> list[str]:
    """中文友好的分词。

    策略: 对连续中文字符生成 2-gram + 3-gram + 单字,
    连续英文保留为词,数字保留,标点和空白忽略。

    n-gram 只在**同一连续中文片段内**生成,不会跨片段拼接。
    """
    tokens: list[str] = []
    spans = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z]+|\d+", text)

    for span in spans:
        # 英文/数字: 整体作为一个 token
        if not re.match(r"[\u4e00-\u9fff]", span):
            tokens.append(span)
            continue

        # 中文: 生成多粒度 n-gram (仅在此片段内)
        chars = list(span)
        tokens.extend(chars)
        for i in range(len(chars) - 1):
            tokens.append(chars[i] + chars[i + 1])
        for i in range(len(chars) - 2):
            tokens.append(chars[i] + chars[i + 1] + chars[i + 2])

    return tokens
