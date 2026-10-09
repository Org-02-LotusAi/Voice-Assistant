"""Create the two seed Voicebox cloned profiles ("Ava Warm", "James Calm"). Idempotent by name.

Run (no project venv needed):
  uv run --no-project --python 3.12 --with httpx python scripts/seed_voicebox_profiles.py [--generate]

--generate additionally renders one Turbo sentence for "Ava Warm" through Voicebox's own
POST /generate and prints wall time, audio duration and real-time factor (RTF = wall / audio).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
VOICES = ROOT / "assets" / "voices"
SEEDS = [
    ("Ava Warm", "ava_warm", "Synthetic seed voice (Windows SAPI Zira). Authorized by construction."),
    ("James Calm", "james_calm", "Synthetic seed voice (Windows SAPI David). Authorized by construction."),
]


def ensure_profile(client: httpx.Client, name: str, stem: str, description: str) -> str:
    resp = client.get("/profiles")
    resp.raise_for_status()
    existing = next((p for p in resp.json() if p["name"] == name), None)
    if existing is None:
        resp = client.post(
            "/profiles",
            json={
                "name": name,
                "description": description,
                "language": "en",
                "voice_type": "cloned",
                "default_engine": "chatterbox_turbo",
            },
        )
        resp.raise_for_status()
        existing = resp.json()
        print(f"created profile {name}")
    else:
        print(f"profile {name} already exists")
    profile_id = existing["id"]

    samples = client.get(f"/profiles/{profile_id}/samples")
    samples.raise_for_status()
    if not samples.json():
        wav = VOICES / f"{stem}.wav"
        text = (VOICES / f"{stem}.txt").read_text(encoding="utf-8").strip()
        with wav.open("rb") as fh:
            up = client.post(
                f"/profiles/{profile_id}/samples",
                files={"file": (wav.name, fh, "audio/wav")},
                data={"reference_text": text},
                timeout=300,
            )
        up.raise_for_status()
        print(f"  uploaded sample {wav.name}")
    return profile_id


def generate(client: httpx.Client, profile_id: str, text: str) -> None:
    t0 = time.perf_counter()
    resp = client.post(
        "/generate",
        json={"profile_id": profile_id, "text": text, "language": "en", "engine": "chatterbox_turbo"},
    )
    resp.raise_for_status()
    gen_id = resp.json()["id"]
    while True:
        status = client.get(f"/generate/{gen_id}/status", timeout=900).text
        # SSE stream ends when the generation is completed/failed; last data line is the final state.
        last = [ln for ln in status.splitlines() if ln.startswith("data: ")][-1]
        state = json.loads(last[6:])
        if state["status"] in ("completed", "failed"):
            break
    wall = time.perf_counter() - t0
    if state["status"] == "failed":
        sys.exit(f"generation failed: {state.get('error')}")
    audio = client.get(f"/audio/{gen_id}")
    audio.raise_for_status()
    duration = state["duration"]
    print(
        f"generation {gen_id}: wall={wall:.1f}s audio={duration:.2f}s RTF={wall / duration:.2f} "
        f"({len(audio.content)} bytes wav)"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"))
    ap.add_argument("--generate", action="store_true", help="also run one Turbo generation for Ava Warm")
    ap.add_argument(
        "--text",
        default="Hello, this is Ava. It is a pleasure to speak with you today, and I am glad to help.",
    )
    args = ap.parse_args()
    with httpx.Client(base_url=args.url, timeout=60) as client:
        ids = {}
        for name, stem, desc in SEEDS:
            ids[name] = ensure_profile(client, name, stem, desc)
            print(f"{name}: {ids[name]}")
        if args.generate:
            generate(client, ids["Ava Warm"], args.text)


if __name__ == "__main__":
    main()
