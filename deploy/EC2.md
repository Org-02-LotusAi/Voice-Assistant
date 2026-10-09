# Deploying Service 015 on an EC2 GPU host

**Verification status of this document.** Nothing here has been executed on EC2: the development machine is a CPU-only
Windows laptop with no AWS access in this task. What WAS verified locally: the `service015` image builds and serves
`/health`, the UI, and spawns the runtime worker against a LiveKit container built from `deploy/livekit.yaml`;
`docker compose --profile cpu|gpu|sip|tls config` validates. Every EC2-, NVIDIA- and CUDA-specific step below is
**unverified** and is marked as such. Treat the first deployment as the verification and fix this file from what you learn.

## 1. Architecture on one host

```
Internet
  |  443 (Caddy, TLS)  -> service015 :8015   (API + Workbench UI over https; getUserMedia needs https)
  |  443 (Caddy, TLS)  -> livekit    :7880   (wss signalling)
  |  7881/tcp, 7882/udp -> livekit             (WebRTC media, direct, NOT through Caddy)
  |  5060 + 10000-20000/udp -> livekit/sip     (only with the `sip` profile, for Service 014 / phones)
Docker network: redis, livekit, service015 (spawns the runtime worker), voicebox + engine (GPU container)
```

One host runs everything. The runtime worker (Moonshine STT, Silero VAD, LLM client) runs inside `service015`; the
GPU is used only by the `engine` container (Chatterbox Turbo TTS). The LLM is remote (Groq or any OpenAI-compatible endpoint).

## 2. Instance sizing

| Instance | GPU | VRAM | Notes |
|---|---|---|---|
| `g4dn.xlarge` | 1x T4 | 16 GB | 4 vCPU, 16 GB RAM. Cheapest option; fine for 1-2 concurrent Identities. Chatterbox Turbo (about 3.9 GB of weights) fits with room to spare. |
| `g5.xlarge` | 1x A10G | 24 GB | 4 vCPU, 16 GB RAM. Faster; preferred for the latency evidence run. |

Both: 100 GB gp3 root volume (images about 15 GB with the CUDA layer, model cache about 6 GB, logs, DB). RAM matters as much
as VRAM: each launched Identity holds Moonshine and Silero in memory (about 1-2 GB), and `SERVICE015_MAX_ACTIVE_IDENTITIES`
(default 4) caps the number. On 16 GB RAM keep it at 2-3 until measured. These figures are estimates, not measurements.

## 3. Host preparation (unverified)

Use Ubuntu 22.04. Either start from an AWS Deep Learning Base AMI (Ubuntu 22.04; NVIDIA driver and Docker preinstalled,
skip 3.1-3.2) or a plain Ubuntu 22.04 AMI:

```bash
# 3.1 NVIDIA driver (plain AMI only) and reboot
sudo apt-get update && sudo apt-get install -y ubuntu-drivers-common && sudo ubuntu-drivers install && sudo reboot
nvidia-smi                      # must list the T4 / A10G with a CUDA 12.x capable driver (>= 570 for CUDA 12.8 wheels)

# 3.2 Docker Engine + compose plugin: follow https://docs.docker.com/engine/install/ubuntu/ then
sudo usermod -aG docker $USER && newgrp docker

# 3.3 NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu22.04 nvidia-smi    # must show the GPU
```

The `engine/Dockerfile` CUDA variant uses `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04` and torch wheels from the
`cu128` index (the same index `scripts/setup_deps.sh` uses for `DEVICE=cuda`). The driver on the host must support CUDA 12.8.
If you must use an older driver, change the `cu128` index and the base image tag together in `engine/Dockerfile`
and `scripts/setup_deps.*` (and re-pin the base on a CUDA 12.x tag your driver supports).

## 4. Networking

Elastic IP (static public IP) and two DNS names pointing to it, for example `app.example.com` (UI + API) and
`lk.example.com` (LiveKit signalling). Security group inbound:

