"""
配置管理 - 参考 bili2text 的 Settings 设计
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional


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
    ):
        self.workspace_root = workspace_root or Path.cwd() / "output"

        # 目录配置
        self.downloads_dir = self.workspace_root / "downloads"
        self.audio_dir = self.workspace_root / "audio"
        self.transcripts_dir = self.workspace_root / "transcripts"
        self.metadata_dir = self.workspace_root / "metadata"

        # 跨 run 持久化缓存（v3.2.0a）
        # None 意味着遵循 XDG / VIDEO2TEXT_CACHE_DIR / 默认值
        self.cache_dir: Optional[Path] = Path(cache_dir) if cache_dir else None

        # 模型配置
        self.model = model
        self.device = device
        self.engine = engine
        self.language = language

        # 高级功能
        self.hf_token = hf_token or os.environ.get("HF_TOKEN")
        self.diarization = diarization

        # 微信公众号 cookies：dict 优先，文件兜底
        self.wechat_cookies: Dict[str, str] = dict(wechat_cookies or {})
        self.wechat_cookies_file: Optional[Path] = (
            Path(wechat_cookies_file) if wechat_cookies_file else None
        )
        if self.wechat_cookies_file and not self.wechat_cookies:
            self.wechat_cookies = _load_cookie_file(self.wechat_cookies_file)

        # 反向兼容：允许通过环境变量注入 cookies
        if not self.wechat_cookies:
            env_cookie = os.environ.get("VIDEO2TEXT_WECHAT_COOKIE")
            if env_cookie:
                self.wechat_cookies = _parse_cookie_string(env_cookie)

        self.ensure_directories()

    def ensure_directories(self):
        """确保所有目录存在"""
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
        except Exception:
            pass

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
