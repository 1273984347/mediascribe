---
name: "video2text-wechat"
description: "Extracts text and OCR from WeChat MP (公众号) articles. Invoke when the user shares a `mp.weixin.qq.com/s/...` link or asks to archive a WeChat article as Markdown."
---

# Video2Text — WeChat MP

A focused skill for the project's most error-prone downloader.  Use
it whenever the URL is a WeChat official-account article (图文 or
视频消息), regardless of whether the article is public or
login-protected.

## When to use

- The URL contains `mp.weixin.qq.com/s/...` (or `/s?...`).
- The user pastes a WeChat article and wants a `.md` copy.
- The user has WeChat login cookies they want to inject so private
  articles can be saved.
- The article is image-heavy and the user wants OCR on top of the
  text (hand-written notes, screenshots, etc.).

## When NOT to use

- The URL is a public video on YouTube / Bilibili / Douyin — use
  the generic `video2text` skill instead.
- The user only wants a link preview / OG metadata — read the HTML
  directly, no need to fire up Whisper or OCR.

## How to invoke

### Public article (no cookies)

```bash
python -m video2text --url "https://mp.weixin.qq.com/s/abc?__biz=..." \
    --ocr-engine auto
```

### Login-wall article (cookies inline)

```bash
python -m video2text --url "https://mp.weixin.qq.com/s/abc?__biz=..." \
    --wechat-cookies "wxuin=123; pass_ticket=xyz; ticket=..."
```

### Cookies from a file (Netscape / JSON / key=value)

```bash
# Export cookies.json from your browser using "Get cookies.txt LOCALLY"
python -m video2text --url "..." --wechat-cookie-file cookies.json
```

### Environment variable (CI / Docker)

```bash
export VIDEO2TEXT_WECHAT_COOKIE='{"wxuin": "abc", "pass_ticket": "xyz"}'
python -m video2text --url "..."
```

### Pick an OCR engine

| Engine | Quality | Speed | Install |
|--------|---------|-------|---------|
| `paddleocr` | best for Chinese | medium | `pip install paddleocr` |
| `easyocr` | good, easy setup | slow first call, then cached | `pip install easyocr` |
| `pytesseract` | baseline | fast | `apt install tesseract-ocr` + `pip install pytesseract` |
| `auto` (default) | uses the first one found | mixed | — |

`auto` walks the chain `paddleocr → pytesseract → easyocr`.  If all
three fail, the article is still saved with `wechat_mp_status: partial`.

### Save the images alongside the markdown

```bash
python -m video2text --url "..." --save-images --ocr-engine easyocr
```

Images land in `output/downloads/wechat_mp_<id>/` and the markdown
gets a `![alt](file://...)` reference for each one.

### Bilingual subtitles (video messages)

```bash
python -m video2text --url "..." --bilingual --lang zh --lang en
```

The output has both the original and the English segment labels
side-by-side, suitable for language learners.

## Diagnostic tooling

```bash
# Run the dedicated WeChat MP E2E suite
python scripts/run_wechat_mp_e2e.py --from-fixture \
    --wechat-cookies "wxuin=abc; pass_ticket=xyz" --dry-run

# Real-URL smoke (needs network)
VIDEO2TEXT_E2E=1 python -m pytest douyin_batch/tests/test_e2e_real_urls.py::TestRealUrlWechatMp

# Regression: detector must recognise both /s? and /s/abc forms
python -m pytest douyin_batch/tests/test_wechat_mp_detector.py
```

## Failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `RuntimeError: 无法拉取微信公众号文章 HTML` | IP blocked (1023/1024) or 403 | inject cookies, retry from a different IP, or wait |
| `无法从文章中提取正文或视频` | empty `<div id="js_content">` | cookies are wrong, or article was deleted |
| `OCR 部分失败: 2/5` | partial engine failure | article is still saved as `partial`; install more engines and retry |
| `wechat_mp_status: none` | metadata missing | re-run with `--save-images` to force a metadata write |

## Related files

- `video2text/downloaders/wechat_mp.py` — the downloader
- `video2text/config.py` — cookie parsing (Netscape / JSON / key=value)
- `douyin_batch/tests/test_wechat_mp_detector.py` — regression tests
- `docs/e2e-results.md` — how to record a successful end-to-end run
