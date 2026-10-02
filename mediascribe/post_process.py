"""
转录后处理 (v3.2.0b)

1. 术语校正 (ASR 常把专有名词听错)
2. Prompt 模板系统
3. 智能模型推荐

用法
----

```python
from mediascribe.post_process import post_process_transcript
from pathlib import Path

raw = Path("output/transcripts/article.md").read_text(encoding="utf-8")
fixed = post_process_transcript(raw)
Path("output/transcripts/article_corrected.md").write_text(fixed, encoding="utf-8")
```

术语表
------
默认术语表覆盖教育/历史/文学领域的常见 ASR 错误。
调用方可通过 ``custom_terms`` 追加,或通过
``MEDIASCRIBE_CUSTOM_TERMS`` 环境变量 (JSON dict)。

模型推荐
--------
``auto_select_model(duration_seconds)`` 按视频时长推荐
模型,平衡速度与精度。

Prompt 模板
----------
``get_prompt_template(domain)`` 返回领域专属 prompt,
喂给 ``Pipeline.transcribe(..., prompt=...)``。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

_logger = logging.getLogger(__name__)

__all__ = [
    "post_process_transcript",
    "detect_repeated_pairs",
    "auto_select_model",
    "get_prompt_template",
    "setup_hf_mirror",
    "DEFAULT_TERMS",
    "PROMPT_TEMPLATES",
]

# ---------------------------------------------------------------------------
# 术语校正
# ---------------------------------------------------------------------------
DEFAULT_TERMS: dict[str, str] = {
    # 教育类
    "获取病": "霍去病",
    "下水温": "下水文",
    "矛盾而分法": "矛盾二分法",
    "注价老师": "助教老师",
    "月经老师": "岳江老师",
    "某篇布局": "谋篇布局",
    "下水温章": "下水文章",
}


# ---------------------------------------------------------------------------
# 幻觉重复收敛 — ASR 解码循环的确定性自愈
# ---------------------------------------------------------------------------
# large-v3 实测偶发同一句连续 3-40 次的解码循环(45 集徐涛课程出现
# 30+ 处)。>= MIN_REPEAT 连相同句判为循环收敛为 1; 2 连保留(老师
# 口语强调)。逐行处理, 不跨段, 不改动非重复内容。
#
# 两层检测(2026-10-01): 带内部逗号的循环句按短语切后会形成
# A,B,A,B 交替, 短语级"连续相同"比对失效, 故先做整句级(只按句末
# 标点切, 归一化抹掉内部标点)收敛, 再跑短语级兜底截断尾/无句末
# 标点的短语循环。
HALLUCINATION_MIN_REPEAT = 3
_SENT_SPLIT_RE = None  # 延迟编译见 _split_sentence_units
_SENT_END_SPLIT_RE = None  # 延迟编译见 _split_full_sentences

_UNIT_PUNCT = r"[,。?!;;,.;?!]"
_SENT_END_PUNCT = r"[。?!;;?!]"  # 句末标点(不含逗号)


def _split_sentence_units(line: str) -> list:
    """把一行按中英文标点/空白切成 (句子, 尾分隔符) 单元, 保留原文所有字符。

    v3.4.3: 空白也作边界 — ASR 文本存在空格分隔的重复片段
    ("我们的困难 不是不幸" / "…学学的了 是该让…学学的了"), 此前
    空格把重复单元黏成一个, 收敛与检测双双漏判(2026-10-02 两集实锤)。
    """
    import re as _re

    parts = _re.split(r"(" + _UNIT_PUNCT + r"|\s+)", line)
    units, cur = [], ""
    for tok in parts:
        cur += tok
        if tok and _re.fullmatch(_UNIT_PUNCT + r"|\s+", tok):
            units.append(cur)
            cur = ""
    if cur:
        units.append(cur)
    return units


def _split_full_sentences(line: str) -> list:
    """把一行只按句末标点切成单元, 保留原文所有字符(内部逗号随句保留)。"""
    import re as _re

    parts = _re.split(r"(" + _SENT_END_PUNCT + r")", line)
    units, cur = [], ""
    for tok in parts:
        cur += tok
        if _re.fullmatch(_SENT_END_PUNCT, tok or ""):
            units.append(cur)
            cur = ""
    if cur:
        units.append(cur)
    return units


def _normalize_sentence(unit: str) -> str:
    import re as _re

    return _re.sub(r"[\s" + _UNIT_PUNCT[1:-1] + r"]", "", unit)


def _collapse_identical_runs(units: list, min_repeat: int) -> tuple:
    """单元列表内连续 ``min_repeat`` 次以上归一化相同的收敛为 1。

    v3.4.3: 空白单元(纯空白定界符, 归一化为空)在连续性判断中透明 —
    "A,␣A,␣B" 视作 A,A,B 相邻(2026-10-02 菜市场一集实锤); 被删除
    的重复单元之间的空白单元一并移除, 重拼接保持原文其余部分不变。
    """
    out = list(units)
    removed_total = 0
    while True:
        content = [(i, _normalize_sentence(u)) for i, u in enumerate(out) if _normalize_sentence(u)]
        target = None
        s = 0
        while s < len(content):
            e = s
            while e + 1 < len(content) and content[e + 1][1] == content[s][1]:
                e += 1
            if e - s + 1 >= min_repeat and len(content[s][1]) >= 4:
                target = (s, e)
                break
            s = e + 1
        if target is None:
            break
        s, e = target
        lo, hi = content[s][0], content[e][0]
        removed_total += e - s
        out = out[: lo + 1] + out[hi + 1 :]
    return out, removed_total


def collapse_hallucination_repeats(text: str, min_repeat: int = HALLUCINATION_MIN_REPEAT) -> tuple:
    """收敛 ASR 解码循环产生的连续重复句。

    逐行处理, 两层收敛, 返回 ``(新文本, 删除句数)``:

    1. 整句级: 行内只按句末标点切句, 连续 ``min_repeat`` 次以上完全
       相同(忽略空白与全部标点差异)的整句收敛为 1 句 — 覆盖带内部
       逗号的循环句(A,B,A,B 交替模式)。
    2. 短语级: 再按全部标点切分, 收敛无句末标点的短语循环与截断尾。

    长度 <4 的"句子"(如单字语气词)不参与判定, 避免误伤口语。
    """
    if not text:
        return text, 0
    removed_total = 0
    out_lines = []
    for line in text.splitlines():
        sentences, removed = _collapse_identical_runs(_split_full_sentences(line), min_repeat)
        removed_total += removed
        units, removed = _collapse_identical_runs(
            _split_sentence_units("".join(sentences)), min_repeat
        )
        removed_total += removed
        out_lines.append("".join(units))
    return "\n".join(out_lines), removed_total


def _common_suffix_len(a: str, b: str) -> int:
    """两串公共后缀长度。"""
    n = 0
    while n < len(a) and n < len(b) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n


# 应答式口语重复的启发式(供甄别参考, 非判定), 词表用真实样本定标:
# * echo 命中: "…写在第一个分论点。/你要写在第一个分论点,你就偏题了"
#   (2026-10-02 过度坦诚一集真应答) — 第二遍重复以第二人称称呼开头;
# * artifact 保持: "…去考北大,×2,所以说…" / "…找到它。×2,所以…"
#   (人的寿命/刻舟求剑两集真伪影) — 所以/于是/然后 等承接词常跟在
#   伪影后, 不作 echo 信号; 仅真转折词出现在后续句时才倾向 echo。
_ECHO_SECOND_PERSON = ("你要", "你就", "你说", "你们", "请问", "各位")
_ECHO_CONTRAST = (
    "但是",
    "但",
    "然而",
    "可是",
    "不过",
)  # "其实"移出: 刻舟/菜市场两集反例, 它常跟在伪影后


def _is_echo_suspect(prev_core: str, second_core: str, next_core: str) -> bool:
    """应答式信号: 第二遍重复以第二人称称呼开头, 或其前/后一句为真转折。"""
    return (
        second_core.startswith(_ECHO_SECOND_PERSON)
        or prev_core.startswith(_ECHO_CONTRAST)
        or next_core.startswith(_ECHO_CONTRAST)
    )


def detect_repeated_pairs_detailed(text: str, min_len: int = 6, suffix_len: int = 8) -> list:
    """:func:`detect_repeated_pairs` 的分级版本, 返回
    ``[{"text": ..., "kind": "echo"|"artifact"}]``。

    ``kind`` 为启发式标注(供人工甄别参考, 非判定), 词表与判定面
    用四个真实样本定标(见 _ECHO_* 注释):
    * ``echo`` — 第二遍重复以第二人称称呼开头(讲者复述学生主张再
      反驳, 如"…写在第一个分论点。/你要写在第一个分论点,你就偏题了",
      过度坦诚一集真应答), 或后续句为真转折 → 更可能是应答式口语
      重复, 保留;
    * ``artifact`` — 其余 → 更可能是 ASR 解码循环伪影, 建议人工合并。

    与 :func:`detect_repeated_pairs` 同一套三级扫描(句级/短语级/
    共享后缀, 含跨行承接), 独立实现以携带位置信息。
    """
    if not text:
        return []
    suspects: list = []
    seen: set = set()

    def _report(unit: str, echo: bool) -> None:
        if unit not in seen:
            seen.add(unit)
            suspects.append({"text": unit.strip(), "kind": "echo" if echo else "artifact"})

    def _echo(prev_core: str, second_core: str, next_core: str) -> bool:
        return _is_echo_suspect(prev_core, second_core, next_core)

    sent_carry: list = [None]
    unit_carry: list = [None]

    def _scan_run(seq_items: list, carry: list) -> None:
        """恰好 2 连扫描(跨行由 carry 承接上一行末单元; 空白单元透明)。"""
        seq = ([carry[0]] if carry and carry[0] else []) + seq_items
        cores = [_normalize_sentence(u) for u in seq]
        content = [i for i, c in enumerate(cores) if c]
        i = 0
        while i < len(content):
            idx = content[i]
            core = cores[idx]
            if len(core) >= min_len:
                j = i
                while j + 1 < len(content) and cores[content[j + 1]] == core:
                    j += 1
                if j - i + 1 == 2:
                    second = cores[content[i + 1]]
                    nxt = cores[content[j + 1]] if j + 1 < len(content) else ""
                    prev = cores[content[i - 1]] if i > 0 else ""
                    _report(seq[idx], _echo(prev, second, nxt))
                i = j + 1
                continue
            i += 1
        last = content[-1] if content else None
        carry.clear()
        carry.append(seq[last] if last is not None else (carry[0] if carry else None))

    for line in text.splitlines():
        # 1) 句级恰好 2 连(跨行由 carry 承接)
        _scan_run(_split_full_sentences(line), sent_carry)
        # 2) 短语级恰好 2 连
        units = _split_sentence_units(line)
        _scan_run(units, unit_carry)
        # 3) 共享后缀的部分重复(第二遍以第二人称/后续句真转折 → echo)
        for k in range(len(units) - 1):
            c1 = _normalize_sentence(units[k])
            c2 = _normalize_sentence(units[k + 1])
            if c1 == c2 or min(len(c1), len(c2)) < min_len:
                continue
            if _common_suffix_len(c1, c2) >= suffix_len:
                nxt = _normalize_sentence(units[k + 2]) if k + 2 < len(units) else ""
                _report(units[k + 1], _echo(c1, c2, nxt))
    return suspects


def detect_repeated_pairs(text: str, min_len: int = 6, suffix_len: int = 8) -> list:
    """检测收敛后仍存在的可疑重复(不修改文本, 供审校提示)。两类:

    1. **相邻恰好 2 连**的相同单元 — ≥3 连已被自动收敛, 剩下的 2 连
       要么是 ASR 伪影(需人工合并, 2026-10-02 批三集连续出现), 要么
       是口语强调(应保留), 如"考北大"句;
    2. **共享后缀的部分重复** — 相邻两单元尾部重叠 ≥ ``suffix_len``
       字, 后者是前者的冗余复述(如"…在终点的地方被定义的, 而是在
       终点的地方被定义的", 2026-10-02"人的寿命"一集实锤)。

    与 :func:`collapse_hallucination_repeats` 同一套切分/归一化逻辑;
    归一化长度 < ``min_len`` 的短语(如"活在当下"式口语强调)忽略,
    避免告警噪音; 只告警不修改。
    """
    if not text:
        return []
    suspects: list = []
    seen: set = set()
    sent_carry: list = [None]  # 跨行承接上一行末句(段落间 2 连)
    unit_carry: list = [None]

    def _report(unit: str, key: str) -> None:
        if key not in seen:
            seen.add(key)
            suspects.append(unit.strip())

    def _scan_run(units: list, seen: set, suspects: list, carry: list) -> None:
        """单元序列内恰好 2 连扫描; ``carry=[prev_unit]`` 承接跨行边界。

        空白单元(归一化为空)在连续性判断中透明 — "A,␣A" 视作相邻。
        """
        seq = ([carry[0]] if carry and carry[0] else []) + units
        cores = [_normalize_sentence(u) for u in seq]
        content = [i for i, c in enumerate(cores) if c]
        i = 0
        while i < len(content):
            idx = content[i]
            core = cores[idx]
            if len(core) >= min_len:
                j = i
                while j + 1 < len(content) and cores[content[j + 1]] == core:
                    j += 1
                if j - i + 1 == 2:
                    key = core
                    if key not in seen:
                        seen.add(key)
                        suspects.append(seq[idx].strip())
                i = j + 1
                continue
            i += 1
        last = content[-1] if content else None
        carry.clear()
        carry.append(seq[last] if last is not None else (carry[0] if carry else None))

    for line in text.splitlines():
        # 1) 句级恰好 2 连(仅按句末标点切分) — 复读句常含逗号, 只在
        #    短语级扫描会被逗号切碎而漏检(2026-10-02"刻舟求剑"一集
        #    实锤: "所以,在最后的升华点的时候,我们一定能够找到它。"
        #    句级 A,A,B 模式漏报); 跨段落(空行分隔)的 2 连由 carry 承接
        sentences = _split_full_sentences(line)
        _scan_run(sentences, seen, suspects, sent_carry)
        # 2) 短语级恰好 2 连(全部标点切分, 覆盖无句末标点的短语循环)
        units = _split_sentence_units(line)
        _scan_run(units, seen, suspects, unit_carry)
        # 3) 共享后缀的部分重复(完全相同的对归上面两级, 跳过)
        for k in range(len(units) - 1):
            c1 = _normalize_sentence(units[k])
            c2 = _normalize_sentence(units[k + 1])
            if c1 == c2 or min(len(c1), len(c2)) < min_len:
                continue
            if _common_suffix_len(c1, c2) >= suffix_len:
                _report(units[k + 1], "sfx:" + c2)
    return suspects


def post_process_transcript(
    text: str,
    custom_terms: Optional[dict[str, str]] = None,
    *,
    merge_env: bool = True,
    merge_learned: bool = True,
) -> str:
    """转录后术语校正。

    术语合并优先级 (低 → 高)::

        DEFAULT_TERMS < learned_terms < env MEDIASCRIBE_CUSTOM_TERMS < custom_terms

    Parameters
    ----------
    text
        原始转录文本(Markdown 或纯文本均可)。
    custom_terms
        额外术语字典;key 为错误词,value 为正确词。
    merge_env
        如果为 True,从 ``MEDIASCRIBE_CUSTOM_TERMS`` 环境变量读取
        JSON dict 并合并。
    merge_learned
        如果为 True,从 :func:`mediascribe.learn.get_learned_terms`
        加载自动学习的术语并合并。

    Returns
    -------
    替换后的文本。
    """
    terms = dict(DEFAULT_TERMS)

    # 自动学习术语 (优先级高于默认,低于用户自定义)
    if merge_learned:
        try:
            from .learn import get_learned_terms

            terms.update(get_learned_terms())
        except Exception:
            pass  # 学习模块不可用时不阻塞

    if merge_env:
        env_raw = os.environ.get("MEDIASCRIBE_CUSTOM_TERMS")
        if env_raw:
            try:
                terms.update(json.loads(env_raw))
            except json.JSONDecodeError as exc:
                # P2-13 / DRL R2 F-7 对齐: 静默忽略会让用户以为 env
                # 已生效。log warning 带异常与原文摘要,方便定位拼写
                # / 引号错误,且不阻塞主流程。
                _logger.warning(
                    "MEDIASCRIBE_CUSTOM_TERMS JSON 解析失败(%s),已忽略该环境变量;内容摘要: %.120s",
                    exc,
                    env_raw,
                )
    if custom_terms:
        terms.update(custom_terms)

    for wrong, right in terms.items():
        text = text.replace(wrong, right)
    return text


# ---------------------------------------------------------------------------
# 智能模型推荐
# ---------------------------------------------------------------------------
def auto_select_model(duration_seconds: int, *, prefer_quality: bool = False) -> str:
    """按视频时长自动推荐模型。

    Parameters
    ----------
    duration_seconds
        视频总秒数。
    prefer_quality
        如果为 True,优先精度(大模型)而非速度。

    Returns
    -------
    模型名 (``"small"`` / ``"medium"`` / ``"large-v3"``)。
    """
    if prefer_quality:
        if duration_seconds < 600:
            return "large-v3"
        if duration_seconds < 3600:
            return "medium"
        return "small"
    # 默认:平衡速度优先
    if duration_seconds < 300:
        return "medium"
    if duration_seconds < 1800:
        return "small"
    return "tiny"


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------
PROMPT_TEMPLATES: dict[str, str] = {
    "education": ("这是一段关于教育的视频，涉及高考、作文、教学等概念。"),
    "tech": ("这是一段技术分享视频，涉及编程、算法、架构等术语。"),
    "literature": ("这是一段文学/人文类视频，涉及古诗词、作家、历史人物等。"),
    "general": "",
}


def get_prompt_template(
    domain: str = "general",
    *,
    custom_prompt: Optional[str] = None,
) -> str:
    """获取领域专属 prompt,可与 custom_prompt 拼接。

    Parameters
    ----------
    domain
        领域名 (``education`` / ``tech`` / ``literature`` / ``general``)。
    custom_prompt
        用户额外 prompt,会拼接到模板后面。

    Returns
    -------
    完整 prompt 字符串。
    """
    base = PROMPT_TEMPLATES.get(domain, PROMPT_TEMPLATES["general"])
    if custom_prompt:
        if base:
            return base + " " + custom_prompt
        return custom_prompt
    return base


# ---------------------------------------------------------------------------
# HuggingFace 镜像
# ---------------------------------------------------------------------------
def setup_hf_mirror() -> str:
    """设置 HuggingFace 国内镜像。

    优先级: ``HF_ENDPOINT`` 环境变量 > ``https://hf-mirror.com``。
    返回值: 实际生效的镜像 URL。

    注意: 必须在 ``import transformers`` / ``import huggingface_hub``
    之前调用,否则镜像 URL 已被旧值缓存。
    """
    mirror = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_ENDPOINT", mirror)
    return mirror