| Port | Proto | Source | Purpose |
|---|---|---|---|
| 22 | tcp | your admin IP only | SSH |
| 80, 443 | tcp | 0.0.0.0/0 | Caddy (ACME HTTP-01 + https/wss) |
| 7881 | tcp | 0.0.0.0/0 | LiveKit WebRTC over TCP (fallback for clients that cannot use UDP) |
| 7882 | udp | 0.0.0.0/0 | LiveKit WebRTC media (single muxed UDP port; this deployment sets `udp_port: 7882`, no 50000-60000 range) |
| 5060 | udp + tcp | the SIP provider's addresses | SIP signalling (`sip` profile only) |
| 10000-20000 | udp | the SIP provider's addresses (or 0.0.0.0/0) | RTP media (`sip` profile only; range set in `docker-compose.yml`) |

Do NOT open 7880, 8015, 17493, 17494 or 6379 publicly: they are reachable only through Caddy or the Docker network
(`SERVICE015_BIND=127.0.0.1` keeps 8015 on loopback; Voicebox and the engine publish loopback-only ports for host scripts).
If you run LiveKit with the default `use_external_ip` discovery instead, the UDP range LiveKit documents (50000-60000) would
apply; this stack pins a single UDP port and advertises `LIVEKIT_NODE_IP` instead.
A TURN server is not part of this stack: clients behind UDP-blocking firewalls use the TCP 7881 fallback.

## 5. Configure and start

```bash
git clone <this repo> service015 && cd service015
cp .env.example .env && chmod 600 .env
$EDITOR .env
```

Edit at least:

```
SERVICE015_API_KEYS=<long-random-admin>:admin,<long-random-operator>:operator,<long-random-viewer>:viewer
LLM_API_KEY=<groq or compatible key>
LIVEKIT_API_KEY=<generate>           # livekit-server generate-keys, or any random key id
LIVEKIT_API_SECRET=<generate, >= 32 chars>
LIVEKIT_NODE_IP=<the Elastic IP>
LIVEKIT_PUBLIC_URL=wss://lk.example.com
SERVICE015_PUBLIC_URL=https://app.example.com
SERVICE015_DOMAIN=app.example.com
LIVEKIT_DOMAIN=lk.example.com
COMPOSE_PROFILES=gpu,tls             # add ,sip for phones
```

Start (the first start builds the images: the CUDA engine image takes a long time, plan 20+ minutes; unmeasured):

```bash
docker compose up -d --build
docker compose ps
curl -s https://app.example.com/health        # {"status":"ok",...,"voicebox":"ok","voice_engine":"ok"} once models are loaded
```

`COMPOSE_PROFILES` in `.env` is what makes plain `docker compose ...` pick the stack; you can instead pass
`--profile gpu --profile tls` on every command. `LIVEKIT_URL` inside the `service015` container is overridden by
compose to `ws://livekit:7880`; browsers get `LIVEKIT_PUBLIC_URL` in join tokens.

### 5.1 First-run model downloads and warm-up

All downloads go to named volumes (`model-cache`, `service015-cache`, `voicebox-store`) and survive restarts. Measured on
the CPU laptop (`docs/DEPENDENCIES.md`) and therefore only indicative for GPU:

| Step | What | Size / time |
|---|---|---|
| Chatterbox Turbo weights (engine, first start) | `ResembleAI/chatterbox-turbo` from Hugging Face | 3.9 GB; about 3 min download on the laptop; model load about 35-55 s CPU, expected seconds on GPU (unverified) |
| Moonshine + Silero (first Identity launch) | downloaded by the runtime job process | small (hundreds of MB); about 10-20 s setup per job process on CPU |
| Voice conditionals per profile | `prepare_conditionals` | about 4-7 s per voice on CPU; cached afterwards |

Wait for `docker compose logs -f engine-gpu` to show the model ready and `curl -s http://127.0.0.1:17494/health` to report
`"status":"ok"` before the first launch.

### 5.2 Seed voices and a first Identity

```bash
curl -s http://127.0.0.1:17493/health                              # Voicebox up (loopback port)
uv run --no-project --python 3.12 --with httpx python scripts/seed_voicebox_profiles.py     # needs uv on the host
docker compose exec service015 python scripts/authorize_seed_voices.py
```

