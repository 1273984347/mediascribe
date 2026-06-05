# E2E Test Results

The E2E suite is opt-in via `VIDEO2TEXT_E2E=1`. To capture real
results, run on a host with network + cookies + ASR installed:

```bash
# 1. Real-URL resolution tests (no ASR, fast)
VIDEO2TEXT_E2E=1 python -m pytest douyin_batch/tests/test_e2e_real_urls.py -v

# 2. Full smoke test (real Whisper, slow)
python douyin_batch_v3.py --user <REAL_DOUYIN_URL> --max 3 --lang en
python douyin_batch_v3.py --user <REAL_BILIBILI_URL> --max 3 --lang zh
python -m video2text --url "https://www.youtube.com/watch?v=xxx"
python -m video2text --url "https://mp.weixin.qq.com/s/xxx"
```

## Smoke test matrix

| Platform | Test URL | Expected time | Expected output |
|----------|----------|---------------|-----------------|
| Bilibili | `https://www.bilibili.com/video/BV1xx411c7mD` | ~30s | 1 `.md` transcript |
| Douyin | `https://www.douyin.com/video/7234567890123456789` | ~30s | 1 `.md` transcript |
| YouTube | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` | ~45s | 1 `.md` transcript |
| Xiaohongshu | explore URL with `xsec_token` | ~60s | 1 `.md` transcript (Playwright) |
| WeChat MP (text) | `https://mp.weixin.qq.com/s/xxx` | ~10s | 1 `.md` (no ASR) |
| WeChat MP (video) | message with embedded mp4 | ~60s | 1 `.md` transcript |

## Cookie injection

To exercise the 3-tier cookie fallback, set cookies three different
ways and verify the same result:

```bash
# 1. --wechat-cookies "wxuin=abc123; pass_ticket=def456"
python -m video2text --url "https://mp.weixin.qq.com/s/xxx" \
    --wechat-cookies "wxuin=abc123; pass_ticket=def456"

# 2. --wechat-cookie-file /path/to/cookies.txt
python -m video2text --url "https://mp.weixin.qq.com/s/xxx" \
    --wechat-cookie-file cookies.txt

# 3. env var (only when --wechat-cookies / --wechat-cookie-file are not set)
export WECHAT_COOKIES='{"wxuin": "abc123", "pass_ticket": "def456"}'
python -m video2text --url "https://mp.weixin.qq.com/s/xxx"
```

## Bilingual JSON

```bash
# English (default)
python douyin_batch_v3.py --user "https://..." --json

# Chinese labels
python douyin_batch_v3.py --user "https://..." --bilingual-json --lang zh

# Filter to specific platforms
python douyin_batch_v3.py --user "https://..." \
    --platform youtube --platform wechat_mp --json
```

## OCR engines

To check that the OCR fallback chain works, install only one engine
and confirm the article is still saved with `wechat_mp_status: partial`:

```bash
# Install only easyocr
pip install easyocr

# Run on a public WeChat image article
python -m video2text --url "https://mp.weixin.qq.com/s/IMAGE_ARTICLE_URL"

# Verify metadata
cat output/xxx.json | python -c "import json,sys; print(json.load(sys.stdin)['wechat_mp_status'])"
# Expected: "partial" (no paddleocr, no pytesseract, only easyocr worked)
```

## Results

Populate this section after running the smoke tests on a real host:

| Date | Tester | Platform | URL | Status | Notes |
|------|--------|----------|-----|--------|-------|
| | | | | | |
