"""公众号图片 OCR 子系统 — 从 ``wechat_mp.py`` 拆出(P2-1,2026-11-20 架构评审)。

此前 ``wechat_mp.py`` 是"四个模块塞一个文件"(文章抓取 / 三引擎 OCR /
视频下载 / 文本落盘),OCR ~250 行与下载职责无关。拆出后:

* 本模块只管 **识别**: 三引擎适配(paddleocr / pytesseract / easyocr)、
  级联与降级语义、引擎单例缓存;
* 下载/保存图片仍属 ``wechat_mp``(网络与凭据关注点) ——
  ``recognize_image`` 只吃 PIL Image,不做 I/O。

级联语义(原样保留,2026-11-03 拆分时定稿):

* ``engine="auto"``(默认)按 paddleocr → pytesseract → easyocr 顺序尝试,
  任一成功即返回;
* 显式指定引擎时只走该引擎,失败直接返回 None(不静默回退);
* 任何失败(依赖未安装 / 识别为空)返回 ``None``,永不抛 —— 调用方安全降级。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - 类型标注用
    from PIL import Image

logger = logging.getLogger(__name__)

__all__ = [
    "clear_ocr_cache",
    "ocr_via_paddle",
    "ocr_via_tesseract",
    "ocr_via_easyocr",
    "recognize_image",
]


# ---------------------------------------------------------------------------
# 引擎单例缓存 — easyocr.Reader / paddleocr.PaddleOCR 加载耗时数秒
# (下载模型 + 构建神经网络),多图 OCR 复用同一实例(P2-2)
# ---------------------------------------------------------------------------
_EASYOCR_READER_CACHE: dict = {}
_PADDLEOCR_INSTANCE_CACHE: dict = {}


def _get_easyocr_reader(langs: list):
    """返回缓存的 easyocr.Reader。键为 lang tuple。

    缓存命中时不触发 easyocr 模块导入（避免在没装 easyocr 的
    测试环境下报错）。缓存未命中时才尝试 ``import easyocr``。
    """
    key = tuple(langs)
    reader = _EASYOCR_READER_CACHE.get(key)
    if reader is not None:
        return reader
    # 缓存未命中：实际加载（导入可能在未安装时抛 ImportError）
    import easyocr  # type: ignore  # local import keeps module load fast

    reader = easyocr.Reader(list(langs), gpu=False, verbose=False)
    _EASYOCR_READER_CACHE[key] = reader
    return reader


def _get_paddleocr_ocr():
    """返回缓存的 PaddleOCR 实例（中文，带角度分类）。

    与 ``_get_easyocr_reader`` 相同的惰性单例模式：缓存命中时不触发
    paddleocr 模块导入；未命中才 ``import paddleocr``（未安装时抛
    ImportError，由调用方降级）。
    """
    key = "ch"
    ocr = _PADDLEOCR_INSTANCE_CACHE.get(key)
    if ocr is not None:
        return ocr
    from paddleocr import PaddleOCR  # type: ignore  # local import

    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    _PADDLEOCR_INSTANCE_CACHE[key] = ocr
    return ocr


def clear_ocr_cache() -> None:
    """测试和长进程用：清空 OCR 引擎缓存以释放内存。"""
    _EASYOCR_READER_CACHE.clear()
    _PADDLEOCR_INSTANCE_CACHE.clear()


# ---------------------------------------------------------------------------
# 三引擎适配 — 原 WechatMpDownloader._ocr_via_* 方法,签名去 self
# ---------------------------------------------------------------------------
def ocr_via_paddle(img: "Image.Image", engine: str) -> Optional[str]:
    """引擎 1：paddleocr（中文公众号文章首选）。显式指定失败即 None。"""
    try:
        # PaddleOCR 初始化耗时较长，复用进程内单例（与 easyocr 一致）
        ocr = _get_paddleocr_ocr()
        result = ocr.ocr(img, cls=True)
        lines: list = []
        for page in result or []:
            for item in page or []:
                if item and len(item) >= 2 and item[1]:
                    lines.append(item[1][0])
        if lines:
            return "\n".join(lines)
    except Exception:
        if engine == "paddleocr":
            return None
    return None


def ocr_via_tesseract(img: "Image.Image", engine: str, lang: str) -> Optional[str]:
    """引擎 2：pytesseract（轻量，多语言）。显式指定失败即 None。"""
    try:
        import pytesseract  # type: ignore

        text: str = pytesseract.image_to_string(img, lang=lang)
        if text and text.strip():
            return text.strip()
    except Exception:
        if engine == "pytesseract":
            return None
    return None


def ocr_via_easyocr(img: "Image.Image", engine: str, lang: str) -> Optional[str]:
    """引擎 3：easyocr（兜底）。显式指定失败即 None。"""
    # pytesseract 风格的语言代码 → easyocr 语言列表
    langs_map = {
        "chi_sim+eng": ["ch_sim", "en"],
        "chi_tra+eng": ["ch_tra", "en"],
        "eng": ["en"],
    }
    try:
        easyocr_langs = langs_map.get(lang, [lang])
        # Reader 加载耗时 ~3s，复用进程内单例
        reader = _get_easyocr_reader(easyocr_langs)
        result = reader.readtext(img, detail=0)
        if result:
            return "\n".join(result)
    except Exception:
        if engine == "easyocr":
            return None
    return None


def recognize_image(
    img: "Image.Image", *, engine: str = "auto", lang: str = "chi_sim+eng"
) -> Optional[str]:
    """级联入口:按 ``engine`` 策略跑三引擎,任一成功即返回文本。

    语义与拆分前 ``WechatMpDownloader._ocr_image`` 的级联部分逐字一致。"""
    if engine in ("auto", "paddleocr"):
        text = ocr_via_paddle(img, engine)
        if text:
            return text
    if engine in ("auto", "pytesseract"):
        text = ocr_via_tesseract(img, engine, lang)
        if text:
            return text
    if engine in ("auto", "easyocr"):
        text = ocr_via_easyocr(img, engine, lang)
        if text:
            return text
    return None
