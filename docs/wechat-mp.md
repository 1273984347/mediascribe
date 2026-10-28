# WeChat MP

The `mp.weixin.qq.com` downloader handles official-account
articles (公众号) — both text-with-images and video messages.

## CLI usage

The article URL is the positional `input` of the `transcribe`
subcommand:

```bash
python -m mediascribe transcribe "https://mp.weixin.qq.com/s/abc?__biz=..."
```

### Login-wall article (cookies inline)

```bash
python -m mediascribe transcribe "..." \
  --wechat-cookies "wxuin=abc; pass_ticket=xyz; ticket=..."
```

### Cookies from a file

```bash
python -m mediascribe transcribe "..." --wechat-cookie-file cookies.json
```

Accepted cookie file formats:

* **Netscape** (the default `cookies.txt` from browser extensions)
* **JSON** object mapping `name -> value`
* **key=value** lines, separated by `;` or newlines

### Environment variable

```bash
export MEDIASCRIBE_WECHAT_COOKIE='{"wxuin": "abc", "pass_ticket": "xyz"}'
python -m mediascribe transcribe "..."
```

## OCR engines

OCR runs on the images embedded in an article.  Choose the engine
per request in the **Web UI** or the **MCP server** (`ocr_engine`
parameter); the CLI has no OCR flags.

| Engine | Quality | Speed | Install |
|--------|---------|-------|---------|
| `paddleocr` | best for Chinese | medium | `pip install paddleocr` |
| `easyocr` | good, easy setup | slow first call, then cached | `pip install easyocr` |
| `pytesseract` | baseline | fast | `apt install tesseract-ocr` + `pip install pytesseract` |
| `auto` (default) | uses the first one found | mixed | — |
| `none` | skip OCR entirely | — | — |

`auto` walks `paddleocr → pytesseract → easyocr`.  If all three
fail, the article is still saved with `wechat_mp_status: partial`.

Security note (2026-10-29): image/video URLs extracted from the
article are only fetched when they point at Tencent-controlled CDN
domains, and WeChat cookies are attached only to those requests —
article-embedded URLs on other domains are skipped.

## Concurrent OCR (v3.0)

The downloader dispatches image OCR over a `ThreadPoolExecutor`
with up to 4 workers.  The `easyocr.Reader` instance is cached
module-level so subsequent images in the same run skip the
~3 second model load.

## Video messages

Video-type article messages download the embedded MP4 (Tencent CDN
only) and run the standard ASR flow — pick the language with
`--language`, exactly like any other video:

```bash
python -m mediascribe transcribe "https://mp.weixin.qq.com/s?__biz=...&mid=..." \
  --language zh
```

(The batch subcommand's `--bilingual-json` flag only switches the
*JSON report* to localized labels; it does not render bilingual
transcripts.)

## Real-URL smoke test

```bash
python scripts/e2e/run_wechat_mp_e2e.py --from-fixture \
  --wechat-cookies "wxuin=abc; pass_ticket=xyz" --dry-run
```

## Failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `RuntimeError: 无法拉取微信公众号文章 HTML` | IP blocked (1023/1024) or 403 | inject cookies, retry from a different IP |
| `无法从文章中提取正文或视频` | empty `<div id="js_content">` | cookies are wrong, or article was deleted |
| `OCR 部分失败: 2/5` | partial engine failure | article is still saved as `partial`; install more engines |
| `已拒绝携带微信 cookies 请求该地址` | URL host is not `mp.weixin.qq.com` | submit the article URL from the address bar, not a wrapped/shortened link |
