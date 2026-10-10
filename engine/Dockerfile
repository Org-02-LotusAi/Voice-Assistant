# Voicebox headless backend + Chatterbox voice-engine sidecar, one image, two processes (see docker-compose.yml:
# `voicebox` runs backend.main, `engine` runs engine/chatterbox_server.py; both share the model cache volume).
#
#   CPU  : docker build -f engine/Dockerfile --build-arg DEVICE=cpu  -t service015-engine:cpu  .
#   CUDA : docker build -f engine/Dockerfile --build-arg DEVICE=cuda -t service015-engine:cuda .
#          (run with the NVIDIA container toolkit: `--gpus all`, or the compose `gpu` profile)
#
# Pins mirror docs/DEPENDENCIES.md and scripts/setup_deps.sh (keep them in sync):
#   Voicebox 8af7efe62fab8d33e2a5dfbedabaa45f68a5184f, chatterbox-tts 0.1.7 (PyPI, what Voicebox uses),
#   chatterbox GitHub master 5de7a54aa4e5e2baadb0182dde554908b48b85c2 (engine only; has the `nano` option).
# Torch comes from the CPU or cu128 wheel index; cu128 wheels ship their own CUDA runtime libraries, the nvidia/cuda
# base adds the cuDNN runtime and the standard driver/library layout for the NVIDIA container runtime.
ARG DEVICE=cpu

FROM python:3.12.12-slim-bookworm AS base-cpu
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04 AS base-cuda

FROM base-${DEVICE} AS final
ARG DEVICE=cpu
ARG VOICEBOX_SHA=8af7efe62fab8d33e2a5dfbedabaa45f68a5184f
ARG CHATTERBOX_VERSION=0.1.7
ARG CHATTERBOX_MASTER_SHA=5de7a54aa4e5e2baadb0182dde554908b48b85c2

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv

ENV DEBIAN_FRONTEND=noninteractive \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/hf \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates build-essential ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10016 vbx \
    && mkdir -p /opt/venv /opt/python /opt/voicebox /opt/chatterbox-src /models /var/lib/voicebox-store /app \
    && chown -R vbx:vbx /opt/venv /opt/python /opt/voicebox /opt/chatterbox-src /models /var/lib/voicebox-store /app /home/vbx

USER vbx
WORKDIR /opt

RUN uv venv --python 3.12 /opt/venv

# Voicebox at the pinned commit.
RUN git clone https://github.com/jamiepine/voicebox /opt/voicebox \
    && git -C /opt/voicebox -c advice.detachedHead=false checkout ${VOICEBOX_SHA}

# Torch first (device-specific wheel index), then the Voicebox set that is known to install, then chatterbox-tts.
COPY --chown=vbx:vbx scripts/voicebox-requirements.chatterbox-turbo.txt /tmp/voicebox-requirements.txt
RUN if [ "$DEVICE" = "cuda" ]; then IDX=https://download.pytorch.org/whl/cu128; else IDX=https://download.pytorch.org/whl/cpu; fi \
    && uv pip install --python /opt/venv/bin/python torch torchaudio --index-url "$IDX" \
    && uv pip install --python /opt/venv/bin/python -r /tmp/voicebox-requirements.txt \
         --extra-index-url "$IDX" --index-strategy unsafe-best-match \
    && uv pip install --python /opt/venv/bin/python --no-deps "chatterbox-tts==${CHATTERBOX_VERSION}"

# Chatterbox GitHub master for the engine (PyPI 0.1.7 has no `nano`); the engine prefers CHATTERBOX_SRC on sys.path.
RUN git clone https://github.com/resemble-ai/chatterbox /opt/chatterbox-src \
    && git -C /opt/chatterbox-src -c advice.detachedHead=false checkout ${CHATTERBOX_MASTER_SHA}

COPY --chown=vbx:vbx engine /app/engine

# Voicebox data dir must NOT contain a path component named "data" (docs/DEPENDENCIES.md trap 1).
ENV CHATTERBOX_SRC=/opt/chatterbox-src/src \
    VOICE_ENGINE_DEVICE=${DEVICE} \
    VOICE_ENGINE_HOST=0.0.0.0 \
    VOICE_ENGINE_PORT=17494 \
    VOICEBOX_URL=http://voicebox:17493
VOLUME ["/models", "/var/lib/voicebox-store"]
EXPOSE 17493 17494

HEALTHCHECK --interval=20s --timeout=5s --start-period=120s --retries=10 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:17494/health', timeout=4).status == 200 else 1)"

# Default process = the engine; compose overrides the command for the voicebox service.
CMD ["python", "/app/engine/chatterbox_server.py"]
