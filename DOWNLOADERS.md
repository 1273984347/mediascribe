# Downloaders

> **Per-platform downloader reference** — what each downloader supports,
> what it cannot do, and the fallback chain when it fails.

MediaScribe routes a `SourceRef` to exactly one downloader at runtime. The
selection happens in [`mediascribe/pipeline.py::Pipeline._get_downloader`](../mediascribe/pipeline.py)
using the following priority order:

| Priority | `SourceRef.kind` | Downloader           | Notes                                  |
| -------- | ---------------- | -------------------- | -------------------------------------- |
| 1        | explicit         | user-provided        | `Pipeline(downloader=...)`             |
| 2        | `xiaohongshu`    | `XiaohongshuDownloader` | Playwright required; fallback → yt-dlp |
| 3        | `douyin`         | `DouyinDownloader`     | No cookies; fallback → yt-dlp          |
| 4        | `youtube`        | `YouTubeDownloader`    | Tuned player_clients; fallback → yt-dlp|
| 5        | everything else  | `YtDlpDownloader`      | Generic fallback                       |

Source detection happens in [`mediascribe/inputs.py::parse_source`](../mediascribe/inputs.py).

---

## Bilibili — `YtDlpDownloader`

- **Kind**: `bilibili`
- **URL patterns**: `https://www.bilibili.com/video/BV...`,
  `https://www.bilibili.com/video/av...`, `b23.tv/...`, bare `BV1xxx...`
- **Requires**: `yt-dlp`
- **Works without login** for free videos. Members-only / paywalled content
  requires a `SESSDATA` cookie (not bundled).
- **Headers**: Sends `Referer: https://www.bilibili.com/` so `412 Precondition
  Failed` is avoided. UA impersonates a modern Chrome.
- **Failures**:
  - `geetest` captcha → re-run from a residential IP, or supply cookies.
  - 404 → the BV id is invalid or the video was removed.

## Douyin — `DouyinDownloader`

- **Kind**: `douyin`
- **URL patterns**: `https://www.douyin.com/video/...`, `v.douyin.com/...`,
  and **direct** `https://www.douyinvod.com/...` (the real CDN).
- **Requires**: `requests` only (no Playwright).
- **Strategy**: Most links reach the CDN via a single `window.__INITIAL_STATE__`
  read. Cookies are not needed because Douyin serves the underlying media
  URL without an `auth_token` for ordinary posts.
- **Failures**:
  - Login-wall posts → fall back to manual extraction (open devtools, find
    `douyinvod.com` request, paste as input).
  - Geo-restricted posts → use a CN proxy.

## YouTube — `YouTubeDownloader`

- **Kind**: `youtube`
- **URL patterns**: `youtube.com/watch?v=...`, `youtu.be/...`,
  `youtube-nocookie.com/embed/...`, `m.youtube.com/...`
- **Requires**: `yt-dlp`
- **Tuning**: passes `extractor_args.youtube.player_client` as
  `["web_safari", "ios", "android", "web_embedded"]` to dodge 403 / SABR
  streaming / login walls. No `PO Token` is required for these clients.
- **Format**: prefers `bv*[ext=mp4]+ba[ext=m4a]` (merged to mp4) and falls
  back to any combined format.
- **Failures**:
  - `Sign in to confirm you’re not a bot` → re-run on a residential IP, or
    supply cookies (`-Cookies` yt-dlp option).
  - Region-locked video → use a proxy from the video's region.
  - Age-restricted video → supply YouTube session cookies.

## Xiaohongshu — `XiaohongshuDownloader`

- **Kind**: `xiaohongshu`
- **URL patterns**:
  - `https://www.xiaohongshu.com/explore/<note_id>?xsec_token=...`
  - `https://www.xiaohongshu.com/discovery/item/<note_id>`
  - `https://xhslink.com/a/...` (short links — expanded with HTTP HEAD)
- **Requires**: `playwright` (`pip install playwright && python -m playwright install chromium`)
- **Strategy**: Headless Chromium navigates to the note, waits for the
  `<video>` element, and pulls `currentSrc` / `src`. If absent, falls back
  to `window.__INITIAL_STATE__` and finally a CDN regex against the page
  HTML. CDN domain: `sns-video-*.xhscdn.com` (video) and
  `sns-img-*.xhscdn.com` (image posts).
- **Image posts** (图文笔记): saves the first image as `.jpg`. Useful as a
  transcript source only for OCR — by default MediaScribe only handles
  audio/video.
- **Failures**:
  - Login-wall notes → user must paste a logged-in cookie in the
    Playwright `context` (TODO: cookie import flag).
  - Notes that require a follow → cannot be downloaded.
- **Optional dep**: When `playwright` is not installed, the downloader
  returns `None` and the pipeline falls back to `YtDlpDownloader` (which
  will also fail for most xhs URLs because the page is JS-rendered).

