"""Audio-path probe: does Chatterbox mispronounce a phrase, or does Moonshine mishear it?

Synthesizes phrases through the engine (POST /preview, the same synthesis path the runtime streams), transcribes the result with
the same Moonshine model the harness uses, and writes the raw wavs + transcripts. Because the TEXT is known exactly, any
mismatch is the audio path (TTS pronunciation or STT hearing), never the LLM.

  uv run python -m tests.e2e.probe_tts --out evidence/<ts>/audio_path_probe.json [--repeat 3] [--profile <voicebox profile id>]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import wave
from pathlib import Path

import httpx
import numpy as np
from livekit import rtc

from .audio import STT_RATE, Transcriber, write_wav
from .expectations import mentions

PHRASES = (
    ("I work for Safi.", "Safi"),
    ("My name is Ava.", "Ava"),
    ("I'm an Account Specialist here at Safi.", "Safi"),
)


def to_16k(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    if rate == STT_RATE:
        return pcm
    resampler = rtc.AudioResampler(rate, STT_RATE, num_channels=1)
    out = bytearray()
    for frame in resampler.push(bytearray(pcm)):
        out += bytes(frame.data)
    for frame in resampler.flush():
        out += bytes(frame.data)
    return bytes(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine-url", default="http://127.0.0.1:17494")
    ap.add_argument("--profile", default="6326829e-38ae-475f-aedb-5c293aa43e51", help="Ava Warm by default")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw-dir", default=None, help="where to keep the wavs (default: next to --out under raw/)")
    args = ap.parse_args()
    out = Path(args.out)
    raw = Path(args.raw_dir) if args.raw_dir else out.parent.parent / "raw" / out.parent.name / "probe"
    tr = Transcriber()
    import asyncio

    results = []
    for text, fact in PHRASES:
        for i in range(args.repeat):
            r = httpx.post(args.engine_url + "/preview", json={"profile_id": args.profile, "text": text}, timeout=300)
            r.raise_for_status()
            pcm = to_16k(r.content)
            write_wav(raw / f"{len(results):02d}.wav", pcm)
            heard = asyncio.run(tr.transcribe(pcm))
            results.append({"text": text, "fact": fact, "run": i + 1, "transcript": heard, "fact_heard": mentions(heard, fact), "audio_s": round(len(pcm) / 2 / STT_RATE, 2)})
            print(results[-1], flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    bad = [r for r in results if not r["fact_heard"]]
    print(f"{len(results) - len(bad)}/{len(results)} syntheses transcribed with the fact intact")
    sys.exit(0)


if __name__ == "__main__":
    main()
