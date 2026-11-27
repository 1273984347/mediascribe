# Getting started

This page walks through the most common workflows. ASR inference runs locally
by default. Fetching a URL and downloading missing model weights require network
access; a local file plus pre-provisioned weights can run fully offline.

## 1. Install

System dependencies:

* `ffmpeg` (and `ffprobe`) on `$PATH` for audio extraction and chunking.
* A writable model cache. The location depends on the engine; see
  [Configuration](configuration.md#model-weight-caches).

### PyPI

```bash
# Minimal install with the default ASR engine
pip install "mediascribe[faster-whisper]"
```

The standard default is **`faster-whisper` + `small`**. If faster-whisper is
not installed, MediaScribe falls back to `whisper` and prints a notice.
WhisperX is never selected merely because it is installed; choose it explicitly
when you need its alignment or diarization features.

Useful combinations:

```bash
pip install "mediascribe[whisperx]"          # opt-in WhisperX engine
pip install "mediascribe[ocr]"               # WeChat MP image-article OCR
pip install "mediascribe[web,faster-whisper]" # Web UI + default ASR engine
pip install "mediascribe[mcp,faster-whisper]" # MCP server + default ASR engine
pip install "mediascribe[dev]"               # tests + lint
```

`[all]` includes the Whisper engines, OCR, MCP, Web UI, and LLM extra, but **not**
`[vad]`. Install `mediascribe[vad]` only for the optional Python chunking API;
it does not change ordinary `transcribe` behaviour.

For browser-based downloaders, install the Playwright browser separately:

```bash
playwright install chromium
```

### Source checkout

```bash
git clone https://github.com/1273984347/mediascribe.git
cd mediascribe
pip install -e ".[faster-whisper]"
```

For the full source/development environment:

```bash
pip install -r requirements.txt
```

`requirements.txt` is an install **shim**, not a pure base-dependency list. It
currently expands to `-e .[web,faster-whisper,whisper,mcp]`.

## 2. Prepare model weights

Installing the package does not download ASR weights, and normal transcription
does not download missing weights by default.

### Online first use

Explicitly authorize the one-time model download:

```bash
python -m mediascribe doctor
python -m mediascribe transcribe "https://www.youtube.com/watch?v=..." \
  --model small --language en --allow-model-download
```

`verify` uses the same opt-in rule:

```bash
python -m mediascribe verify output/transcripts/<transcript>.md
# If the required weights are missing and you are online:
python -m mediascribe verify output/transcripts/<transcript>.md \
  --allow-model-download
```

### Offline use

1. Pre-provision the selected engine's weights in its engine-specific cache.
   `doctor` prints the expected location for missing weights.
2. Confirm offline readiness:

   ```bash
   python -m mediascribe doctor --offline
   ```

3. Transcribe a local file without download authorization:

   ```bash
   python -m mediascribe transcribe "video.mp4" --language zh
   ```

There is no `transcribe --offline` flag. The offline path is pre-provisioned
weights plus the default `--allow-model-download=False` behaviour.

## 3. Transcribe a single video

```bash
python -m mediascribe transcribe "https://www.youtube.com/watch?v=..." \
  --model small --language en

# Local file
python -m mediascribe transcribe "video.mp4" --language zh
```

Useful flags (see `python -m mediascribe transcribe --help` for the full list):
`--engine faster-whisper`, `--timestamps`, `--audio-only`,
`--cross-check small`, `--output`, and `--allow-model-download`.

What you get:

```text
output/
├── downloads/<title>.mp4
├── audio/<title>.wav
├── transcripts/<title>-<timestamp>.md   ← the transcript
├── metadata/<title>-<timestamp>.json    ← engine, segments, errors
└── LATEST.txt                           ← newest default transcript path
```

The default Markdown body follows ASR speech boundaries but has **no timestamp
prefixes**. Add `--timestamps` for per-paragraph `[mm:ss]` markers. A custom
`--output` path does not replace `LATEST.txt`; an archive run may instead point
`LATEST.txt` at its latest summary report.

## 4. Transcribe a WeChat MP article

```bash
python -m mediascribe transcribe "https://mp.weixin.qq.com/s/abc?__biz=..." \
  --wechat-cookies "wxuin=abc123; pass_ticket=xyz"
```

OCR of embedded images is configured per request in the **Web UI** or the
**MCP server** (`ocr_engine`, `save_images`) — the CLI has no OCR flags. See
[WeChat MP](wechat-mp.md) for the full guide.

## 5. Batch from a creator's user page

```bash
python -m mediascribe archive --user "https://www.bilibili.com/..." \
  -n 10 --platform bilibili --lang zh --json
```

The archive output directory contains `summary_<timestamp>.md`,
`results_<timestamp>.json`, and fixed-name latest copies `summary.md` and
`results.json`.

## 6. Web UI

```bash
pip install "mediascribe[web,faster-whisper]"
python -m mediascribe.web.app --port 8000
```

Open `http://127.0.0.1:8000` and paste up to 20 URLs at once. Progress is reported
for six stages — `preflight`, `model_prepare`, `download`, `extract_audio`,
`transcribe`, and `assemble`. It is stage-level progress, not a measured
percentage within the current stage.

## 7. MCP server (for LLM agents)

```bash
pip install "mediascribe[mcp,faster-whisper]"
python -m mediascribe.mcp_server
```

Connect your MCP client (Claude Desktop, Cursor, Cline, etc.) to the stdio
transport. See [MCP server](mcp.md) for the tool list. The current
`transcribe_video` parameters use `source`, `whisper_model`, and `output_dir`
(not `url_or_path`, `model`, or `output`).

## 8. Structured JSON for agents

`--json` and `--bilingual-json` apply to `archive` / `mediascribe-batch`, not to
every CLI command:

```bash
python -m mediascribe archive --user <URL> --json
mediascribe-batch --user <URL> --json
```

Both emit the stable `mediascribe.agent-output/v1` schema.

## 9. Local events and metrics

MediaScribe records privacy-whitelisted lifecycle events in a local SQLite
database. They are not uploaded. The default retention is **90 days**.

```bash
python -m mediascribe events list
python -m mediascribe events export --output output/events.json
python -m mediascribe events metrics
python -m mediascribe events clear
```

`clear` removes event history but preserves the install identity and first-run
marker, so clearing does not create a new installation on the next run.

Metrics count distinct `install_id` values, not runs or event rows. Internal
test events are excluded. Rates use these denominators:

* `submission_rate` = submitted installs / new installations.
* `success_rate` = successful installs / new installations (not submissions).
* `open_rate` = opened installs / successful installs.
* `recovery_success_rate` = recovered installs / recovery-selected installs.

A successful install must have a successful terminal result with an existing,
non-empty artifact and consistent metadata. Cancelled, no-speech, and failed
runs are not successes. A rate is `null` when its denominator is zero; after a
clear, for example, later successes can coexist with `new_installations: 0` and
a `null` `success_rate`.

## 10. Verify the install

```bash
python -m mediascribe doctor
# Strict offline readiness after pre-provisioning weights:
python -m mediascribe doctor --offline
python scripts/run_tests.py
```

`doctor` checks ffmpeg / GPU / engines / model caches in one pass;
`scripts/run_tests.py` forwards to pytest with the CI-matched flags.
