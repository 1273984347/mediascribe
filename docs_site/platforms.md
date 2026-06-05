# Supported platforms

| Platform | Auto-detect | Login needed | Notes |
|----------|-------------|--------------|-------|
| Bilibili | `bilibili.com`, `b23.tv` | optional | BV-id and short links supported |
| Douyin | `douyin.com`, `v.douyin.com` | recommended | vod fallback to yt-dlp |
| YouTube | `youtube.com`, `youtu.be` | no | 4-client player rotation |
| Xiaohongshu | `xiaohongshu.com`, `xhslink.com` | required | Playwright + cookies |
| WeChat MP | `mp.weixin.qq.com/s/...` | optional | Text + image OCR |
| TikTok | `tiktok.com` | no | yt-dlp generic |

## Detector rules

The platform is detected from the URL alone.  The detection
logic lives in `video2text/inputs.py::parse_source`.  The order
of checks matters: TikTok is checked before YouTube (because
`tiktok.com/@user/video/...` and `youtube.com/@user/video/...`
share a similar tail), and YouTube is checked before generic
`youtu.be` short links.

## Adding a new platform

1. Subclass `video2text.downloaders.base.Downloader`.
2. Implement `supports(source)` and `download(source, settings, **kwargs)`.
3. Register it in `parse_source` *and* in the plugin entry_points
   so third-party packages can override or extend.
4. Add tests under `douyin_batch/tests/`.

## URL transformers

Some short links need to be HEAD-resolved before detection.
The default chain is in `video2text/url_utils.py`.  Plugins
can add their own by registering a callable under the
`video2text.url_transformers` entry point.  See
[Plugins](plugins.md).
