# Video2Text Web UI — one-click image.
# Multi-stage build to keep the runtime image small.
# The image only ships the Web UI (no Whisper weights) so it can
# be run on a CPU-only host or a GPU host with `--gpus all`.

# ----- builder: install video2text with [web,ocr] extras -----
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
COPY video2text ./video2text
COPY scripts ./scripts
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

# Create a non-root user (``video2text``, uid 1000) and own both the
# application directory and the workspace.  Running as root inside a
# container is convenient but a real-world attacker surface: any
# container-escape vulnerability is amplified if the exploit lands
# with uid 0.
RUN groupadd --system --gid 1000 video2text && \
    useradd  --system --uid 1000 --gid video2text \
             --home-dir /workspace --shell /usr/sbin/nologin \
             --comment "video2text service account" video2text && \
    mkdir -p /workspace && \
    chown -R video2text:video2text /workspace /app
WORKDIR /app
ENV VIDEO2TEXT_WORKSPACE=/workspace

USER video2text
EXPOSE 8000

# HEALTHCHECK lives inside the image (in addition to any docker-compose
# override) so `docker ps` shows healthy/unhealthy state without extra
# tooling.  curl is already in the runtime image for this reason.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --silent --fail --max-time 4 http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "video2text.web.app", "--host", "0.0.0.0", "--port", "8000"]
