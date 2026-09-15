"""
配置管理 - 参考 bili2text 的 Settings 设计
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

_logger = logging.getLogger(__name__)

# 合法转录引擎（与 mediascribe/transcribers/factory.py、web 层校验一致）
_VALID_ENGINES = ("whisper", "faster-whisper", "whisperx")
# 合法模型名（与 CLI --model choices、web 层 TranscribeRequest 校验一致）
_VALID_MODELS = frozenset(
    {
        "tiny",
        "base",
        "small",
        "medium",
        "large",
        "large-v1",
        "large-v2",
        "large-v3",
        "distil-large-v2",
        "distil-large-v3",
    }
)


class Settings:
    """应用程序配置"""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        model: str = "small",
        device: Optional[str] = None,
        engine: str = "whisper",
        language: Optional[str] = None,
        hf_token: Optional[str] = None,
        diarization: bool = False,
        wechat_cookies: Optional[Dict[str, str]] = None,
        wechat_cookies_file: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        llm_post_process: Optional[Dict[str, Any]] = None,
        timestamps: bool = False,
        audio_only: bool = False,
    ):
        # workspace 根目录：显式参数 > MEDIASCRIBE_WORKSPACE 环境变量 > 默认 ./output
        # （Docker 镜像 ENV MEDIASCRIBE_WORKSPACE=/workspace 指向挂载卷；web 层读取
        # 同一 env，此处保持“env 优先于默认值、显式参数最优先”的一致语义。）
        if workspace_root is not None:
            self.workspace_root = Path(workspace_root)
        else:
            env_workspace = os.environ.get("MEDIASCRIBE_WORKSPACE", "").strip()
            self.workspace_root = Path(env_workspace) if env_workspace else Path.cwd() / "output"

        # 引擎 / 模型尽早校验：非法值立即 ValueError，避免转录中途才失败
        if engine not in _VALID_ENGINES:
            raise ValueError(f"Unknown engine {engine!r}. Allowed: {', '.join(_VALID_ENGINES)}.")
        # "auto" = 按音频时长自动选模型（v3.4.0，CLI --model auto），
        # 由 Pipeline._AutoModelTranscriber 拿到真实音频时长后惰性解析。
        if model != "auto" and model not in _VALID_MODELS:
            raise ValueError(
                f"Unknown model {model!r}. Allowed: auto, {', '.join(sorted(_VALID_MODELS))}."
            )

        # 目录配置
        self.downloads_dir = self.workspace_root / "downloads"
        self.audio_dir = self.workspace_root / "audio"
        self.transcripts_dir = self.workspace_root / "transcripts"
        self.metadata_dir = self.workspace_root / "metadata"

        # 跨 run 持久化缓存（v3.2.0a）
        # None 意味着遵循 XDG / MEDIASCRIBE_CACHE_DIR / 默认值
        self.cache_dir: Optional[Path] = Path(cache_dir) if cache_dir else None

        # 模型配置（已通过上方 _VALID_ENGINES / _VALID_MODELS 校验）
        self.model = model
        self.engine = engine
        # device 默认 auto-resolve (CUDA > Metal > ROCm > CPU)
        self.device = device if device else os.environ.get("MEDIASCRIBE_DEVICE", "auto")
        self.language = language

        # 高级功能
        self.hf_token = hf_token or os.environ.get("HF_TOKEN")
        self.diarization = diarization

        # v3.4.0: Markdown 正文段落带 [mm:ss] 时间戳前缀（CLI --timestamps）
        self.timestamps = bool(timestamps)

        # v3.4.2: 只拉音轨(转录场景够用, 省 ~70% 带宽; B 站直走 API 音轨)
        self.audio_only = bool(audio_only) or os.environ.get(
            "MEDIASCRIBE_AUDIO_ONLY", ""
        ).lower() in ("1", "true")

        # 微信公众号 cookies：dict 优先，文件兜底
        self.wechat_cookies: Dict[str, str] = dict(wechat_cookies or {})
        self.wechat_cookies_file: Optional[Path] = (
            Path(wechat_cookies_file) if wechat_cookies_file else None
        )
        if self.wechat_cookies_file and not self.wechat_cookies:
            self.wechat_cookies = _load_cookie_file(self.wechat_cookies_file)

        # 反向兼容：允许通过环境变量注入 cookies
        if not self.wechat_cookies:
            env_cookie = os.environ.get("MEDIASCRIBE_WECHAT_COOKIE")
            if env_cookie:
                self.wechat_cookies = _parse_cookie_string(env_cookie)

        # v3.2.0d: LLM 后处理配置（OpenAI 兼容 API）
        # 显式 dict 参数优先；环境变量次之；默认禁用（必须显式开启，
        # 仅设置 API_KEY 不再自动启用，避免误触外部计费 API）
        self.llm_post_process: Dict[str, Any] = dict(llm_post_process or {})
        # 环境变量兜底
        env_api_key = os.environ.get("MEDIASCRIBE_LLM_API_KEY", "").strip()
        if env_api_key and "api_key" not in self.llm_post_process:
            self.llm_post_process.setdefault("api_key", env_api_key)
        if "api_base" not in self.llm_post_process:
            # api_base 无默认厂商值：必须由用户显式配置（env 或参数）
            env_api_base = os.environ.get("MEDIASCRIBE_LLM_API_BASE", "").strip()
            if env_api_base:
                self.llm_post_process["api_base"] = env_api_base
        if "model" not in self.llm_post_process:
            self.llm_post_process.setdefault(
                "model",
                os.environ.get("MEDIASCRIBE_LLM_MODEL", "deepseek-chat").strip(),
            )
        if "enabled" not in self.llm_post_process:
            # 安全默认 False：仅当 MEDIASCRIBE_LLM_ENABLED 显式为
            # 1/true/yes/on 时才启用
            enabled_env = os.environ.get("MEDIASCRIBE_LLM_ENABLED", "").strip().lower()
            self.llm_post_process["enabled"] = enabled_env in ("1", "true", "yes", "on")
        # 启用 LLM 时必须已显式配置 api_base，否则在构造期给出清晰错误
        if self.llm_post_process.get("enabled") and not self.llm_post_process.get("api_base"):
            raise ValueError(
                "LLM 后处理已启用（enabled=True），但未配置 api_base。"
                " 请设置环境变量 MEDIASCRIBE_LLM_API_BASE"
                "（如 https://api.deepseek.com / https://api.openai.com/v1），"
                " 或在 Settings(llm_post_process={'api_base': ...}) 中显式传入。"
            )

        self.ensure_directories()

    def ensure_directories(self):
        """确保所有目录存在。

        注意：构造 Settings 时自动创建目录是刻意保留的副作用
        （历史契约，pipeline / web 层均依赖目录在构造后即存在），
        不要改成惰性创建。
        """
        for dir_path in [
            self.workspace_root,
            self.downloads_dir,
            self.audio_dir,
            self.transcripts_dir,
            self.metadata_dir,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)


def _load_cookie_file(path: Path) -> Dict[str, str]:
    """
    解析 cookie 文件。支持两种格式：
    - Netscape（来自浏览器扩展 "Get cookies.txt"）:
        # Netscape HTTP Cookie File
        domain  TRUE  /  FALSE  0  name  value
    - 简单 JSON: ``{"name": "value", ...}``
    """
    path = Path(path)
    if not path.exists():
        _logger.warning("cookie 文件不存在: %s（忽略，按无 cookies 处理）", path)
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    return _parse_cookie_string(text)


def _parse_cookie_string(text: str) -> Dict[str, str]:
    """统一解析入口：先尝试 JSON，失败回退到 Netscape 风格。"""
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            import json

            data = json.loads(text)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception as exc:  # JSON 解析失败 → 回退 Netscape 解析
            _logger.debug("cookie JSON 解析失败, 回退 Netscape 解析: %r", exc)

    cookies: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        # Netscape tab-separated: domain TAB ... TAB name TAB value
        # 简单 key=value 形式：name=value
        if "=" in line and "\t" not in line:
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip().strip(";")
            if name:
                cookies[name] = value
        else:
            parts = line.split("\t")
            if len(parts) >= 7:
                name = parts[5].strip()
                value = parts[6].strip()
                if name:
                    cookies[name] = value
    return cookies
