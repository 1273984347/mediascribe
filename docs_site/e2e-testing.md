# E2E testing

End-to-end tests exercise the full pipeline against real public
URLs.  They are **opt-in** because they require network access
and may be flaky on third-party platforms.

## Quick start

```bash
# Detection only (no downloads, no Whisper)
python scripts/run_real_e2e.py --url-only

# Full pipeline
python scripts/run_real_e2e.py
```

The runner writes three files to `./e2e-results/`:

* `summary.json` — machine-readable
* `summary.txt` — human-readable text
* `summary.md` — Markdown table for inclusion in PRs / docs

## WeChat MP dedicated suite

```bash
python scripts/run_wechat_mp_e2e.py --from-fixture \
  --wechat-cookies "wxuin=abc; pass_ticket=xyz" --dry-run
```

The WeChat MP suite accepts a fixture file
(`scripts/wechat_mp_urls.txt`) and supports all three cookie
injection paths: inline, file, and environment variable.

## Pytest E2E

```bash
VIDEO2TEXT_E2E=1 python -m pytest douyin_batch/tests/test_e2e_real_urls.py
```

Without the `VIDEO2TEXT_E2E=1` env var, the E2E tests are
skipped so unit-test runs do not hit the network.

## Recording a release

```bash
python scripts/capture_e2e_recording.py --output-dir ./e2e-recording
```

This runs both suites, copies the reports into a timestamped
directory, and adds a top-level `README.md` describing the
package.  Attach the resulting `e2e-recording/<timestamp>/` to
your GitHub release so reviewers can audit behaviour without
running the suite themselves.

## Adding a new platform to the matrix

1. Add a `E2ECase` to `E2E_TEST_URLS` in
   `scripts/run_real_e2e.py`.
2. Add a fixture URL to `scripts/wechat_mp_urls.txt` (only if
   the platform is WeChat-shaped).
3. Update `docs/e2e-results.md` with the new expected output.

## Known flakiness

| Platform | Flaky bit | Mitigation |
|----------|-----------|------------|
| YouTube | occasional 403 from certain ASNs | retry with `--player-clients` rotation |
| Douyin | cookies expire quickly | refresh cookies before each run |
| Xiaohongshu | Playwright headless detection | use `headless=False` once for cookie priming |
| WeChat MP | IP rate limit (1023/1024) | wait 30 min or rotate IP |
