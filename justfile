# Video2Text — Justfile (alternative to Makefile for AI agents)
# Install just: https://github.com/casey/just
# Run `just` to list all recipes.

set shell := ["powershell", "-NoProfile", "-Command"]  # Windows-friendly default
# On Linux/macOS, uncomment:
# set shell := ["bash", "-uc"]

# Use Python from PATH
python := "python"

# List available recipes
default:
    @just --list

# Install runtime dependencies
install:
    {{python}} -m pip install -r requirements.txt

# Install runtime + dev dependencies
dev:
    {{python}} -m pip install -r requirements.txt -r requirements-dev.txt

# Install Playwright browser (for Douyin/Bilibili scraping)
install-browser:
    {{python}} -m pip install playwright
    {{python}} -m playwright install chromium

# Run all unit tests (38 tests, must all pass)
test:
    {{python}} run_tests.py

# Run unit tests with verbose output
test-verbose:
    {{python}} run_tests.py -v

# Run i18n integration tests
test-i18n:
    {{python}} douyin_batch/tests/test_i18n_integration.py

# Run cross-platform tests
test-cross:
    {{python}} douyin_batch/tests/test_cross_platform.py

# Lint with ruff (lint + format check)
lint:
    {{python}} -m ruff check video2text douyin_batch scripts examples
    {{python}} -m ruff format --check --diff video2text douyin_batch scripts examples

# Auto-format with ruff
format:
    {{python}} -m ruff check --fix video2text douyin_batch scripts examples
    {{python}} -m ruff format video2text douyin_batch scripts examples

# Run the v3 feature demo
demo:
    {{python}} demo_v3.py

# Remove __pycache__, build artefacts
clean:
    Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Recurse -Directory -Filter .pytest_cache | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Recurse -File -Filter "*.pyc" | Remove-Item -Force -ErrorAction SilentlyContinue

# Smoke test for AI agents: syntax + imports + tests
verify: test-syntax test-imports test
    @echo ""
    @echo "All verifications passed."

# Test that all .py files parse
test-syntax:
    {{python}} douyin_batch/tests/test_syntax.py

# Test that all modules import
test-imports:
    {{python}} douyin_batch/tests/test_imports.py

# ---------------------------------------------------------------------------
# One-click Web UI launcher
# ---------------------------------------------------------------------------
# Delegates to scripts/one_click_up.py.

# Start Web UI in the foreground (Ctrl-C to stop)
up:
    {{python}} scripts/one_click_up.py up

# Start Web UI as a daemon (returns immediately)
up-daemon:
    {{python}} scripts/one_click_up.py up --daemon

# Stop a daemonised Web UI
down:
    {{python}} scripts/one_click_up.py stop

# Show whether the Web UI is running
status:
    {{python}} scripts/one_click_up.py status

# Tail the .one-click.log log file (Ctrl-C to stop)
logs:
    Get-Content -Path .one-click.log -Wait -ErrorAction SilentlyContinue

# Start the Web UI via Docker compose
docker-up:
    {{python}} scripts/one_click_up.py up --daemon --mode docker
