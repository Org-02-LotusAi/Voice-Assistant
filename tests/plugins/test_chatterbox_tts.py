import asyncio
import time

import httpx
import pytest
from livekit.agents import APIConnectionError, APIConnectOptions, APIError, APIStatusError

from service015.voice.livekit_tts import ChatterboxTTS

ONE_SECOND = b"\x01\x02" * 24000  # 1 s of s16le mono 24 kHz
NO_RETRY = APIConnectOptions(max_retry=0, timeout=5.0)


class GatedStream(httpx.AsyncByteStream):
    """Yields the given parts; each part waits for its gate so the test controls arrival timing."""

    def __init__(self, parts, gates, fail_with=None):
        self.parts, self.gates, self.fail_with = parts, gates, fail_with
        self.closed = asyncio.Event()

    async def __aiter__(self):
        for part, gate in zip(self.parts, self.gates):
            await gate.wait()
            yield part
        if self.fail_with:
            raise self.fail_with

    async def aclose(self):
        self.closed.set()


class FakeEngine:
    def __init__(self, stream, state="complete", status=200, headers=None):
        self.stream, self.state, self.status = stream, state, status
        self.headers = {"content-type": "audio/pcm", "x-sample-rate": "24000", "x-request-id": "rq1", "x-chunk-count": "2"}
        self.headers.update(headers or {})
        self.posts, self.gets = [], []

    def transport(self):
        async def handle(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                self.posts.append(request.read())
                if self.status != 200:
                    return httpx.Response(self.status, json={"code": "profile_not_found", "detail": "no"})
                return httpx.Response(200, headers=self.headers, stream=self.stream)
            self.gets.append(request.url.path)
            done = self.state == "complete"
            return httpx.Response(
                200,
                json={
                    "request_id": "rq1",
                    "state": self.state,
                    "chunks_total": 2,
                    "chunks_done": 2 if done else 1,
                    "code": None if done else "synthesis_failed",
                    "detail": "x",
                },
            )

        return httpx.MockTransport(handle)


def make_tts(engine, **kw):
    return ChatterboxTTS(base_url="http://engine.test", profile_id="vp-ava", transport=engine.transport(), **kw)


def test_pushes_audio_incrementally_and_reports_first_audio():
    async def go():
        gates = [asyncio.Event(), asyncio.Event()]
        stream = GatedStream([ONE_SECOND, ONE_SECOND], gates)
        engine = FakeEngine(stream)
        seen = []
        t = make_tts(engine, on_first_audio=seen.append)
        cs = t.synthesize("Hello there.", conn_options=NO_RETRY)
        frames = []
        t0 = time.monotonic()

        async def consume():
            async for ev in cs:
                frames.append(ev.frame.duration)

        task = asyncio.create_task(consume())
        gates[0].set()
        for _ in range(100):
            if frames:
                break
            await asyncio.sleep(0.02)
        assert frames, "no audio emitted while the second chunk is still withheld"
        assert not gates[1].is_set()
        assert len(seen) == 1 and seen[0] >= t0
        before_second = len(frames)
        gates[1].set()
        await asyncio.wait_for(task, 10)
        await cs.aclose()
        await t.aclose()
        return engine, before_second, frames

    engine, before_second, frames = asyncio.run(go())
    assert abs(sum(frames) - 2.0) < 0.05
    assert len(frames) > before_second
    assert b'"vp-ava"' in engine.posts[0] and b"Hello there." in engine.posts[0]
    assert engine.gets == ["/requests/rq1"]


def test_failed_state_after_stream_raises_api_error():
    async def go():
        gate = asyncio.Event()
        gate.set()
        engine = FakeEngine(GatedStream([ONE_SECOND], [gate]), state="failed")
        cs = make_tts(engine).synthesize("Hello there.", conn_options=NO_RETRY)
        with pytest.raises(APIError, match="synthesis_failed"):
            async for _ in cs:
                pass
        await cs.aclose()

    asyncio.run(go())


def test_truncated_http_stream_raises_connection_error():
    async def go():
        gate = asyncio.Event()
        gate.set()
        stream = GatedStream([ONE_SECOND], [gate], fail_with=httpx.RemoteProtocolError("peer closed"))
        cs = make_tts(FakeEngine(stream)).synthesize("Hello there.", conn_options=NO_RETRY)
        with pytest.raises(APIConnectionError):
            async for _ in cs:
                pass
        await cs.aclose()

    asyncio.run(go())


def test_http_error_status_maps_to_status_error():
    async def go():
        engine = FakeEngine(GatedStream([], []), status=404)
        cs = make_tts(engine).synthesize("Hi.", conn_options=NO_RETRY)
        with pytest.raises(APIStatusError) as ei:
            async for _ in cs:
                pass
        await cs.aclose()
        return ei.value

    err = asyncio.run(go())
    assert err.status_code == 404 and err.retryable is False


def test_unreachable_engine_raises_connection_error():
    async def go():
        def refuse(request):
            raise httpx.ConnectError("refused")

        t = ChatterboxTTS(base_url="http://engine.test", profile_id="vp", transport=httpx.MockTransport(refuse))
        cs = t.synthesize("Hi.", conn_options=NO_RETRY)
        with pytest.raises(APIConnectionError):
            async for _ in cs:
                pass
        await cs.aclose()

    asyncio.run(go())


def test_cancellation_closes_http_stream_promptly():
    async def go():
        gates = [asyncio.Event(), asyncio.Event()]
        stream = GatedStream([ONE_SECOND, ONE_SECOND], gates)
        engine = FakeEngine(stream)
        t = make_tts(engine)
        cs = t.synthesize("A longer sentence for barge in.", conn_options=NO_RETRY)
        got = asyncio.Event()

        async def consume():
            async for _ in cs:
                got.set()

        task = asyncio.create_task(consume())
        gates[0].set()
        await asyncio.wait_for(got.wait(), 10)
        assert not stream.closed.is_set()
        t0 = time.monotonic()
        await cs.aclose()  # barge-in: the framework closes the stream while chunk 2 is still being generated
        await asyncio.wait_for(stream.closed.wait(), 1.0)
        elapsed = time.monotonic() - t0
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await t.aclose()
        return elapsed, engine

    elapsed, engine = asyncio.run(go())
    assert elapsed < 1.0
    assert engine.gets == []  # a cancelled stream is not treated as a completion check
