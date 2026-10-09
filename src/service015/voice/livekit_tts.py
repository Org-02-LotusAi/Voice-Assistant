"""LiveKit Agents TTS plugin that streams PCM from the voice engine sidecar (engine/, POST /synthesize/stream).

The sidecar protocol (headers, truncation detection via GET /requests/{id}) lives in `service015.voice.engine`
(`open_engine_stream`); this module only maps its neutral `EngineStreamError` to the framework's API errors, so a truncated
or failed stream always raises and never ends silently. Cancellation (barge-in) leaves the `async with`, which closes the HTTP
response immediately.

`on_first_audio(t_monotonic)` (T3): fires at most once per `synthesize()` call, at the first PCM byte received from the engine
(before the framework's emitter buffers it into 200 ms frames). The framework calls `synthesize()` once per sentence, so
latching "first audio of the turn" is the caller's (runtime's) job.

Retry policy: once any audio bytes were received the error is made non-retryable, so the framework never replays a sentence the
listener has already (partly) heard. Retries happen only for failures before the first audio byte.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx
from livekit.agents import APIConnectionError, APIConnectOptions, APIError, APIStatusError, APITimeoutError, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

from service015.voice.engine import SAMPLE_RATE, EngineStreamError, open_engine_stream

log = logging.getLogger("service015.voice")

NUM_CHANNELS = 1
FirstAudioCallback = Callable[[float], None]


class ChatterboxTTS(tts.TTS):
    """Non-streaming-input TTS (the framework wraps it in a sentence adapter); the audio output is streamed."""

    def __init__(
        self,
        *,
        base_url: str,
        profile_id: str,
        read_timeout_s: float = 120.0,
        on_first_audio: FirstAudioCallback | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False), sample_rate=SAMPLE_RATE, num_channels=NUM_CHANNELS
        )
        self._base = base_url.rstrip("/")
        self._profile_id = profile_id
        self._read_timeout_s = read_timeout_s
        self.on_first_audio = on_first_audio
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def model(self) -> str:
        return "chatterbox-turbo"

    @property
    def provider(self) -> str:
        return "chatterbox"

    @property
    def profile_id(self) -> str:
        return self._profile_id

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(10.0))
        return self._client

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChatterboxChunkedStream:
        return ChatterboxChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class ChatterboxChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: ChatterboxTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._engine: ChatterboxTTS = tts
        self._first_audio_fired = False

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        eng = self._engine
        timeout = httpx.Timeout(
            connect=self._conn_options.timeout, read=eng._read_timeout_s, write=self._conn_options.timeout,
            pool=self._conn_options.timeout,
        )
        pushed = 0
        try:
            # leaving this block (normally, on error, or on CancelledError from a barge-in) closes the response
            async with open_engine_stream(
                eng._http(), eng._base, eng._profile_id, self._input_text, timeout=timeout,
                on_first_byte=self._notify_first_audio,
            ) as es:
                output_emitter.initialize(
                    request_id=es.request_id or "unknown", sample_rate=SAMPLE_RATE, num_channels=NUM_CHANNELS,
                    mime_type="audio/pcm",
                )
                async for chunk in es.chunks():
                    pushed += len(chunk)
                    output_emitter.push(chunk)
                await es.verify()
                output_emitter.flush()
        except EngineStreamError as exc:
            raise _api_error(exc, audio_received=pushed > 0) from exc

    def _notify_first_audio(self) -> None:
        cb = self._engine.on_first_audio
        if cb is None or self._first_audio_fired:
            return
        self._first_audio_fired = True
        try:
            cb(time.monotonic())
        except Exception:
            # a telemetry hook must not kill speech, but it must not vanish either
            log.exception("on_first_audio callback raised")


def _api_error(exc: EngineStreamError, *, audio_received: bool) -> APIError:
    retryable = exc.retryable and not audio_received
    if exc.kind == "timeout":
        return APITimeoutError(f"voice engine timed out: {exc.detail}", retryable=retryable)
    if exc.kind == "connection":
        return APIConnectionError(f"voice engine stream failed: {exc.detail}", retryable=retryable)
    if exc.kind == "http_status":
        return APIStatusError(
            f"voice engine returned {exc.status} {exc.code}", status_code=exc.status or -1,
            request_id=exc.request_id, body=exc.body.decode("utf-8", "replace") or None, retryable=retryable,
        )
    return APIError(
        f"voice engine stream did not complete: {exc.code}: {exc.detail}", body=exc.details or None, retryable=retryable
    )
