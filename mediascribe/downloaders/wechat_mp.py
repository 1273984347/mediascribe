"""
微信公众号文章/视频内容下载器

支持的链接形式
- `https://mp.weixin.qq.com/s?__biz=...&mid=...&idx=...`  (图文消息)
- `https://mp.weixin.qq.com/s?__biz=...&mid=...&idx=...&scene=...`  (视频消息)

提取策略
- GET 拉取 HTML（带真实 UA + Referer: mp.weixin.qq.com）
- 解析 `<div id="js_content">` 节点（按 div 标签配平，正确处理嵌套 div），
  提取纯文本与图片 alt
- 元数据：og:title / author / nickname
- 视频消息：`var.video_iframe` / `var.video_url` 指向真实 mp4，
  可走 whisper 流程

为什么是独立类？
- 公众号不属于音视频平台，文章正文就是文本（不需要 ASR）
- pipeline 见到 `wechat_mp` 文本型文章时，跳过 ASR、直接落盘 markdown
- 视频型公众号消息仍走标准 ASR 流程

限制
- 大量公众号有 IP 风控，频繁请求会触发 1023 / 1024 错误码
- 已删除 / 被封禁文章无法恢复
- 仅支持公开文章；登录态文章需要 cookie

v3.2.0g:
- ``print`` → ``logging.getLogger(__name__)``，--json 模式下不污染 stdout
- ``js_content`` 提取改用 ``_extract_div_block`` 按标签配平，嵌套 div 不再截断
- 下载收敛到 ``_http_download.stream_download``（.part 临时文件 + 失败清理）
- OCR 进度用独立计数器，且结果按图片顺序回填
- PaddleOCR 引擎按模块级单例缓存（与 easyocr 一致），多图不再重复初始化
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from html import unescape
from pathlib import Path
from typing import Any, Optional

import requests

from ..config import Settings
from ..models import DownloadResult, SourceRef
from ._http_download import build_http_session, stream_download
from .base import Downloader

logger = logging.getLogger(__name__)


class WechatMpDownloader(Downloader):
    """微信公众号内容下载器（图文 / 视频）。"""

    name = "wechat_mp"

    MP_HOST = "mp.weixin.qq.com"

    def __init__(self) -> None:
        # 公众号 cookies 注入（login-wall 文章）
        # 优先使用 settings.wechat_cookies
        # 解析顺序：dict → file → env (MEDIASCRIBE_WECHAT_COOKIE)
        self._active_cookies: dict = {}
        self._cookies_source: str = "none"

    def attach_cookies(
        self,
        cookies: Optional[dict] = None,
        cookies_file: Optional[Path] = None,
    ) -> None:
        """
        显式注入 cookies。优先级：cookies > cookies_file。
        接受 Netscape / JSON / key=value 单行格式。
        """
        from ..config import _load_cookie_file

        if cookies:
            self._active_cookies = {str(k): str(v) for k, v in cookies.items()}
            self._cookies_source = "dict"
        elif cookies_file:
            self._active_cookies = _load_cookie_file(Path(cookies_file))
            self._cookies_source = f"file:{cookies_file}"
        else:
            self._active_cookies = {}
            self._cookies_source = "none"

    # ------------------------------------------------------------------
    # Downloader interface
    # ------------------------------------------------------------------
    def supports(self, source: SourceRef) -> bool:
        if source.kind == "wechat_mp":
            return True
        url = (source.url or "").lower()
        return self.MP_HOST in url and "/s?" in url

    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress: Optional[Any] = None,
        ocr_engine: Optional[str] = None,
        ocr_lang: Optional[str] = None,
        save_images: bool = False,
    ) -> DownloadResult:
        url = source.url
        if not url:
            raise ValueError("需要提供微信公众号文章 URL")

        logger.info("目标链接: %s", url)

        # 暂存 OCR 选项，供 _ocr_image / _ocr_images 使用
        self._ocr_engine: str = (ocr_engine or "auto").lower()
        self._ocr_lang: str = (ocr_lang or "chi_sim+eng")
        self._save_images: bool = bool(save_images)

        # 自动注入 cookies（来自 settings.wechat_cookies）
        if settings.wechat_cookies and not self._active_cookies:
            self._active_cookies = dict(settings.wechat_cookies)
            self._cookies_source = "settings"
            logger.info("使用 settings 注入的 %d 个 cookie", len(self._active_cookies))

        html = self._fetch_html(url)
        if not html:
            raise RuntimeError("无法拉取微信公众号文章 HTML")

        meta = self._extract_meta(html)
        text = self._extract_text(html)
        image_urls = self._extract_image_urls(html)
        video_url = self._extract_video_url(html)

        if not text and not video_url and not image_urls:
            raise RuntimeError(
                "无法从文章中提取正文或视频。可能文章已删除、被封禁或需登录。"
            )

        # 公众号图片文章：尝试 OCR（依赖可选；缺 OCR 引擎时降级为 partial）
        ocr_texts: list = []
        ocr_success = 0
        ocr_total = 0
        if image_urls and not video_url:
            logger.info("检测到 %d 张图片，尝试 OCR...", len(image_urls))
            ocr_texts, ocr_success, ocr_total = self._ocr_images(
                image_urls, settings.downloads_dir
            )

        if video_url:
            # 视频型公众号消息：下载视频并保留文本作为副标题
            logger.info("检测到视频消息，开始下载视频...")
            video_path = self._download_video(video_url, settings.downloads_dir)
            if not video_path or not video_path.exists():
                raise RuntimeError(f"下载失败: 找不到文件 {video_path}")
            logger.info("视频下载成功: %s", video_path.name)
            return DownloadResult(
                source=source,
                video_path=video_path,
                title=meta.get("title") or f"wechat_mp_{int(time.time())}",
                webpage_url=url,
                metadata={
                    "url": url,
                    "kind": "wechat_mp_video",
                    "video_url": video_url,
                    "title": meta.get("title"),
                    "author": meta.get("author"),
                    "nickname": meta.get("nickname"),
                    "excerpt": (text or "")[:500],
                },
            )

        # 纯文本公众号文章：写入一个 .md 形式的伪音频占位文件，
        # pipeline 在检测到 metadata['wechat_mp_text'] 时跳过 ASR，
        # 直接用 `text` 字段作为 transcript 内容。
        if text is None:
            text = ""

        # 计算 status：纯文本 → success，文本 OK 但 OCR 部分失败 → partial
        status = "success"
        if ocr_total > 0 and ocr_success < ocr_total:
            status = "partial"
            logger.warning("OCR 部分失败: %d/%d，标记为 partial", ocr_success, ocr_total)

        text_placeholder = self._write_text_stub(text, settings.audio_dir, meta)
        return DownloadResult(
            source=source,
            video_path=text_placeholder,
            title=meta.get("title") or f"wechat_mp_{int(time.time())}",
            webpage_url=url,
            metadata={
                "url": url,
                "kind": "wechat_mp_article",
                "title": meta.get("title"),
                "author": meta.get("author"),
                "nickname": meta.get("nickname"),
                "wechat_mp_text": True,
                "wechat_mp_status": status,
                "text": text,
                "image_urls": image_urls,
                "ocr_texts": ocr_texts,
                "ocr_success": ocr_success,
                "ocr_total": ocr_total,
            },
        )

    # ------------------------------------------------------------------
    # HTML fetch + parse
    # ------------------------------------------------------------------
    def _fetch_html(self, url: str) -> Optional[str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://mp.weixin.qq.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        try:
            resp = requests.get(
                url,
                headers=headers,
                cookies=self._active_cookies or None,
                timeout=20,
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as e:
            logger.warning("拉取文章 HTML 失败: %s (%s)", url, e)
            return None

    def _extract_meta(self, html: str) -> dict:
        """从 meta 标签和 #js_name 提取元数据。"""
        meta: dict = {}

        # og:title
        m = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if m:
            meta["title"] = unescape(m.group(1).strip())

        # author
        m = re.search(
            r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if m:
            meta["author"] = unescape(m.group(1).strip())

        # 公众号昵称
        m = re.search(
            r'<a[^>]+id=["\']js_name["\'][^>]*>([^<]+)</a>',
            html,
        )
        if m:
            meta["nickname"] = unescape(m.group(1).strip())

        return meta

    # 嵌入式 script / style 也要剔除（js_content 内部可能有内嵌 <div>）
    _DIV_TAG_RE = re.compile(r"<div\b[^>]*>|</div\s*>", re.IGNORECASE)

    @classmethod
    def _extract_div_block(cls, html: str, anchor_substr: str) -> Optional[str]:
        """
        定位包含 ``anchor_substr`` 的 ``<div ...>`` 起始标签，按 ``<div>`` /
        ``</div>`` 标签配平找到配对的闭合标签，返回完整的块。

        旧实现用 ``[\\s\\S]*?</div>`` 非贪婪匹配，遇到第一个内嵌 ``</div>``
        就截断（js_content 内部通常嵌套大量 div，正文/图片都会丢）。

        Returns:
            完整的 div 块（含起止标签）；找不到锚点时返回 None；
            HTML 未闭合（截断响应）时返回剩余全部内容。
        """
        # 大小写不敏感地找锚点（id="js_content" / id='js_content' 均支持）
        m = None
        for anchor in (anchor_substr, anchor_substr.replace('"', "'")):
            m = re.search(re.escape(anchor), html, re.IGNORECASE)
            if m:
                break
        if not m:
            return None

        # 找到包含锚点的起始 <div ...> 标签（锚点在标签属性里）
        open_tag = None
        for tag in re.finditer(r"<div\b[^>]*>", html, re.IGNORECASE):
            if tag.start() <= m.start() < tag.end():
                open_tag = tag
                break
        if open_tag is None:
            return None

        # 从起始标签之后开始配平计数
        depth = 1
        for tag in cls._DIV_TAG_RE.finditer(html, open_tag.end()):
            if tag.group(0).lower().startswith("</div"):
                depth -= 1
                if depth == 0:
                    return html[open_tag.start(): tag.end()]
            else:
                depth += 1

        # 标签未闭合（响应被截断）：返回剩余内容，尽量不丢数据
        return html[open_tag.start():]

    def _extract_text(self, html: str) -> Optional[str]:
        """
        从 `<div id="js_content">...</div>` 节点中提取纯文本。
        按段落聚合，跳过内嵌脚本和样式。
        """
        block = self._extract_div_block(html, 'id="js_content"')
        if not block:
            return None
        # 去掉 script / style
        block = re.sub(r"<script[\s\S]*?</script>", " ", block, flags=re.IGNORECASE)
        block = re.sub(r"<style[\s\S]*?</style>", " ", block, flags=re.IGNORECASE)

        # 段落分隔：</p> / <br> / </section>
        block = re.sub(
            r"<(?:br\s*/?|/?p\s*/?|/?section\s*/?)\s*>",
            "\n",
            block,
            flags=re.IGNORECASE,
        )

        # 去掉所有 HTML 标签
        text = re.sub(r"<[^>]+>", " ", block)

        # 反转义
        text = unescape(text)

        # 合并空白
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n\n".join(lines) if lines else None

    def _extract_video_url(self, html: str) -> Optional[str]:
        """
        视频型公众号消息通常在 JS 里有 `var video_iframe = "..."` 或
        `var video_url = "..."` 指向真实 mp4。
        """
        patterns = [
            r'var\s+video_iframe\s*=\s*["\']([^"\']+)["\']',
            r'var\s+video_url\s*=\s*["\']([^"\']+)["\']',
            r'data-src=["\']([^"\']+\.mp4[^"\']*)["\']',
            r'<source[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                url = unescape(m.group(1)).replace("\\/", "/")
                if url.startswith("//"):
                    url = "https:" + url
                if url.startswith("http"):
                    return url
        return None

    def _extract_image_urls(self, html: str) -> list:
        """
        提取 `#js_content` 内的所有图片 URL，按出现顺序去重。
        公众号 CDN: `mmbiz.qpic.cn` / `mmbiz.qlogo.cn` / 自有域。
        """
        block = self._extract_div_block(html, 'id="js_content"')
        if not block:
            return []

        urls: list = []
        seen: set = set()

        patterns = [
            r'data-src=["\']([^"\']+)["\']',
            r'<img[^>]+src=["\']([^"\']+)["\']',
        ]
        for pat in patterns:
            for match in re.finditer(pat, block, re.IGNORECASE):
                url = unescape(match.group(1)).replace("\\/", "/")
                if url.startswith("//"):
                    url = "https:" + url
                if not url.startswith("http"):
                    continue
                if "data:image" in url:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                urls.append(url)
        return urls

    def _ocr_image(self, image_url: str, save_dir: Path) -> Optional[str]:
        """
        下载图片并尝试 OCR。引擎选择规则：

        - ``self._ocr_engine == "auto"``（默认）按以下顺序尝试：
          1. paddleocr（中文识别效果最好）
          2. pytesseract（轻量，多语言）
          3. easyocr（兜底）
        - 显式指定 ``paddleocr / pytesseract / easyocr`` 时只走该引擎，
          失败直接返回 None（不静默回退到其他引擎）。

        任意一个失败（依赖未安装 / 网络 / 识别为空）都返回 None。
        不抛异常，永远安全降级。
        """
        try:
            resp = requests.get(
                image_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://mp.weixin.qq.com/",
                },
                cookies=self._active_cookies or None,
                timeout=20,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("下载图片失败: %s (%s)", image_url, e)
            return None

        try:
            from io import BytesIO

            from PIL import Image

            img = Image.open(BytesIO(resp.content))
        except Exception as e:
            logger.warning("解析图片失败: %s", e)
            return None

        # 可选：把图片保存到本地
        if getattr(self, "_save_images", False):
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
                ext = ".png"
                if "format" in img.info:
                    ext = "." + img.format.lower()
                file_name = f"wemp_{int(time.time() * 1000)}{ext}"
                img.save(save_dir / file_name)
            except Exception as e:
                logger.warning("保存图片失败: %s", e)

        engine = getattr(self, "_ocr_engine", "auto") or "auto"
        lang = getattr(self, "_ocr_lang", "chi_sim+eng") or "chi_sim+eng"

        # 引擎 1：paddleocr（中文公众号文章首选）
        if engine in ("auto", "paddleocr"):
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

        # 引擎 2：pytesseract
        if engine in ("auto", "pytesseract"):
            try:
                import pytesseract  # type: ignore

                text = pytesseract.image_to_string(img, lang=lang)
                if text and text.strip():
                    return text.strip()
            except Exception:
                if engine == "pytesseract":
                    return None

        # 引擎 3：easyocr（兜底）
        if engine in ("auto", "easyocr"):
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

    def _ocr_images(
        self, image_urls: list, save_dir: Path
    ) -> tuple:
        """
        批量 OCR 多张图片。

        并发策略：使用 ``ThreadPoolExecutor`` 并行下载 + OCR，因为
        OCR 是 I/O-bound（下载图片 + 引擎处理）。线程数 = min(4, len)，
        在单机场景下既不会触发反爬，又能榨干 CPU。

        v3.2.0g: 进度用独立 done 计数器（原公式恒等恒为 1/total），
        且 futures 结果按 ``image_urls`` 的原始顺序回填，保证
        ``ocr_texts`` 与图片顺序一一对应。

        Returns
        -------
        (ocr_texts, success_count, total_count)
        """
        if not image_urls:
            return [], 0, 0

        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = min(4, len(image_urls))
        total = len(image_urls)

        def _one(url: str) -> Optional[str]:
            try:
                return self._ocr_image(url, save_dir)
            except Exception:
                return None

        results: list = [None] * total
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_one, u): i for i, u in enumerate(image_urls)}
            for fut in as_completed(futures):
                results[futures[fut]] = fut.result()
                done += 1
                logger.info("OCR 进度: %d/%d (并发=%d)", done, total, max_workers)

        ocr_texts = [r for r in results if r]
        success = len(ocr_texts)
        return ocr_texts, success, total

    def _download_video(self, video_url: str, save_dir: Path) -> Optional[Path]:
        """下载视频消息。

        v3.2.0g: 委托公共 ``stream_download``（带重试的 session、
        ``.part`` 临时文件、失败清理），与其他下载器共享实现。
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://mp.weixin.qq.com/",
        }
        # uuid 短后缀防止同秒文件名碰撞（P2-3）
        file_path = save_dir / f"wechat_mp_{uuid.uuid4().hex[:8]}.mp4"
        session = build_http_session()
        if self._active_cookies:
            session.cookies.update(self._active_cookies)
        try:
            return stream_download(
                video_url,
                file_path,
                session=session,
                headers=headers,
                timeout=120,
            )
        except Exception as e:
            logger.warning("下载视频失败: %s (%s)", video_url, e)
            return None

    def _write_text_stub(
        self, text: str, save_dir: Path, meta: dict
    ) -> Path:
        """
        把纯文本公众号文章保存为伪音频占位文件。

        设计理由
        - pipeline 当前统一要求 `video_path` 或 `audio_path` 存在。
        - 我们把这个占位文件存到 `audio_dir` 下，扩展名 `.txt`，
          metadata 中 `wechat_mp_text=True` 触发 pipeline 跳过 ASR。
        """
        save_dir.mkdir(parents=True, exist_ok=True)
        # uuid 短后缀防止同秒文件名碰撞（P2-3）
        file_name = f"wechat_mp_{uuid.uuid4().hex[:8]}.txt"
        file_path = save_dir / file_name
        file_path.write_text(text, encoding="utf-8")
        return file_path


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------
# easyocr.Reader / paddleocr.PaddleOCR 加载耗时数秒（下载模型 + 构建神经网络）。
# 多张图片 OCR 时应复用同一实例，避免每张图重新初始化（P2-2）。
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
