# Publishing v3.1.0 to GitHub Releases

This document walks through releasing `mediascribe` v3.1.0.
The release artefact is **GitHub Releases** (not PyPI) because
the project's primary distribution channel is `git clone +
pip install -e .` for the open-source audience.

## 1. Pre-flight checks

```bash
# Run the full test suite (275+ tests)
python -m pytest douyin_batch/tests/ -q

# Lint
python -m ruff check mediascribe/ douyin_batch/ scripts/ extension/ docs_site/

# Verify build metadata
python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project'])"

# Validate mkdocs config
python -c "import yaml; yaml.unsafe_load(open('mkdocs.yml'))"

# Validate the extension manifest
python -c "import json; m=json.load(open('extension/manifest.json')); print(m['version'])"
```

All four must be green before proceeding.

## 2. Build sdist + wheel

```bash
pip install build twine
rm -rf build/ dist/ mediascribe.egg-info/
python -m build --sdist --wheel --outdir dist/
ls -la dist/
# dist/mediascribe-3.1.0.tar.gz
# dist/mediascribe-3.1.0-py3-none-any.whl
```

## 3. Validate with twine

```bash
python -m twine check dist/*
# Checking dist/mediascribe-3.1.0-py3-none-any.whl: PASSED
# Checking dist/mediascribe-3.1.0.tar.gz: PASSED
```

## 4. (Optional) Upload to TestPyPI for a smoke install

```bash
python -m twine upload --repository testpypi dist/*
python -m venv /tmp/mediascribe-verify
source /tmp/mediascribe-verify/bin/activate
pip install --index-url https://test.pypi.org/simple/ mediascribe==3.1.0
mediascribe --help
```

## 5. Commit + tag

```bash
git add -A
git commit -m "v3.1.0 — plugins, chunking, OTel, browser extension, mkdocs, CI matrix"
git tag -a v3.1.0 -m "v3.1.0 — plugin system, long-video chunking, OTel mini-SDK, Web UI, browser extension, mkdocs site, 5x3 CI matrix, 275 tests"
git push origin main
git push origin v3.1.0
```

## 6. Create the GitHub Release

### Option A — via the gh CLI

```bash
gh release create v3.1.0 \
  --title "v3.1.0 — Plugin system, long-video chunking, OTel, Web UI, browser extension, mkdocs, CI matrix" \
  --notes-file RELEASE_NOTES_v3.1.0.md \
  dist/mediascribe-3.1.0.tar.gz \
  dist/mediascribe-3.1.0-py3-none-any.whl
```

### Option B — via the GitHub web UI

1. Visit `https://github.com/<owner>/mediascribe/releases/new`
2. Choose tag `v3.1.0`
3. Title: `v3.1.0 — Plugin system, long-video chunking, OTel, Web UI, browser extension, mkdocs, CI matrix`
4. Body: paste the contents of `RELEASE_NOTES_v3.1.0.md`
5. Attach `dist/mediascribe-3.1.0.tar.gz` and `dist/mediascribe-3.1.0-py3-none-any.whl`
6. Click "Publish release"

## 7. Generate the E2E recording asset (recommended)

```bash
# On a machine with full network access:
python scripts/capture_e2e_recording.py --output-dir ./e2e-recording
# Attach e2e-recording/<timestamp>/.zip to the release as a separate asset
```

## 8. Post-release

- [ ] Verify the GitHub Release assets download correctly
- [ ] Verify the GitHub Pages site builds (after enabling the workflow)
- [ ] Bump `version` in `pyproject.toml` to `3.1.1.dev0` for next cycle
- [ ] Announce on social media / Discord / mailing list

## Common errors

### `twine: 403 Forbidden`

You're not the maintainer of the `mediascribe` package on PyPI.
Skip the PyPI step and use GitHub Releases only.

### `Module not found: mediascribe.egg-info`

```bash
rm -rf build/ dist/ mediascribe.egg-info/ && python -m build
```

### `mkdocs.yml: !!python/name not understood`

PyYAML `safe_load` cannot parse the `!!python/name:` tags that
mkdocs uses for `pymdownx` extensions.  Use `unsafe_load` or
install `mkdocs` and let it validate the file itself.

### `extension/manifest.json: missing icon`

Generate icons via the helper in `extension/README.md` (1×1
transparent PNG is enough for CI; replace with real artwork
before publishing to the Chrome Web Store).
