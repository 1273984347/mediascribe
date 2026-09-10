# Video2Text v3.2.0 — Planning Draft

> Status: **draft for community review**.  Last updated 2026-06-06.
>
> v3.1.0 just shipped (547 tests / 97 % coverage).  This document
> collects candidate work for v3.2.0, grouped by area, with rough
> effort / impact ratings.

## How to read this

* **Effort** — XS (< 1 day) · S (1-3 days) · M (3-7 days) · L (1-2 weeks) · XL (2+ weeks)
* **Impact** — Low · Medium · High · Critical
* **Tier** — must / should / could / won't

## Tier 1 — Must-have (ship-blockers for v3.2.0)

None.  v3.1.0 already meets all declared v3.1.0 goals.  v3.2.0 is a
**feature release**, not a stability release.

## Tier 2 — Should-have (v3.2.0 headline features)

### 1. VAD-based long-video chunking
**Effort** M · **Impact** High

* Add `silero-vad` as an optional extra
* Replace fixed-window chunking with VAD-aware boundaries when
  `audio_utils.split_by_vad(audio_path)` returns multiple segments
* Falls back to fixed-window when VAD is disabled / unavailable
* Tests: silence-only, all-speech, mixed; boundary detection
  accuracy; 5 s overlap still applied
* Update [docs_site/chunking.md](chunking.md) — remove "No VAD-based
  splitting" from Limitations

### 2. Persistent chunk + download cache
**Effort** M · **Impact** High

* Reuse the existing `DownloadCache` and `ProcessCache` but route
  storage to a workspace-pinned directory (e.g. `~/.cache/video2text/`)
* SHA-256 keyed content addressing for downloads; mtime + size keyed
  for chunks
* `--cache-clear` / `--cache-info` CLI flags
* 30-day TTL with LRU eviction; honours `XDG_CACHE_HOME`
* Update [docs_site/performance.md](performance.md) and
  [docs_site/chunking.md](chunking.md) — remove "not persistent" notes

### 3. Profile CLI 透出
**Effort** S · **Impact** Medium

* New `python -m video2text.profile_cli <run.jsonl>` command
* Reads `@profile_step` JSONL output and renders a Markdown / JSON report
* Aggregates by stage (`download`, `transcribe`, `merge`, `export`)
* Supports `--top N`, `--by-stage`, `--since YYYY-MM-DD` filters
* Optionally `python -m video2text.profile_cli --watch` for live tailing
* Adds docs section in [docs_site/performance.md](performance.md)

### 4. CI matrix split (lint / unit / integration)
**Effort** S · **Impact** Medium

* Reorganise [`.github/workflows/test.yml`](.github/workflows/test.yml)
  into three jobs that run in parallel:
  * `lint` — ruff + bandit + mypy (≈ 15 s)
  * `unit` — `pytest -m "not integration"` (≈ 60 s)
  * `integration` — `pytest -m "integration"` (≈ 90 s)
* Required jobs (`lint` + `unit`) gate merges; `integration` is a
  scheduled nightly + manual trigger
* `pyproject.toml` adds markers: `integration`, `slow`, `network`
* Expected PR feedback: 15-30 s (was 175 s)

## Tier 3 — Could-have (nice-to-have, time permitting)

### 5. WhisperX default for word-level timestamps
**Effort** S · **Impact** Medium

* `Settings(whisper_engine="whisperx")` is currently opt-in
* Make `whisperx` the default when installed; fall back to `whisper`
  when not
* Detect via `importlib.util.find_spec("whisperx")`
* Add `--prefer-engine` flag for explicit override
* Tests: default selection + override

### 6. Plugin registry CLI
**Effort** S · **Impact** Low / Medium

* `python -m video2text.plugins list` — enumerate installed
  Downloader / Transcriber / URLTransformer plugins
* `python -m video2text.plugins info <name>` — show source, class,
  supported platforms / engine
* `python -m video2text.plugins verify` — sanity-check each plugin's
  `supports()` method
* Doc-only, no behaviour change

### 7. Plugin marketplace (single source of truth)
**Effort** L · **Impact** Medium

* Curated index of community plugins in `docs_site/plugin-marketplace.md`
* Each entry: name, repo, version, install command, supported
  platforms, screenshot, last-updated
* Manual submission via PR
* "Featured" badge for entries with ≥ 50 stars and CI green
* **Deferred to v3.3** unless there is strong community interest
* v3.2 ships just the index page and contribution guide

### 8. Web UI: per-stage progress + cancellation
**Effort** M · **Impact** High (UX)

* `WebSocket /ws/progress/<job_id>` stream emits per-stage updates
  (download %, transcribe %, merge %)
* Frontend shows a 3-bar progress widget
* `POST /api/jobs/<job_id>/cancel` flips a flag the pipeline watches
  between stages
* Updates [docs_site/web-ui.md](web-ui.md)

### 9. Lightweight transcript diff (CLI)
**Effort** S · **Impact** Low

* `python -m video2text.diff OLD.md NEW.md` highlights added / removed /
  changed lines
* Useful for: re-running the same video after engine upgrades
* Pure stdlib (no extra deps)

### 10. Multi-engine consensus mode (experimental)
**Effort** L · **Impact** High (accuracy)

* Run two engines (e.g. `whisper` + `whisperx`) on the same audio
* Diff transcripts; flag segments where the two disagree
* Optional `--consensus=strict` writes only segments both engines
  agree on
* Heavier compute cost (2×); opt-in only

## Tier 4 — Won't-have in v3.2.0

* **Cloud / SaaS mode** — explicitly out of scope; the project is
  offline-first
* **Real-time streaming transcription** — would require a complete
  re-architecture
* **Mobile app** — no native iOS / Android clients planned
* **GUI rewrite** (Electron, Tauri) — not a priority while the CLI
  and Web UI are stable

## Suggested milestone split

| Milestone | Scope                                                          | Target |
|-----------|----------------------------------------------------------------|--------|
| 3.2.0a    | Tier 2 items 1-2 (VAD + persistent cache)                     | T+2 weeks |
| 3.2.0b    | Tier 2 items 3-4 (profile CLI + CI matrix split)              | T+3 weeks |
| 3.2.0     | Tier 3 item 5 (WhisperX default) + Tier 3 item 6 (plugin CLI) | T+4 weeks |
| 3.2.1     | Tier 3 items 7-8 (marketplace page + WS progress)             | T+5 weeks |

## Open questions for the community

1. Should `whisperx` become the default?  Adds an install cost
   (`pip install torch torchaudio`) that may surprise new users.
2. Where should the persistent cache live?  `~/.cache/video2text/`
   (XDG), or workspace-relative (easier to clean up)?
3. Should the marketplace be a static docs page (Tier 3 item 7) or
   a proper registry (deferred to v3.3+)?
4. VAD: opt-in or opt-out default?  VAD is slightly slower on
   silence-only audio.

---

**Total Tier 2 work**: 4 items · M + M + S + S = roughly 2-3 weeks of
focused engineering.

**Test budget**: maintain 95 %+ coverage.  Budget: 30-50 new tests
across all of v3.2.0.
