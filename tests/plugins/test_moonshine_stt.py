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
