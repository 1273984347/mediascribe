# Video2Text — Agent-friendly Makefile
#
# Works on Linux, macOS, and Windows (with GNU Make from MinGW / Chocolatey /
# Scoop, or via WSL). Every target is also a no-op `python -m` command so
# AI agents can run them directly without `make`.
#
# Common targets:
#   make help       list all targets
#   make install    install runtime deps
#   make dev        install runtime + dev deps
#   make test       run unit tests (must all pass: 38 tests)
#   make lint       run ruff (lint + format check)
#   make format     auto-format with ruff
#   make demo       run demo_v3.py
#   make clean      remove __pycache__ and build artefacts
#   make verify     import + syntax + tests (agent smoke test)
#   make up         one-click: start Web UI in foreground (Ctrl-C to stop)
#   make up-daemon  one-click: start Web UI in background, return immediately
#   make down       one-click: stop the daemonised Web UI
#   make status     one-click: show whether the Web UI is running
#   make logs       one-click: tail -f the .one-click.log log file
#   make docker-up  one-click: start Web UI via Docker compose

PY      ?= python
PIP     ?= $(PY) -m pip
BLACK   ?= $(PY) -m black
ISORT   ?= $(PY) -m isort
FLAKE8  ?= $(PY) -m flake8
PYTEST  ?= $(PY) -m pytest

REQUIREMENTS      := requirements.txt
REQUIREMENTS_DEV  := requirements-dev.txt

.PHONY: help install dev test test-verbose lint format demo clean verify all install-browser up up-daemon down status logs docker-up

help:
	@echo "Video2Text — available targets:"
	@echo "  install         pip install -r $(REQUIREMENTS)"
	@echo "  dev             pip install -r $(REQUIREMENTS) -r $(REQUIREMENTS_DEV)"
	@echo "  install-browser playwright install chromium (for Douyin/Bilibili scraping)"
	@echo "  test            run unit tests (run_tests.py)"
	@echo "  test-verbose    run unit tests with verbose output"
	@echo "  test-i18n       run i18n integration tests"
	@echo "  test-cross      run cross-platform tests"
	@echo "  lint            ruff check + ruff format --check"
	@echo "  format          ruff check --fix + ruff format"
	@echo "  demo            run demo_v3.py"
	@echo "  clean           remove __pycache__ and build artefacts"
	@echo "  verify          import + syntax + tests (agent smoke test)"
	@echo ""
	@echo "One-click Web UI (Docker + browser extension):"
	@echo "  up              start Web UI in foreground (Ctrl-C to stop)"
	@echo "  up-daemon       start Web UI in background, return immediately"
	@echo "  down            stop the daemonised Web UI"
	@echo "  status          show whether the Web UI is running"
	@echo "  logs            tail -f the .one-click.log log file"
	@echo "  docker-up       start Web UI via Docker compose"

install:
	$(PIP) install -r $(REQUIREMENTS)

dev:
	$(PIP) install -r $(REQUIREMENTS) -r $(REQUIREMENTS_DEV)

install-browser:
	$(PIP) install playwright
	$(PY) -m playwright install chromium

test:
	$(PY) run_tests.py

test-verbose:
	$(PY) run_tests.py -v

test-i18n:
	$(PY) douyin_batch/tests/test_i18n_integration.py

test-cross:
	$(PY) douyin_batch/tests/test_cross_platform.py

test-imports:
	$(PY) douyin_batch/tests/test_imports.py

test-syntax:
	$(PY) douyin_batch/tests/test_syntax.py

lint:
	$(FLAKE8) --max-line-length=100 video2text douyin_batch
	$(BLACK) --check --diff video2text douyin_batch

format:
	$(RUFF) check --fix video2text douyin_batch scripts examples
	$(RUFF) format video2text douyin_batch scripts examples

demo:
	$(PY) demo_v3.py

clean:
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name *.egg-info -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf build dist .coverage htmlcov 2>/dev/null || true
	@echo "Cleaned."

verify: test-syntax test-imports test
	@echo ""
	@echo "✅ All verifications passed."

all: dev lint test

# ---------------------------------------------------------------------------
# One-click Web UI launcher
# ---------------------------------------------------------------------------
# All targets below delegate to scripts/one_click_up.py, which is the
# canonical implementation.  On Windows the .ps1 wrapper is equivalent;
# on Linux/macOS the .sh wrapper is equivalent.

up:
	$(PY) scripts/one_click_up.py up

up-daemon:
	$(PY) scripts/one_click_up.py up --daemon

down:
	$(PY) scripts/one_click_up.py stop

status:
	-$(PY) scripts/one_click_up.py status

logs:
	@if [ -f .one-click.log ]; then tail -f .one-click.log; else echo "(no .one-click.log yet)"; fi

docker-up:
	$(PY) scripts/one_click_up.py up --daemon --mode docker

# Help AI agents discover the right command
# Equivalent without `make`:
#   pip install -r requirements.txt
#   python run_tests.py
#   python -m black --check video2text douyin_batch
#   python -m flake8 --max-line-length=100 video2text douyin_batch
