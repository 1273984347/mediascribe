"""Web 层的 pydantic 请求 / 响应模型。

从 ``mediascribe/web/app.py`` 拆出(2026-10-25 职责拆分)。
pydantic 是可选依赖 —— 缺失时本模块只导出占位类, 保证 import 不炸
(``create_app`` 会先抛 RuntimeError)。
"""

from __future__ import annotations

from typing import List, Optional

from ..i18n import t

try:
    from pydantic import BaseModel, Field, field_validator

    _PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover - optional
    _PYDANTIC_AVAILABLE = False


if _PYDANTIC_AVAILABLE:

    class TranscribeRequest(BaseModel):
        urls: List[str] = Field(..., min_length=1, max_length=20)
        engine: str = Field("whisper", pattern="^(whisper|faster-whisper|whisperx|whisper-cpp)$")
        model: str = Field(
            "small",
            pattern="^(tiny|base|small|medium|large|large-v1|large-v2|large-v3|distil-large-v2|distil-large-v3)$",
        )
        language: Optional[str] = None
        ocr_engine: str = Field("auto", pattern="^(auto|paddleocr|pytesseract|easyocr|none)$")
        wechat_cookies: Optional[str] = None
        save_images: bool = False
        bilingual: bool = False

        @field_validator("urls")
        @classmethod
        def _reject_non_http_urls(cls, v: List[str]) -> List[str]:
            # SSRF 防护: 拒绝 file:// / ftp:// / data: / gopher:// 等非 http(s) 协议。
            # 允许: http(s) URL / 本地文件路径 / 抖音短链接文本(无 "://")
            DANGEROUS = (
                # 网络协议
                "file://",
                "ftp://",
                "ftps://",
                "data:",
                "gopher://",
                "dict://",
                "ldap://",
                "ldaps://",
                "jar://",
                "netdoc://",
                # DRL R2 补全 (F-1): 浏览器/脚本 scheme (XSS 反射 + 浏览器协议误用)
                "javascript:",
                "vbscript:",
                "blob:",
                "view-source:",
                "about:",
                "chrome:",
                "chrome-extension:",
                "moz-extension:",
            )
            for url in v:
                low = url.strip().lower()
                if low.startswith(DANGEROUS):
                    raise ValueError(t("WEB_URL_SCHEME_BLOCKED", url=url[:60]))
            return v

    class TranscribeItem(BaseModel):
        url: str
        ok: bool
        engine: Optional[str] = None
        markdown: Optional[str] = None
        title: Optional[str] = None
        error: Optional[str] = None
        wechat_mp_status: Optional[str] = None
        ocr_success: int = 0
        ocr_total: int = 0

    class TranscribeResponse(BaseModel):
        results: List[TranscribeItem]

else:

    class TranscribeRequest:  # type: ignore[no-redef]
        """占位 —— pydantic 未安装时供 import 不报错。"""

    class TranscribeItem:  # type: ignore[no-redef]
        """占位 —— pydantic 未安装时供 import 不报错。"""

    class TranscribeResponse:  # type: ignore[no-redef]
        """占位 —— pydantic 未安装时供 import 不报错。"""
