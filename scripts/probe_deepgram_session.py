"""Live multi-turn probe for Deepgram mode (STT + Aura TTS): drives a scripted human through several turns in its OWN API process
(temp DB, port 18015, DEBUG runtime log) and reports, per turn, what the LLM generated, what the human actually heard, and whether
the reply was cut short.

  uv run python scripts/probe_deepgram_session.py [--turns q_greeting,q_name,...] [--tag NAME]

Requires LiveKit up and DEEPGRAM_API_KEY / LLM_API_KEY in .env. Needs no Chatterbox or Voicebox. Stop the main API first (two
workers would compete for the same dispatches). Output: evidence/deepgram-<tag>/ (summary.json, runtime log).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests.e2e.driver import Ctx  # noqa: E402
from tests.e2e.stack import ApiProcess, Config  # noqa: E402

DEFAULT_TURNS = "q_greeting,q_name,q_company,q_role,q_long_answer,turn_01,turn_02,turn_03,turn_04,turn_05,q_long_answer,turn_06"


async def echo_loop(human, gain: float, delay_ms: int) -> None:
    """Simulated speaker-to-mic leak: replay each audio frame the agent publishes into the human's mic, attenuated and delayed."""
    import numpy as np
    from livekit import rtc

    from tests.e2e.audio import STT_RATE

    source = await human._ensure_mic(STT_RATE)
    lock = asyncio.Lock()
    inner = source.capture_frame

    async def guarded(frame) -> None:  # one writer at a time: `say` and this loop share the mic source
        async with lock:
            await inner(frame)

    source.capture_frame = guarded  # type: ignore[method-assign]
    saying = [False]
    say = human.say

    async def say_flagged(*a, **kw):
        saying[0] = True
        try:
            return await say(*a, **kw)
        finally:
            saying[0] = False

    human.say = say_flagged  # type: ignore[method-assign]
    seen = 0
    step = STT_RATE // 100
    queue: list[tuple[float, bytes]] = []
    while True:
        frames = human.frames
        while seen < len(frames):
            f = frames[seen]
            seen += 1
            pcm = (np.frombuffer(f.pcm, dtype=np.int16).astype(np.float32) * gain).clip(-32768, 32767).astype(np.int16).tobytes()
            queue.append((f.t + delay_ms / 1000.0, pcm))
        now = time.monotonic()
        while queue and queue[0][0] <= now:
            _, pcm = queue.pop(0)
            if len(pcm) == step * 2 and not saying[0]:
                await source.capture_frame(rtc.AudioFrame(pcm, STT_RATE, 1, step))
        await asyncio.sleep(0.005)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", default=DEFAULT_TURNS)
    ap.add_argument("--tag", default=time.strftime("%Y%m%dT%H%M%S"))
    ap.add_argument("--voice", default="deepgram:aura-2-thalia-en")
    ap.add_argument("--echo", type=float, default=0.0, help="feed the agent's own audio back into the mic at this gain (0 = off)")
    ap.add_argument("--barge", action="store_true", help="run the barge-in acceptance scenario (a real interruption) instead of the turns")
    ap.add_argument("--echo-delay-ms", type=int, default=150)
    args = ap.parse_args()

    cfg = Config(ava_voice=args.voice)
    work = Path(tempfile.mkdtemp(prefix="s015-dg-probe-"))
    evidence = ROOT / "evidence" / f"deepgram-{args.tag}"
    raw = evidence / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    api = ApiProcess(cfg, work, extra_env={
        "SERVICE015_STT_PROVIDER": "deepgram", "SERVICE015_TTS_PROVIDER": "deepgram", "SERVICE015_RUNTIME_LOG": "DEBUG",
    })
    api.start("probe")
    ctx = Ctx(cfg, api, evidence, raw)
    summary: list[dict] = []
    try:
        await api.wait_healthy()
        if args.barge:
            from tests.e2e import scenarios

            res = await scenarios.scenario_barge_in(ctx)
            res.finalize()
            for c in res.checks:
                print(c.name, "->", "PASS" if c.ok else "FAIL", c.detail)
            await scenarios.end_session(ctx, "ava")
            return 0
        ident = await ctx.ensure_identity("ava")
        iid = ident["identity_id"]
        await ctx.launch_ready(iid)
        human, info = await ctx.join(iid)
        print("greeting:", info.get("greeting_transcript"), flush=True)
        echo_task = asyncio.create_task(echo_loop(human, args.echo, args.echo_delay_ms)) if args.echo > 0 else None
        for n, stem in enumerate(args.turns.split(",")):
            r = await ctx.ask(human, iid, stem, "probe", f"{n:02d}-{stem}")
            row = r.row or {}
            entry = {
                "turn": n, "stem": stem, "said": r.human_says, "llm_text": row.get("agent_text"), "heard": r.reply.transcript,
                "reply_started": r.reply.started, "reply_complete": r.reply.complete,
                "audio_s": round(len(r.reply.pcm) / 2 / 16000, 2), "interrupted": row.get("interrupted"),
            }
            summary.append(entry)
            print(json.dumps(entry), flush=True)
        if echo_task is not None:
            echo_task.cancel()
        await ctx.leave(human)
        await ctx.stop(iid)
    finally:
        await ctx.cleanup()
        await api.stop()
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        for name in ("runtime-worker.rotating.log",):
            if (work / name).exists():
                shutil.copy(work / name, evidence / name)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
