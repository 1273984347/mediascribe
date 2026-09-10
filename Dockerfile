# MediaScribe Web UI — one-click image.
# Multi-stage build to keep the runtime image small.
# The image only ships the Web UI (no Whisper weights) so it can
# be run on a CPU-only host or a GPU host with `--gpus all`.

# ----- builder: install mediascribe with [web,ocr] extras -----
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_NO_COMPILE=1

# ffmpeg is required for the chunked transcriber + audio extract
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY mediascribe ./mediascribe
COPY scripts ./scripts
# douyin_batch/ + douyin_batch_v3.py are declared in pyproject.toml
# ([tool.setuptools] packages / py-modules); without them setuptools
# fails with "package directory 'douyin_batch' does not exist" and the
# ``mediascribe-batch`` entry point would be broken.
COPY douyin_batch ./douyin_batch
COPY douyin_batch_v3.py ./
# --no-cache-dir keeps the build context small; PIP_NO_CACHE_DIR=1 above
# also stops pip from keeping a local cache inside the layer.
RUN pip install --prefix=/install --no-cache-dir ".[web,ocr]"

# ----- runtime: minimal image with the installed package -----
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime deps: ffmpeg, tini (proper signal handling), curl (healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg tini curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# Create a non-root user (``mediascribe``, uid 1000) and own both the
# application directory and the workspace.  Running as root inside a
# container is convenient but a real-world attacker surface: any
# container-escape vulnerability is amplified if the exploit lands
# with uid 0.
RUN groupadd --system --gid 1000 mediascribe && \
    useradd  --system --uid 1000 --gid mediascribe \
             --home-dir /workspace --shell /usr/sbin/nologin \
             --comment "mediascribe service account" mediascribe && \
    mkdir -p /workspace && \
    chown -R mediascribe:mediascribe /workspace /app
WORKDIR /app
ENV MEDIASCRIBE_WORKSPACE=/workspace

USER mediascribe
EXPOSE 8000

# HEALTHCHECK lives inside the image (in addition to any docker-compose
# override) so `docker ps` shows healthy/unhealthy state without extra
# tooling.  curl is already in the runtime image for this reason.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --silent --fail --max-time 4 http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "mediascribe.web.app", "--host", "0.0.0.0", "--port", "8000"]