(`scripts/authorize_seed_voices.py` runs inside the `service015` container, which holds the database; `assets/voices` is
copied into the image. The seed script talks to Voicebox on the host loopback port.) Then use the Workbench at
`https://app.example.com/` (paste an operator key) or the API per `docs/INTEGRATION.md`.

To load the knowledge for the Service Expert, bind-mount a git checkout so provenance (repo, branch, SHA) resolves:
`docker compose run --rm -v "$PWD:/repo:ro" service015 service015-expert ingest --root /repo` (unverified: git may report
"dubious ownership" for the mounted tree; if so run `ingest` from the host with the same `SERVICE015_DB_PATH` volume or set
`git config --global --add safe.directory /repo` first).

## 6. Latency evidence on the GPU host (TEST 11)

This is the run that decides the SOW latency ceiling (P95 T0->T4 <= 1,500 ms; target P50 <= 700 ms). On the host,
not in a container (the harness imports `tests/`), with the compose stack up and `LIVEKIT_URL=ws://localhost:7880`,
`LIVEKIT_API_KEY/SECRET`, `LLM_API_KEY`, `VOICEBOX_URL`, `VOICE_ENGINE_URL` set in the host `.env`:

```bash
uv sync
uv run python scripts/run_acceptance.py --only self_knowledge,isolation,ten_turn,latency,barge_in,default_room,restart
```

Do NOT pass `--allow-latency-fail` here. Evidence lands in `evidence/<UTC timestamp>/` (`latency.md` has the P50/P95/MAX
table and the per-component breakdown). Notes: (a) the acceptance harness starts its own API process on `--api-port` with a
temporary database, so it does not touch the compose `service015` data; stop the compose `service015` container first if
port 8015 is the harness port, or pass another `--api-port`; (b) the `failure` scenario restarts the engine through
`scripts/run_engine.ps1` (Windows only) and does not work against the containerised engine on Linux, so it is excluded above;
(c) none of this has been run on a GPU yet.

## 7. LiveKit Cloud alternative (unverified)

Skip the `livekit`, `redis`, `sip` and `caddy`-for-LiveKit parts and use LiveKit Cloud's SFU:

1. Create a project at LiveKit Cloud and note the project URL (`wss://<project>.livekit.cloud`), API key and secret.
2. In `.env`: `LIVEKIT_URL=wss://<project>.livekit.cloud`, `LIVEKIT_PUBLIC_URL=` (empty: the same URL serves browsers),
   `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`. In `docker-compose.yml` the `service015` service forces `LIVEKIT_URL: ws://livekit:7880`:
   remove that line (and the `livekit` dependency) or use an override file for a Cloud deployment.
3. Start only what is needed: `docker compose up -d --build service015 voicebox-gpu engine-gpu` (profile `gpu` active).
   Only 80/443 (and 22) need to be open; media is between clients and LiveKit Cloud, and the `engine` sidecar is reached
   only from `service015`.
4. Telephony: LiveKit Cloud provides SIP (inbound/outbound trunks and dispatch rules) as a managed product, so the `sip`
   profile is not needed. Service 014 creates its trunks there; set `SERVICE014_MODE=livekit_sip` and
   `SERVICE014_SIP_TRUNK_ID=<outbound trunk id>` and follow `docs/PHONE_DOGFOOD.md`. Verify the exact SIP product setup in
   LiveKit's current docs; this repo has not been run against LiveKit Cloud.
5. The GPU engine remains self-hosted either way (LiveKit Cloud does not run Chatterbox).

## 8. Operations

- Logs: `docker compose logs -f service015 engine-gpu voicebox-gpu livekit`. The runtime worker logs to
  `/var/lib/service015/runtime-worker.log` inside the `service015` volume.
- Update: `git pull && docker compose up -d --build`. Data (DB, models, voices) lives in named volumes. Back up
  `service015-data` (SQLite) and `voicebox-store` (cloned voice profiles and reference samples).
- Single API process per database: do not scale `service015` to multiple replicas.
- Secrets live only in `.env` (mode 600, git-ignored). Rotate by editing `.env` and `docker compose up -d`.
- Cost control: stop the instance when idle; the GPU is the main cost. Models stay on the EBS volume.
