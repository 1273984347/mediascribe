"""LLM 后处理模块 (v3.2.0d)

ASR 转录文本的 LLM 后处理步骤，用于修正 Whisper 系列模型在中文转录中
常见的三类错误：

1. **专有名词错误**：佛尔摩斯→福尔摩斯、钢国→刚果、马克多文→马克·吐温
2. **同音字错误**：急转线→转捩点、续世→叙事、泄度→亵渎
3. **标点 / 分段**：补全逗号、句号、问号；按语义切分段落

设计原则
--------

* **不改原话**：不增删内容、不做文辞润色、不调整句式。只修错字 + 标点 + 分段。
* **错误兜底**：API 调用失败时返回原文 + ``"llm-failed"`` 状态，不阻塞 pipeline。
* **显式开关**：默认 ``enabled=False``，避免破坏现有行为。通过 Settings 或环境变量启用。
* **OpenAI 兼容**：通过 ``api_base`` 切换 DeepSeek / Qwen / OpenAI / 本地模型。
* **可观测**：返回 ``(text, status)`` 元组，status 写入 metadata + md 头部 banner。

环境变量
--------

* ``VIDEO2TEXT_LLM_API_KEY`` — API key
* ``VIDEO2TEXT_LLM_API_BASE`` — 默认 ``https://api.deepseek.com``
* ``VIDEO2TEXT_LLM_MODEL`` — 默认 ``deepseek-chat``
* ``VIDEO2TEXT_LLM_ENABLED`` — ``"1"`` / ``"true"`` 启用

用法
----

```python
from video2text.llm_post_process import LLMPostProcessor

proc = LLMPostProcessor.from_env()
text, status = proc.post_process(raw_text, context={
    "title": "视频标题",
    "source_url": "https://...",
    "kind": "douyin",
})
# status: "llm-reviewed" | "llm-failed" | "llm-disabled"
```
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status constants — 写入 metadata + md banner
# ---------------------------------------------------------------------------
STATUS_REVIEWED = "llm-reviewed"      # LLM 后处理成功
STATUS_FAILED = "llm-failed"          # LLM 调用失败，回退到原文
STATUS_DISABLED = "llm-disabled"      # LLM 后处理未启用
STATUS_SKIPPED = "llm-skipped"        # 启用但跳过（如文本太短）

# 最短文本阈值：小于此长度不调用 LLM（避免无意义请求）
MIN_TEXT_LENGTH = 50

# v3.2.0e: API 重试配置
_API_MAX_RETRIES = 3
_API_BACKOFF_BASE = 1.0  # 指数退避基数: 1s, 2s, 4s


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是一个 ASR（自动语音识别）转录后处理助手。下面会给你一段视频的 ASR 原始转录文本（机器语音识别输出），可能包含以下问题：

1. 专有名词错误：佛尔摩斯→福尔摩斯、钢国→刚果、马克多文→马克·吐温、北戈基尔221b→贝克街221B、太武士河→泰晤士河、克南道尔→柯南·道尔
2. 同音字错误：急转线→转捩点、续世的引擎→叙事的引擎、泄度→亵渎、势度→制度、突灾者→施暴者
3. 标点缺失或错误：补全逗号、句号、问号、引号、书名号
4. 缺少段落切分：按语义节点切分段落

**严格规则（不可违反）**：
- 不要做文辞润色，不要调整句式，不要增删内容
- 保持说话者的原话和原顺序
- 只修错字 + 标点 + 分段
- 如果不确定某个词，保留原文不要瞎改

**输出格式**：
- 直接输出修正后的 Markdown 文本
- 不要添加任何解释、注释、前后缀
- 不要输出 ```markdown 代码块标记
- 第一行不要加标题（标题由调用方添加）"""

USER_PROMPT_TEMPLATE = """视频标题（用于上下文判断专有名词）：
{title}

视频来源：{source_kind}
{source_url_line}

ASR 原始转录：
---
{text}
---

请输出修正后的转录文本（仅 Markdown 正文，按语义切分段落）。"""


