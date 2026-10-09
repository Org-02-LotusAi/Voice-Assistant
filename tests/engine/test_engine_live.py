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
    """After the client drops the stream, the engine reports fewer chunks generated than requested, and a follow-up
    request's first chunk arrives within a bounded time (<= the abandoned in-flight chunk plus its own first chunk)."""
    long_text = ("Hello, this is Ava, and I am happy to help. It is a pleasure to speak with you today. "
                 "Please tell me what you need. I will do my best to assist you right away.")
    t0 = time.perf_counter()
    with httpx.stream("POST", f"{engine_url}/synthesize/stream", json={"profile_id": profile_id, "text": long_text}, timeout=SLOW) as r:
        rid, total = r.headers["x-request-id"], int(r.headers["x-chunk-count"])
        assert total >= 4
        next(r.iter_bytes())  # first chunk received, then drop the connection
    first_chunk_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    with httpx.stream("POST", f"{engine_url}/synthesize/stream", json={"profile_id": profile_id, "text": "Okay, sure."}, timeout=SLOW) as r2:
        it = r2.iter_bytes()
        next(it)
        follow_first_s = time.perf_counter() - t1
        for _ in it:
            pass
    # Worst case: wait for the in-flight 2nd chunk (<= 120 chars, here about 3x the ~35-char first chunk) + own chunk.
    print(f"\nabandoned first chunk {first_chunk_s:.1f}s; follow-up first chunk {follow_first_s:.1f}s")
    assert follow_first_s < 5 * first_chunk_s + 5
    st = httpx.get(f"{engine_url}/requests/{rid}", timeout=10).json()
    assert st["state"] == "cancelled"
    assert st["chunks_done"] < st["chunks_total"] and st["chunks_done"] <= 2, st


def test_preview_returns_wav(engine_url, profile_id):
    r = httpx.post(f"{engine_url}/preview", json={"profile_id": profile_id, "text": "Hello there, friend."}, timeout=SLOW)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert w.getnframes() > 24000 // 2
