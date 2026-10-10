import asyncio
import time
import wave
from pathlib import Path

import pytest
from livekit import rtc
from livekit.agents import stt

from service015.stt.moonshine import MoonshineSTT, load_transcriber

WAV = Path(__file__).resolve().parents[2] / "assets" / "test_audio" / "q_name.wav"


@pytest.fixture(scope="module")
def transcriber():
    t0 = time.monotonic()
    t = load_transcriber("en", "small")
    print(f"\nmoonshine small load: {time.monotonic() - t0:.1f}s")
    return t


def _frames(path: Path, ms: int = 20, trailing_silence_s: float = 1.0) -> list[rtc.AudioFrame]:
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 16000
        pcm = w.readframes(w.getnframes())
    pcm += b"\x00\x00" * int(16000 * trailing_silence_s)
    step = 16 * ms * 2
    out = []
    for i in range(0, len(pcm), step):
        chunk = pcm[i : i + step]
        out.append(rtc.AudioFrame(chunk, 16000, 1, len(chunk) // 2))
    return out


async def _collect(stream: stt.RecognizeStream) -> list[stt.SpeechEvent]:
    return [ev async for ev in stream]


def test_streaming_transcribes_real_wav(transcriber):
    async def go():
        engine = MoonshineSTT(transcriber)
        stream = engine.stream()
        collector = asyncio.create_task(_collect(stream))
        for f in _frames(WAV):
            stream.push_frame(f)
            await asyncio.sleep(0)
        stream.end_input()
        events = await asyncio.wait_for(collector, 60)
        await stream.aclose()
        return events

    events = asyncio.run(go())
    types = [e.type for e in events]
    finals = [e for e in events if e.type == stt.SpeechEventType.FINAL_TRANSCRIPT]
    assert finals, f"no FINAL_TRANSCRIPT in {types}"
    assert "name" in " ".join(e.alternatives[0].text for e in finals).lower()
    assert types[0] == stt.SpeechEventType.START_OF_SPEECH
    assert types[-1] == stt.SpeechEventType.END_OF_SPEECH
    assert types.index(stt.SpeechEventType.FINAL_TRANSCRIPT) < len(types) - 1  # END_OF_SPEECH follows the final
    assert types.count(stt.SpeechEventType.START_OF_SPEECH) == types.count(stt.SpeechEventType.END_OF_SPEECH)


def test_loop_stays_responsive_while_feeding(transcriber):
    async def go():
        stream = MoonshineSTT(transcriber).stream()
        collector = asyncio.create_task(_collect(stream))
        ticks, worst = 0, 0.0
        last = time.monotonic()
        for f in _frames(WAV):
            stream.push_frame(f)
            await asyncio.sleep(0.005)
            now = time.monotonic()
            worst = max(worst, now - last)
            last = now
            ticks += 1
        stream.end_input()
        await asyncio.wait_for(collector, 60)
        await stream.aclose()
        return worst

    worst = asyncio.run(go())
    print(f"worst loop gap while feeding: {worst * 1000:.0f} ms")
    assert worst < 0.5


def test_offline_recognize_not_supported(transcriber):
    assert MoonshineSTT(transcriber).capabilities.offline_recognize is False


def test_worker_failure_surfaces_as_api_error():
    from livekit.agents import APIError

    class Boom:
        def create_stream(self, **_):
            raise RuntimeError("model gone")

    async def go():
        stream = MoonshineSTT(Boom()).stream()
        errors = []
        engine_errors = []
        stream._stt.on("error", lambda e: engine_errors.append(e))
        stream.push_frame(rtc.AudioFrame(b"\x00\x00" * 320, 16000, 1, 320))
        try:
            async for _ in stream:
                pass
        except APIError as exc:
            errors.append(exc)
        await stream.aclose()
        return errors, engine_errors

    errors, engine_errors = asyncio.run(go())
    assert engine_errors and isinstance(engine_errors[0].error, APIError)
    assert "model gone" in str(engine_errors[0].error)


# ---------- review fixes: close, Error events, lock/concurrency, flush, downmix, backlog, language ----------

import numpy as np  # noqa: E402
from moonshine_voice.transcriber import Error as MoonshineErrorEvent  # noqa: E402

from service015.stt.moonshine import SAMPLE_RATE, _WorkQueue  # noqa: E402


class FakeMStream:
    def __init__(self, on_add=None):
        self.listeners, self.closed, self.stopped, self.added = [], threading.Event(), 0, []
        self._on_add = on_add

    def add_listener(self, fn):
        self.listeners.append(fn)

    def start(self):
        pass

    def stop(self):
        self.stopped += 1

    def add_audio(self, data, sample_rate):
        self.added.append(len(data))
        if self._on_add:
            self._on_add(self)

    def close(self):
        self.closed.set()


class FakeTranscriber:
    def __init__(self, on_add=None):
        self.streams = []
        self._on_add = on_add

    def create_stream(self, **_):
        s = FakeMStream(self._on_add)
        self.streams.append(s)
        return s


import threading  # noqa: E402


def _frame(n=320, channels=1):
    return rtc.AudioFrame(b"\x10\x00" * n * channels, 16000, channels, n)


def test_moonshine_error_event_surfaces_as_api_error_and_stream_is_closed():
    from livekit.agents import APIError

    tr = FakeTranscriber(on_add=lambda ms: [fn(MoonshineErrorEvent(error=RuntimeError("native boom"), stream_handle=1)) for fn in ms.listeners])

    async def go():
        engine = MoonshineSTT(tr)
        errors = []
        engine.on("error", errors.append)
        stream = engine.stream()
        stream.push_frame(_frame())
        with pytest.raises(APIError, match="native boom"):
            async for _ in stream:
                pass
        await stream.aclose()
        return errors

    errors = asyncio.run(go())
    assert errors and "native boom" in str(errors[0].error)
    assert tr.streams[0].closed.wait(5), "native stream must be closed after a failure"


def test_native_stream_closed_on_normal_end_and_on_cancel():
    tr = FakeTranscriber()

    async def go():
        a = MoonshineSTT(tr).stream()
        a.push_frame(_frame())
        a.end_input()
        async for _ in a:
            pass
        await a.aclose()
        b = MoonshineSTT(tr).stream()
        b.push_frame(_frame())
        await asyncio.sleep(0.2)
        await b.aclose()  # cancelled mid-stream

    asyncio.run(go())
    assert len(tr.streams) == 2
    assert all(s.closed.wait(5) for s in tr.streams)
    assert tr.streams[0].stopped >= 1  # end_input = flush + stop: the tail is transcribed
    assert tr.streams[1].stopped == 0  # cancel skips the final transcription


def test_stereo_frames_are_downmixed_to_mono():
    tr = FakeTranscriber()

    async def go():
        s = MoonshineSTT(tr).stream()
        s.push_frame(_frame(320, channels=2))
        s.end_input()
        async for _ in s:
            pass
        await s.aclose()

    asyncio.run(go())
    assert tr.streams[0].added == [320]


def test_language_mismatch_raises():
    async def go():
        with pytest.raises(ValueError, match="language"):
            MoonshineSTT(FakeTranscriber(), language="en").stream(language="fr")
        ok = MoonshineSTT(FakeTranscriber(), language="en").stream(language="en-US")
        await ok.aclose()

    asyncio.run(go())


def test_work_queue_drops_oldest_audio_but_never_control_items():
    q = _WorkQueue(max_samples=1000)
    q.put_audio(np.full(600, 1, dtype=np.float32))
    q.put_control("flush")
    q.put_audio(np.full(600, 2, dtype=np.float32))  # 1200 > 1000: oldest audio dropped
    first = q.get()
    assert first == "flush"
    rest = q.get()
    assert isinstance(rest, np.ndarray) and rest[0] == 2
    assert q.dropped_samples == 600


def test_flush_without_trailing_silence_finalizes(transcriber):
    async def go():
        stream = MoonshineSTT(transcriber).stream()
        events = []
        got_final = asyncio.Event()

        async def collect():
            async for ev in stream:
                events.append(ev)
                if ev.type == stt.SpeechEventType.END_OF_SPEECH:
                    got_final.set()

        task = asyncio.create_task(collect())
        for f in _frames(WAV, trailing_silence_s=0.0):
            stream.push_frame(f)
            await asyncio.sleep(0)
        stream.flush()
        await asyncio.wait_for(got_final.wait(), 30)  # arrives via flush(), before end_input()
        stream.end_input()
        await asyncio.wait_for(task, 30)
        await stream.aclose()
        return events

    events = asyncio.run(go())
    types = [e.type for e in events]
    finals = [e for e in events if e.type == stt.SpeechEventType.FINAL_TRANSCRIPT]
    assert finals and "name" in finals[0].alternatives[0].text.lower()
    assert types.index(stt.SpeechEventType.FINAL_TRANSCRIPT) < types.index(stt.SpeechEventType.END_OF_SPEECH)


def test_two_concurrent_streams_on_one_transcriber_both_finalize(transcriber):
    async def one(engine):
        stream = engine.stream()
        task = asyncio.create_task(_collect(stream))
        for f in _frames(WAV):
            stream.push_frame(f)
            await asyncio.sleep(0.005)
        stream.end_input()
        events = await asyncio.wait_for(task, 120)
        await stream.aclose()
        return events

    async def go():
        engine = MoonshineSTT(transcriber)
        return await asyncio.gather(one(engine), one(engine))

    for events in asyncio.run(go()):
        finals = [e for e in events if e.type == stt.SpeechEventType.FINAL_TRANSCRIPT]
        assert finals and "name" in " ".join(e.alternatives[0].text for e in finals).lower()


# ---------- coalescing: several Moonshine lines of one user turn -> ONE FINAL ----------


class LineStarted:
    def __init__(self, line):
        self.line = line


class LineUpdated(LineStarted):
    pass


class LineCompleted(LineStarted):
    pass


class _Line:
    def __init__(self, text, start=0.0, duration=1.0):
        self.text, self.start_time, self.duration = text, start, duration


def _run_lines(emit, quiet_s=0.15, wait_s=0.6):
    """Feeds one frame; `emit(listeners)` plays Moonshine events from the worker thread; returns the SpeechEvents."""
    fired = []

    def on_add(ms):
        if not fired:
            fired.append(1)
            emit(ms.listeners)

    async def go():
        stream = MoonshineSTT(FakeTranscriber(on_add=on_add)).stream()
        stream.coalesce_quiet_s = quiet_s
        stream.push_frame(_frame())
        await asyncio.sleep(wait_s)
        stream.end_input()
        events = [ev async for ev in stream]
        await stream.aclose()
        return events

    return asyncio.run(go())


def _types(events, t):
    return [e for e in events if e.type == t]


def test_multi_segment_utterance_yields_exactly_one_final():
    def emit(ls):
        for fn in ls:
            fn(LineStarted(_Line("hello", 0.0, 0.4)))
            fn(LineUpdated(_Line("hello there", 0.0, 0.8)))
            fn(LineCompleted(_Line("hello there", 0.0, 1.0)))
            fn(LineCompleted(_Line("how are you", 1.2, 0.9)))
            fn(LineCompleted(_Line("today", 2.3, 0.4)))

    events = _run_lines(emit)
    finals = _types(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert len(finals) == 1
    assert finals[0].alternatives[0].text == "hello there how are you today"
    assert len(_types(events, stt.SpeechEventType.END_OF_SPEECH)) == 1
    assert _types(events, stt.SpeechEventType.INTERIM_TRANSCRIPT), "interim behavior is unchanged"
    types = [e.type for e in events]
    assert types.index(stt.SpeechEventType.FINAL_TRANSCRIPT) < types.index(stt.SpeechEventType.END_OF_SPEECH)


def test_lines_separated_by_a_long_pause_are_separate_turns():
    def emit(ls):
        def later():
            time.sleep(0.5)  # longer than the quiet window: a new utterance
            for fn in ls:
                fn(LineCompleted(_Line("second turn", 5.0, 1.0)))

        for fn in ls:
            fn(LineCompleted(_Line("first turn", 0.0, 1.0)))
        threading.Thread(target=later).start()

    events = _run_lines(emit, quiet_s=0.15, wait_s=1.2)
    assert [e.alternatives[0].text for e in _types(events, stt.SpeechEventType.FINAL_TRANSCRIPT)] == ["first turn", "second turn"]
    assert len(_types(events, stt.SpeechEventType.END_OF_SPEECH)) == 2


def test_flush_emits_the_buffered_lines_as_one_final_immediately():
    def emit(ls):
        for fn in ls:
            fn(LineCompleted(_Line("one", 0.0, 0.5)))
            fn(LineCompleted(_Line("two", 0.6, 0.5)))

    # a huge quiet window: only end of input can release the FINAL
    events = _run_lines(emit, quiet_s=30.0, wait_s=0.3)
    finals = _types(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert [e.alternatives[0].text for e in finals] == ["one two"]
    assert len(_types(events, stt.SpeechEventType.END_OF_SPEECH)) == 1
