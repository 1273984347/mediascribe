# WeChat MP

The `mp.weixin.qq.com` downloader handles official-account
articles (公众号) — both text-with-images and video messages.

## CLI usage

### Public article (no cookies)

```bash
python -m video2text --url "https://mp.weixin.qq.com/s/abc?__biz=..." \
  --ocr-engine auto
```

### Login-wall article (cookies inline)

```bash
python -m video2text --url "..." \
  --wechat-cookies "wxuin=abc; pass_ticket=xyz; ticket=..."
```

### Cookies from a file

```bash
python -m video2text --url "..." --wechat-cookie-file cookies.json
```

Accepted cookie file formats:

* **Netscape** (the default `cookies.txt` from browser extensions)
* **JSON** object mapping `name -> value`
* **key=value** lines, separated by `;` or newlines

### Environment variable

```bash
export VIDEO2TEXT_WECHAT_COOKIE='{"wxuin": "abc", "pass_ticket": "xyz"}'
python -m video2text --url "..."
```

## OCR engines

| Engine | Quality | Speed | Install |
|--------|---------|-------|---------|
| `paddleocr` | best for Chinese | medium | `pip install paddleocr` |
| `easyocr` | good, easy setup | slow first call, then cached | `pip install easyocr` |
| `pytesseract` | baseline | fast | `apt install tesseract-ocr` + `pip install pytesseract` |
| `auto` | uses the first one found | mixed | — |

`auto` walks `paddleocr → pytesseract → easyocr`.  If all three
fail, the article is still saved with `wechat_mp_status: partial`.

## Concurrent OCR (v3.0)

The downloader dispatches image OCR over a `ThreadPoolExecutor`
with up to 4 workers.  The `easyocr.Reader` instance is cached
module-level so subsequent images in the same run skip the
~3 second model load.

## Bilingual video messages

When the article is a video with embedded subtitles, add
`--bilingual` to render both the original and English tracks
side-by-side:

```bash
python -m video2text --url "..." --bilingual --lang en
```

## Real-URL smoke test

```bash
python scripts/run_wechat_mp_e2e.py --from-fixture \
  --wechat-cookies "wxuin=abc; pass_ticket=xyz" --dry-run
```

## Failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `RuntimeError: 无法拉取微信公众号文章 HTML` | IP blocked (1023/1024) or 403 | inject cookies, retry from a different IP |
| `无法从文章中提取正文或视频` | empty `<div id="js_content">` | cookies are wrong, or article was deleted |
| `OCR 部分失败: 2/5` | partial engine failure | article is still saved as `partial`; install more engines |
| `wechat_mp_status: none` | metadata missing | re-run with `--save-images` to force a metadata write |
