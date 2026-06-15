# syntax=docker/dockerfile:1
# estleg-mcp: remote (streamable-HTTP) MCP server over the Estonian Legal Ontology.
#
# Mirrors the Seadusloome image conventions (python:3.13-slim + uv) so it
# deploys the same way on Coolify. The ~1.5 GB ontology corpus is NOT baked
# into the image; docker/entrypoint.sh clones/pulls it onto a persistent
# volume at boot (LFS skipped — estleg reads only the per-file JSON blobs).
#
# Coolify: set Base Directory = mcp_server, Dockerfile = docker/Dockerfile,
# attach a persistent volume at /data, and set ESTLEG_TOKEN (+ the domain).

FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml README.md ./
COPY estleg_mcp/ estleg_mcp/
# Build a self-contained venv with the package + its HTTP extra (uvicorn).
RUN uv venv /app/.venv && \
    VIRTUAL_ENV=/app/.venv uv pip install --no-cache ".[http]"

FROM python:3.13-slim
# git + git-lfs: the entrypoint clones the corpus repo at boot. curl: healthcheck.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git git-lfs curl && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" \
    ESTLEG_TRANSPORT=http \
    ESTLEG_HOST=0.0.0.0 \
    ESTLEG_PORT=8000 \
    ESTLEG_CORPUS=/data/estonian-legal-ontology \
    ESTLEG_CORPUS_REPO=https://github.com/henrikaavik/estonian-legal-ontology.git \
    ESTLEG_CORPUS_BRANCH=main
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${ESTLEG_PORT}/healthz" || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
