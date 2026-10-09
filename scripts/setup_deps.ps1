<#
Installs the external dependencies for Service 015 on Windows:
  - clones Voicebox into deps/voicebox at the pinned commit
  - creates deps/voicebox/.venv (Python 3.12, uv) with torch (CPU wheels, or CUDA 12.8 wheels with -Device cuda)
  - installs the minimal chatterbox_turbo dependency set + chatterbox-tts==0.1.7 (--no-deps)
  - pulls the pinned livekit-server Docker image
Usage: powershell -File scripts/setup_deps.ps1 [-Device cpu|cuda] [-Full]
  -Full  try the complete backend/requirements.txt (LuxTTS, kokoro, ...) instead of the minimal set.
         On Windows this fails (misaki[ja] -> pyopenjtalk build); see docs/DEPENDENCIES.md.
#>
param(
    [ValidateSet('cpu', 'cuda')][string]$Device = 'cpu',
    [switch]$Full
)
$ErrorActionPreference = 'Stop'
$VoiceboxSha = '8af7efe62fab8d33e2a5dfbedabaa45f68a5184f'
$ChatterboxVersion = '0.1.7'
$LivekitImage = 'livekit/livekit-server:v1.13.7'
$root = Split-Path -Parent $PSScriptRoot
$vb = Join-Path $root 'deps/voicebox'
$py = Join-Path $vb '.venv/Scripts/python.exe'

function Run([string]$what, [scriptblock]$cmd) {
    Write-Host "==> $what"
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "failed: $what (exit $LASTEXITCODE)" }
}

New-Item -ItemType Directory -Force -Path (Join-Path $root 'deps') | Out-Null
if (-not (Test-Path (Join-Path $vb '.git'))) {
    Run 'clone voicebox' { git clone https://github.com/jamiepine/voicebox $vb }
}
Run "checkout voicebox $VoiceboxSha" { git -C $vb -c advice.detachedHead=false checkout $VoiceboxSha }

if (-not (Test-Path $py)) { Run 'create venv' { uv venv --python 3.12 (Join-Path $vb '.venv') } }

$torchIndex = if ($Device -eq 'cuda') { 'https://download.pytorch.org/whl/cu128' } else { 'https://download.pytorch.org/whl/cpu' }
Run "torch ($Device)" { uv pip install --python $py torch torchaudio --index-url $torchIndex }

if ($Full) {
    Run 'full backend requirements' { uv pip install --python $py -r (Join-Path $vb 'backend/requirements.txt') --extra-index-url $torchIndex --index-strategy unsafe-best-match }
} else {
    Run 'minimal chatterbox_turbo requirements' { uv pip install --python $py -r (Join-Path $PSScriptRoot 'voicebox-requirements.chatterbox-turbo.txt') --extra-index-url $torchIndex --index-strategy unsafe-best-match }
}
Run "chatterbox-tts==$ChatterboxVersion (--no-deps)" { uv pip install --python $py --no-deps "chatterbox-tts==$ChatterboxVersion" }
Run 'torch sanity' { & $py -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())" }

Run "docker pull $LivekitImage" { docker pull $LivekitImage }
Write-Host 'Done. Next: scripts/run_deps.ps1 -Warm   (first -Warm downloads ~3.9 GB of Chatterbox Turbo weights)'
