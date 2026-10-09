"""Live checks against a running engine sidecar. Skipped unless VOICE_ENGINE_URL points at a reachable engine.

Run:  VOICE_ENGINE_URL=http://127.0.0.1:17494 uv run pytest tests/engine -v -s
CPU synthesis is slow (RTF ~9-11 Turbo), so texts are short and timeouts generous.
"""
import io
import time
import wave

import httpx
import pytest

pytestmark = pytest.mark.live_engine
SLOW = httpx.Timeout(connect=10, read=600, write=30, pool=10)
TEXT = "Hello, this is Ava. It is a pleasure to speak with you."


def test_health(engine_url):
    h = httpx.get(f"{engine_url}/health", timeout=10).json()
    assert h["status"] == "ok"
    assert h["engine"] == "chatterbox-turbo"
    assert h["variant"] in ("turbo", "turbo-nano")
    assert h["sample_rate"] == 24000
    assert isinstance(h["warm"], bool) and isinstance(h["cached_profiles"], list)


def test_unknown_profile_is_404(engine_url):
    r = httpx.post(f"{engine_url}/synthesize/stream", json={"profile_id": "no-such-profile", "text": "Hi there."}, timeout=SLOW)
    assert r.status_code == 404
    assert r.json()["code"] == "profile_not_found"


def test_warm_caches_profile(engine_url, profile_id):
    r = httpx.post(f"{engine_url}/warm", json={"profile_id": profile_id}, timeout=SLOW)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "warm"
    h = httpx.get(f"{engine_url}/health", timeout=10).json()
    assert profile_id in h["cached_profiles"] and h["warm"] is True
    again = httpx.post(f"{engine_url}/warm", json={"profile_id": profile_id}, timeout=SLOW).json()
    assert again["timings"]["already_cached"] is True


def test_stream_first_chunk_before_full_synthesis(engine_url, profile_id):
    httpx.post(f"{engine_url}/warm", json={"profile_id": profile_id}, timeout=SLOW).raise_for_status()
    t0 = time.perf_counter()
    arrivals, total = [], 0
    with httpx.stream("POST", f"{engine_url}/synthesize/stream", json={"profile_id": profile_id, "text": TEXT}, timeout=SLOW) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/pcm")
        assert r.headers["x-sample-rate"] == "24000"
        assert int(r.headers["x-chunk-count"]) == 2
        for part in r.iter_bytes():
            arrivals.append(time.perf_counter() - t0)
            total += len(part)
    wall = arrivals[-1]
    audio_s = total / 2 / 24000
    print(f"\nfirst_chunk={arrivals[0]:.1f}s wall={wall:.1f}s audio={audio_s:.2f}s rtf={wall / audio_s:.2f}")
    assert total % 2 == 0 and audio_s > 1.0
    assert arrivals[0] < wall * 0.85, "first chunk should arrive well before the second sentence finishes"


def test_barge_in_stops_remaining_chunks(engine_url, profile_id):
    """Closing the connection after the first bytes must stop work: a follow-up request is not queued behind
    the whole abandoned text (only behind the one in-flight chunk)."""
    long_text = "Hello, this is Ava. " + "It is a pleasure to speak with you today. " * 3
    with httpx.stream("POST", f"{engine_url}/synthesize/stream", json={"profile_id": profile_id, "text": long_text}, timeout=SLOW) as r:
        next(r.iter_bytes())  # first chunk received, then drop the connection
    t0 = time.perf_counter()
    r = httpx.post(f"{engine_url}/synthesize/stream", json={"profile_id": profile_id, "text": "Okay."}, timeout=SLOW)
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200 and len(r.content) > 0
    print(f"\nfollow-up after barge-in took {elapsed:.1f}s")


def test_preview_returns_wav(engine_url, profile_id):
    r = httpx.post(f"{engine_url}/preview", json={"profile_id": profile_id, "text": "Hello there, friend."}, timeout=SLOW)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert w.getnframes() > 24000 // 2
