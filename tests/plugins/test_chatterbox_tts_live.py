"""Live: real sidecar via the plugin. CPU synthesis is slow (first chunk ~10-15 s), so a very short text and generous timeouts."""
import asyncio
import os
import time

import httpx
import pytest
from livekit.agents import APIConnectOptions

from service015.voice.livekit_tts import ChatterboxTTS

ENGINE_URL = os.environ.get("VOICE_ENGINE_URL", "http://127.0.0.1:17494")
PROFILE_ID = os.environ.get("LIVE_TTS_PROFILE_ID", "6326829e-38ae-475f-aedb-5c293aa43e51")  # seeded "Ava Warm"


def _up() -> bool:
    try:
        r = httpx.get(ENGINE_URL + "/health", timeout=3)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


@pytest.mark.skipif(not _up(), reason="voice engine not reachable/ready")
def test_live_plugin_streams_audio_from_sidecar():
    async def go():
        firsts = []
        t = ChatterboxTTS(base_url=ENGINE_URL, profile_id=PROFILE_ID, read_timeout_s=300, on_first_audio=firsts.append)
        t0 = time.monotonic()
        cs = t.synthesize("Hello, this is Ava.", conn_options=APIConnectOptions(max_retry=0, timeout=30))
        audio = 0.0
        async for ev in cs:
            audio += ev.frame.duration
        await cs.aclose()
        await t.aclose()
        return t0, time.monotonic() - t0, firsts, audio

    t0, wall, firsts, audio = asyncio.run(asyncio.wait_for(go(), 400))
    print(f"\nlive plugin: wall={wall:.1f}s first_audio_after={firsts[0] - t0:.1f}s audio={audio:.2f}s")
    assert len(firsts) == 1 and audio > 0.8
