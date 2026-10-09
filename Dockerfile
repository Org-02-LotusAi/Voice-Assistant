# Service 015 API + runtime worker supervisor (the API process spawns `python -m service015.runtime.worker`).
# Build:  docker build -t service015 .
# The voice engine (Chatterbox) and Voicebox live in engine/Dockerfile; this image has no torch.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HOME=/home/service015

# git: the Service Expert records repo/branch/SHA provenance when it ingests a bind-mounted checkout.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10015 service015 \
    && mkdir -p /app /var/lib/service015 /opt/venv /home/service015/.cache \
    && chown -R service015:service015 /app /var/lib/service015 /opt/venv /home/service015

USER service015
WORKDIR /app

# Dependencies first (cached layer), then the project.
COPY --chown=service015:service015 pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY --chown=service015:service015 src ./src
COPY --chown=service015:service015 openapi.yaml ./openapi.yaml
COPY --chown=service015:service015 docs ./docs
COPY --chown=service015:service015 scripts ./scripts
COPY --chown=service015:service015 assets/voices ./assets/voices
RUN uv sync --frozen --no-dev

ENV SERVICE015_HOST=0.0.0.0 \
    SERVICE015_PORT=8015 \
    SERVICE015_DB_PATH=/var/lib/service015/service015.db
VOLUME /var/lib/service015
EXPOSE 8015

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8015/health', timeout=4).status == 200 else 1)"

CMD ["service015-api"]
