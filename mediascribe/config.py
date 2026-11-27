"""
配置管理 - 参考 bili2text 的 Settings 设计
"""

from __future__ import annotations

import logging
import os
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, Optional

from .i18n import t

_logger = logging.getLogger(__name__)

# 合法转录引擎（与 mediascribe/transcribers/factory.py、web 层校验一致）
_VALID_ENGINES = ("whisper", "faster-whisper", "whisperx", "whisper-cpp")
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


class EngineUnavailableError(RuntimeError):
    """No supported ASR engine is installed for automatic selection."""

    code = "ENGINE_UNAVAILABLE"

    def __init__(self) -> None:
        super().__init__(t("ENGINE_UNAVAILABLE"))


class DeviceUnavailableError(RuntimeError):
    """An explicitly requested compute device cannot be used."""

    code = "DEVICE_UNAVAILABLE"

    def __init__(self, device: str) -> None:
        super().__init__(device)
        self.device = device


def _module_available(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


def resolve_default_engine() -> tuple[str, str]:
    """Resolve the automatic engine and its user-visible notice."""
    if _module_available("faster_whisper"):
        return "faster-whisper", ""
    if _module_available("whisper"):
        return "whisper", t("ENGINE_FALLBACK_WHISPER")
    raise EngineUnavailableError()


def default_engine() -> str:
    """Return the automatic ASR engine, raising when none is available."""
    return resolve_default_engine()[0]


def is_engine_available(engine: str) -> bool:
    """Return whether an explicit engine can be constructed on this host."""
    modules = {
        "whisper": "whisper",
        "faster-whisper": "faster_whisper",
        "whisperx": "whisperx",
    }
    if engine in modules:
        return _module_available(modules[engine])
    if engine == "whisper-cpp":
        try:
            from .transcribers.whisper_cpp import is_available
        except Exception:  # pragma: no cover - optional dependency
            return False
        return is_available()
    return False


def _valid_device(device: str) -> bool:
    if not isinstance(device, str):
        return False
    if device in {"auto", "cpu", "cuda", "metal", "rocm"}:
        return True
    return device.startswith("cuda:") and device[5:].isdigit()


class Settings:
    """应用程序配置"""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        model: Optional[str] = None,
        device: Optional[str] = None,
        engine: Optional[str] = None,
        language: Optional[str] = None,
        hf_token: Optional[str] = None,
        diarization: bool = False,
        wechat_cookies: Optional[Dict[str, str]] = None,
        wechat_cookies_file: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        llm_post_process: Optional[Dict[str, Any]] = None,
        timestamps: bool = False,
        audio_only: Optional[bool] = None,
        cleanup_media: bool = False,
        allow_model_download: bool = False,
        zh_simplify: Optional[bool] = None,
    ):
        # workspace 根目录：显式参数 > MEDIASCRIBE_WORKSPACE 环境变量 > 默认 ./output
        # （Docker 镜像 ENV MEDIASCRIBE_WORKSPACE=/workspace 指向挂载卷；web 层读取
        # 同一 env，此处保持“env 优先于默认值、显式参数最优先”的一致语义。）
        if workspace_root is not None:
            self.workspace_root = Path(workspace_root)
        else:
            env_workspace = os.environ.get("MEDIASCRIBE_WORKSPACE", "").strip()
            self.workspace_root = Path(env_workspace) if env_workspace else Path.cwd() / "output"

        # 显式传入的 engine 永远优先；自动模式由统一解析器处理可用性与回退提示。
        self.engine_notice = ""
        self.requested_engine = engine or "auto"
        self.engine_explicit = bool(engine)
        if engine:
            resolved_engine = engine
        else:
            resolved_engine, self.engine_notice = resolve_default_engine()
        # 引擎 / 模型尽早校验：非法值立即 ValueError，避免转录中途才失败
        if resolved_engine not in _VALID_ENGINES:
            raise ValueError(
                f"Unknown engine {resolved_engine!r}. Allowed: {', '.join(_VALID_ENGINES)}."
            )
        # "auto" = 按音频时长自动选模型（v3.4.0，CLI --model auto），
        # 由 Pipeline._AutoModelTranscriber 拿到真实音频时长后惰性解析。
        self.requested_model = model or "auto"
        self.model_explicit = model not in (None, "auto")
        resolved_model = model or "small"
        if resolved_model != "auto" and resolved_model not in _VALID_MODELS:
            raise ValueError(
                f"Unknown model {model!r}. Allowed: auto, {', '.join(sorted(_VALID_MODELS))}."
            )
        self.requested_device = device or os.environ.get("MEDIASCRIBE_DEVICE", "auto") or "auto"
        self.device_explicit = self.requested_device != "auto"
        resolved_device = self.requested_device
        if not _valid_device(resolved_device):
            raise ValueError(
                f"Unknown device {resolved_device!r}. Allowed: auto, cpu, cuda, cuda:N, metal, rocm."
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
        self.model = resolved_model
        self.engine = resolved_engine
        # device 默认 auto-resolve (CUDA > Metal > ROCm > CPU)
        self.device = resolved_device
        self.language = language

        # 高级功能
        self.hf_token = hf_token or os.environ.get("HF_TOKEN")
        self.diarization = diarization

        # v3.4.0: Markdown 正文段落带 [mm:ss] 时间戳前缀（CLI --timestamps）
        self.timestamps = bool(timestamps)

        # 中文简繁统一（2026-10-23 选型落地）：转写稿默认输出**简体**。
        #
        # 背景：Whisper 训练语料中文简繁混杂，不同引擎默认输出不同 ——
        # openai-whisper / whisper.cpp 不注入简体 prompt 时输出繁体
        # （实测同一段音频字级 CER：繁体 33.61% vs 简体 0.41%）。
        # 对用户而言这意味着**换引擎就会改变转写稿字形**。
        #
        # 优先级：显式参数 > 环境变量 MEDIASCRIBE_ZH_SIMPLIFY > 默认开启。
        # 关闭后：引擎侧仍会注入简体引导 prompt（那是从源头保证质量），
        # 只是输出层不再做兜底转换。
        env_zh_simplify = os.environ.get("MEDIASCRIBE_ZH_SIMPLIFY", "").strip().lower()
        if zh_simplify is not None:
            self.zh_simplify = bool(zh_simplify)
        elif env_zh_simplify in ("0", "false", "no", "off"):
            self.zh_simplify = False
        else:
            self.zh_simplify = True

        # v3.4.2: 只拉音轨(转录场景够用, 省 ~70% 带宽; B 站直走 API 音轨)。
        # 2026-11-03 首航自测校准: 默认改为 **开启** — 常规全量路径会先白等
        # B 站 mcdn P2P 节点超时再触发兜底(实测 40s+ 且白下整条视频 ~10 倍
        # 体积), audio_only 直走 playurl API 从源头绕开该坑。转录产品没有
        # 需要保留视频的下游场景(verify 重下也只需音轨)。
        # 三态解析: 显式参数 > env(宽词表 1/true/yes/on) > 默认 True;
        # 关闭用 MEDIASCRIBE_AUDIO_ONLY=0/false/no/off。
        env_audio = os.environ.get("MEDIASCRIBE_AUDIO_ONLY", "").strip().lower()
        if audio_only is not None:
            self.audio_only = audio_only
        elif env_audio in ("1", "true", "yes", "on"):
            self.audio_only = True
        elif env_audio in ("0", "false", "no", "off"):
            self.audio_only = False
        else:
            self.audio_only = True

        # v3.4.3: 转录成功后自动清理中间媒体(downloads/ 视频与 audio/ 音频)。
        # 只删托管目录内本任务产生的文件, 用户自备的本地视频/音频永不删除;
        # transcripts/ metadata/ wiki 产物不受影响。CLI --cleanup-media 或
        # 环境变量 MEDIASCRIBE_CLEANUP_MEDIA 同效。
        self.cleanup_media = bool(cleanup_media) or os.environ.get(
            "MEDIASCRIBE_CLEANUP_MEDIA", ""
        ).lower() in ("1", "true", "yes", "on")
        self.allow_model_download = bool(allow_model_download)

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
            raise ValueError(t("LLM_API_BASE_REQUIRED"))

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
