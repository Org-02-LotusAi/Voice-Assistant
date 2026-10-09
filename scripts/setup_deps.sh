#!/usr/bin/env bash
# Installs the external dependencies for Service 015 on Linux (EC2) or Git Bash:
#   clones Voicebox at the pinned commit, creates deps/voicebox/.venv (Python 3.12, uv), installs torch
#   (CPU wheels, or CUDA 12.8 wheels with DEVICE=cuda), the minimal chatterbox_turbo set, chatterbox-tts, and pulls the LiveKit image.
# Usage: DEVICE=cpu|cuda [FULL=1] scripts/setup_deps.sh
#   FULL=1 tries the complete backend/requirements.txt instead of the minimal set.
set -euo pipefail

VOICEBOX_SHA=8af7efe62fab8d33e2a5dfbedabaa45f68a5184f
CHATTERBOX_VERSION=0.1.7
LIVEKIT_IMAGE=livekit/livekit-server:v1.13.7
DEVICE="${DEVICE:-cpu}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VB="$ROOT/deps/voicebox"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) PY="$VB/.venv/Scripts/python.exe" ;;
  *) PY="$VB/.venv/bin/python" ;;
esac

case "$DEVICE" in
  cpu) TORCH_INDEX=https://download.pytorch.org/whl/cpu ;;
  cuda) TORCH_INDEX=https://download.pytorch.org/whl/cu128 ;;
  *) echo "DEVICE must be cpu or cuda" >&2; exit 2 ;;
esac

mkdir -p "$ROOT/deps"
[ -d "$VB/.git" ] || git clone https://github.com/jamiepine/voicebox "$VB"
git -C "$VB" -c advice.detachedHead=false checkout "$VOICEBOX_SHA"

[ -x "$PY" ] || uv venv --python 3.12 "$VB/.venv"

uv pip install --python "$PY" torch torchaudio --index-url "$TORCH_INDEX"
if [ "${FULL:-0}" = "1" ]; then
  uv pip install --python "$PY" -r "$VB/backend/requirements.txt" --extra-index-url "$TORCH_INDEX" --index-strategy unsafe-best-match
else
  uv pip install --python "$PY" -r "$ROOT/scripts/voicebox-requirements.chatterbox-turbo.txt" --extra-index-url "$TORCH_INDEX" --index-strategy unsafe-best-match
fi
uv pip install --python "$PY" --no-deps "chatterbox-tts==$CHATTERBOX_VERSION"
"$PY" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"

docker pull "$LIVEKIT_IMAGE"
echo "Done. Next: WARM=1 scripts/run_deps.sh   (first warm-up downloads ~3.9 GB of Chatterbox Turbo weights)"
