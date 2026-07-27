# Design Intelligence System
#
# Default build is CPU-only and small (~250 MB): analysis, the design-features
# embedder, LanceDB and the MCP server. Pass BUILD_EXTRAS to pull in a torch
# stack when you want SigLIP2 or ColPali:
#
#   docker build -t design-intel .
#   docker build --build-arg BUILD_EXTRAS="server,torch" -t design-intel:torch .

FROM python:3.11-slim AS base

ARG BUILD_EXTRAS=server

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DI_INDEX_PATH=/data/index \
    DI_CACHE_DIR=/data/cache \
    DI_MEMORY_PATH=/data/memory \
    HF_HOME=/data/models

WORKDIR /app

# libgomp is needed by pyarrow/lancedb; nothing else is required at runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install ".[${BUILD_EXTRAS}]"

COPY tools ./tools

# references are mounted read-only; the index, cache and taste memory persist
VOLUME ["/data", "/references"]

RUN useradd --create-home --uid 10001 design \
    && mkdir -p /data /references \
    && chown -R design:design /data /app
USER design

# stdio is the default so `docker run -i` works directly as an MCP server
ENV DI_TRANSPORT=stdio
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD design-intel stats --json > /dev/null || exit 1

ENTRYPOINT ["design-intel"]
CMD ["serve", "--transport", "stdio"]