# ---------------------------------------------------------------------------
# LLMPostProcessor
# ---------------------------------------------------------------------------
@dataclass
class LLMPostProcessor:
    """LLM 后处理处理器。

    使用 OpenAI 兼容 API（支持 DeepSeek / Qwen / OpenAI / 本地模型）。

    Attributes
    ----------
    api_key : str
        API key。必填。
    api_base : str
        API base URL。默认 ``https://api.deepseek.com``。
    model : str
        模型名。默认 ``deepseek-chat``。
    enabled : bool
        是否启用。默认 ``True``（由 Settings 层控制总开关）。
    timeout : float
        请求超时秒数。默认 120s（长文本需要时间）。
    max_chars : int
        单次请求的最大字符数。超过则截断（避免 token 爆炸）。
        默认 12000（约 4000-6000 tokens，留足输出空间）。
    """

    api_key: str
    api_base: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    enabled: bool = True
    timeout: float = 120.0
    max_chars: int = 12000
    # v3.2.0e: OpenAI client 复用，避免每次 post_process 重新建连
    _client: Any = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "LLMPostProcessor":
        """从环境变量构造。

        环境变量：
        - ``VIDEO2TEXT_LLM_API_KEY`` (必填)
        - ``VIDEO2TEXT_LLM_API_BASE`` (默认 https://api.deepseek.com)
        - ``VIDEO2TEXT_LLM_MODEL`` (默认 deepseek-chat)
        - ``VIDEO2TEXT_LLM_ENABLED`` (默认 1 / true 启用)
        - ``VIDEO2TEXT_LLM_TIMEOUT`` (默认 120)
        - ``VIDEO2TEXT_LLM_MAX_CHARS`` (默认 12000)
        """
        api_key = os.environ.get("VIDEO2TEXT_LLM_API_KEY", "").strip()
        api_base = os.environ.get(
            "VIDEO2TEXT_LLM_API_BASE", "https://api.deepseek.com"
        ).strip()
        model = os.environ.get("VIDEO2TEXT_LLM_MODEL", "deepseek-chat").strip()
        enabled_env = os.environ.get("VIDEO2TEXT_LLM_ENABLED", "1").strip().lower()
        enabled = enabled_env in ("1", "true", "yes", "on")
        try:
            timeout = float(os.environ.get("VIDEO2TEXT_LLM_TIMEOUT", "120"))
        except ValueError:
            timeout = 120.0
        try:
            max_chars = int(os.environ.get("VIDEO2TEXT_LLM_MAX_CHARS", "12000"))
        except ValueError:
            max_chars = 12000

        return cls(
            api_key=api_key,
            api_base=api_base,
            model=model,
            enabled=enabled and bool(api_key),
            timeout=timeout,
            max_chars=max_chars,
        )

    @classmethod
    def from_settings(cls, settings: Any) -> Optional["LLMPostProcessor"]:
        """从 Settings 对象构造。

        Settings 需有 ``llm_post_process`` 属性（dict 或 namespace）。
        如果 Settings 没有此属性，回退到环境变量。
        """
        llm_cfg = getattr(settings, "llm_post_process", None)
        if llm_cfg is None:
            return cls.from_env()
        if isinstance(llm_cfg, dict):
            return cls(
                api_key=llm_cfg.get("api_key", ""),
                api_base=llm_cfg.get("api_base", "https://api.deepseek.com"),
                model=llm_cfg.get("model", "deepseek-chat"),
                enabled=llm_cfg.get("enabled", True) and bool(llm_cfg.get("api_key")),
                timeout=llm_cfg.get("timeout", 120.0),
                max_chars=llm_cfg.get("max_chars", 12000),
            )
        # namespace / SimpleNamespace
        return cls(
            api_key=getattr(llm_cfg, "api_key", ""),
            api_base=getattr(llm_cfg, "api_base", "https://api.deepseek.com"),
            model=getattr(llm_cfg, "model", "deepseek-chat"),
            enabled=getattr(llm_cfg, "enabled", True) and bool(getattr(llm_cfg, "api_key", "")),
            timeout=getattr(llm_cfg, "timeout", 120.0),
            max_chars=getattr(llm_cfg, "max_chars", 12000),
        )

    # ------------------------------------------------------------------
    # 核心方法
    # ------------------------------------------------------------------
    def post_process(
        self,
        text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """对 ASR 原文做 LLM 后处理。

        Parameters
        ----------
        text : str
            ASR 原始转录文本。
        context : dict, optional
            上下文信息，可包含：
            - ``title``: 视频标题（强烈推荐，用于专有名词判断）
            - ``source_url``: 视频源 URL
            - ``kind``: 视频来源类型（douyin / bilibili / youtube 等）

        Returns
        -------
        (text, status) : tuple[str, str]
            - ``text``: 处理后的文本（失败时返回原文）
            - ``status``: ``STATUS_REVIEWED`` / ``STATUS_FAILED`` /
              ``STATUS_DISABLED`` / ``STATUS_SKIPPED``
        """
        if not self.enabled:
            return text, STATUS_DISABLED
        if not self.api_key:
            logger.warning("LLM post-process enabled but api_key is empty")
            return text, STATUS_DISABLED
        if not text or len(text.strip()) < MIN_TEXT_LENGTH:
            logger.info("LLM post-process skipped: text too short (%d chars)", len(text or ""))
            return text, STATUS_SKIPPED

        # 截断超长文本（保留尾部，避免 token 爆炸）
        truncated = False
        if len(text) > self.max_chars:
            logger.warning(
                "LLM post-process: text %d chars exceeds max %d, truncating",
                len(text), self.max_chars,
            )
            text = text[:self.max_chars]
            truncated = True

        context = context or {}
        prompt = self._build_prompt(text, context)

        try:
            result = self._call_api(prompt)
            if not result or not result.strip():
                logger.warning("LLM post-process returned empty result")
                return text, STATUS_FAILED
            # 清理模型可能添加的代码块标记
            cleaned = self._strip_code_fences(result.strip())
            if truncated:
                cleaned += "\n\n<!-- LLM post-process: 输入文本超长已截断 -->"
            logger.info("LLM post-process: success (%d → %d chars)", len(text), len(cleaned))
            return cleaned, STATUS_REVIEWED
        except Exception as exc:
            logger.warning("LLM post-process failed: %r", exc)
            return text, STATUS_FAILED

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _build_prompt(self, text: str, context: Dict[str, Any]) -> str:
        """构造 user prompt。"""
        title = context.get("title") or "(无标题)"
        kind = context.get("kind") or "unknown"
        url = context.get("source_url")
        url_line = f"视频 URL：{url}\n" if url else ""
        return USER_PROMPT_TEMPLATE.format(
            title=title,
            source_kind=kind,
            source_url_line=url_line,
            text=text,
        )

    def _get_client(self):
        """获取或创建复用的 OpenAI client。

        v3.2.0e: client 复用避免每次 ``post_process`` 重新建 TCP + TLS，
        每次省 200-500ms。
        """
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "openai 库未安装，请运行: pip install openai"
            ) from e
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=self.timeout,
        )
        return self._client

    def _call_api(self, user_prompt: str) -> str:
        """调用 OpenAI 兼容 API，带重试。

        v3.2.0e: 对 429 / 5xx / 超时 / 网络错误指数退避重试最多
        ``_API_MAX_RETRIES`` 次。4xx（除 429）不重试，直接抛。
        """
        client = self._get_client()
        last_exc: Optional[Exception] = None
        for attempt in range(1, _API_MAX_RETRIES + 1):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.0,    # 确定性输出，避免润色
                    max_tokens=4096,
                    stream=False,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_exc = exc
                # 判断是否可重试
                if not self._is_retryable(exc):
                    logger.warning(
                        "LLM API 调用失败 (不可重试, attempt=%d/%d): %r",
                        attempt, _API_MAX_RETRIES, exc,
                    )
                    raise
                if attempt < _API_MAX_RETRIES:
                    backoff = _API_BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        "LLM API 调用失败 (attempt=%d/%d, %.1fs 后重试): %r",
                        attempt, _API_MAX_RETRIES, backoff, exc,
                    )
                    time.sleep(backoff)
                else:
                    logger.warning(
                        "LLM API 调用失败 (已达最大重试 %d 次): %r",
                        _API_MAX_RETRIES, exc,
                    )
        # 所有重试耗尽
        raise last_exc if last_exc else RuntimeError("LLM API 调用失败，未知原因")

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """判断异常是否可重试。

        可重试:
        - 429 Too Many Requests
        - 5xx 服务端错误
        - Timeout / APITimeoutError
        - APIConnectionError（网络抖动）
        不可重试:
        - 4xx（除 429）— 客户端错误，重试无用
        - AuthenticationError — key 错误
        - BadRequestError — 请求格式错误
        """
        # OpenAI SDK 异常类名判断（避免硬 import 依赖）
        exc_name = type(exc).__name__
        if exc_name in (
            "APITimeoutError",
            "APIConnectionError",
            "APIError",
            "Timeout",
            "TimeoutError",
        ):
            return True
        # HTTP status_code 属性（OpenAI SDK 的 APIStatusError）
        status = getattr(exc, "status_code", None) or getattr(
            getattr(exc, "response", None), "status_code", None
        )
        if status is None:
            # 未知异常类型，保守起见重试一次
            return True
        if status == 429:
            return True
        if 500 <= status < 600:
            return True
        return False

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """剥离模型可能加的 ```markdown ... ``` 标记。"""
        text = text.strip()
        if text.startswith("```"):
            # 去掉首行 ```xxx
            lines = text.split("\n", 1)
            if len(lines) == 2:
                text = lines[1]
            else:
                text = ""
            # 去掉尾部 ```
            if text.endswith("```"):
                text = text[:-3]
        return text.strip()


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------
def llm_post_process(
    text: str,
    context: Optional[Dict[str, Any]] = None,
    processor: Optional[LLMPostProcessor] = None,
) -> Tuple[str, str]:
    """便捷封装：从环境变量构造 processor 并执行后处理。

    Returns
    -------
    (text, status) : tuple[str, str]
    """
    if processor is None:
        processor = LLMPostProcessor.from_env()
    return processor.post_process(text, context=context)


def build_status_banner(status: str, model: str = "") -> str:
    """根据 status 生成 md 头部 banner HTML 注释。

    Examples
    --------
    >>> build_status_banner("llm-reviewed", "deepseek-chat")
    '<!-- post-process: llm-reviewed (model=deepseek-chat) -->'
    >>> build_status_banner("llm-disabled")
    '<!-- post-process: llm-disabled (raw ASR output, not LLM-reviewed) -->'
    """
    if status == STATUS_REVIEWED:
        model_part = f" (model={model})" if model else ""
        return f"<!-- post-process: llm-reviewed{model_part} -->"
    if status == STATUS_FAILED:
        return "<!-- post-process: llm-failed (fell back to raw ASR output) -->"
    if status == STATUS_SKIPPED:
        return "<!-- post-process: llm-skipped (text too short) -->"
    # STATUS_DISABLED or unknown
    return "<!-- post-process: llm-disabled (raw ASR output, not LLM-reviewed) -->"
