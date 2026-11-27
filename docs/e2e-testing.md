# E2E testing

End-to-end tests exercise the full pipeline against real public
URLs.  They are **opt-in** because they require network access
and may be flaky on third-party platforms.

## Quick start

```bash
# Detection only (no downloads, no Whisper)
python scripts/e2e/run_real_e2e.py --url-only

# Full pipeline
python scripts/e2e/run_real_e2e.py
```

The runner writes three files to `./e2e-results/`:

* `summary.json` — machine-readable
* `summary.txt` — human-readable text
* `summary.md` — Markdown table for inclusion in PRs / docs

## WeChat MP dedicated suite

```bash
python scripts/e2e/run_wechat_mp_e2e.py --from-fixture \
  --wechat-cookies "wxuin=abc; pass_ticket=xyz" --dry-run
```

The WeChat MP suite accepts a fixture file
(`scripts/e2e/wechat_mp_urls.txt`) and supports all three cookie
injection paths: inline, file, and environment variable.

## Pytest E2E

```bash
MEDIASCRIBE_E2E=1 python -m pytest tests/test_e2e_real_urls.py
```

Without the `MEDIASCRIBE_E2E=1` env var, the E2E tests are
skipped so unit-test runs do not hit the network.

## Recording a release

```bash
python scripts/e2e/capture_e2e_recording.py --output-dir ./e2e-recording
```

This runs both suites, copies the reports into a timestamped
directory, and adds a top-level `README.md` describing the
package.  Attach the resulting `e2e-recording/<timestamp>/` to
your GitHub release so reviewers can audit behaviour without
running the suite themselves.

## Offline clean-environment first-success gate

The release gate is separate from the public-URL suites. It installs the built
wheel into a fresh venv, uses a fixed local golden WAV and a pre-staged
faster-whisper `small` cache, and runs with model/network access disabled:

```bash
python scripts/gates/first_success_gate.py \
  --wheel dist/mediascribe-3.4.0a1-py3-none-any.whl \
  --wheelhouse wheelhouse \
  --model-cache model-cache \
  --corpus tests/fixtures/golden/us016 \
  --work-dir output/first-success
```

The gate verifies corpus SHA-256 hashes, runs `doctor --offline`, transcribes
the local WAV with explicit `faster-whisper/small/cpu`, validates Markdown body,
required semantics and metadata, then checks local events and metrics. The
wheelhouse and fresh-venv install must include the complete
`mediascribe[faster-whisper]` extra, not only the base wheel. It writes
`first-success-report.json` and `first-success-report.md`. Resource shortages
return `RESOURCE_GUARD_BLOCKED`; they are not reported as success.

The release workflow prepares the offline wheelhouse and fixed model revision,
runs this gate, and makes both PyPI and GitHub publishing depend on it.

## Real faster-whisper interruption and resume gate

The resume gate uses a parent process and two fresh worker processes. The parent
starts real `faster-whisper/small/cpu/zh` inference on the fixed golden WAV,
waits until at least two segments are durably committed, terminates the worker,
reopens the same SQLite workspace, and starts a new worker that explicitly
resumes the interrupted job:

```bash
python scripts/gates/faster_whisper_resume_gate.py \
  --corpus tests/fixtures/golden/us016 \
  --model-cache model-cache \
  --out output/faster-whisper-resume/run-001 \
  --interrupt-after 2 \
  --timeout 1800 \
  --min-available-mb 2048
```

The model cache must already contain the fixed faster-whisper `small` snapshot.
The gate sets `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and `PIP_NO_INDEX=1`;
it never authorizes a model download. A resource-blocked preflight writes a
`blocked` report and exits with code 2 without starting a worker.

A passing run writes `resume-report.json`, `job-events.json`,
`final-segments.json`, `final-snapshot.json`, the resumed Markdown, and separate
original/resume worker logs. The report records old/new attempt IDs, checkpoint
cursor, resume start, wall-clock, RTF, sampled peak RSS/VRAM, corpus hashes,
hardware/software provenance, and the effective decode payload. Validation fails
closed unless the durable prefix is unchanged, a new suffix exists, boundary
text is not duplicated, segment indexes/timelines remain continuous, the final
segment covers the audio tail within five seconds, and the durable event sequence
contains interruption, explicit resume, and terminal success.

The unit suite uses fake workers with real SQLite to test controlled termination,
restart recovery, resume execution, report validation, and resource-blocked
behavior without loading a model. Real ASR evidence is produced only when the
command above runs on a qualified runner.

## Adding a new platform to the matrix

1. Add a `E2ECase` to `E2E_TEST_URLS` in
   `scripts/e2e/run_real_e2e.py`.
2. Add a fixture URL to `scripts/e2e/wechat_mp_urls.txt` (only if
   the platform is WeChat-shaped).
3. Run the matrix locally and record the expected output for the new case
   (E2E result tables are local-only, not committed).

## Known flakiness

| Platform | Flaky bit | Mitigation |
|----------|-----------|------------|
| YouTube | occasional 403 from certain ASNs | retry with `--player-clients` rotation |
| Douyin | cookies expire quickly | refresh cookies before each run |
| Xiaohongshu | Playwright headless detection | use `headless=False` once for cookie priming |
| WeChat MP | IP rate limit (1023/1024) | wait 30 min or rotate IP |
