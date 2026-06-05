"""
微信公众号文章/视频内容下载器

支持的链接形式
- `https://mp.weixin.qq.com/s?__biz=...&mid=...&idx=...`  (图文消息)
- `https://mp.weixin.qq.com/s?__biz=...&mid=...&idx=...&scene=...`  (视频消息)

提取策略
- GET 拉取 HTML（带真实 UA + Referer: mp.weixin.qq.com）
- 解析 `<div id="js_content">` 节点，提取纯文本与图片 alt
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
"""
from __future__ import annotations

import re
import time
from html import unescape
from pathlib import Path
from typing import Any, Optional

import requests

from ..config import Settings
from ..models import DownloadResult, SourceRef
from .base import Downloader


class WechatMpDownloader(Downloader):
    """微信公众号内容下载器（图文 / 视频）。"""

    name = "wechat_mp"

    MP_HOST = "mp.weixin.qq.com"

    def __init__(self) -> None:
        # 公众号 cookies 注入（login-wall 文章）
        # 优先使用 settings.wechat_cookies
        # 解析顺序：dict → file → env (VIDEO2TEXT_WECHAT_COOKIE)
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

        print(f"   🔗 目标链接: {url}")

        # 暂存 OCR 选项，供 _ocr_image / _ocr_images 使用
        self._ocr_engine: str = (ocr_engine or "auto").lower()
        self._ocr_lang: str = (ocr_lang or "chi_sim+eng")
        self._save_images: bool = bool(save_images)

        # 自动注入 cookies（来自 settings.wechat_cookies）
        if settings.wechat_cookies and not self._active_cookies:
            self._active_cookies = dict(settings.wechat_cookies)
            self._cookies_source = "settings"
            print(f"   🍪 使用 settings 注入的 {len(self._active_cookies)} 个 cookie")

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
            print(f"   🖼️  检测到 {len(image_urls)} 张图片，尝试 OCR...")
            ocr_texts, ocr_success, ocr_total = self._ocr_images(
                image_urls, settings.downloads_dir
            )

        if video_url:
            # 视频型公众号消息：下载视频并保留文本作为副标题
            print("   🎬 检测到视频消息，开始下载视频...")
            video_path = self._download_video(video_url, settings.downloads_dir)
            if not video_path or not video_path.exists():
                raise RuntimeError(f"下载失败: 找不到文件 {video_path}")
            print(f"   ✅ 视频下载成功: {video_path.name}")
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
            print(
                f"   ⚠️  OCR 部分失败: {ocr_success}/{ocr_total}，"
                f"标记为 partial"
            )

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
            print(f"   ⚠️  拉取文章 HTML 失败: {e}")
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

    def _extract_text(self, html: str) -> Optional[str]:
        """
        从 `<div id="js_content">...</div>` 节点中提取纯文本。
        按段落聚合，跳过内嵌脚本和样式。
        """
        m = re.search(
            r'<div[^>]+id=["\']js_content["\'][\s\S]*?</div>',
            html,
            re.IGNORECASE,
        )
        if not m:
            return None

        block = m.group(0)
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
        m = re.search(
            r'<div[^>]+id=["\']js_content["\'][\s\S]*?</div>',
            html,
            re.IGNORECASE,
        )
        if not m:
            return []

        block = m.group(0)
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
            print(f"   ⚠️  下载图片失败: {e}")
            return None

        try:
            from io import BytesIO

            from PIL import Image

            img = Image.open(BytesIO(resp.content))
        except Exception as e:
            print(f"   ⚠️  解析图片失败: {e}")
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
                print(f"   ⚠️  保存图片失败: {e}")

        engine = getattr(self, "_ocr_engine", "auto") or "auto"
        lang = getattr(self, "_ocr_lang", "chi_sim+eng") or "chi_sim+eng"

        # 引擎 1：paddleocr（中文公众号文章首选）
        if engine in ("auto", "paddleocr"):
            try:
                from paddleocr import PaddleOCR  # type: ignore

                ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
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

        Returns
        -------
        (ocr_texts, success_count, total_count)
        """
        if not image_urls:
            return [], 0, 0

        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = min(4, len(image_urls))
        ocr_texts: list = []
        success = 0

        def _one(url: str) -> Optional[str]:
            try:
                return self._ocr_image(url, save_dir)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_one, u): u for u in image_urls}
            for fut in as_completed(futures):
                text = fut.result()
                if text:
                    success += 1
                    ocr_texts.append(text)
                print(
                    f"   🖼️  OCR 进度: {success + (len(ocr_texts) - success)}/"
                    f"{len(image_urls)} (并发={max_workers})"
                )
        return ocr_texts, success, len(image_urls)

    def _download_video(self, video_url: str, save_dir: Path) -> Optional[Path]:
        save_dir.mkdir(parents=True, exist_ok=True)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://mp.weixin.qq.com/",
        }
        try:
            resp = requests.get(
                video_url,
                headers=headers,
                cookies=self._active_cookies or None,
                stream=True,
                timeout=120,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"   ❌ 下载视频失败: {e}")
            return None

        file_name = f"wechat_mp_{int(time.time())}.mp4"
        file_path = save_dir / file_name
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        percent = downloaded / total * 100
                        print(
                            f"\r   📥 下载进度: {percent:.1f}% "
                            f"({downloaded}/{total} 字节)",
                            end="",
                        )
        print()
        return file_path

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
        file_name = f"wechat_mp_{int(time.time())}.txt"
        file_path = save_dir / file_name
        file_path.write_text(text, encoding="utf-8")
        return file_path


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------
# easyocr.Reader 加载耗时 ~3s（下载模型 + 构建神经网络）。多张图片 OCR 时，
# 同一个 (lang) 组合应当复用同一 Reader，避免重复加载。
_EASYOCR_READER_CACHE: dict = {}


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


def clear_ocr_cache() -> None:
    """测试和长进程用：清空 easyocr Reader 缓存以释放内存。"""
    _EASYOCR_READER_CACHE.clear()