## Local files — no downloader

- **Kind**: `video` or `audio`
- **Path**: any path that exists on disk. Audio extensions:
  `.mp3 .wav .m4a .flac .aac .ogg`. Video extensions:
  `.mp4 .mkv .avi .mov .flv .webm .m4v`.

---

## Fallback chain (in code)

```python
# mediascribe/pipeline.py
if source.kind == "xiaohongshu":
    try: return XiaohongshuDownloader()
    except Exception: pass   # → YtDlpDownloader
if source.kind == "douyin":
    try: return DouyinDownloader()
    except Exception: pass
if source.kind == "youtube":
    try: return YouTubeDownloader()
    except Exception: pass
return YtDlpDownloader()
```

If a specialized downloader raises during `download()` (not just at init),
the exception propagates and the pipeline aborts. The fallback above only
covers the **constructor** — to fall back at runtime, wrap the
`current_downloader.download(...)` call in your own try/except.

## Known failure modes

| Platform      | Symptom                                          | Root cause                              | Workaround |
|---------------|--------------------------------------------------|-----------------------------------------|------------|
| YouTube       | `Sign in to confirm you're not a bot`            | YouTube SABR streaming / PO token       | Re-run on a residential IP, or pass a YouTube cookie via `yt-dlp --cookies`. |
| YouTube       | `HTTP Error 403: Forbidden`                      | Player client mismatch                  | `pip install -U yt-dlp` (≥ 2024.10.7 supports current clients). |
| YouTube       | `Video unavailable` for age-restricted content   | Requires sign-in                        | Use `--cookies-from-browser chrome` with a logged-in profile. |
| YouTube       | Slow or stalls on long videos                    | Format selection picks separate streams | Add `format="bv*+ba/b"` to `_build_ydl_opts`. |
| Bilibili      | `412 Precondition Failed`                        | Missing `Referer` header                | We already set it. If still failing, update `yt-dlp`. |
| Bilibili      | `geetest` captcha wall                           | Anti-bot triggered on datacenter IP     | Use a residential IP, or pass `SESSDATA` cookie. |
| Bilibili      | 404 on a valid BV id                             | Video removed / region-locked           | Verify in a browser. |
| Douyin        | `无法获取抖音视频的真实媒体 URL`                 | JS-rendered page or login-wall post     | Open in a browser, copy the `douyinvod.com` request URL, re-run with that. |
| Douyin        | `douyinvod.com` 403 after direct use             | CDN signed URL expired (~24h)           | Re-extract from the page; do not cache media URLs. |
| Xiaohongshu   | `playwright` not installed                       | Missing optional dep                    | `pip install playwright && python -m playwright install chromium`. |
| Xiaohongshu   | `无法获取小红书视频的真实媒体 URL`               | Login wall / follow-required note       | Provide cookies via `settings.cookies` or a logged-in Playwright context. |
| Xiaohongshu   | Image-only note (图文笔记)                        | No video to download                    | Returns the first image as `.jpg`. Use OCR downstream if needed. |
| WeChat MP     | `无法从文章中提取正文或视频`                     | Article deleted / banned / login-only   | The page returns a 200 with a stub; verify the URL in a browser first. |
| WeChat MP     | `拉取文章 HTML 失败`                             | IP rate-limited (10.43 / 10.44 codes)   | Wait a few minutes, or route through a CN residential IP. |
| WeChat MP     | Video URL present but download 403               | Referer is required                     | We send `Referer: https://mp.weixin.qq.com/`; do not change. |
| All           | `ffmpeg` not found in `PATH`                     | Optional dep missing                    | `pip install imageio-ffmpeg` or install ffmpeg system-wide. |
| All           | `SSL: CERTIFICATE_VERIFY_FAILED`                 | Corporate MITM proxy                    | Set `REQUESTS_CA_BUNDLE` to your CA, or pass `verify=False` at your own risk. |
| All           | Audio extraction returns None                    | Container unsupported by ffmpeg         | Convert the file with `ffmpeg -i input.ext -c copy output.mp4` first. |

If you hit a new failure not listed here, please open an issue with the
full traceback and a minimal repro URL.

## Adding a new downloader

1. Subclass `Downloader` in `mediascribe/downloaders/<name>.py`.
2. Implement `name` (str), `download(source, settings, *, progress=None)`,
   and optionally `supports(source)`.
3. Register the class in `mediascribe/downloaders/__init__.py` `__all__`.
4. Add a URL-detect branch in `mediascribe/inputs.py::parse_source`.
5. Add a branch in `Pipeline._get_downloader`.
6. Add a branch in `mcp_server._tool_detect_platform`.
7. Add tests in `douyin_batch/tests/test_new_downloaders.py`.
8. Update this file.
