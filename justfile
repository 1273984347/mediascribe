# MediaScribe — Justfile (alternative to Makefile for AI agents)
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

# Run unit tests (same selection as CI's unit job)
test:
    {{python}} -m pytest -m "not integration and not network" --ignore=tests/test_e2e_real_urls.py -q

# Run unit tests with verbose output
test-verbose:
    {{python}} -m pytest -m "not integration and not network" --ignore=tests/test_e2e_real_urls.py

# Run the full integration/e2e suite (needs MEDIASCRIBE_E2E=1 + network)
test-e2e:
    {{python}} -m pytest tests/test_e2e_real_urls.py

# Run i18n integration tests
test-i18n:
    {{python}} -m pytest tests/test_i18n_integration.py

# Run cross-platform tests
test-cross:
    {{python}} -m pytest tests/test_cross_platform.py

# Lint with ruff (same parameters as CI)
lint:
    {{python}} -m ruff check .
    {{python}} -m ruff format --check .

# Auto-format with ruff
format:
    {{python}} -m ruff check --fix .
    {{python}} -m ruff format .

# Pre-push gate: lint + fast pytest (scripts/pre_push_gate.py)
gate:
    {{python}} scripts/pre_push_gate.py

# Gate then push — use this instead of raw `git push`
push: gate
    git push

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
    {{python}} -m pytest tests/test_syntax.py

# Test that all modules import
test-imports:
    {{python}} -m pytest tests/test_imports.py

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
